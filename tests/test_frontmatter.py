# tests/test_frontmatter.py
from wiki_daemon.frontmatter import parse, dump


def test_parse_with_frontmatter():
    text = "---\ntype: source\ntitle: Hi\n---\nbody line\n"
    meta, body = parse(text)
    assert meta == {"type": "source", "title": "Hi"}
    assert body == "body line\n"


def test_parse_without_frontmatter():
    text = "no frontmatter here\n"
    meta, body = parse(text)
    assert meta == {}
    assert body == "no frontmatter here\n"


def test_parse_empty_frontmatter():
    text = "---\n---\nbody\n"
    meta, body = parse(text)
    assert meta == {}
    assert body == "body\n"


def test_dump_roundtrip():
    meta = {"type": "entity", "title": "Acme"}
    body = "Some body.\n"
    text = dump(meta, body)
    meta2, body2 = parse(text)
    assert meta2 == meta
    assert body2 == body
