from pathlib import Path

import pytest

from wiki_daemon.importer import _slugify, _dest_name


def test_slugify_basic():
    assert _slugify("My Cool Note!") == "my-cool-note"


def test_slugify_collapses_and_trims():
    assert _slugify("  --Foo__Bar.. ") == "foo-bar"


def test_slugify_empty_falls_back():
    assert _slugify("---") == "source"


def test_dest_name_adds_date_prefix():
    assert _dest_name("notes", "2026-06-02") == "2026-06-02-notes.md"


def test_dest_name_skips_double_date_prefix():
    # stem already starts with a YYYY-MM-DD- prefix -> don't prepend again
    assert _dest_name("2026-05-31-acme", "2026-06-02") == "2026-05-31-acme.md"


def test_dest_name_slugifies_tail_after_date_prefix():
    # keep the stem's own date, but still slugify the rest (no spaces/case)
    assert _dest_name("2026-05-31-Acme Corp!", "2026-06-02") == "2026-05-31-acme-corp.md"


# ---------------------------------------------------------------------------
# Task 2: import_source
# ---------------------------------------------------------------------------
from wiki_daemon.config import Config
from wiki_daemon.frontmatter import parse
from wiki_daemon.importer import import_source


def _cfg(tmp_path):
    return Config(vault=tmp_path / "v", state_root=tmp_path / "s")


def test_import_copies_and_leaves_original(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "Hello World.md"
    src.write_text("---\ntype: source\ntitle: Hi\n---\nbody\n", encoding="utf-8")

    dest = import_source(cfg, src)

    assert dest.parent == cfg.raw_sources
    assert dest.name.endswith("-hello-world.md")
    assert dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert src.exists()  # original untouched (copy, never move)


def test_import_collision_appends_suffix(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "2026-05-31-acme.md"
    src.write_text("---\ntype: source\n---\nbody\n", encoding="utf-8")
    (cfg.raw_sources / "2026-05-31-acme.md").write_text("existing", encoding="utf-8")

    dest = import_source(cfg, src)

    assert dest.name == "2026-05-31-acme-2.md"


def test_import_synthesizes_frontmatter_when_absent(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "raw-clip.md"
    src.write_text("just some text\n", encoding="utf-8")

    dest = import_source(cfg, src)

    meta, body = parse(dest.read_text(encoding="utf-8"))
    assert meta["type"] == "source"
    assert meta["title"] == "Raw Clip"
    assert "captured_at" in meta
    assert body == "just some text\n"


def test_import_keeps_existing_frontmatter_verbatim(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "clip.md"
    original = "---\ntype: source\ntitle: Keep\n---\nverbatim\n"
    src.write_text(original, encoding="utf-8")

    dest = import_source(cfg, src)

    assert dest.read_text(encoding="utf-8") == original


def test_import_missing_path_raises(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        import_source(cfg, tmp_path / "nope.md")


def test_import_non_utf8_raises(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "binary.md"
    src.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ValueError):
        import_source(cfg, src)


# --- multi-format conversion (2026-06-09) ---
from wiki_daemon.config import Config
from wiki_daemon.importer import import_source, normalize_in_place


def test_import_converts_pdf_via_seam(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    import wiki_daemon.importer as imp
    monkeypatch.setattr(imp, "convert_to_markdown",
                        lambda p: "# Converted\nfrom a pdf\n")
    ext = tmp_path / "Report.pdf"
    ext.write_bytes(b"%PDF-1.4 fake bytes")
    dest = import_source(cfg, ext)
    assert dest.suffix == ".md"
    body = dest.read_text(encoding="utf-8")
    assert "from a pdf" in body
    assert body.startswith("---")          # synthesized frontmatter
    assert ext.exists()                     # external original untouched


def test_import_text_still_passthrough(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    ext = tmp_path / "note.txt"
    ext.write_text("plain note\n", encoding="utf-8")
    dest = import_source(cfg, ext)
    assert dest.suffix == ".md"
    assert "plain note" in dest.read_text(encoding="utf-8")


def test_normalize_in_place_converts_and_archives(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    import wiki_daemon.importer as imp
    monkeypatch.setattr(imp, "convert_to_markdown",
                        lambda p: "# Doc\nconverted body\n")
    raw = cfg.raw_sources / "paper.pdf"
    raw.write_bytes(b"%PDF fake")
    md = normalize_in_place(cfg, raw)
    assert md.suffix == ".md" and md.parent == cfg.raw_sources
    assert "converted body" in md.read_text(encoding="utf-8")
    assert not raw.exists()                              # moved out of sources
    assert (cfg.raw_originals / "paper.pdf").exists()    # archived
