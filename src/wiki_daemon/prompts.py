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


def query_prompt(question: str, *, save: bool = False) -> str:
    base = (
        "Follow the QUERY operation defined in CLAUDE.md exactly.\n"
        f"Answer this question from the wiki, citing the pages you used: {question}\n"
        "Read wiki/index.md first, open the relevant pages, and synthesize an "
        "answer with [[wiki-link]] citations. Use rich Markdown where it helps — "
        "comparison tables, Mermaid diagrams (```mermaid), and fenced code "
        "snippets (e.g. ```python) — but do NOT execute code. Do not modify "
        "anything under raw/."
    )
    if not save:
        return base + (
            "\nThis is READ-ONLY: do not create or edit any files. Print the "
            "answer (Markdown tables and ```mermaid/code blocks are fine in text)."
        )
    return base + (
        "\nThen SAVE-QUERY: write the answer as wiki/queries/<kebab-slug>.md with "
        "frontmatter `type: query` and `query: \"<the question>\"` (plus title and "
        "updated), update wiki/index.md, and append a line to wiki/log.md. You MAY "
        "also write companion artifact files alongside it (e.g. a Marp deck "
        "wiki/queries/<kebab-slug>-deck.md) and link them — Markdown/text only, no "
        "code execution."
    )


def lint_prompt() -> str:
    return (
        "Follow the LINT operation defined in CLAUDE.md exactly.\n"
        "This is READ-ONLY: scan the wiki for contradictions, stale claims, and "
        "data gaps. List each issue with the page(s) involved. Do not modify any "
        "files."
    )


def lint_repair_prompt(findings_text: str, deep_report: str = "") -> str:
    deep = f"\nSemantic findings to also resolve:\n{deep_report}" if deep_report else ""
    return (
        "Follow the LINT REPAIR operation defined in CLAUDE.md exactly.\n"
        f"Fix these findings:\n{findings_text}{deep}\n"
        "For each: create the missing page OR remove the dead link (your "
        "judgment), de-orphan pages by linking/indexing them, fill index gaps, "
        "and reconcile contradictions — preserving sources: traceability. Update "
        "wiki/index.md and append a line to wiki/log.md. Do not modify anything "
        "under raw/."
    )
