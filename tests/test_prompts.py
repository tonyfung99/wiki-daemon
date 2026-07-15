# tests/test_prompts.py
from wiki_daemon.prompts import ingest_prompt, apply_clarification_prompt


def test_ingest_prompt_names_the_file_and_schema():
    p = ingest_prompt("raw/sources/2026-05-31-acme.md")
    assert "raw/sources/2026-05-31-acme.md" in p
    assert "project instructions" in p   # provider-neutral (each agent reads its own brain file)
    assert "INGEST" in p.upper()


def test_ingest_prompt_headless_mentions_review_queue():
    p = ingest_prompt("raw/sources/x.md")
    assert "wiki/review/" in p
    assert "never block" in p.lower()


def test_ingest_prompt_interactive_says_ask():
    p = ingest_prompt("raw/sources/x.md", interactive=True)
    assert "ask" in p.lower()
    assert "interactive" in p.lower()


def test_ingest_prompt_interactive_asks_with_numbered_options():
    p = ingest_prompt("raw/sources/x.md", interactive=True).lower()
    assert "numbered" in p and "option" in p
    assert "recommend" in p  # mark a recommended default
    assert "number or" in p  # accept a number or free text


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


def test_query_prompt_save_quotes_title():
    # A title with a colon must not produce invalid YAML frontmatter, so the
    # prompt must instruct a quoted title.
    p = query_prompt("What is photosynthesis?", save=True)
    assert 'title: "' in p


from wiki_daemon.prompts import lint_prompt, lint_repair_prompt


def test_lint_prompt_readonly_semantic():
    p = lint_prompt()
    assert "LINT" in p.upper()
    assert "read-only" in p.lower() or "do not modify" in p.lower()
    assert "contradiction" in p.lower()


def test_lint_repair_prompt_includes_findings_and_log():
    p = lint_repair_prompt("- dead link [[X]] in a.md", deep_report="contradiction Y")
    assert "[[X]]" in p
    assert "contradiction Y" in p
    assert "wiki/log.md" in p
    assert "LINT REPAIR" in p.upper()
