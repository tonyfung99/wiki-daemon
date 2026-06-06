"""Tests for maintainer.py — vault CLAUDE.md (the maintainer brain) drift logic."""
from wiki_daemon.maintainer import (
    apply_upgrade,
    missing_sections,
    sections,
    template_text,
)

# headers the bundled template defines, in order
TEMPLATE_HEADERS = [
    "## Layers",
    "## Page rules",
    "## INGEST operation",
    "## RAISE CLARIFICATION (during INGEST)",
    "## APPLY CLARIFICATION",
    "## QUERY operation",
    "## SAVE-QUERY",
    "## LINT operation",
    "## LINT REPAIR",
]


def _old_prefix_vault() -> str:
    """The template truncated just before RAISE CLARIFICATION — i.e. the stale
    brain we actually saw in the wild (INGEST only, no clarification/query/lint)."""
    tmpl = template_text()
    idx = tmpl.index("## RAISE CLARIFICATION")
    return tmpl[:idx].rstrip() + "\n"


def test_sections_parses_template_headers_in_order():
    headers = [s.header for s in sections(template_text())]
    assert headers == TEMPLATE_HEADERS


def test_section_text_includes_header_and_body():
    by = {s.header: s.text for s in sections(template_text())}
    query = by["## QUERY operation"]
    assert query.startswith("## QUERY operation")
    assert "Read `wiki/index.md`" in query
    assert "## SAVE-QUERY" not in query  # stops at the next header


def test_missing_sections_on_old_prefix_returns_tail():
    missing = [s.header for s in missing_sections(_old_prefix_vault())]
    assert missing == [
        "## RAISE CLARIFICATION (during INGEST)",
        "## APPLY CLARIFICATION",
        "## QUERY operation",
        "## SAVE-QUERY",
        "## LINT operation",
        "## LINT REPAIR",
    ]


def test_missing_sections_empty_when_current_matches_template():
    assert missing_sections(template_text()) == []


def test_apply_upgrade_appends_missing_and_reports_headers():
    new_text, added = apply_upgrade(_old_prefix_vault())
    assert added == [
        "## RAISE CLARIFICATION (during INGEST)",
        "## APPLY CLARIFICATION",
        "## QUERY operation",
        "## SAVE-QUERY",
        "## LINT operation",
        "## LINT REPAIR",
    ]
    # every template header is now present
    assert [s.header for s in sections(new_text)] == TEMPLATE_HEADERS
    assert new_text.endswith("\n")


def test_apply_upgrade_is_idempotent():
    once, _ = apply_upgrade(_old_prefix_vault())
    twice, added = apply_upgrade(once)
    assert added == []
    assert twice == once


def test_apply_upgrade_preserves_customization():
    custom = _old_prefix_vault().replace(
        "## INGEST operation\n",
        "## INGEST operation\nMY CUSTOM RULE: always tag finance sources.\n",
    )
    new_text, _ = apply_upgrade(custom)
    assert "MY CUSTOM RULE: always tag finance sources." in new_text
    assert [s.header for s in sections(new_text)] == TEMPLATE_HEADERS


def test_apply_upgrade_noop_returns_same_text():
    text = template_text()
    new_text, added = apply_upgrade(text)
    assert added == []
    assert new_text == text
