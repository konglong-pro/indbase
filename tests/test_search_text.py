from indbase_core.search_text import (
    ascii_query_terms,
    build_fts_text,
    cjk_bigrams,
    cjk_query_terms,
    cjk_runs,
    cjk_substring_score,
    contains_cjk,
    normalize_search_text,
    prepare_cjk_fallback_query,
    prepare_search_text,
)


def test_normalize_search_text_nfkc_casefolds_and_collapses_whitespace() -> None:
    text = "  ＡＢＣ１２３　Knowledge\r\nBase\u200b  "

    assert normalize_search_text(text) == "abc123 knowledge base"


def test_contains_cjk_detects_chinese_and_japanese_after_normalization() -> None:
    assert contains_cjk("local 知识库") is True
    assert contains_cjk("ﾃｽﾄ") is True
    assert contains_cjk("plain english") is False
    assert contains_cjk("") is False


def test_cjk_runs_do_not_cross_ascii_space_or_punctuation() -> None:
    text = "AI知识库・検索 テストabc中文"

    assert cjk_runs(text) == ("知识库", "検索", "テスト", "中文")
    assert cjk_bigrams(text) == ("知识", "识库", "検索", "テス", "スト", "中文")


def test_cjk_bigrams_can_include_single_character_runs_for_indexing() -> None:
    assert cjk_bigrams("测 A 试", include_unigrams=True) == ("测", "试")
    assert cjk_bigrams("测试", include_unigrams=True) == ("测试",)


def test_query_terms_keep_ascii_and_cjk_terms_separate() -> None:
    assert ascii_query_terms("Agent 知识 DB_1") == ("agent", "db_1")
    assert cjk_query_terms("知识 数据库") == ("知识", "数据库", "数据", "据库")


def test_build_fts_text_adds_cjk_bigrams_without_losing_original_text() -> None:
    fts_text = build_fts_text("Personal 知识数据库")

    assert fts_text.startswith("personal 知识数据库")
    assert "知识" in fts_text
    assert "识数" in fts_text
    assert "数据" in fts_text
    assert "据库" in fts_text


def test_prepare_search_text_returns_normalized_bigrams_and_fts_text() -> None:
    prepared = prepare_search_text("  日本語テスト  ")

    assert prepared.normalized == "日本語テスト"
    assert prepared.cjk_bigrams == ("日本", "本語", "語テ", "テス", "スト")
    assert prepared.fts_text == "日本語テスト 日本 本語 語テ テス スト"


def test_prepare_cjk_fallback_query_and_score_fields_by_simple_weight() -> None:
    query = prepare_cjk_fallback_query("知识库")

    assert query.has_cjk is True
    assert query.normalized == "知识库"
    assert query.runs == ("知识库",)
    assert query.terms == ("知识库", "知识", "识库")
    assert cjk_substring_score(query, title="知识库", heading_path="", text="") == 30
    assert cjk_substring_score(query, title="", heading_path="个人知识库", text="") == 20
    assert cjk_substring_score(query, title="", heading_path="", text="这是知识库内容") == 10
    assert cjk_substring_score(query, title="知识库", heading_path="知识库", text="知识库") == 60


def test_cjk_substring_score_ignores_non_cjk_queries() -> None:
    assert cjk_substring_score("knowledge", title="knowledge", text="knowledge") == 0
