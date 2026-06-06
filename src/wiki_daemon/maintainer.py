# src/wiki_daemon/maintainer.py
"""The vault `CLAUDE.md` (the "maintainer brain") drift logic.

Pure functions, no CLI or daemon state. The bundled template is the source of
truth; a vault brain is "stale" when it is missing a `## ` section the template
defines. `apply_upgrade` repairs non-destructively by appending the missing
sections — it never rewrites existing ones, so user customizations survive.
"""
from __future__ import annotations

from collections import namedtuple
from importlib import resources

Section = namedtuple("Section", "header text")


def template_text() -> str:
    """The bundled canonical CLAUDE.md template."""
    return resources.files("wiki_daemon.templates").joinpath("CLAUDE.md").read_text(
        encoding="utf-8")


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
