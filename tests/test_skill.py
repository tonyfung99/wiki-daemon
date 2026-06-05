"""Drift guard for the Hermes skill.

Keeps skills/wiki/SKILL.md honest as the CLI evolves: every `wiki <subcommand>`
the skill demonstrates in a fenced code block must be a real subcommand in
build_parser(). Mechanical — no network, no `claude` calls.
"""
import argparse
import re
from pathlib import Path

from wiki_daemon.cli import build_parser

SKILL = Path(__file__).resolve().parents[1] / "skills" / "wiki" / "SKILL.md"


def _valid_subcommands() -> set[str]:
    """Top-level subcommand names registered on the parser."""
    parser = build_parser()
    names: set[str] = set()
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    return names


def _wiki_invocations() -> list[str]:
    """First token after `wiki ` for each command line inside a ``` fence."""
    tokens: list[str] = []
    in_fence = False
    for line in SKILL.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        m = re.match(r"\s*wiki\s+(.*)", line)
        if not m:
            continue
        # first token that isn't a flag (-x) or a <placeholder>
        for tok in m.group(1).split():
            if tok.startswith("-") or tok.startswith("<"):
                continue
            tokens.append(tok)
            break
    return tokens


def test_skill_file_exists():
    assert SKILL.is_file(), f"missing skill file: {SKILL}"


def test_skill_only_references_real_subcommands():
    valid = _valid_subcommands()
    invocations = _wiki_invocations()
    assert invocations, "no `wiki <cmd>` examples found in SKILL.md"
    unknown = sorted(t for t in invocations if t not in valid)
    assert not unknown, (
        f"SKILL.md references unknown subcommands {unknown}; "
        f"valid are {sorted(valid)}"
    )


def test_skill_frontmatter_name_matches_folder():
    """Hermes requires the frontmatter `name` to match the skill folder."""
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
    assert m, "no `name:` in frontmatter"
    assert m.group(1) == SKILL.parent.name == "wiki"
