# Daemon-side Query Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make saved wiki queries fast and reliable by generating the answer read-only and having the daemon persist the result files (query page, index line, log line) in plain Python, instead of the agent's unreliable workspace-write path.

**Architecture:** A new `query_store.py` module owns persistence: `persist_query(cfg, question, answer) -> (bool, str)` writes `wiki/queries/<slug>.md`, inserts a line under `## Queries` in `index.md`, and appends a line to `log.md`, guarded by a module-level lock. `ops.query` always runs the agent read-only and, for `save=True`, calls `persist_query`. The `query_prompt` save branch and the old `_verify_query` grep are removed.

**Tech Stack:** Python 3.12, pytest, PyYAML (via existing `frontmatter` module). Venv at `.venv`; run tests with `.venv/bin/pytest -q`.

---

## File Structure

- **Create** `src/wiki_daemon/query_store.py` — persistence module (title/slug helpers, index/log writers, `persist_query`).
- **Create** `tests/test_query_store.py` — unit tests for the module.
- **Modify** `src/wiki_daemon/ops.py` — `query()` always read-only + call `persist_query`; delete now-unused `_verify_query`, `_query_recorded`, `_normalize_q`.
- **Modify** `src/wiki_daemon/prompts.py` — `query_prompt` drops its `save` branch.
- **Modify** `tests/test_ops.py` — update query tests for the new flow.
- **Modify** `tests/test_prompts.py` — `query_prompt` is always read-only.
- **Modify** `src/wiki_daemon/templates/AGENTS.md` — trim the `SAVE-QUERY` section.

Reference (existing, do not change):
- `QueryResult` (`ops.py`): `@dataclass` with `ok: bool`, `answer: str = ""`, `saved: bool = False`, `reason: str = ""`, `kind: str = ""`.
- `frontmatter.dump(meta: dict, body: str) -> str` — serializes `---\n<yaml>\n---\n<body>`; YAML-safe (quotes values with colons).
- `frontmatter.parse(text: str) -> tuple[dict, str]`.
- `Config` properties: `cfg.wiki` → `<vault>/wiki` (Path).
- `run_agent(provider, prompt, cwd, *, write, timeout=300, runner=...) -> AgentResult` (`AgentResult.ok`, `.stdout`, `.stderr`).

---

## Task 1: Title and slug helpers

**Files:**
- Create: `src/wiki_daemon/query_store.py`
- Test: `tests/test_query_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_store.py
from wiki_daemon.query_store import _slugify, _title_from_question, _normalize_q


def test_slugify_basic():
    assert _slugify("Hello World: A Test!") == "hello-world-a-test"


def test_slugify_collapses_and_trims():
    assert _slugify("  Multiple   spaces & symbols?? ") == "multiple-spaces-symbols"


def test_slugify_limits_length():
    slug = _slugify("word " * 40)
    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_slugify_non_empty_fallback():
    assert _slugify("!!!") == "query"


def test_title_from_question_capitalizes_and_trims():
    assert _title_from_question("what is a daemon?") == "What is a daemon?"


def test_title_from_question_truncates_long():
    title = _title_from_question("a " * 100)
    assert len(title) <= 80


def test_normalize_q_collapses_whitespace():
    assert _normalize_q("  a\n b   c ") == "a b c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'wiki_daemon.query_store'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/wiki_daemon/query_store.py
"""Daemon-side persistence of saved query answers.

The agent generates the answer read-only; the daemon writes the result files
itself (query page + index line + log line) instead of using the agent's
unreliable workspace-write path.
"""
from __future__ import annotations

import re
import threading
from datetime import date
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import dump, parse

_SLUG_MAX = 60
_TITLE_MAX = 80

# Serializes persist_query's read-modify-write of index.md / log.md so
# concurrent queries (multiple API threads) can't corrupt those shared files.
_write_lock = threading.Lock()


def _normalize_q(s: str) -> str:
    return " ".join(s.split())


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > _SLUG_MAX:
        s = s[:_SLUG_MAX].rsplit("-", 1)[0] if "-" in s[:_SLUG_MAX] else s[:_SLUG_MAX]
        s = s.strip("-")
    return s or "query"


def _title_from_question(question: str) -> str:
    t = _normalize_q(question)
    if len(t) > _TITLE_MAX:
        t = t[:_TITLE_MAX].rsplit(" ", 1)[0].rstrip()
    return t[:1].upper() + t[1:] if t else "Query"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/query_store.py tests/test_query_store.py
git commit -m "feat(query_store): title and slug helpers"
```

---

## Task 2: Insert a line under an index section

**Files:**
- Modify: `src/wiki_daemon/query_store.py`
- Test: `tests/test_query_store.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_query_store.py
from wiki_daemon.query_store import _insert_under_section


def test_insert_under_existing_section():
    text = "# Index\n\n## Concepts\n\n## Queries\n"
    out = _insert_under_section(text, "## Queries", "- [[slug|Title]]")
    assert "## Queries\n- [[slug|Title]]\n" in out


def test_insert_newest_first():
    text = "# Index\n\n## Queries\n- [[old|Old]]\n"
    out = _insert_under_section(text, "## Queries", "- [[new|New]]")
    lines = out.splitlines()
    qi = lines.index("## Queries")
    assert lines[qi + 1] == "- [[new|New]]"
    assert lines[qi + 2] == "- [[old|Old]]"


def test_insert_section_missing_appends_it():
    text = "# Index\n"
    out = _insert_under_section(text, "## Queries", "- [[slug|Title]]")
    assert "## Queries\n- [[slug|Title]]\n" in out
    assert out.endswith("\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: FAIL with `ImportError: cannot import name '_insert_under_section'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/wiki_daemon/query_store.py
def _insert_under_section(text: str, section: str, line: str) -> str:
    """Insert `line` immediately after the `section` header (newest first). If
    the section is absent, append the section header and the line at the end."""
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for l in lines:
        out.append(l)
        if not inserted and l.strip() == section:
            out.append(line)
            inserted = True
    if not inserted:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(section)
        out.append(line)
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/query_store.py tests/test_query_store.py
git commit -m "feat(query_store): index section insertion helper"
```

---

## Task 3: Find an existing page for a question (dedup)

**Files:**
- Modify: `src/wiki_daemon/query_store.py`
- Test: `tests/test_query_store.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_query_store.py
from pathlib import Path
from wiki_daemon.frontmatter import dump
from wiki_daemon.query_store import _find_existing_page, _unique_slug


def _write(p: Path, question: str) -> None:
    p.write_text(dump({"type": "query", "query": question}, "body\n"), encoding="utf-8")


def test_find_existing_page_matches_normalized_question(tmp_path):
    # Normalization is whitespace-only: extra spaces match, so the re-spaced
    # question resolves to the same page.
    _write(tmp_path / "a.md", "What  is   a daemon?")
    assert _find_existing_page(tmp_path, "What is a daemon?") == tmp_path / "a.md"


def test_find_existing_page_none_when_absent(tmp_path):
    _write(tmp_path / "a.md", "different question")
    assert _find_existing_page(tmp_path, "What is a daemon?") is None


def test_unique_slug_dedupes(tmp_path):
    (tmp_path / "foo.md").write_text("x", encoding="utf-8")
    assert _unique_slug(tmp_path, "foo") == "foo-2"
    (tmp_path / "foo-2.md").write_text("x", encoding="utf-8")
    assert _unique_slug(tmp_path, "foo") == "foo-3"


def test_unique_slug_free(tmp_path):
    assert _unique_slug(tmp_path, "bar") == "bar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: FAIL with `ImportError: cannot import name '_find_existing_page'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/wiki_daemon/query_store.py
def _find_existing_page(qdir: Path, question: str) -> Path | None:
    """Return the queries page whose `query:` frontmatter matches `question`
    (whitespace-normalized), or None. Malformed pages are skipped."""
    if not qdir.is_dir():
        return None
    target = _normalize_q(question)
    for page in sorted(qdir.glob("*.md")):
        try:
            meta, _ = parse(page.read_text(encoding="utf-8"))
        except OSError:
            continue
        q = meta.get("query")
        if q is not None and _normalize_q(str(q)) == target:
            return page
    return None


def _unique_slug(qdir: Path, slug: str) -> str:
    """A slug whose `<slug>.md` does not yet exist in qdir (append -2, -3, ...)."""
    if not (qdir / f"{slug}.md").exists():
        return slug
    n = 2
    while (qdir / f"{slug}-{n}.md").exists():
        n += 1
    return f"{slug}-{n}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/query_store.py tests/test_query_store.py
git commit -m "feat(query_store): existing-page lookup and unique slug"
```

---

## Task 4: `persist_query` — write page, index, log

**Files:**
- Modify: `src/wiki_daemon/query_store.py`
- Test: `tests/test_query_store.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_query_store.py
from wiki_daemon.config import Config
from wiki_daemon.frontmatter import parse
from wiki_daemon.query_store import persist_query


def _cfg(tmp_path) -> Config:
    vault = tmp_path / "vault"
    (vault / "wiki" / "queries").mkdir(parents=True)
    (vault / "wiki" / "index.md").write_text(
        "# Index\n\n## Queries\n", encoding="utf-8")
    (vault / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    return Config(vault=vault)


def test_persist_writes_page_index_log(tmp_path):
    cfg = _cfg(tmp_path)
    saved, reason = persist_query(cfg, "What is a daemon?", "A background process. [[Daemon]]\n")
    assert saved is True and reason == ""
    pages = list((cfg.wiki / "queries").glob("*.md"))
    assert len(pages) == 1
    meta, body = parse(pages[0].read_text(encoding="utf-8"))
    assert meta["type"] == "query"
    assert meta["query"] == "What is a daemon?"
    assert meta["title"] == "What is a daemon?"
    assert "background process" in body
    idx = (cfg.wiki / "index.md").read_text(encoding="utf-8")
    assert pages[0].stem in idx
    log = (cfg.wiki / "log.md").read_text(encoding="utf-8")
    assert "query | What is a daemon?" in log


def test_persist_title_with_colon_is_valid_yaml(tmp_path):
    cfg = _cfg(tmp_path)
    saved, _ = persist_query(cfg, "DIAGNOSTIC: reply ok", "ok\n")
    assert saved is True
    page = next((cfg.wiki / "queries").glob("*.md"))
    meta, _ = parse(page.read_text(encoding="utf-8"))  # must not raise
    assert meta["query"] == "DIAGNOSTIC: reply ok"


def test_persist_reask_updates_in_place(tmp_path):
    cfg = _cfg(tmp_path)
    persist_query(cfg, "What is a daemon?", "first answer\n")
    persist_query(cfg, "What  is a daemon?", "second answer\n")  # re-spaced same q
    pages = list((cfg.wiki / "queries").glob("*.md"))
    assert len(pages) == 1  # updated in place, no duplicate
    _, body = parse(pages[0].read_text(encoding="utf-8"))
    assert "second answer" in body
    idx = (cfg.wiki / "index.md").read_text(encoding="utf-8")
    assert idx.count("## Queries") == 1
    assert idx.count(pages[0].stem) == 1  # index line not duplicated on update


def test_persist_returns_false_on_write_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    import wiki_daemon.query_store as qs
    orig = Path.write_text

    def boom(self, *a, **k):
        if self.name.endswith(".md") and "queries" in str(self):
            raise OSError("disk full")
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", boom)
    saved, reason = persist_query(cfg, "Q", "A\n")
    assert saved is False
    assert "disk full" in reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: FAIL with `ImportError: cannot import name 'persist_query'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/wiki_daemon/query_store.py
def _write_index_line(cfg: Config, slug: str, title: str) -> None:
    index = cfg.wiki / "index.md"
    text = index.read_text(encoding="utf-8") if index.exists() else "# Index\n"
    line = f"- [[{slug}|{title}]]"
    if line in text:
        return
    index.write_text(_insert_under_section(text, "## Queries", line), encoding="utf-8")


def _append_log(cfg: Config, question: str, today: str) -> None:
    log = cfg.wiki / "log.md"
    text = log.read_text(encoding="utf-8") if log.exists() else "# Log\n"
    if not text.endswith("\n"):
        text += "\n"
    log.write_text(text + f"## [{today}] query | {question}\n", encoding="utf-8")


def persist_query(cfg: Config, question: str, answer: str) -> tuple[bool, str]:
    """Write the query answer into the vault: a `wiki/queries/<slug>.md` page,
    a line under `## Queries` in index.md, and a log.md line. Returns
    (True, "") on success, or (False, reason) on any I/O failure — the answer is
    still returned to the caller; only `saved` is False."""
    with _write_lock:
        try:
            qdir = cfg.wiki / "queries"
            qdir.mkdir(parents=True, exist_ok=True)
            title = _title_from_question(question)
            today = date.today().isoformat()
            existing = _find_existing_page(qdir, question)
            if existing is not None:
                path = existing
                is_new = False
            else:
                slug = _unique_slug(qdir, _slugify(title))
                path = qdir / f"{slug}.md"
                is_new = True
            meta = {"type": "query", "title": title, "query": question,
                    "updated": today}
            path.write_text(dump(meta, answer), encoding="utf-8")
            if is_new:
                _write_index_line(cfg, path.stem, title)
            _append_log(cfg, question, today)
            return True, ""
        except OSError as exc:
            return False, f"save failed: {exc}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_query_store.py -q`
Expected: PASS (18 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/query_store.py tests/test_query_store.py
git commit -m "feat(query_store): persist_query writes page, index, and log"
```

---

## Task 5: `prompts.query_prompt` — always read-only

**Files:**
- Modify: `src/wiki_daemon/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# replace/adjust the query_prompt tests in tests/test_prompts.py
from wiki_daemon.prompts import query_prompt


def test_query_prompt_is_read_only():
    p = query_prompt("What is a daemon?")
    assert "What is a daemon?" in p
    assert "READ-ONLY" in p
    # No save/write instructions remain:
    assert "SAVE-QUERY" not in p
    assert "wiki/queries/" not in p
    assert "Marp" not in p


def test_query_prompt_takes_no_save_kwarg():
    import inspect
    sig = inspect.signature(query_prompt)
    assert "save" not in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prompts.py -q`
Expected: FAIL (the current `query_prompt` has a `save` parameter and emits SAVE-QUERY text).

- [ ] **Step 3: Write minimal implementation**

Replace the existing `query_prompt` in `src/wiki_daemon/prompts.py` with:

```python
def query_prompt(question: str) -> str:
    return (
        "Follow the QUERY operation defined in your project instructions exactly.\n"
        f"Answer this question from the wiki, citing the pages you used: {question}\n"
        "Read wiki/index.md first, open the relevant pages, and synthesize an "
        "answer with [[wiki-link]] citations. Use rich Markdown where it helps — "
        "comparison tables, Mermaid diagrams (```mermaid), and fenced code "
        "snippets (e.g. ```python) — but do NOT execute code. Do not modify "
        "anything under raw/.\n"
        "This is READ-ONLY: do not create or edit any files. Print the answer "
        "(Markdown tables and ```mermaid/code blocks are fine in text)."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prompts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/prompts.py tests/test_prompts.py
git commit -m "refactor(prompts): query_prompt is always read-only"
```

---

## Task 6: `ops.query` — read-only agent + daemon persistence

**Files:**
- Modify: `src/wiki_daemon/ops.py`
- Test: `tests/test_ops.py`

- [ ] **Step 1: Write the failing test**

```python
# add/adjust in tests/test_ops.py
from wiki_daemon.config import Config
from wiki_daemon.ops import query


def _make_runner(recorder):
    # runner(cmd, cwd, timeout) -> (returncode, stdout, stderr)
    def runner(cmd, cwd, timeout):
        recorder["cmd"] = cmd
        return 0, "ANSWER [[Daemon]]\n", ""
    return runner


def _vault(tmp_path):
    v = tmp_path / "vault"
    (v / "wiki" / "queries").mkdir(parents=True)
    (v / "wiki" / "index.md").write_text("# Index\n\n## Queries\n", encoding="utf-8")
    (v / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    return v


def test_query_read_only_no_save(tmp_path):
    cfg = Config(vault=_vault(tmp_path), provider="codex")
    rec = {}
    r = query(cfg, "What is a daemon?", save=False, runner=_make_runner(rec))
    assert r.ok and r.answer.startswith("ANSWER")
    assert r.saved is False
    # No page written when save=False:
    assert list((cfg.wiki / "queries").glob("*.md")) == []
    # The agent was invoked read-only (codex read-only cmd, not workspace-write):
    assert "workspace-write" not in " ".join(rec["cmd"])


def test_query_save_persists_via_daemon(tmp_path):
    cfg = Config(vault=_vault(tmp_path), provider="codex")
    r = query(cfg, "What is a daemon?", save=True, runner=_make_runner({}))
    assert r.ok and r.saved is True
    pages = list((cfg.wiki / "queries").glob("*.md"))
    assert len(pages) == 1
    assert "ANSWER" in pages[0].read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ops.py -q -k query`
Expected: FAIL — current `query` runs `write=save` (workspace-write when saving) and does not persist via the daemon.

- [ ] **Step 3: Write minimal implementation**

In `src/wiki_daemon/ops.py`:

1. Add the import near the other `wiki_daemon` imports:

```python
from wiki_daemon.query_store import persist_query
```

2. Replace the `query` function body with:

```python
def query(cfg: Config, question: str, *, save: bool = False,
          runner: Runner | None = None) -> QueryResult:
    """Answer a question from the wiki. The agent runs READ-ONLY; with save=True
    the daemon files the answer as a wiki/queries/ page itself (the agent's
    workspace-write path is unreliable for this)."""
    kwargs = {} if runner is None else {"runner": runner}
    result = run_agent(
        get_provider(cfg), query_prompt(question), cfg.vault,
        write=False, timeout=cfg.query_timeout, **kwargs)
    if not result.ok:
        return QueryResult(ok=False, kind=classify_failure(result),
                           reason=f"agent failed: {(result.stdout + result.stderr).strip()[:300]}")
    answer = result.stdout
    if not save:
        return QueryResult(ok=True, answer=answer)
    saved, reason = persist_query(cfg, question, answer)
    return QueryResult(ok=True, answer=answer, saved=saved,
                       reason=("" if saved else reason))
```

3. Delete the now-unused `_verify_query`, `_query_recorded`, and `_normalize_q`
   functions from `ops.py` (they were only used by the old save path). If
   `parse` is no longer referenced anywhere else in `ops.py`, remove its import
   (`from wiki_daemon.frontmatter import parse`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ops.py -q`
Expected: PASS. If a previously-existing test asserted the old save behavior (agent writes the page / `_verify_query`), update it to the new flow or delete it.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/ops.py tests/test_ops.py
git commit -m "feat(ops): query runs read-only and persists via the daemon"
```

---

## Task 7: Trim the template `AGENTS.md` SAVE-QUERY section

**Files:**
- Modify: `src/wiki_daemon/templates/AGENTS.md`

- [ ] **Step 1: Edit the template**

Remove the entire `## SAVE-QUERY` section (the numbered list about writing
`wiki/queries/<kebab-slug>.md`, the Marp-deck companion line, updating
`index.md`, and appending to `log.md`). Leave the `## QUERY operation` section
intact. Rationale: the daemon now owns persistence, so these agent instructions
are dead for queries; existing vaults are unaffected because the daemon sends the
read-only prompt.

- [ ] **Step 2: Verify no test references the removed section**

Run: `.venv/bin/pytest -q -k "agents or template or scaffold"`
Expected: PASS (no test asserts the SAVE-QUERY text). If one does, update it.

- [ ] **Step 3: Commit**

```bash
git add src/wiki_daemon/templates/AGENTS.md
git commit -m "docs(template): drop SAVE-QUERY from AGENTS.md (daemon persists now)"
```

---

## Task 8: Full suite and wrap-up

**Files:** none (verification).

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all tests). Investigate and fix any failure before proceeding —
in particular any older `test_ops.py` / `test_prompts.py` test still asserting the
removed `save`-mode behavior.

- [ ] **Step 2: Confirm no dangling references**

Run: `grep -rn "_verify_query\|_query_recorded\|query_prompt(.*save" src/ tests/`
Expected: no matches (all removed). Fix any that remain.

- [ ] **Step 3: Final commit if anything was adjusted**

```bash
git add -A
git commit -m "test: finalize daemon-side query persistence"
```

---

## Notes for the executor

- **No API/client change.** `QueryResult` and the HTTP JSON shape are unchanged
  (`answer`, `saved`, `saveError`, `citations`), so WikiReader needs nothing.
- **Deferred (out of scope):** companion Marp decks; a self-healing reconcile that
  rebuilds the `## Queries` index section from the folder (see the design's
  Concurrency note).
- **Open detail:** the `- [[<slug>|<title>]]` index line format should be checked
  against the live vault's actual `## Queries` convention when the daemon host is
  reachable, and adjusted if the maintainer uses a different style.
