from indbase_core.protected_spans import spans_for_export_markdown, spans_for_normalize_body, validate_protected_spans


def test_export_frontmatter_is_protected() -> None:
    markdown = "---\ntitle: demo\n---\n\n# Hello\n"
    spans = spans_for_export_markdown(markdown, input_kind="source_revision")
    assert spans[0].kind == "frontmatter"
    assert markdown[spans[0].start : spans[0].end].startswith("---")


def test_translation_source_section_is_protected() -> None:
    body = "### Source\n\noriginal line\n\n### Translation\n\n[target] text\n"
    spans = spans_for_export_markdown(f"---\n---\n\n{body}", input_kind="translation")
    kinds = {span.kind for span in spans}
    assert "translation_source" in kinds


def test_validate_detects_protected_change() -> None:
    original = "```py\nprint('x')\n```\n"
    updated = "```py\nprint('y')\n```\n"
    spans = spans_for_normalize_body(original)
    assert validate_protected_spans(original, updated, spans)
