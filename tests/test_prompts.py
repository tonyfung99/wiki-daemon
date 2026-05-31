# tests/test_prompts.py
from wiki_daemon.prompts import ingest_prompt


def test_ingest_prompt_names_the_file_and_schema():
    p = ingest_prompt("raw/sources/2026-05-31-acme.md")
    assert "raw/sources/2026-05-31-acme.md" in p
    assert "CLAUDE.md" in p
    assert "INGEST" in p.upper()
