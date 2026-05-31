# src/wiki_daemon/prompts.py
"""Thin operation prompts. The real algorithm lives in the vault's CLAUDE.md."""
from __future__ import annotations


def ingest_prompt(source_rel_path: str) -> str:
    return (
        "Follow the INGEST operation defined in CLAUDE.md exactly.\n"
        f"Ingest this single source file into the wiki: {source_rel_path}\n"
        "Create/update the relevant entity and concept pages, ensure a source "
        "summary page exists, update wiki/index.md, and append to wiki/log.md. "
        "Do not modify anything under raw/."
    )
