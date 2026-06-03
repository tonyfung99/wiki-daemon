# tests/test_prompts.py
from wiki_daemon.prompts import ingest_prompt, apply_clarification_prompt


def test_ingest_prompt_names_the_file_and_schema():
    p = ingest_prompt("raw/sources/2026-05-31-acme.md")
    assert "raw/sources/2026-05-31-acme.md" in p
    assert "CLAUDE.md" in p
    assert "INGEST" in p.upper()


def test_ingest_prompt_headless_mentions_review_queue():
    p = ingest_prompt("raw/sources/x.md")
    assert "wiki/review/" in p
    assert "never block" in p.lower()


def test_ingest_prompt_interactive_says_ask():
    p = ingest_prompt("raw/sources/x.md", interactive=True)
    assert "ask" in p.lower()
    assert "interactive" in p.lower()


def test_apply_clarification_prompt_names_file_and_op():
    p = apply_clarification_prompt("wiki/review/calvin.md")
    assert "wiki/review/calvin.md" in p
    assert "APPLY CLARIFICATION" in p.upper()


from wiki_daemon.prompts import query_prompt


def test_query_prompt_readonly_names_question_and_no_files():
    p = query_prompt("What is photosynthesis?")
    assert "What is photosynthesis?" in p
    assert "QUERY" in p.upper()
    assert "read-only" in p.lower() or "read only" in p.lower()
    assert "do not execute code" in p.lower()
    assert "mermaid" in p.lower()


def test_query_prompt_save_mentions_queries_and_type():
    p = query_prompt("What is photosynthesis?", save=True)
    assert "wiki/queries/" in p
    assert "type: query" in p
    assert "wiki/log.md" in p
