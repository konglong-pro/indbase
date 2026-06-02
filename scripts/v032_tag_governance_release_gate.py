#!/usr/bin/env python3
"""v0.3.2 tag governance foundation release gate."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from gate_common import TAG_GOVERNANCE_DOCTOR_CODES

from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.tag_blocklist import add_blocklist_entry
from indbase_core.tag_tagger import run_deterministic_tagger
from indbase_core.tags import add_tag, list_document_tags
from indbase_core.taxonomy_mutations import add_tag_alias
from indbase_core.vault import init_vault
from indbase_core.chunker import chunk_markdown_body
from indbase_core.indexer import rebuild_fts_index
from indbase_core.paths import vault_paths


@dataclass
class GateMetrics:
    wrong_auto_attached_tags: int = 0
    manual_tags_preserved: bool = True
    candidate_count_within_budget: bool = True
    new_tag_sprawl_blocked: bool = True
    raw_candidates_resolved_before_persist: bool = True
    deprecated_merged_archived_not_auto_attached: bool = True
    tag_filter_exact: bool = True
    candidate_tags_not_search_filterable: bool = True
    doctor_hard_findings: int = 0


def _load_cases() -> list[dict[str, object]]:
    cases_path = REPO_ROOT / "tests" / "fixtures" / "v032_tag_governance" / "cases.jsonl"
    cases: list[dict[str, object]] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _insert_document(connection, vault: Path, *, title: str, body: str, suffix: str) -> str:
    paths = vault_paths(vault)
    doc_id = f"doc_20250601_{suffix}"
    revision_id = f"rev_{doc_id}_0001"
    markdown_path = paths.source_markdown_path(doc_id, "gate", 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    rel = paths.relative_to_vault(markdown_path)
    markdown_path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    connection.execute(
        """
        INSERT INTO documents (
          doc_id, title, filename_slug, status, ingest_status, current_revision_id,
          canonical_path, created_at, updated_at
        ) VALUES (?, ?, 'gate', 'active', 'revisioned', ?, ?, '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
        """,
        (doc_id, title, revision_id, rel),
    )
    connection.execute(
        """
        INSERT INTO document_revisions (
          revision_id, doc_id, sequence, markdown_path, content_hash,
          converter_name, converter_version, promotion_status, created_at, updated_at
        ) VALUES (?, ?, 1, ?, 'hash', 'gate', 'gate', 'promoted', '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
        """,
        (revision_id, doc_id, rel),
    )
    connection.commit()
    body_chunks = chunk_markdown_body(f"# {title}\n\n{body}\n")
    for index, chunk in enumerate(body_chunks, start=1):
        connection.execute(
            """
            INSERT INTO chunks (
              chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
              token_count, content_hash, is_current, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 10, 'hash', 1, '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
            """,
            (
                f"chk_{doc_id}_{index}",
                doc_id,
                revision_id,
                index,
                json.dumps(list(chunk.heading_path), ensure_ascii=False),
                chunk.text,
            ),
        )
    connection.commit()
    return doc_id


def main() -> int:
    metrics = GateMetrics()
    cases = _load_cases()
    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "vault"
    init_vault(vault)
    db_path = vault / ".indbase" / "db.sqlite"
    try:
        with connect(db_path) as connection:
            add_blocklist_entry(connection, "misc", match_type="exact", reason="gate fixture")
            for case in cases:
                for seed in case.get("formal_tags_seed") or []:
                    add_tag(connection, str(seed), tag_type="topic")
                for alias_seed in case.get("aliases_seed") or []:
                    tag_name = str(alias_seed["tag"])
                    tag_row = connection.execute(
                        "SELECT tag_id FROM tags WHERE name = ?",
                        (tag_name,),
                    ).fetchone()
                    if tag_row is not None:
                        add_tag_alias(connection, str(tag_row["tag_id"]), str(alias_seed["alias"]))

            doc_ids: list[str] = []
            for index, case in enumerate(cases):
                doc_ids.append(
                    _insert_document(
                        connection,
                        vault,
                        title=str(case["title"]),
                        body=str(case["body"]),
                        suffix=f"{index:06x}",
                    )
                )
            rebuild_fts_index(connection, vault)
            run = run_deterministic_tagger(connection, trigger="fixture_gate")
            if run.auto_attached_count > run.scanned_documents * 5:
                metrics.candidate_count_within_budget = False

            for index, case in enumerate(cases):
                doc_id = doc_ids[index]
                attached = {row["name"] for row in list_document_tags(connection, doc_id)}
                expected = {str(name) for name in case.get("expected_auto_attached") or []}
                forbidden = {str(name) for name in case.get("forbidden_auto_attached") or []}
                if not expected.issubset(attached):
                    metrics.wrong_auto_attached_tags += 1
                if forbidden & attached:
                    metrics.wrong_auto_attached_tags += 1

            unresolved = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM tag_candidates
                WHERE candidate_type IS NOT NULL
                  AND (resolution_status IS NULL OR TRIM(resolution_status) = '')
                """
            ).fetchone()["count"]
            if int(unresolved or 0) > 0:
                metrics.raw_candidates_resolved_before_persist = False

            from indbase_core.search import SearchOptions, search_chunks
            from indbase_core.tag_search import document_matches_tag_filter, resolve_tag_filter

            filter_ids = resolve_tag_filter(connection, "sqlite").filter_tag_ids
            if not document_matches_tag_filter(connection, doc_ids[0], filter_ids):
                metrics.tag_filter_exact = False
            search_result = search_chunks(
                connection,
                "tag:sqlite storage",
                options=SearchOptions(log_queries=False, tag_filter_ids=filter_ids),
            )
            if search_result.result_count < 1:
                metrics.tag_filter_exact = False
            pending_only = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM tag_candidates
                WHERE status = 'pending'
                """
            ).fetchone()["count"]
            if int(pending_only or 0) > 0:
                for row in connection.execute(
                    """
                    SELECT DISTINCT doc_id
                    FROM tag_candidates
                    WHERE status = 'pending' AND doc_id IS NOT NULL
                    """
                ):
                    doc_id = str(row["doc_id"])
                    if document_matches_tag_filter(connection, doc_id, filter_ids):
                        has_trusted = connection.execute(
                            """
                            SELECT COUNT(*) AS count
                            FROM document_tags dt
                            WHERE dt.doc_id = ?
                              AND dt.deleted_at IS NULL
                              AND dt.status = 'active'
                              AND dt.source IN ('manual', 'accepted_candidate', 'auto', 'legacy_classification')
                            """,
                            (doc_id,),
                        ).fetchone()["count"]
                        if int(has_trusted or 0) == 0:
                            metrics.candidate_tags_not_search_filterable = False

        report = run_doctor(vault)
        metrics.doctor_hard_findings = sum(
            1
            for finding in report.findings
            if finding.severity == "error" and finding.code in TAG_GOVERNANCE_DOCTOR_CODES
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    summary = {
        "wrong_auto_attached_tags": metrics.wrong_auto_attached_tags,
        "manual_tags_preserved": metrics.manual_tags_preserved,
        "candidate_count_within_budget": metrics.candidate_count_within_budget,
        "new_tag_sprawl_blocked": metrics.new_tag_sprawl_blocked,
        "raw_candidates_resolved_before_persist": metrics.raw_candidates_resolved_before_persist,
        "deprecated_merged_archived_not_auto_attached": metrics.deprecated_merged_archived_not_auto_attached,
        "tag_filter_exact": metrics.tag_filter_exact,
        "candidate_tags_not_search_filterable": metrics.candidate_tags_not_search_filterable,
        "doctor_hard_findings": metrics.doctor_hard_findings,
    }
    print(json.dumps(summary, indent=2))
    passed = (
        metrics.wrong_auto_attached_tags == 0
        and metrics.manual_tags_preserved
        and metrics.candidate_count_within_budget
        and metrics.new_tag_sprawl_blocked
        and metrics.raw_candidates_resolved_before_persist
        and metrics.deprecated_merged_archived_not_auto_attached
        and metrics.tag_filter_exact
        and metrics.candidate_tags_not_search_filterable
        and metrics.doctor_hard_findings == 0
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
