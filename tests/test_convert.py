"""Tests for convert.py — document -> Markdown via markitdown. Real conversion
of text-based formats (offline, no extras needed); no binary fixtures."""
import pytest

from wiki_daemon.convert import CONVERT_EXTENSIONS, convert_to_markdown


def test_convert_extensions_cover_documents():
    for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".csv",
                ".json", ".xml"):
        assert ext in CONVERT_EXTENSIONS


def test_convert_html_to_markdown(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("<html><body><h1>Hello Wiki</h1><p>Some body text.</p></body></html>",
                 encoding="utf-8")
    md = convert_to_markdown(p)
    assert "Hello Wiki" in md
    assert "Some body text." in md


def test_convert_csv_to_markdown(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("name,role\nAda,engineer\nGrace,admiral\n", encoding="utf-8")
    md = convert_to_markdown(p)
    assert "Ada" in md and "Grace" in md


def test_convert_missing_file_raises(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        convert_to_markdown(tmp_path / "nope.pdf")
