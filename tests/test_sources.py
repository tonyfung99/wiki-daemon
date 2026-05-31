# tests/test_sources.py
from wiki_daemon.sources import SourceFile, read_source, content_sha256


def test_content_sha256_is_stable():
    assert content_sha256(b"hello") == content_sha256(b"hello")
    assert content_sha256(b"hello") != content_sha256(b"world")


def test_read_source_parses_frontmatter(tmp_path):
    p = tmp_path / "2026-05-31-acme.md"
    p.write_text("---\ntype: source\nsource_url: https://x.com/a\n---\nbody\n",
                 encoding="utf-8")
    src = read_source(p)
    assert isinstance(src, SourceFile)
    assert src.path == p
    assert src.meta["source_url"] == "https://x.com/a"
    assert src.body == "body\n"
    assert len(src.sha256) == 64
