"""Shared helpers for v0.2 release gates."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
IND_B = ROOT / ".venv" / "Scripts" / "indb.exe"

TRUSTED_NEEDLE = "TRUSTED_GATE_NEEDLE_42"
REVIEW_NEEDLE = "REVIEW_GATE_NEEDLE_SHORT"

TRUSTED_MARKDOWN = (
    "# Trusted Gate Fixture\n\n"
    + (
        "This fixture body is intentionally long enough to pass the indbase swallow promotion "
        "gate, produce trusted-current revisions, and remain searchable in default FTS. "
    )
    * 5
    + f"\n\nNeedle: {TRUSTED_NEEDLE}\n"
)

SHORT_MARKDOWN = f"# Short Review Fixture\n\nBrief. {REVIEW_NEEDLE}\n"


def run_pytest_gate() -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pytest gate failed (exit {completed.returncode})\n{completed.stdout}")
    return {"status": "passed", "layer": "A", "name": "pytest"}


def run_compile_gate() -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"compile gate failed (exit {completed.returncode})")
    return {"status": "passed", "layer": "B", "name": "compileall"}


def gate_skip(layer: str, name: str, reason: str) -> dict[str, object]:
    return {"status": "skipped", "layer": layer, "name": name, "reason": reason}


def install_deterministic_swallow_stub() -> None:
    """Patch swallow adapter before importing ingest/conversion pipelines."""
    import indbase_core.conversion as conversion_module
    from indbase_core.normalizers import normalize_tier1_source, normalize_tier2_source
    from indbase_core.swallow_adapter import (
        ArchiveExpansionCandidate,
        ArchiveLogicalDocumentCandidate,
        ArtifactManifest,
        ConversionCandidate,
        SwallowIngestAdapter,
        SwallowProvenance,
    )

    class DeterministicSwallowAdapter:
        def __init__(self, *, vault_path: Path | str, config) -> None:
            self.store_root = Path(vault_path) / ".indbase" / "cache" / "swallow"
            self.config = config

        def _job_id(self, key: str) -> str:
            return "gate_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

        def _write_job_files(self, job_id: str) -> tuple[str, str, str]:
            job_dir = self.store_root / "jobs" / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            trace_rel = f"jobs/{job_id}/trace.jsonl"
            manifest_rel = f"jobs/{job_id}/manifest.json"
            ingest_rel = f"jobs/{job_id}/ingest_document.json"
            (self.store_root / trace_rel).write_text('{"event":"deterministic_gate"}\n', encoding="utf-8")
            (self.store_root / manifest_rel).write_text('{"gate":"deterministic"}\n', encoding="utf-8")
            (self.store_root / ingest_rel).write_text('{"gate":"deterministic"}\n', encoding="utf-8")
            return trace_rel, manifest_rel, ingest_rel

        def convert_file(self, path: Path | str) -> ConversionCandidate:
            source = Path(path)
            source_type = source.suffix.lower().lstrip(".")
            if source_type in {"md", "txt", "html", "csv", "json"}:
                normalized = normalize_tier1_source(source, source_type)
            else:
                normalized = normalize_tier2_source(source, source_type)

            job_id = self._job_id(str(source))
            trace_rel, manifest_rel, ingest_rel = self._write_job_files(job_id)
            body = normalized.markdown
            is_short = "short-review" in source.name.lower() or len(body.strip()) < 80
            if is_short:
                return ConversionCandidate(
                    title=source.stem,
                    markdown_body=body,
                    status="partial",
                    quality_score=0.72,
                    warnings=("text_length_below_300",),
                    provenance=SwallowProvenance(
                        swallow_job_id=job_id,
                        swallow_raw_id=f"raw_{job_id}",
                        swallow_document_id=f"doc_{job_id}",
                        swallow_version="deterministic-gate",
                        primary_worker="plain_text_worker",
                        worker_version="gate",
                        worker_chain=("plain_text_worker@gate", "quality_checker@gate"),
                        trace_path=trace_rel,
                        manifest_path=manifest_rel,
                        ingest_document_path=ingest_rel,
                    ),
                    artifact_manifest=ArtifactManifest(
                        required=(trace_rel, manifest_rel, ingest_rel),
                    ),
                )
            return ConversionCandidate(
                title=source.stem,
                markdown_body=body if TRUSTED_NEEDLE in body else TRUSTED_MARKDOWN,
                status="success",
                quality_score=0.94,
                warnings=normalized.warnings,
                provenance=SwallowProvenance(
                    swallow_job_id=job_id,
                    swallow_raw_id=f"raw_{job_id}",
                    swallow_document_id=f"doc_{job_id}",
                    swallow_version="deterministic-gate",
                    primary_worker="plain_text_worker",
                    worker_version="gate",
                    worker_chain=("plain_text_worker@gate", "quality_checker@gate", "markdown_normalizer@gate"),
                    trace_path=trace_rel,
                    manifest_path=manifest_rel,
                    ingest_document_path=ingest_rel,
                ),
                artifact_manifest=ArtifactManifest(
                    required=(trace_rel, manifest_rel, ingest_rel),
                ),
                source_locators=(),
            )

        def convert_url(self, url: str) -> ConversionCandidate:
            job_id = self._job_id(url)
            trace_rel, manifest_rel, ingest_rel = self._write_job_files(job_id)
            snapshot_rel = f"jobs/{job_id}/intermediate/playwright/rendered.html"
            snapshot_path = self.store_root / snapshot_rel
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                f"<html><body><h1>Snapshot</h1><p>{TRUSTED_NEEDLE} from URL gate fixture.</p></body></html>",
                encoding="utf-8",
            )
            body = f"# URL Fixture\n\n{TRUSTED_NEEDLE} captured from browser snapshot.\n\n" + ("Supporting paragraph. " * 20)
            return ConversionCandidate(
                title="URL Fixture",
                markdown_body=body,
                status="success",
                quality_score=0.93,
                provenance=SwallowProvenance(
                    swallow_job_id=job_id,
                    swallow_raw_id=f"raw_{job_id}",
                    swallow_document_id=f"doc_{job_id}",
                    swallow_version="deterministic-gate",
                    primary_worker="playwright_worker",
                    worker_version="gate",
                    worker_chain=("playwright_worker@gate", "quality_checker@gate"),
                    trace_path=trace_rel,
                    manifest_path=manifest_rel,
                    ingest_document_path=ingest_rel,
                    access_context="public_url",
                ),
                artifact_manifest=ArtifactManifest(required=(trace_rel, manifest_rel, ingest_rel, snapshot_rel)),
                source_locators=(
                    {
                        "kind": "web_snapshot",
                        "url": url,
                        "artifact": snapshot_rel,
                    },
                ),
                source_snapshot_path=snapshot_rel,
                access_context="public_url",
            )

        def expand_archive(self, source_archive: Path | str) -> ArchiveExpansionCandidate:
            archive = Path(source_archive)
            job_id = self._job_id(str(archive))
            trace_rel, manifest_rel, ingest_rel = self._write_job_files(job_id)
            member_rel = f"jobs/{job_id}/intermediate/export_archive/conversations.json"
            (self.store_root / member_rel).parent.mkdir(parents=True, exist_ok=True)
            (self.store_root / member_rel).write_text('{"conversations": []}\n', encoding="utf-8")
            alpha_body = (
                "# Alpha Archive Child\n\n"
                f"Alpha archive needle with {TRUSTED_NEEDLE} in child one. "
                + ("Extra archive conversation text for promotion. " * 12)
            )
            beta_body = (
                "# Beta Archive Child\n\n"
                "Beta archive needle in child two. "
                + ("More trusted archive text for promotion gate length. " * 12)
            )
            aggregate = ConversionCandidate(
                title="Archive Package",
                markdown_body="# Archive Package\n\n" + TRUSTED_NEEDLE + "\n",
                status="success",
                quality_score=0.94,
                provenance=SwallowProvenance(
                    swallow_job_id=job_id,
                    swallow_raw_id=f"raw_{job_id}",
                    swallow_document_id=f"doc_{job_id}",
                    swallow_version="deterministic-gate",
                    primary_worker="export_archive_worker",
                    worker_version="gate",
                    worker_chain=("export_archive_worker@gate", "quality_checker@gate"),
                    trace_path=trace_rel,
                    manifest_path=manifest_rel,
                    ingest_document_path=ingest_rel,
                    access_context="local_archive",
                ),
                artifact_manifest=ArtifactManifest(
                    required=(trace_rel, manifest_rel, ingest_rel, member_rel),
                ),
                access_context="local_archive",
            )
            return ArchiveExpansionCandidate(
                archive_type="chatgpt_export",
                aggregate_candidate=aggregate,
                logical_documents=(
                    ArchiveLogicalDocumentCandidate(
                        logical_source_id="conv-alpha",
                        title="Alpha Archive Child",
                        markdown_body=alpha_body,
                        source_locators=(
                            {
                                "kind": "archive_member",
                                "archive_type": "chatgpt_export",
                                "member_path": "conversations.json",
                                "logical_source_id": "conv-alpha",
                                "conversation_index": 1,
                                "artifact": member_rel,
                            },
                        ),
                    ),
                    ArchiveLogicalDocumentCandidate(
                        logical_source_id="conv-beta",
                        title="Beta Archive Child",
                        markdown_body=beta_body,
                        source_locators=(
                            {
                                "kind": "archive_member",
                                "archive_type": "chatgpt_export",
                                "member_path": "conversations.json",
                                "logical_source_id": "conv-beta",
                                "conversation_index": 2,
                                "artifact": member_rel,
                            },
                        ),
                    ),
                ),
            )

    import indbase_core.swallow_adapter as swallow_adapter_module

    swallow_adapter_module.SwallowIngestAdapter = DeterministicSwallowAdapter  # type: ignore[misc]
    conversion_module.SwallowIngestAdapter = DeterministicSwallowAdapter  # type: ignore[misc]


def configure_v02_vault(
    vault_path: Path | str,
    *,
    swallow_ingest: bool = True,
    transition_output: bool = True,
    ocr: bool = False,
    asr: bool = False,
    web_ingest: bool = True,
    min_markdown_chars: int = 80,
) -> Path:
    from indbase_core.config import default_config, load_config, save_config
    from indbase_core.paths import vault_paths
    from indbase_core.transition_runtime import install_runtime

    paths = vault_paths(vault_path)
    if paths.config_path.is_file():
        config = load_config(paths.config_path)
    else:
        config = default_config(paths.root)
    config = replace(
        config,
        features=replace(
            config.features,
            swallow_ingest=swallow_ingest,
            transition_output=transition_output,
            ocr=ocr,
            asr=asr,
            web_ingest=web_ingest,
        ),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=min_markdown_chars),
        ),
    )
    save_config(config, paths.config_path)
    if transition_output:
        install_runtime(paths.root, run_npm_install=False)
    return paths.root


def doctor_hard_metrics(vault_path: Path | str) -> dict[str, object]:
    from indbase_core.doctor import run_doctor

    report = run_doctor(vault_path)
    hard = [
        finding
        for finding in report.findings
        if finding.severity in {"error", "critical"}
    ]
    return {
        "exit_code": report.exit_code,
        "critical_doctor_findings": len(hard),
        "codes": [finding.code for finding in hard],
        "findings": [
            {"severity": finding.severity, "code": finding.code, "message": finding.message}
            for finding in hard
        ],
    }


def assert_doctor_clean(vault_path: Path | str) -> None:
    metrics = doctor_hard_metrics(vault_path)
    if metrics["critical_doctor_findings"]:
        raise RuntimeError(f"doctor hard findings != 0: {metrics}")


def search_count(vault_path: Path | str, query: str) -> int:
    from indbase_core.db import connect
    from indbase_core.search import SearchOptions, search_chunks

    connection = connect(Path(vault_path) / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(connection, query, options=SearchOptions(top_k=5))
        return len(result.results)
    finally:
        connection.close()


def write_gate_summary(summary: dict[str, object], *, gate_name: str, gate_root: Path | None = None) -> None:
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print(f"{gate_name}=passed")
    if gate_root is not None:
        print(f"GATE_ROOT={gate_root}")


def copy_vault(vault: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(vault, destination)
    return destination


def make_archive_fixture(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("conversations.json", json.dumps({"conversations": []}))


def env_enabled(name: str) -> bool:
    return os.environ.get(name) == "1"


def run_subprocess_gate(script_name: str) -> dict[str, object]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    src_path = str(ROOT / "src")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name)],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{script_name} failed (exit {completed.returncode})\n{completed.stdout}")
    summary: dict[str, object] = {"status": "passed", "script": script_name}
    for line in completed.stdout.splitlines():
        if line.startswith("{") and line.endswith("}"):
            try:
                summary.update(json.loads(line))
            except json.JSONDecodeError:
                pass
    return summary
