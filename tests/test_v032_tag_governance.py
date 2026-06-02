"""v0.3.2 tag governance foundation tests."""

from __future__ import annotations

from pathlib import Path

from conftest_output import insert_minimal_document
from indbase_core.db import connect, initialize_database
from indbase_core.tag_admission import evaluate_tag_admission
from indbase_core.tag_blocklist import add_blocklist_entry, match_blocklist
from indbase_core.tag_governance_eval import evaluate_tag_governance
from indbase_core.tag_resolution import is_auto_attach_eligible, resolve_tag_candidate
from indbase_core.tag_volume_budget import (
    TagBudgetCounters,
    TagVolumeBudget,
    check_auto_attach_budget,
    check_candidate_budget,
    record_auto_attach,
    record_candidate,
)
from indbase_core.tags import add_document_tag, add_tag, attach_document_tag_by_id, list_document_tags
from indbase_core.tag_candidates import list_tag_candidates, promote_tag_candidate, record_missing_tag_candidate
from indbase_core.tag_governance_candidates import insert_tag_governance_candidate
from indbase_core.tag_governance_eval import TagGovernanceDecision, evaluate_tag_governance
from indbase_core.tag_governance_review import (
    accept_tag_governance_candidate,
    reject_tag_governance_candidate,
)
from indbase_core.tag_resolution import resolve_tag_candidate
from indbase_core.tag_tagger import run_deterministic_tagger
from indbase_core.taxonomy_mutations import add_tag_alias, deprecate_tag, merge_tags
from indbase_core.vault import init_vault


def _column_names(connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _insert_current_chunk(
    connection,
    doc_id: str,
    revision_id: str,
    *,
    text: str,
    sequence: int = 1,
) -> str:
    chunk_id = f"chk_v032_{doc_id}_{sequence}"
    connection.execute(
        """
        INSERT INTO chunks (
          chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
          token_count, content_hash, is_current, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '[]', ?, 10, 'hash', 1, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (chunk_id, doc_id, revision_id, sequence, text),
    )
    connection.commit()
    return chunk_id


def test_v032_migration_adds_governance_tables_and_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    applied = initialize_database(db_path)

    assert "0011_v032_tag_governance_foundation" in applied

    connection = connect(db_path)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "tagger_runs",
            "tagger_results",
            "tag_feedback",
            "tag_blocklist",
            "tag_governance_events",
        } <= tables

        tag_columns = _column_names(connection, "tags")
        assert {"scope", "scope_category_id", "merged_into_tag_id", "policy_warning_json"} <= tag_columns

        alias_columns = _column_names(connection, "tag_aliases")
        assert {"locale", "source", "created_by"} <= alias_columns

        document_tag_columns = _column_names(connection, "document_tags")
        assert "candidate_id" in document_tag_columns

        candidate_columns = _column_names(connection, "tag_candidates")
        assert {
            "candidate_type",
            "raw_name",
            "target_tag_id",
            "proposed_name",
            "doc_id",
            "revision_id",
            "tagger_run_id",
            "tagger_result_id",
            "resolution_status",
            "admission_status",
            "budget_status",
            "policy_decision_json",
            "budget_decision_json",
            "review_item_id",
        } <= candidate_columns
    finally:
        connection.close()


def test_v032_preserves_manual_tag_and_legacy_candidate_flow(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "governance-smoke", tag_type="topic")
        doc_id = insert_minimal_document(connection, vault, body="# Smoke\nlegacy candidate flow.\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]

        add_document_tag(connection, doc_id, "governance-smoke")
        record_missing_tag_candidate(
            connection,
            doc_id=doc_id,
            revision_id=str(revision_id),
            tag_name="legacy-candidate",
            tag_type="topic",
        )
        candidate_id = connection.execute(
            "SELECT candidate_id FROM tag_candidates WHERE normalized_name = 'legacy-candidate'"
        ).fetchone()["candidate_id"]
        promote_tag_candidate(connection, candidate_id, tag_type="topic")

        tags = [row["name"] for row in list_document_tags(connection, doc_id)]
        candidates = list_tag_candidates(connection, status="accepted", limit=10)
        lifecycle_count = connection.execute(
            "SELECT COUNT(*) AS count FROM tag_lifecycle_events"
        ).fetchone()["count"]

    assert tag_id.startswith("tag_")
    assert "governance-smoke" in tags
    assert any(row["normalized_name"] == "legacy-candidate" for row in candidates)
    assert lifecycle_count >= 2


def test_v032_tagger_run_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    with connect(db_path) as connection:
        now = "2026-06-02T12:00:00+00:00"
        connection.execute(
            """
            INSERT INTO tagger_runs(
              tagger_run_id, trigger, tagger_version, harness_version, policy_version,
              auto_attach_threshold, per_doc_auto_attach_limit, per_doc_candidate_limit,
              per_run_new_tag_proposal_limit, per_run_total_candidate_limit,
              scanned_documents, auto_attached_count, candidate_count,
              new_tag_proposal_count, blocked_candidate_count, preserved_manual_count,
              error_count, status, created_at, finished_at
            )
            VALUES (
              'tagrun_test', 'fixture_gate', 'v032-deterministic', 'v032-harness', 'v032-policy',
              0.85, 5, 5, 20, 200,
              1, 0, 0, 0, 0, 0, 0, 'succeeded', ?, ?
            )
            """,
            (now, now),
        )
        connection.commit()
        row = connection.execute(
            "SELECT status, policy_version FROM tagger_runs WHERE tagger_run_id = 'tagrun_test'"
        ).fetchone()

    assert row["status"] == "succeeded"
    assert row["policy_version"] == "v032-policy"


def test_tag_resolution_canonical_and_alias(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        rag_id = add_tag(connection, "RAG", tag_type="method")
        add_tag_alias(connection, rag_id, "retrieval augmented generation")
        direct = resolve_tag_candidate(connection, "rag")
        alias = resolve_tag_candidate(connection, "Retrieval Augmented Generation")

    assert direct.outcome == "canonical"
    assert direct.canonical_tag_id == rag_id
    assert alias.outcome == "canonical"
    assert alias.via_alias is True
    assert alias.canonical_tag_id == rag_id
    assert is_auto_attach_eligible(direct)


def test_tag_resolution_merged_and_deprecated(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        target_id = add_tag(connection, "canonical-target", tag_type="topic")
        legacy_id = add_tag(connection, "legacy-label", tag_type="topic")
        now = "2026-06-02T12:00:00+00:00"
        connection.execute(
            """
            UPDATE tags
            SET status = 'deprecated', merged_into_tag_id = ?, updated_at = ?
            WHERE tag_id = ?
            """,
            (target_id, now, legacy_id),
        )
        connection.commit()
        merged = resolve_tag_candidate(connection, "legacy-label")
        connection.execute(
            "UPDATE tags SET status = 'deprecated', merged_into_tag_id = NULL WHERE tag_id = ?",
            (target_id,),
        )
        connection.commit()
        deprecated = resolve_tag_candidate(connection, "canonical-target")

    assert merged.outcome == "merged"
    assert merged.canonical_tag_id == target_id
    assert not is_auto_attach_eligible(merged)
    assert deprecated.outcome == "deprecated"


def test_tag_blocklist_exact_and_contains(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_blocklist_entry(connection, "spam-tag", match_type="exact")
        add_blocklist_entry(connection, "draft", match_type="contains", reason="draft noise")
        exact = resolve_tag_candidate(connection, "spam-tag")
        contains = resolve_tag_candidate(connection, "my-draft-notes")

    assert exact.outcome == "blocked"
    assert contains.outcome == "blocked"
    assert match_blocklist(connection, "unrelated") is None


def test_tag_admission_blocks_low_value_proposals(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        path_like = evaluate_tag_admission(connection, r"C:\vault\notes\draft.md")
        vague = evaluate_tag_admission(connection, "misc")
        version = evaluate_tag_admission(connection, "release v1.2.3")
        category = evaluate_tag_admission(connection, "computer science")

    assert path_like.outcome == "rejected"
    assert "path_like" in path_like.reasons
    assert vague.outcome == "rejected"
    assert "vague" in vague.reasons
    assert version.outcome == "rejected"
    assert "version_like" in version.reasons
    assert category.outcome == "rejected"
    assert "category_equivalent" in category.reasons


def test_tag_volume_budget_limits(tmp_path: Path) -> None:
    budget = TagVolumeBudget(
        per_doc_auto_attach_limit=2,
        per_doc_candidate_limit=2,
        per_run_new_tag_proposal_limit=1,
        per_run_total_candidate_limit=3,
    )
    counters = TagBudgetCounters()

    assert check_auto_attach_budget(budget, counters, doc_id="doc_a").outcome == "allowed"
    record_auto_attach(counters, doc_id="doc_a")
    record_auto_attach(counters, doc_id="doc_a")
    assert check_auto_attach_budget(budget, counters, doc_id="doc_a").outcome == "denied"

    assert (
        check_candidate_budget(budget, counters, doc_id="doc_a", is_new_tag_proposal=True).outcome
        == "allowed"
    )
    record_candidate(counters, doc_id="doc_a", is_new_tag_proposal=True)
    assert (
        check_candidate_budget(budget, counters, doc_id="doc_a", is_new_tag_proposal=False).outcome
        == "allowed"
    )
    record_candidate(counters, doc_id="doc_a", is_new_tag_proposal=False)
    assert check_candidate_budget(budget, counters, doc_id="doc_a", is_new_tag_proposal=True).outcome == "denied"


def test_tag_scope_mismatch_proposes_instead_of_canonical(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        scoped_id = add_tag(connection, "scoped-only", tag_type="topic")
        connection.execute(
            """
            UPDATE tags
            SET scope = 'category_bound', scope_category_id = 'cat_computer_science'
            WHERE tag_id = ?
            """,
            (scoped_id,),
        )
        connection.commit()
        resolution = resolve_tag_candidate(
            connection,
            "scoped-only",
            doc_category_id="cat_humanities",
        )

    assert resolution.outcome == "propose_new"
    assert "scope_mismatch" in resolution.reasons


def test_evaluate_tag_governance_compose(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "sqlite", tag_type="tool")
        existing = evaluate_tag_governance(
            connection,
            "sqlite",
            doc_id="doc_1",
            doc_category_id="cat_computer_science",
        )
        blocked = evaluate_tag_governance(
            connection,
            "misc",
            doc_id="doc_1",
            doc_category_id="cat_computer_science",
        )
        counters = TagBudgetCounters()
        counters.new_tag_proposals_run = 20
        budget_denied = evaluate_tag_governance(
            connection,
            "brand-new-topic",
            doc_id="doc_1",
            doc_category_id="cat_computer_science",
            counters=counters,
        )

    assert existing.candidate_type == "attach_existing"
    assert existing.allowed is True
    assert blocked.allowed is False
    assert blocked.admission is not None
    assert blocked.admission.outcome == "rejected"
    assert budget_denied.budget is not None
    assert budget_denied.budget.outcome == "denied"


def test_deterministic_tagger_auto_attaches_canonical_tag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "sqlite", tag_type="tool")
        doc_id = insert_minimal_document(connection, vault, body="# Notes\n\nsqlite\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="sqlite")

        formal_before = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE deleted_at IS NULL"
        ).fetchone()["count"]
        run = run_deterministic_tagger(connection, trigger="fixture_gate", doc_id=doc_id)
        formal_after = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE deleted_at IS NULL"
        ).fetchone()["count"]

        tag_rows = list_document_tags(connection, doc_id)
        run_row = connection.execute(
            "SELECT status, auto_attached_count FROM tagger_runs WHERE tagger_run_id = ?",
            (run.tagger_run_id,),
        ).fetchone()
        result_row = connection.execute(
            """
            SELECT outcome, auto_attached_tag_ids_json
            FROM tagger_results
            WHERE tagger_run_id = ?
            """,
            (run.tagger_run_id,),
        ).fetchone()

    assert run.auto_attached_count >= 1
    assert run_row["status"] == "succeeded"
    assert result_row["outcome"] in {"auto_attached", "candidates_created"}
    assert formal_before == formal_after
    assert any(row["name"] == "sqlite" and row["source"] == "auto" for row in tag_rows)


def test_deterministic_tagger_preserves_manual_tags(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        manual_tag_id = add_tag(connection, "manual-preserve", tag_type="topic")
        doc_id = insert_minimal_document(connection, vault, body="# Doc\n\nPlain body text.\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="Plain body text.")
        add_document_tag(connection, doc_id, "manual-preserve", source="manual")

        run_deterministic_tagger(connection, trigger="fixture_gate", doc_id=doc_id)
        tags = list_document_tags(connection, doc_id)
        manual_row = connection.execute(
            """
            SELECT source
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND t.tag_id = ?
              AND dt.deleted_at IS NULL
            """,
            (doc_id, manual_tag_id),
        ).fetchone()

    assert manual_row is not None
    assert manual_row["source"] == "manual"
    assert any(row["name"] == "manual-preserve" for row in tags)


def test_deterministic_tagger_creates_propose_new_without_formal_tag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = insert_minimal_document(
            connection,
            vault,
            body="# Doc\n\nquantum widget design pipeline overview.\n",
        )
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(
            connection,
            doc_id,
            str(revision_id),
            text="quantum widget design pipeline overview.",
        )
        formal_before = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE deleted_at IS NULL"
        ).fetchone()["count"]

        run = run_deterministic_tagger(connection, trigger="fixture_gate", doc_id=doc_id)

        formal_after = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE deleted_at IS NULL"
        ).fetchone()["count"]
        candidates = connection.execute(
            """
            SELECT candidate_type, status
            FROM tag_candidates
            WHERE tagger_run_id = ?
            """,
            (run.tagger_run_id,),
        ).fetchall()

    assert formal_before == formal_after
    assert run.new_tag_proposal_count >= 1
    assert any(row["candidate_type"] == "propose_new" and row["status"] == "pending" for row in candidates)


def test_deterministic_tagger_blocks_blocklisted_phrases(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "spam-tag", tag_type="topic")
        add_blocklist_entry(connection, "spam-tag", match_type="exact")
        doc_id = insert_minimal_document(connection, vault, body="# Doc\n\nspam-tag\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="spam-tag")

        run = run_deterministic_tagger(connection, trigger="fixture_gate", doc_id=doc_id)
        blocked_json = connection.execute(
            """
            SELECT blocked_candidates_json
            FROM tagger_results
            WHERE tagger_run_id = ?
            """,
            (run.tagger_run_id,),
        ).fetchone()["blocked_candidates_json"]

    import json

    blocked = json.loads(str(blocked_json))
    assert run.blocked_candidate_count >= 1
    assert any(item.get("outcome") == "blocked" for item in blocked)


def test_deterministic_tagger_attach_existing_candidate_below_auto_threshold(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "sqlite", tag_type="tool")
        doc_id = insert_minimal_document(
            connection,
            vault,
            body="# Doc\n\nsqlite database notes.\n",
        )
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="sqlite database notes.")

        run = run_deterministic_tagger(
            connection,
            trigger="fixture_gate",
            doc_id=doc_id,
            auto_attach_threshold=0.95,
        )
        candidates = connection.execute(
            """
            SELECT candidate_type, target_tag_id
            FROM tag_candidates
            WHERE tagger_run_id = ?
            """,
            (run.tagger_run_id,),
        ).fetchall()
        auto_rows = connection.execute(
            """
            SELECT source
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND t.normalized_name = 'sqlite'
              AND dt.deleted_at IS NULL
            """,
            (doc_id,),
        ).fetchall()

    assert run.auto_attached_count == 0
    assert any(row["candidate_type"] == "attach_existing" for row in candidates)
    assert not any(row["source"] == "auto" for row in auto_rows)


def _insert_active_document(
    connection,
    vault: Path,
    *,
    doc_id: str,
    revision_id: str,
    body: str,
) -> str:
    from indbase_core.paths import vault_paths

    vp = vault_paths(vault)
    markdown_path = vp.source_markdown_path(doc_id, doc_id.split("_")[-1][:8], 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    rel = vp.relative_to_vault(markdown_path)
    markdown_path.write_text(
        (
            f"---\nschema_version: indbase.source.v1\ntype: source_document\n"
            f"doc_id: {doc_id}\nrevision_id: {revision_id}\ntitle: {doc_id}\n---\n\n{body}"
        ),
        encoding="utf-8",
    )
    connection.execute(
        """
        INSERT INTO documents (
          doc_id, title, filename_slug, status, ingest_status, current_revision_id,
          canonical_path, created_at, updated_at
        ) VALUES (?, ?, ?, 'active', 'revisioned', ?, ?, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (doc_id, doc_id, doc_id, revision_id, rel),
    )
    connection.execute(
        """
        INSERT INTO document_revisions (
          revision_id, doc_id, sequence, markdown_path, content_hash,
          converter_name, converter_version, promotion_status, created_at, updated_at
        ) VALUES (?, ?, 1, ?, 'hash', 'test', 'test', 'promoted', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (revision_id, doc_id, rel),
    )
    connection.commit()
    return doc_id


def test_accept_attach_existing_candidate_scoped_to_one_document(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "scoped-attach", tag_type="topic")
        doc_a = _insert_active_document(
            connection,
            vault,
            doc_id="doc_20250101_a1b2c3",
            revision_id="rev_doc_20250101_a1b2c3_0001",
            body="# A\n\nscoped-attach\n",
        )
        doc_b = _insert_active_document(
            connection,
            vault,
            doc_id="doc_20250101_d4e5f6",
            revision_id="rev_doc_20250101_d4e5f6_0001",
            body="# B\n\nscoped-attach\n",
        )
        chunk_a = _insert_current_chunk(
            connection, doc_a, "rev_doc_20250101_a1b2c3_0001", text="scoped-attach"
        )
        _insert_current_chunk(connection, doc_b, "rev_doc_20250101_d4e5f6_0001", text="scoped-attach")
        resolution = resolve_tag_candidate(connection, "scoped-attach")
        decision = TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type="attach_existing",
            allowed=True,
            reasons=resolution.reasons,
        )
        candidate_id = insert_tag_governance_candidate(
            connection,
            candidate_type="attach_existing",
            raw_name="scoped-attach",
            tag_type="topic",
            doc_id=doc_a,
            revision_id="rev_doc_20250101_a1b2c3_0001",
            tagger_run_id="tagrun_test_scope",
            tagger_result_id="tagres_test_scope",
            decision=decision,
            target_tag_id=tag_id,
            confidence=0.8,
            evidence_chunk_ids=[chunk_a],
        )

        accepted = accept_tag_governance_candidate(connection, candidate_id)
        tags_a = list_document_tags(connection, doc_a)
        tags_b = list_document_tags(connection, doc_b)
        feedback_count = connection.execute(
            "SELECT COUNT(*) AS count FROM tag_feedback WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()["count"]

    assert accepted.document_tag_changed is True
    assert accepted.tag_id == tag_id
    assert any(row["name"] == "scoped-attach" for row in tags_a)
    assert not tags_b
    assert feedback_count == 1


def test_accept_propose_new_promotes_and_attaches_with_audit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = insert_minimal_document(
            connection,
            vault,
            body="# Doc\n\nquantum widget design pipeline overview.\n",
        )
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        decision = evaluate_tag_governance(
            connection,
            "quantum widget design",
            doc_id=doc_id,
            doc_category_id="cat_computer_science",
        )
        candidate_id = insert_tag_governance_candidate(
            connection,
            candidate_type="propose_new",
            raw_name="quantum widget design",
            tag_type="topic",
            doc_id=doc_id,
            revision_id=str(revision_id),
            tagger_run_id="tagrun_test_promote",
            tagger_result_id="tagres_test_promote",
            decision=decision,
            confidence=0.75,
        )

        accepted = accept_tag_governance_candidate(connection, candidate_id)
        events = connection.execute(
            """
            SELECT event_type
            FROM tag_governance_events
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchall()
        tags = list_document_tags(connection, doc_id)

    assert accepted.tag_id is not None
    assert accepted.governance_event_id is not None
    assert any(row["event_type"] == "promoted" for row in events)
    assert any(row["name"] == "quantum widget design" and row["source"] == "accepted_candidate" for row in tags)


def test_reject_candidate_writes_feedback_without_document_tag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "reject-me", tag_type="topic")
        doc_id = insert_minimal_document(connection, vault, body="# Doc\n\nreject-me\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        resolution = resolve_tag_candidate(connection, "reject-me")
        decision = TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type="attach_existing",
            allowed=True,
            reasons=resolution.reasons,
        )
        candidate_id = insert_tag_governance_candidate(
            connection,
            candidate_type="attach_existing",
            raw_name="reject-me",
            tag_type="topic",
            doc_id=doc_id,
            revision_id=str(revision_id),
            tagger_run_id="tagrun_test_reject",
            tagger_result_id="tagres_test_reject",
            decision=decision,
            target_tag_id=tag_id,
            confidence=0.7,
        )

        rejected = reject_tag_governance_candidate(
            connection,
            candidate_id,
            reason="not useful",
        )
        tags = list_document_tags(connection, doc_id)
        feedback = connection.execute(
            """
            SELECT action, reason
            FROM tag_feedback
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

    assert rejected.status == "rejected"
    assert not tags
    assert feedback["action"] == "rejected"
    assert feedback["reason"] == "not useful"


def test_taxonomy_mutations_record_governance_events(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        source_id = add_tag(connection, "merge-source", tag_type="topic")
        target_id = add_tag(connection, "merge-target", tag_type="topic")
        merge_tags(connection, source_id, target_id)
        deprecate_tag(connection, target_id)
        events = {
            row["event_type"]
            for row in connection.execute(
                """
                SELECT event_type
                FROM tag_governance_events
                WHERE tag_id IN (?, ?)
                """,
                (source_id, target_id),
            )
        }

    assert "merged" in events
    assert "deprecated" in events


def test_relation_backed_tag_search_filter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "filter-target", tag_type="topic")
        doc_id = insert_minimal_document(
            connection,
            vault,
            body="# Doc\n\nfilter-target appears in trusted assignment only.\n",
        )
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        chunk_id = _insert_current_chunk(
            connection,
            doc_id,
            str(revision_id),
            text="filter-target appears in trusted assignment only.",
        )
        attach_document_tag_by_id(
            connection,
            doc_id,
            tag_id,
            source="accepted_candidate",
            revision_id=str(revision_id),
            evidence_chunk_ids=[chunk_id],
        )
        from indbase_core.indexer import refresh_document_fts_metadata
        from indbase_core.search import SearchOptions, search_chunks

        refresh_document_fts_metadata(connection, doc_id)
        from indbase_core.tag_search import resolve_tag_filter

        filter_ids = resolve_tag_filter(connection, "filter-target").filter_tag_ids
        matched = search_chunks(
            connection,
            "appears",
            options=SearchOptions(log_queries=False, tag_filter_ids=filter_ids),
        )
        prefix = search_chunks(connection, "tag:filter-target appears", options=SearchOptions(log_queries=False))

    assert matched.result_count >= 1
    assert prefix.result_count >= 1


def test_pending_candidate_not_trusted_tag_filter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "pending-only", tag_type="topic")
        doc_id = insert_minimal_document(connection, vault, body="# Doc\n\npending-only\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="pending-only")
        resolution = resolve_tag_candidate(connection, "pending-only")
        decision = TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type="attach_existing",
            allowed=True,
            reasons=resolution.reasons,
        )
        insert_tag_governance_candidate(
            connection,
            candidate_type="attach_existing",
            raw_name="pending-only",
            tag_type="topic",
            doc_id=doc_id,
            revision_id=str(revision_id),
            tagger_run_id="tagrun_pending",
            tagger_result_id="tagres_pending",
            decision=decision,
            target_tag_id=tag_id,
            confidence=0.7,
        )
        from indbase_core.search import SearchOptions, search_chunks
        from indbase_core.tag_search import document_matches_tag_filter, resolve_tag_filter

        filter_ids = resolve_tag_filter(connection, "pending-only").filter_tag_ids
        assert document_matches_tag_filter(connection, doc_id, filter_ids) is False
        result = search_chunks(
            connection,
            "pending-only",
            options=SearchOptions(log_queries=False, tag_filter_ids=filter_ids),
        )
        assert result.result_count == 0


def test_fts_metadata_uses_trusted_document_tags_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "fts-trusted", tag_type="topic")
        doc_id = insert_minimal_document(connection, vault, body="# Doc\n\nbody\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="body")
        attach_document_tag_by_id(
            connection,
            doc_id,
            tag_id,
            source="accepted_candidate",
            revision_id=str(revision_id),
        )
        from indbase_core.indexer import refresh_document_fts_metadata

        refresh_document_fts_metadata(connection, doc_id)
        fts_row = connection.execute(
            "SELECT tags FROM chunks_fts WHERE doc_id = ? LIMIT 1",
            (doc_id,),
        ).fetchone()

    assert fts_row is not None
    assert "fts-trusted" in str(fts_row["tags"])
