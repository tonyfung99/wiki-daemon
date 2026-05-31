"""Ingest orchestration: read -> claude -> verify -> mark processed."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.claude import Runner, run_claude
from wiki_daemon.config import Config
from wiki_daemon.prompts import ingest_prompt
from wiki_daemon.sources import read_source
from wiki_daemon.state import StateStore

_ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]


@dataclass
class IngestResult:
    ok: bool
    skipped: bool = False
    reason: str = ""


def _source_slug(source_path: Path) -> str:
    return source_path.name[:-3] if source_path.name.endswith(".md") else source_path.name


def _verify(cfg: Config, source_path: Path) -> tuple[bool, str]:
    summary = cfg.wiki / "sources" / f"{_source_slug(source_path)}.md"
    if not summary.exists():
        return False, "no source summary page was created"
    if not (cfg.wiki / "index.md").exists():
        return False, "index.md missing"
    if not (cfg.wiki / "log.md").exists():
        return False, "log.md missing"
    return True, ""


def ingest(
    cfg: Config,
    source_path: Path,
    *,
    store: StateStore,
    runner: Runner | None = None,
) -> IngestResult:
    """Ingest a file that is already materialized + stable.

    iCloud materialization and the stability gate are the daemon's job
    (see `icloud.prepare_source`, wired in the daemon worker); this stays pure
    so it is fast to test and reusable by the M1 CLI path on a local vault.
    """
    # resolve() so a symlinked path (e.g. macOS /tmp -> /private/tmp) is
    # comparable to cfg.vault, which is already resolved.
    source_path = Path(source_path).resolve()
    src = read_source(source_path)
    if store.is_processed(src.sha256):
        return IngestResult(ok=True, skipped=True)

    rel = source_path.relative_to(cfg.vault).as_posix()
    kwargs = {} if runner is None else {"runner": runner}
    result = run_claude(
        prompt=ingest_prompt(rel),
        cwd=cfg.vault,
        allowed_tools=_ALLOWED_TOOLS,
        claude_bin=cfg.claude_bin,
        **kwargs,
    )
    if not result.ok:
        return IngestResult(ok=False, reason=f"claude failed: {result.stderr[:200]}")

    ok, reason = _verify(cfg, source_path)
    if not ok:
        return IngestResult(ok=False, reason=reason)

    store.mark_processed(src.sha256, str(source_path))
    return IngestResult(ok=True)
