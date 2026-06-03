# src/wiki_daemon/prompts.py
"""Thin operation prompts. The real algorithm lives in the vault's CLAUDE.md."""
from __future__ import annotations


def ingest_prompt(source_rel_path: str, *, interactive: bool = False) -> str:
    base = (
        "Follow the INGEST operation defined in CLAUDE.md exactly.\n"
        f"Ingest this single source file into the wiki: {source_rel_path}\n"
        "Create/update the relevant entity and concept pages, ensure a source "
        "summary page exists, update wiki/index.md, and append to wiki/log.md. "
        "Do not modify anything under raw/."
    )
    if interactive:
        return base + (
            "\nThis is an INTERACTIVE session: if a structural decision is "
            "genuinely ambiguous, ASK me directly and wait for my answer before "
            "proceeding — do not write a review file."
        )
    return base + (
        "\nYou are headless — never block. If a structural decision is genuinely "
        "ambiguous, make a best-effort choice and record a clarification under "
        "wiki/review/ (RAISE CLARIFICATION in CLAUDE.md)."
    )


def apply_clarification_prompt(review_rel_path: str) -> str:
    return (
        "Follow the APPLY CLARIFICATION operation defined in CLAUDE.md exactly.\n"
        f"Apply this answered review file: {review_rel_path}\n"
        "Update the relevant wiki pages using the user's answer, append a line "
        "to wiki/log.md, then delete the review file. Do not modify anything "
        "under raw/."
    )
