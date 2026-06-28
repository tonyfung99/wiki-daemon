"""Ingest orchestration: read -> claude -> verify -> mark processed."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.agent import (
    InteractiveRunner, Runner, classify_failure, get_provider, run_agent,
    run_agent_interactive,
)
from wiki_daemon.config import Config
from wiki_daemon.frontmatter import parse
from wiki_daemon.prompts import (
    ingest_prompt, apply_clarification_prompt, query_prompt,
    lint_prompt, lint_repair_prompt,
)
from wiki_daemon.sources import read_source
from wiki_daemon.state import StateStore


@dataclass
class IngestResult:
    ok: bool
    skipped: bool = False
    reason: str = ""
    kind: str = ""


@dataclass
class ApplyResult:
    ok: bool
    reason: str = ""


@dataclass
class QueryResult:
    ok: bool
    answer: str = ""
    saved: bool = False
    reason: str = ""
    kind: str = ""


@dataclass
class LintScan:
    ok: bool
    report: str = ""
    reason: str = ""
    kind: str = ""


def _source_referenced(cfg: Config, source_rel: str) -> bool:
    """A summary page exists under wiki/sources/ whose `sources:` frontmatter
    lists this raw source. Verifying by the traceability contract (not a
    filename guess) lets the maintainer name pages by title."""
    sources_dir = cfg.wiki / "sources"
    if not sources_dir.is_dir():
        return False
    for page in sources_dir.glob("*.md"):
        meta, _ = parse(page.read_text(encoding="utf-8"))
        refs = meta.get("sources") or []
        if isinstance(refs, str):
            refs = [refs]
        if source_rel in [str(r) for r in refs]:
            return True
    return False


def _verify(cfg: Config, source_rel: str) -> tuple[bool, str]:
    if not _source_referenced(cfg, source_rel):
        return False, "no source summary page references this source"
    if not (cfg.wiki / "index.md").exists():
        return False, "index.md missing"
    if not (cfg.wiki / "log.md").exists():
        return False, "log.md missing"
    return True, ""


def _normalize_q(s: str) -> str:
    return " ".join(s.split())


def _query_recorded(cfg: Config, question: str) -> bool:
    """A wiki/queries/*.md page records this question in its `query:` frontmatter
    (whitespace-normalized) — the traceability contract for a saved query."""
    qdir = cfg.wiki / "queries"
    if not qdir.is_dir():
        return False
    target = _normalize_q(question)
    for page in qdir.glob("*.md"):
        meta, _ = parse(page.read_text(encoding="utf-8"))
        q = meta.get("query")
        if q is not None and _normalize_q(str(q)) == target:
            return True
    return False


def _verify_query(cfg: Config, question: str) -> tuple[bool, str]:
    if not _query_recorded(cfg, question):
        return False, "no query page records this question"
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
        return IngestResult(ok=True, skipped=True, kind="skipped")

    rel = source_path.relative_to(cfg.vault).as_posix()
    kwargs = {} if runner is None else {"runner": runner}
    result = run_agent(
        get_provider(cfg), ingest_prompt(rel), cfg.vault, write=True, **kwargs)
    if not result.ok:
        return IngestResult(ok=False, kind=classify_failure(result),
                            reason=f"agent failed: {(result.stdout + result.stderr).strip()[:300]}")

    ok, reason = _verify(cfg, rel)
    if not ok:
        return IngestResult(ok=False, reason=reason, kind="verify_error")

    store.mark_processed(src.sha256, str(source_path))
    return IngestResult(ok=True, kind="ok")


def ingest_interactive(
    cfg: Config,
    source_path: Path,
    *,
    store: StateStore,
    runner: InteractiveRunner | None = None,
) -> IngestResult:
    """Interactive ingest: launch `claude` (no -p) so the maintainer can ask the
    user live. Verifies + marks processed only if the session produced a valid
    result (the user may abort)."""
    source_path = Path(source_path).resolve()
    src = read_source(source_path)
    if store.is_processed(src.sha256):
        return IngestResult(ok=True, skipped=True, kind="skipped")

    rel = source_path.relative_to(cfg.vault).as_posix()
    kwargs = {} if runner is None else {"runner": runner}
    code = run_agent_interactive(
        get_provider(cfg), ingest_prompt(rel, interactive=True), cfg.vault,
        write=True, **kwargs)
    if code != 0:
        # Interactive inherits stdio (no captured stderr), so we can't classify
        # auth/timeout the way headless ingest does — report a generic error.
        return IngestResult(ok=False, kind="agent_error",
                            reason=f"interactive agent exited {code}")
    ok, reason = _verify(cfg, rel)
    if not ok:
        return IngestResult(ok=False, reason=reason, kind="verify_error")
    store.mark_processed(src.sha256, str(source_path))
    return IngestResult(ok=True, kind="ok")


def apply_clarification(cfg: Config, review_id: str, *,
                        runner: Runner | None = None) -> ApplyResult:
    """Run a maintainer pass that applies an answered review item, then verify
    the review file was removed (the contract for 'resolved')."""
    from wiki_daemon.review import read_item

    try:
        item = read_item(cfg, review_id)
    except FileNotFoundError as exc:
        return ApplyResult(ok=False, reason=str(exc))
    if item.status != "answered":
        return ApplyResult(ok=False, reason="item is not answered yet")

    review_rel = item.path.relative_to(cfg.vault).as_posix()
    kwargs = {} if runner is None else {"runner": runner}
    result = run_agent(
        get_provider(cfg), apply_clarification_prompt(review_rel), cfg.vault,
        write=True, **kwargs)
    if not result.ok:
        return ApplyResult(ok=False, reason=f"agent failed: {result.stderr[:200]}")
    if item.path.exists():
        return ApplyResult(ok=False, reason="review file not removed; not applied")
    return ApplyResult(ok=True)


def query(cfg: Config, question: str, *, save: bool = False,
          runner: Runner | None = None) -> QueryResult:
    """Answer a question from the wiki. Read-only by default; with save=True the
    maintainer also files the answer as a wiki/queries/ page (verified)."""
    kwargs = {} if runner is None else {"runner": runner}
    result = run_agent(
        get_provider(cfg), query_prompt(question, save=save), cfg.vault,
        write=save, **kwargs)
    if not result.ok:
        return QueryResult(ok=False, kind=classify_failure(result),
                           reason=f"agent failed: {(result.stdout + result.stderr).strip()[:300]}")
    answer = result.stdout
    if not save:
        return QueryResult(ok=True, answer=answer)
    ok2, reason = _verify_query(cfg, question)
    return QueryResult(ok=True, answer=answer, saved=ok2,
                       reason=("" if ok2 else reason))


def lint_deep(cfg: Config, *, runner: Runner | None = None) -> LintScan:
    """Read-only LLM semantic scan: contradictions, stale claims, data gaps."""
    kwargs = {} if runner is None else {"runner": runner}
    result = run_agent(
        get_provider(cfg), lint_prompt(), cfg.vault, write=False, **kwargs)
    if not result.ok:
        return LintScan(ok=False, kind=classify_failure(result),
                        reason=f"agent failed: {(result.stdout + result.stderr).strip()[:300]}")
    return LintScan(ok=True, report=result.stdout)


def lint_repair(cfg: Config, findings_text: str, *, deep_report: str = "",
                runner: Runner | None = None) -> ApplyResult:
    """LLM repair pass (Write/Edit) that fixes the listed findings."""
    kwargs = {} if runner is None else {"runner": runner}
    result = run_agent(
        get_provider(cfg), lint_repair_prompt(findings_text, deep_report),
        cfg.vault, write=True, **kwargs)
    if not result.ok:
        return ApplyResult(ok=False, reason=f"agent failed: {result.stderr[:200]}")
    return ApplyResult(ok=True)
