from pathlib import Path

from indbase_core.paths import normalize_source_uri, slugify, vault_paths


def test_vault_layout_is_created(tmp_path: Path) -> None:
    paths = vault_paths(tmp_path / "vault")

    paths.ensure_layout()

    assert paths.inbox.is_dir()
    assert paths.sources.is_dir()
    assert paths.notes_atomic.is_dir()
    assert paths.outputs_translations.is_dir()
    assert paths.outputs_summaries.is_dir()
    assert paths.originals.is_dir()
    assert paths.config_dir.is_dir()


def test_revision_markdown_path_is_revision_unique(tmp_path: Path) -> None:
    paths = vault_paths(tmp_path / "vault")

    markdown_path = paths.source_markdown_path(
        "doc_20260512_a8f3c2",
        "Agent Harness Design",
        2,
    )

    assert markdown_path.as_posix().endswith(
        "sources/2026/05/agent-harness-design__doc_20260512_a8f3c2__rev_0002.md"
    )


def test_original_path_uses_doc_date(tmp_path: Path) -> None:
    paths = vault_paths(tmp_path / "vault")

    original_path = paths.original_path("doc_20260512_a8f3c2", ".TXT")

    assert original_path.as_posix().endswith(
        ".indbase/originals/2026/05/doc_20260512_a8f3c2/original.txt"
    )


def test_normalize_source_uri_resolves_relative_path(tmp_path: Path) -> None:
    normalized = normalize_source_uri("notes/example.md", base_dir=tmp_path)

    assert normalized.endswith("/notes/example.md")


def test_slugify_has_fallback() -> None:
    assert slugify("Agent Harness Design") == "agent-harness-design"
    assert slugify("!!!", fallback="source") == "source"


def test_slugify_avoids_windows_reserved_names_and_limits_length() -> None:
    assert slugify("CON") == "con-file"
    assert slugify("AUX") == "aux-file"
    assert slugify('name<bad>:"/\\|?* file') == "name-bad-file"
    assert len(slugify("a" * 200)) == 80
