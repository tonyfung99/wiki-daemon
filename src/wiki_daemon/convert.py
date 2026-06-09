# src/wiki_daemon/convert.py
"""Convert documents to Markdown via markitdown (offline, MIT, no API key).

Isolates the markitdown dependency behind one function so importer/daemon (and
tests) have a single seam. Documents only — no image OCR / audio transcription.
"""
from __future__ import annotations

from pathlib import Path

# Formats normalized to Markdown on entry to the vault. HTML/CSV/JSON/XML need no
# markitdown extra; PDF/DOCX/PPTX/XLSX use markitdown[pdf,docx,pptx,xlsx].
CONVERT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx",
                      ".html", ".htm", ".csv", ".json", ".xml"}


def convert_to_markdown(path: Path) -> str:
    """Convert a document at `path` to Markdown text. Raises ValueError on a
    conversion failure or empty result, FileNotFoundError if the file is absent."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")
    from markitdown import MarkItDown  # lazy: keep import cost off other commands
    try:
        text = MarkItDown().convert(str(path)).text_content
    except Exception as exc:  # markitdown raises varied per-format errors
        raise ValueError(f"could not convert {path.name}: {exc}") from exc
    if not text or not text.strip():
        raise ValueError(f"conversion produced no text: {path.name}")
    return text
