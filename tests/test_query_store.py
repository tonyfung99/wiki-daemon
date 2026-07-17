from wiki_daemon.query_store import _slugify, _title_from_question, _normalize_q


def test_slugify_basic():
    assert _slugify("Hello World: A Test!") == "hello-world-a-test"


def test_slugify_collapses_and_trims():
    assert _slugify("  Multiple   spaces & symbols?? ") == "multiple-spaces-symbols"


def test_slugify_limits_length():
    slug = _slugify("word " * 40)
    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_slugify_non_empty_fallback():
    assert _slugify("!!!") == "query"


def test_title_from_question_capitalizes_and_trims():
    assert _title_from_question("what is a daemon?") == "What is a daemon?"


def test_title_from_question_truncates_long():
    title = _title_from_question("a " * 100)
    assert len(title) <= 80


def test_normalize_q_collapses_whitespace():
    assert _normalize_q("  a\n b   c ") == "a b c"
