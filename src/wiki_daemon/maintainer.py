# src/wiki_daemon/maintainer.py
"""The vault `CLAUDE.md` (the "maintainer brain") drift logic.

Pure functions, no CLI or daemon state. The bundled template is the source of
truth; a vault brain is "stale" when it is missing a `## ` section the template
defines. `apply_upgrade` repairs non-destructively by appending the missing
sections — it never rewrites existing ones, so user customizations survive.
"""
from __future__ import annotations

import re
from collections import namedtuple
from importlib import resources

Section = namedtuple("Section", "header text")

# Hidden stamp on the template's first line, e.g. `<!-- wiki-template: v1 -->`.
# Invisible in rendered Markdown and ignored by the maintainer LLM; lets `doctor`
# detect a vault CLAUDE.md whose content is behind the template (not just missing
# sections). Bump the integer whenever the template changes.
_STAMP = re.compile(r"<!--\s*wiki-template:\s*v(\d+)\s*-->")


def template_text() -> str:
    """The bundled canonical CLAUDE.md template."""
    return resources.files("wiki_daemon.templates").joinpath("CLAUDE.md").read_text(
        encoding="utf-8")


def parse_version(text: str) -> int | None:
    """The template version stamped in `text`, or None if absent/malformed."""
    m = _STAMP.search(text)
    return int(m.group(1)) if m else None


def template_version() -> int:
    """The bundled template's version. Assumes the template is always stamped."""
    v = parse_version(template_text())
    if v is None:  # pragma: no cover - the bundled template must carry a stamp
        raise RuntimeError("bundled template is missing its version stamp")
    return v


def sections(text: str) -> list[Section]:
    """Split into `## ` sections: each runs from its header to the next `## `
    (or EOF). Content before the first `## ` (title/intro) is not a section."""
    out: list[Section] = []
    header: str | None = None
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if header is not None:
                out.append(Section(header, "".join(buf)))
            header = line.rstrip()
            buf = [line]
        elif header is not None:
            buf.append(line)
    if header is not None:
        out.append(Section(header, "".join(buf)))
    return out


def missing_sections(current: str) -> list[Section]:
    """Template sections whose `## <header>` line is absent from `current`,
    in template order."""
    present = {s.header for s in sections(current)}
    return [s for s in sections(template_text()) if s.header not in present]


def apply_upgrade(current: str) -> tuple[str, list[str]]:
    """Append every missing template section to the end of `current`, in
    template order. Returns (new_text, added_headers). No-op (returns `current`
    unchanged) when nothing is missing — so it is idempotent."""
    missing = missing_sections(current)
    if not missing:
        return current, []
    parts = [current.rstrip("\n")] + [s.text.strip("\n") for s in missing]
    return "\n\n".join(parts) + "\n", [s.header for s in missing]
