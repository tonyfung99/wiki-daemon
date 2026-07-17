from wiki_daemon.query_store import _slugify, _title_from_question, _normalize_q
from wiki_daemon.query_store import _insert_under_section


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


def test_insert_under_existing_section():
    text = "# Index\n\n## Concepts\n\n## Queries\n"
    out = _insert_under_section(text, "## Queries", "- [[slug|Title]]")
    assert "## Queries\n- [[slug|Title]]\n" in out


def test_insert_newest_first():
    text = "# Index\n\n## Queries\n- [[old|Old]]\n"
    out = _insert_under_section(text, "## Queries", "- [[new|New]]")
    lines = out.splitlines()
    qi = lines.index("## Queries")
    assert lines[qi + 1] == "- [[new|New]]"
    assert lines[qi + 2] == "- [[old|Old]]"


def test_insert_section_missing_appends_it():
    text = "# Index\n"
    out = _insert_under_section(text, "## Queries", "- [[slug|Title]]")
    assert "## Queries\n- [[slug|Title]]\n" in out
    assert out.endswith("\n")
