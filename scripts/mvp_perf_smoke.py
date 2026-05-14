"""Run MVP performance smoke scenarios and emit timing telemetry.

The smoke is diagnostic: it fails only on crashes or consistency breakage.
Timing values are recorded for release close-out comparison, not used as
fixed product promises.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import statistics
import time

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.embeddings import rebuild_vector_index
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()


@dataclass(frozen=True)
class TimedResult:
    seconds: float
    value: object


def main() -> None:
    root = ROOT / ".tmp" / f"mvp-perf-smoke-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)

    scenarios = [
        _run_many_small_files(root / "small-100", count=100),
        _run_many_small_files(root / "medium-500", count=500),
        _run_many_small_files(root / "large-1000", count=1000),
        _run_large_payloads(root / "large-payloads"),
        _run_pdf_batch(root / "pdf-batch", count=20),
        _run_ocr_sidecar_batch(root / "ocr-sidecar-batch", count=20),
    ]

    aggregate = _aggregate(scenarios)
    hard_zero = {
        "critical_doctor_findings": aggregate["critical_doctor_findings"],
        "zero_chunk_current_revisions": aggregate["zero_chunk_current_revisions"],
        "source_shells_searchable": aggregate["source_shells_searchable"],
        "fts_rebuild_failed_documents": aggregate["fts_rebuild_failed_documents"],
        "vector_rebuild_failed_chunks": aggregate["vector_rebuild_failed_chunks"],
        "search_failures": aggregate["search_failures"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"MVP perf smoke hard metrics failed: {hard_zero}; aggregate={aggregate}")

    (root / "perf_scenarios.json").write_text(
        json.dumps({"aggregate": aggregate, "scenarios": scenarios}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(aggregate, sort_keys=True))
    print("MVP_PERF_SMOKE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_many_small_files(root: Path, *, count: int) -> dict[str, object]:
    vault = root / "vault"
    sources = root / "sources"
    sources.mkdir(parents=True)
    for index in range(count):
        extension = ("md", "txt", "csv", "json", "html")[index % 5]
        path = sources / f"note-{index:04d}.{extension}"
        marker = f"perfsmall{count}unique{index:04d}"
        if extension == "md":
            path.write_text(f"# Note {index}\n{marker} 大语言模型\n", encoding="utf-8")
        elif extension == "txt":
            path.write_text(f"{marker} plain text\n", encoding="utf-8")
        elif extension == "csv":
            path.write_text(f"name,value\n{marker},{index}\n", encoding="utf-8")
        elif extension == "json":
            path.write_text(json.dumps({"marker": marker, "index": index}), encoding="utf-8")
        else:
            path.write_text(f"<html><body><h1>Note {index}</h1><p>{marker}</p></body></html>", encoding="utf-8")

    init_vault(vault)
    ingest = _timed(lambda: run_m3_ingest_pipeline(vault, sources, recursive=True))
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return _measure_vault(
            name=f"small_{count}",
            root=root,
            vault=vault,
            connection=connection,
            ingest_seconds=ingest.seconds,
            extra={"input_files": count},
            search_queries=(f"perfsmall{count}unique0000", f"perfsmall{count}unique{count - 1:04d}", "大语言模型"),
        )


def _run_large_payloads(root: Path) -> dict[str, object]:
    vault = root / "vault"
    sources = root / "sources"
    sources.mkdir(parents=True)

    large_paragraph = "largepayloadunique 大语言模型 knowledge database " * 60000
    (sources / "large-markdown-5mb.md").write_text("# Large Markdown\n" + large_paragraph, encoding="utf-8")
    (sources / "large-data.csv").write_text(
        "id,label,value\n" + "\n".join(f"{index},largecsvunique,{index % 17}" for index in range(50000)),
        encoding="utf-8",
    )
    (sources / "large-data.json").write_text(
        json.dumps(
            [{"id": index, "marker": "largejsonunique", "value": index % 23} for index in range(20000)],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    init_vault(vault)
    ingest = _timed(lambda: run_m3_ingest_pipeline(vault, sources, recursive=True))
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return _measure_vault(
            name="large_payloads",
            root=root,
            vault=vault,
            connection=connection,
            ingest_seconds=ingest.seconds,
            extra={"input_files": 3},
            search_queries=("largepayloadunique", "largecsvunique", "largejsonunique"),
        )


def _run_pdf_batch(root: Path, *, count: int) -> dict[str, object]:
    vault = root / "vault"
    sources = root / "sources"
    sources.mkdir(parents=True)
    for index in range(count):
        (sources / f"text-pdf-{index:03d}.pdf").write_bytes(b"%PDF text batch")

    init_vault(vault)
    with _patched_markitdown(lambda _path: "# PDF\npdfbatchunique 大语言模型 text pdf batch\n"):
        ingest = _timed(lambda: run_m3_ingest_pipeline(vault, sources, recursive=True))
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return _measure_vault(
            name="pdf_batch",
            root=root,
            vault=vault,
            connection=connection,
            ingest_seconds=ingest.seconds,
            extra={"input_files": count},
            search_queries=("pdfbatchunique", "大语言模型"),
        )


def _run_ocr_sidecar_batch(root: Path, *, count: int) -> dict[str, object]:
    vault = root / "vault"
    sources = root / "sources"
    sources.mkdir(parents=True)
    for index in range(count):
        (sources / f"scan-{index:03d}.pdf").write_bytes(b"%PDF scanned batch")

    init_vault(vault)
    with _patched_markitdown(lambda _path: " "):
        ingest = _timed(lambda: run_m3_ingest_pipeline(vault, sources, recursive=True))

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        shell_docs = connection.execute(
            """
            SELECT doc_id, original_path
            FROM documents
            WHERE source_type = 'pdf'
              AND current_revision_id IS NULL
              AND deleted_at IS NULL
            ORDER BY created_at, doc_id
            """
        ).fetchall()
        for index, row in enumerate(shell_docs):
            sidecar = (vault / row["original_path"]).with_name("original.pdf.ocr.txt")
            sidecar.write_text(f"ocrbatchunique page text {index} 知識管理\n", encoding="utf-8")
        ocr = _timed(lambda: [run_ocr_for_document(connection, vault, str(row["doc_id"])) for row in shell_docs])
        return _measure_vault(
            name="ocr_sidecar_batch",
            root=root,
            vault=vault,
            connection=connection,
            ingest_seconds=ingest.seconds,
            extra={"input_files": count, "ocr_seconds": _round(ocr.seconds), "ocr_documents": len(shell_docs)},
            search_queries=("ocrbatchunique", "知識管理"),
        )


def _measure_vault(
    *,
    name: str,
    root: Path,
    vault: Path,
    connection,
    ingest_seconds: float,
    extra: dict[str, object],
    search_queries: tuple[str, ...],
) -> dict[str, object]:
    fts = _timed(lambda: rebuild_fts_index(connection, vault))
    vectors = _timed(lambda: rebuild_vector_index(connection))
    doctor = _timed(lambda: run_doctor(vault))
    latency = _search_latency(connection, search_queries)
    counts = _counts(connection)
    doctor_report = doctor.value
    critical = sum(1 for finding in doctor_report.findings if finding.severity in {"error", "critical"})
    fts_desync = sum(1 for finding in doctor_report.findings if finding.code.startswith("fts_"))
    scenario = {
        "name": name,
        **extra,
        **counts,
        "ingest_seconds": _round(ingest_seconds),
        "fts_rebuild_seconds": _round(fts.seconds),
        "vector_rebuild_seconds": _round(vectors.seconds),
        "doctor_seconds": _round(doctor.seconds),
        "search_latency_p50_ms": latency["p50_ms"],
        "search_latency_p95_ms": latency["p95_ms"],
        "search_failures": latency["failures"],
        "db_size_bytes": (vault / ".indbase" / "db.sqlite").stat().st_size,
        "vault_size_bytes": _directory_size(vault),
        "critical_doctor_findings": critical,
        "doctor_exit_code": doctor_report.exit_code,
        "fts_desync": fts_desync,
        "fts_rebuild_failed_documents": fts.value.failed_documents,
        "vector_rebuild_failed_chunks": vectors.value.failed_chunks,
        "zero_chunk_current_revisions": _zero_chunk_current_revisions(connection),
        "source_shells_searchable": _source_shells_searchable(connection),
    }
    return scenario


def _counts(connection) -> dict[str, int]:
    return {
        "documents": _count(connection, "SELECT COUNT(*) AS count FROM documents WHERE deleted_at IS NULL"),
        "active_documents": _count(
            connection,
            "SELECT COUNT(*) AS count FROM documents WHERE status = 'active' AND deleted_at IS NULL",
        ),
        "source_shells": _count(
            connection,
            "SELECT COUNT(*) AS count FROM documents WHERE current_revision_id IS NULL AND deleted_at IS NULL",
        ),
        "unsupported_items": _count(connection, "SELECT COUNT(*) AS count FROM ingest_items WHERE status = 'unsupported'"),
        "failed_items": _count(connection, "SELECT COUNT(*) AS count FROM ingest_items WHERE status = 'failed'"),
        "reviews": _count(connection, "SELECT COUNT(*) AS count FROM review_items"),
        "errors": _count(connection, "SELECT COUNT(*) AS count FROM errors"),
        "chunks": _count(connection, "SELECT COUNT(*) AS count FROM chunks WHERE deleted_at IS NULL"),
        "current_chunks": _count(connection, "SELECT COUNT(*) AS count FROM chunks WHERE is_current = 1 AND deleted_at IS NULL"),
        "fts_rows": _count(connection, "SELECT COUNT(*) AS count FROM chunks_fts"),
        "embeddings": _count(connection, "SELECT COUNT(*) AS count FROM embeddings WHERE deleted_at IS NULL"),
        "translations": _count(connection, "SELECT COUNT(*) AS count FROM translations WHERE deleted_at IS NULL"),
        "candidate_cards": _count(connection, "SELECT COUNT(*) AS count FROM candidate_cards WHERE deleted_at IS NULL"),
    }


def _search_latency(connection, queries: tuple[str, ...], *, repeats: int = 7) -> dict[str, int]:
    latencies: list[float] = []
    failures = 0
    for query in queries:
        for mode in ("fts", "hybrid"):
            for _repeat in range(repeats):
                start = time.perf_counter()
                result = search_chunks(connection, query, options=SearchOptions(mode=mode, log_queries=False))
                latencies.append((time.perf_counter() - start) * 1000)
                if result.result_count <= 0:
                    failures += 1
    return {
        "p50_ms": int(round(statistics.median(latencies))) if latencies else 0,
        "p95_ms": int(round(_percentile(latencies, 95))) if latencies else 0,
        "failures": failures,
    }


def _aggregate(scenarios: list[dict[str, object]]) -> dict[str, object]:
    return {
        "scenarios": len(scenarios),
        "input_files": sum(int(scenario.get("input_files", 0)) for scenario in scenarios),
        "documents": sum(int(scenario["documents"]) for scenario in scenarios),
        "source_shells": sum(int(scenario["source_shells"]) for scenario in scenarios),
        "unsupported_items": sum(int(scenario["unsupported_items"]) for scenario in scenarios),
        "failed_items": sum(int(scenario["failed_items"]) for scenario in scenarios),
        "reviews": sum(int(scenario["reviews"]) for scenario in scenarios),
        "errors": sum(int(scenario["errors"]) for scenario in scenarios),
        "chunks": sum(int(scenario["chunks"]) for scenario in scenarios),
        "current_chunks": sum(int(scenario["current_chunks"]) for scenario in scenarios),
        "fts_rows": sum(int(scenario["fts_rows"]) for scenario in scenarios),
        "embeddings": sum(int(scenario["embeddings"]) for scenario in scenarios),
        "translations": sum(int(scenario["translations"]) for scenario in scenarios),
        "candidate_cards": sum(int(scenario["candidate_cards"]) for scenario in scenarios),
        "critical_doctor_findings": sum(int(scenario["critical_doctor_findings"]) for scenario in scenarios),
        "fts_desync": sum(int(scenario["fts_desync"]) for scenario in scenarios),
        "zero_chunk_current_revisions": sum(int(scenario["zero_chunk_current_revisions"]) for scenario in scenarios),
        "source_shells_searchable": sum(int(scenario["source_shells_searchable"]) for scenario in scenarios),
        "fts_rebuild_failed_documents": sum(int(scenario["fts_rebuild_failed_documents"]) for scenario in scenarios),
        "vector_rebuild_failed_chunks": sum(int(scenario["vector_rebuild_failed_chunks"]) for scenario in scenarios),
        "search_failures": sum(int(scenario["search_failures"]) for scenario in scenarios),
        "ingest_seconds_total": _round(sum(float(scenario["ingest_seconds"]) for scenario in scenarios)),
        "doctor_seconds_total": _round(sum(float(scenario["doctor_seconds"]) for scenario in scenarios)),
        "fts_rebuild_seconds_total": _round(sum(float(scenario["fts_rebuild_seconds"]) for scenario in scenarios)),
        "vector_rebuild_seconds_total": _round(sum(float(scenario["vector_rebuild_seconds"]) for scenario in scenarios)),
        "search_latency_p50_ms_max": max(int(scenario["search_latency_p50_ms"]) for scenario in scenarios),
        "search_latency_p95_ms_max": max(int(scenario["search_latency_p95_ms"]) for scenario in scenarios),
        "db_size_bytes_total": sum(int(scenario["db_size_bytes"]) for scenario in scenarios),
        "vault_size_bytes_total": sum(int(scenario["vault_size_bytes"]) for scenario in scenarios),
    }


@contextmanager
def _patched_markitdown(callback):
    original = normalizers._run_markitdown_file
    normalizers._run_markitdown_file = callback
    try:
        yield
    finally:
        normalizers._run_markitdown_file = original


def _timed(callback) -> TimedResult:
    start = time.perf_counter()
    value = callback()
    return TimedResult(seconds=time.perf_counter() - start, value=value)


def _directory_size(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def _zero_chunk_current_revisions(connection) -> int:
    return _count(
        connection,
        """
        SELECT COUNT(*) AS count
        FROM documents d
        WHERE d.current_revision_id IS NOT NULL
          AND d.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM chunks c
            WHERE c.doc_id = d.doc_id
              AND c.revision_id = d.current_revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
          )
        """,
    )


def _source_shells_searchable(connection) -> int:
    return _count(
        connection,
        """
        SELECT COUNT(*) AS count
        FROM documents d
        JOIN chunks_fts f ON f.doc_id = d.doc_id
        WHERE d.current_revision_id IS NULL
          AND d.deleted_at IS NULL
        """,
    )


def _count(connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()["count"] or 0)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile / 100) * (len(ordered) - 1))))
    return ordered[index]


def _round(value: float) -> float:
    return round(value, 3)


if __name__ == "__main__":
    main()
