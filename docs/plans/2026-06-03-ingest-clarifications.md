# Ingest Clarifications & Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ingest raise clarifications instead of silently guessing — interactive Q&A when a human is at the terminal (TTY), and an async `wiki/review/` queue (resolved via `wiki review` / `wiki review answer`) when not.

**Architecture:** A new `review.py` owns the `wiki/review/*.md` queue (pure file ops). `claude.py` gains an interactive (no `-p`) runner; `ops.py` gains `ingest_interactive` and `apply_clarification`; `prompts.py` selects ask-live vs raise-clarification wording. `cli.py` adds `--interactive`/`--no-interactive` to ingest/import (auto-detecting TTY), a `wiki review` command, and a `review:` status line. The vault `CLAUDE.md` template gains the RAISE/APPLY CLARIFICATION rules and a `wiki/review/` layer. The daemon needs no code change — the maintainer writes review files per `CLAUDE.md`.

**Tech Stack:** Python 3.12, stdlib `argparse`/`subprocess`, `pyyaml` (via `frontmatter`), `pytest`. Run tests with `.venv/bin/pytest`.

**Reference spec:** `docs/specs/2026-06-03-ingest-clarifications-design.md`

---

## File Structure

- **Create** `src/wiki_daemon/review.py` — `ReviewItem` + `list_items`/`read_item`/`write_answer`.
- **Modify** `src/wiki_daemon/config.py` — add `review` path property.
- **Modify** `src/wiki_daemon/scaffold.py` — scaffold `wiki/review`.
- **Modify** `src/wiki_daemon/templates/CLAUDE.md` — review layer + RAISE/APPLY operations.
- **Modify** `src/wiki_daemon/prompts.py` — `ingest_prompt(rel, *, interactive=False)` + `apply_clarification_prompt`.
- **Modify** `src/wiki_daemon/claude.py` — `run_claude_interactive`.
- **Modify** `src/wiki_daemon/ops.py` — `ingest_interactive`, `apply_clarification`, `ApplyResult`.
- **Modify** `src/wiki_daemon/cli.py` — ingest/import interactive flags, `wiki review`, status line.
- **Modify** `README.md` — document the commands.
- **Tests:** new `tests/test_review.py`; extend `tests/test_config.py`, `tests/test_scaffold.py`, `tests/test_prompts.py`, `tests/test_claude.py`, `tests/test_ops.py`, `tests/test_cli.py`.

End every commit body in this plan with:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 1: `Config.review` path + scaffold `wiki/review/`

**Files:**
- Modify: `src/wiki_daemon/config.py`
- Modify: `src/wiki_daemon/scaffold.py`
- Test: `tests/test_config.py`, `tests/test_scaffold.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:
```python
def test_review_path_under_wiki(tmp_path):
    from wiki_daemon.config import Config
    cfg = Config(vault=tmp_path)
    assert cfg.review == tmp_path / "wiki" / "review"
```

In `tests/test_scaffold.py`, the existing loop checks `("entities", "concepts", "sources", "queries")`. Add `"review"` to it. Find:
```python
    for sub in ("entities", "concepts", "sources", "queries"):
```
and change it to:
```python
    for sub in ("entities", "concepts", "sources", "queries", "review"):
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_scaffold.py -v`
Expected: FAIL (`Config` has no `review`; `wiki/review` not created).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/config.py`, add the property next to `wiki`:
```python
    @property
    def review(self) -> Path:
        return self.vault / "wiki" / "review"
```

In `src/wiki_daemon/scaffold.py`, add `wiki/review` to `_DIRS`:
```python
_DIRS = ["raw/sources", "wiki/entities", "wiki/concepts", "wiki/sources",
         "wiki/queries", "wiki/review"]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py tests/test_scaffold.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/config.py src/wiki_daemon/scaffold.py tests/test_config.py tests/test_scaffold.py
git commit -m "feat: add wiki/review layer (Config.review + scaffold)"
```

---

### Task 2: Vault `CLAUDE.md` template — RAISE/APPLY CLARIFICATION

**Files:**
- Modify: `src/wiki_daemon/templates/CLAUDE.md`
- Test: `tests/test_scaffold.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scaffold.py`:
```python
def test_template_has_clarification_sections(tmp_path):
    from wiki_daemon.config import Config
    from wiki_daemon.scaffold import init_vault
    cfg = Config(vault=tmp_path)
    init_vault(cfg)
    text = (cfg.vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "wiki/review/" in text
    assert "RAISE CLARIFICATION" in text
    assert "APPLY CLARIFICATION" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_scaffold.py::test_template_has_clarification_sections -v`
Expected: FAIL (sections absent).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/templates/CLAUDE.md`, add a `wiki/review/` bullet under the
Layers list, right after the `wiki/queries/` line:
```
  - `wiki/review/` — open clarifications you raised during ingest (one file each)
```

Then append these two sections to the END of the file (after the INGEST operation):
```markdown

## RAISE CLARIFICATION (during INGEST)
When a structural decision is genuinely ambiguous — which entity a name refers
to, conflicting facts across sources, whether two things are the same concept,
or missing context you cannot infer — do NOT stall:
1. Make a best-effort choice and note it briefly on the affected page.
2. Record the open question as `wiki/review/<kebab-slug>.md` with frontmatter:
   ```
   ---
   type: review
   status: open
   source: raw/sources/<file>.md
   question: "<the specific question for the user>"
   tentative: "<the best-effort choice you made>"
   created: <YYYY-MM-DD>
   ---
   <optional context>
   ```
Ingest still completes normally; the source is fully processed. If you are told
this is an interactive session, ASK the user directly and wait instead of
writing a review file.

## APPLY CLARIFICATION
Given one answered review file (`status: answered`, with an `answer:` field):
1. Read its `question`, `tentative`, and `answer`.
2. Apply the answer to the relevant wiki pages (rename/merge/split/relink as
   needed), preserving the `sources:` traceability.
3. Append one line to `wiki/log.md`:
   `## [<YYYY-MM-DD>] review | <question>`
4. DELETE the review file (resolution = removal).
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_scaffold.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/templates/CLAUDE.md tests/test_scaffold.py
git commit -m "feat: vault template gains RAISE/APPLY CLARIFICATION operations"
```

---

### Task 3: `review.py` module

**Files:**
- Create: `src/wiki_daemon/review.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review.py
import pytest

from wiki_daemon.config import Config
from wiki_daemon.review import ReviewItem, list_items, read_item, write_answer


def _cfg(tmp_path):
    return Config(vault=tmp_path)


def _seed(cfg, item_id="calvin-vs-dark", status="open", answer=None):
    cfg.review.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: review",
        f"status: {status}",
        "source: raw/sources/2026-06-02-photosynthesis.md",
        'question: "Is Calvin cycle the same as dark reactions?"',
        'tentative: "Treated as the same."',
        "created: 2026-06-03",
    ]
    if answer is not None:
        lines.append(f'answer: "{answer}"')
    lines += ["---", "context body", ""]
    (cfg.review / f"{item_id}.md").write_text("\n".join(lines), encoding="utf-8")


def test_list_items_returns_open(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(cfg)
    items = list_items(cfg)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, ReviewItem)
    assert it.id == "calvin-vs-dark"
    assert it.status == "open"
    assert it.source.endswith("photosynthesis.md")
    assert "Calvin cycle" in it.question
    assert it.answer is None


def test_list_items_empty_when_no_dir(tmp_path):
    assert list_items(_cfg(tmp_path)) == []


def test_list_items_ignores_non_markdown(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert list_items(cfg) == []


def test_read_item_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_item(_cfg(tmp_path), "nope")


def test_write_answer_sets_status_and_answer(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(cfg)
    out = write_answer(cfg, "calvin-vs-dark", "They are the same; keep one page.")
    assert out.status == "answered"
    assert out.answer == "They are the same; keep one page."
    # persisted + body preserved
    again = read_item(cfg, "calvin-vs-dark")
    assert again.status == "answered"
    assert again.answer == "They are the same; keep one page."
    assert "context body" in (cfg.review / "calvin-vs-dark.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_review.py -v`
Expected: FAIL (`ModuleNotFoundError: wiki_daemon.review`).

- [ ] **Step 3: Implement**

```python
# src/wiki_daemon/review.py
"""The wiki/review/ clarification queue: one markdown file per open question.

Pure file operations over the vault — no LLM, no daemon state. The maintainer
(claude) creates these during ingest; `write_answer` records the user's answer;
ops.apply_clarification runs a maintainer pass that resolves (deletes) the file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import dump, parse


@dataclass(frozen=True)
class ReviewItem:
    id: str
    path: Path
    status: str
    source: str
    question: str
    tentative: str
    answer: str | None


def _item_from_file(path: Path) -> ReviewItem:
    meta, _ = parse(path.read_text(encoding="utf-8"))
    return ReviewItem(
        id=path.stem,
        path=path,
        status=str(meta.get("status", "open")),
        source=str(meta.get("source", "")),
        question=str(meta.get("question", "")),
        tentative=str(meta.get("tentative", "")),
        answer=(str(meta["answer"]) if meta.get("answer") is not None else None),
    )


def list_items(cfg: Config) -> list[ReviewItem]:
    """Every wiki/review/*.md, sorted by id. Empty if the dir is absent."""
    if not cfg.review.is_dir():
        return []
    return [_item_from_file(p) for p in sorted(cfg.review.glob("*.md"))]


def read_item(cfg: Config, item_id: str) -> ReviewItem:
    """Load one item by id (filename stem). Raises FileNotFoundError if absent."""
    path = cfg.review / f"{item_id}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no such review item: {item_id}")
    return _item_from_file(path)


def write_answer(cfg: Config, item_id: str, answer: str) -> ReviewItem:
    """Record the user's answer: set status=answered + answer, preserving body.
    Atomic write (temp + os.replace)."""
    path = cfg.review / f"{item_id}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no such review item: {item_id}")
    meta, body = parse(path.read_text(encoding="utf-8"))
    meta["status"] = "answered"
    meta["answer"] = answer
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump(meta, body), encoding="utf-8")
    os.replace(tmp, path)
    return _item_from_file(path)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_review.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/review.py tests/test_review.py
git commit -m "feat: wiki/review clarification queue module"
```

---

### Task 4: `prompts.py` — interactive flag + apply prompt

**Files:**
- Modify: `src/wiki_daemon/prompts.py`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompts.py`:
```python
from wiki_daemon.prompts import apply_clarification_prompt


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_prompts.py -v`
Expected: FAIL (`ingest_prompt` has no `interactive` kwarg; no `apply_clarification_prompt`).

- [ ] **Step 3: Implement**

Replace the body of `src/wiki_daemon/prompts.py` (keep the module docstring) with:
```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/prompts.py tests/test_prompts.py
git commit -m "feat: interactive/headless ingest prompts + apply-clarification prompt"
```

---

### Task 5: `claude.py` — `run_claude_interactive`

**Files:**
- Modify: `src/wiki_daemon/claude.py`
- Test: `tests/test_claude.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude.py`:
```python
from wiki_daemon.claude import run_claude_interactive


def test_run_claude_interactive_builds_argv_without_dash_p():
    seen = {}
    def runner(cmd, cwd):
        seen["cmd"] = cmd
        return 0
    code = run_claude_interactive("hello", cwd=".", allowed_tools=["Read", "Write"],
                                  runner=runner)
    assert code == 0
    assert "-p" not in seen["cmd"]
    assert seen["cmd"][0] == "claude"
    assert "hello" in seen["cmd"]
    assert "--allowed-tools" in seen["cmd"]
    assert "Read,Write" in seen["cmd"]
    assert "--dangerously-skip-permissions" in seen["cmd"]


def test_run_claude_interactive_returns_runner_code():
    code = run_claude_interactive("p", cwd=".", allowed_tools=["Read"],
                                  runner=lambda cmd, cwd: 3)
    assert code == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_claude.py -v`
Expected: FAIL (`cannot import name 'run_claude_interactive'`).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/claude.py`, add after `run_claude` (subprocess is already imported):
```python
# Interactive runner(cmd, cwd) -> returncode. No capture: stdio is inherited so
# the user can converse with claude in their terminal.
InteractiveRunner = Callable[[list[str], Path], int]


def _interactive_subprocess_runner(cmd: list[str], cwd: Path) -> int:
    return subprocess.run(cmd, cwd=str(cwd)).returncode


def run_claude_interactive(
    prompt: str,
    cwd: Path,
    allowed_tools: list[str],
    claude_bin: str = "claude",
    skip_permissions: bool = True,
    runner: InteractiveRunner = _interactive_subprocess_runner,
) -> int:
    """Launch `claude` WITHOUT -p so the model can ask and the user can answer
    live. Returns the process exit code."""
    cmd = [claude_bin, prompt, "--allowed-tools", ",".join(allowed_tools)]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return runner(cmd, Path(cwd))
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_claude.py -v`
Expected: PASS (all claude tests).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/claude.py tests/test_claude.py
git commit -m "feat: run_claude_interactive (no -p, inherited stdio)"
```

---

### Task 6: `ops.py` — `ingest_interactive` + `apply_clarification`

**Files:**
- Modify: `src/wiki_daemon/ops.py`
- Test: `tests/test_ops.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ops.py`. (`_make_source`, `_good_claude`, `_lazy_claude` already exist at the top of this file.)
```python
from wiki_daemon.ops import ingest_interactive, apply_clarification, ApplyResult


def test_ingest_interactive_success(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    def interactive_runner(cmd, cwd):
        # behave like a compliant maintainer (writes summary page + index/log)
        (cfg.wiki / "sources" / "acme-corp.md").write_text(
            "---\ntype: source\nsources: [raw/sources/" + src.name + "]\n---\nsummary\n",
            encoding="utf-8")
        (cfg.wiki / "index.md").write_text("# Index\n", encoding="utf-8")
        (cfg.wiki / "log.md").write_text("# Log\n", encoding="utf-8")
        return 0

    result = ingest_interactive(cfg, src, store=store, runner=interactive_runner)
    assert result.ok is True and result.kind == "ok"
    from wiki_daemon.sources import read_source
    assert store.is_processed(read_source(src).sha256)


def test_ingest_interactive_nonzero_not_processed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    result = ingest_interactive(cfg, src, store=store, runner=lambda cmd, cwd: 1)
    assert result.ok is False and result.kind == "claude_error"
    from wiki_daemon.sources import read_source
    assert store.is_processed(read_source(src).sha256) is False


def _seed_answered_review(cfg, item_id="q1"):
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{item_id}.md").write_text(
        "---\ntype: review\nstatus: answered\n"
        "source: raw/sources/x.md\nquestion: \"q?\"\ntentative: \"t\"\n"
        "answer: \"a\"\ncreated: 2026-06-03\n---\nbody\n", encoding="utf-8")
    return cfg.review / f"{item_id}.md"


def test_apply_clarification_success_deletes_file(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    path = _seed_answered_review(cfg)

    def runner(cmd, cwd, timeout):
        path.unlink()  # maintainer resolves + removes the file
        return 0, "ok\n", ""

    result = apply_clarification(cfg, "q1", runner=runner)
    assert isinstance(result, ApplyResult)
    assert result.ok is True
    assert not path.exists()


def test_apply_clarification_unanswered_fails(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "q2.md").write_text(
        "---\ntype: review\nstatus: open\nsource: raw/sources/x.md\n"
        "question: \"q?\"\ntentative: \"t\"\ncreated: 2026-06-03\n---\nbody\n",
        encoding="utf-8")
    calls = {"n": 0}
    result = apply_clarification(cfg, "q2", runner=lambda *a, **k: calls.__setitem__("n", 1) or (0, "", ""))
    assert result.ok is False and "answered" in result.reason
    assert calls["n"] == 0  # never ran claude


def test_apply_clarification_file_not_removed_fails(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    _seed_answered_review(cfg, "q3")
    result = apply_clarification(cfg, "q3", runner=lambda cmd, cwd, timeout: (0, "ok", ""))
    assert result.ok is False and "not removed" in result.reason
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_ops.py -v`
Expected: FAIL (`cannot import name 'ingest_interactive'`).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/ops.py`:

Update imports — extend the prompts import and the claude import:
```python
from wiki_daemon.claude import Runner, run_claude, run_claude_interactive, classify_failure
from wiki_daemon.prompts import ingest_prompt, apply_clarification_prompt
```

Add the `ApplyResult` dataclass after `IngestResult`:
```python
@dataclass
class ApplyResult:
    ok: bool
    reason: str = ""
```

Add the two functions at the end of the file:
```python
def ingest_interactive(
    cfg: Config,
    source_path: Path,
    *,
    store: StateStore,
    runner=None,
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
    code = run_claude_interactive(
        prompt=ingest_prompt(rel, interactive=True),
        cwd=cfg.vault,
        allowed_tools=_ALLOWED_TOOLS,
        claude_bin=cfg.claude_bin,
        **kwargs,
    )
    if code != 0:
        return IngestResult(ok=False, kind="claude_error",
                            reason=f"interactive claude exited {code}")
    ok, reason = _verify(cfg, rel)
    if not ok:
        return IngestResult(ok=False, reason=reason, kind="verify_error")
    store.mark_processed(src.sha256, str(source_path))
    return IngestResult(ok=True, kind="ok")


def apply_clarification(cfg: Config, review_id: str, *, runner=None) -> ApplyResult:
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
    result = run_claude(
        prompt=apply_clarification_prompt(review_rel),
        cwd=cfg.vault,
        allowed_tools=_ALLOWED_TOOLS,
        claude_bin=cfg.claude_bin,
        **kwargs,
    )
    if not result.ok:
        return ApplyResult(ok=False, reason=f"claude failed: {result.stderr[:200]}")
    if item.path.exists():
        return ApplyResult(ok=False, reason="review file not removed; not applied")
    return ApplyResult(ok=True)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_ops.py -v`
Expected: PASS (all ops tests).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/ops.py tests/test_ops.py
git commit -m "feat: ingest_interactive + apply_clarification ops"
```

---

### Task 7: `cli.py` — `--interactive`/`--no-interactive` on ingest & import

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:
```python
def test_ingest_flags_parse_tristate():
    parser = build_parser()
    assert parser.parse_args(["ingest", "--vault", "/v", "f.md"]).interactive is None
    assert parser.parse_args(["ingest", "--vault", "/v", "--interactive", "f.md"]).interactive is True
    assert parser.parse_args(["ingest", "--vault", "/v", "--no-interactive", "f.md"]).interactive is False


def test_cmd_ingest_uses_interactive_when_forced(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")

    import wiki_daemon.cli as cli
    called = {"interactive": False}
    def fake_interactive(cfg, path, *, store):
        called["interactive"] = True
        from wiki_daemon.ops import IngestResult
        return IngestResult(ok=True, kind="ok")
    monkeypatch.setattr(cli, "ingest_interactive", fake_interactive)

    rc = cli.cmd_ingest(cfg, str(src), interactive=True)
    assert rc == 0 and called["interactive"] is True


def test_cmd_ingest_headless_when_forced_off(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")

    import wiki_daemon.cli as cli
    called = {"headless": False}
    def fake_ingest(cfg, path, *, store):
        called["headless"] = True
        from wiki_daemon.ops import IngestResult
        return IngestResult(ok=True, kind="ok")
    monkeypatch.setattr(cli, "ingest", fake_ingest)

    rc = cli.cmd_ingest(cfg, str(src), interactive=False)
    assert rc == 0 and called["headless"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL (`interactive` not a parser attr; `cmd_ingest` has no `interactive` param).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/cli.py`:

Add the import near the other ops import:
```python
from wiki_daemon.ops import ingest, ingest_interactive
```

Add interactive flags to BOTH the `ingest` and `import` subparsers. After the
`ing.add_argument("file", ...)` line, add:
```python
    ig = ing.add_mutually_exclusive_group()
    ig.add_argument("--interactive", dest="interactive", action="store_true",
                    default=None, help="ask clarifications live (default if a TTY)")
    ig.add_argument("--no-interactive", dest="interactive", action="store_false",
                    help="headless: queue clarifications to wiki/review/")
```
After the `imp.add_argument("file", ...)` line, add the same group bound to `imp`:
```python
    mg = imp.add_mutually_exclusive_group()
    mg.add_argument("--interactive", dest="interactive", action="store_true",
                    default=None, help="ask clarifications live (default if a TTY)")
    mg.add_argument("--no-interactive", dest="interactive", action="store_false",
                    help="headless: queue clarifications to wiki/review/")
```

Add a helper and rewrite `cmd_ingest` to take `interactive`:
```python
def _want_interactive(flag) -> bool:
    """Resolve the tri-state flag: explicit wins, else auto-detect a TTY."""
    return sys.stdin.isatty() if flag is None else flag


def cmd_ingest(cfg: Config, file: str, *, interactive=None) -> int:
    store = StateStore(cfg.processed_json)
    if _want_interactive(interactive):
        result = ingest_interactive(cfg, Path(file), store=store)
    else:
        result = ingest(cfg, Path(file), store=store)
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1
```

Rewrite the ingest tail of `cmd_import` the same way (replace its `result = ingest(cfg, dest, store=store)` block). The full `cmd_import` becomes:
```python
def cmd_import(cfg: Config, file: str, *, interactive=None) -> int:
    try:
        dest = import_source(cfg, Path(file))
    except (FileNotFoundError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported {dest.name}")

    store = StateStore(cfg.processed_json)
    if _want_interactive(interactive):
        result = ingest_interactive(cfg, dest, store=store)
    else:
        result = ingest(cfg, dest, store=store)
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1
```

Update the dispatch in `main()`:
```python
    if ns.command == "ingest":
        return cmd_ingest(cfg, ns.file, interactive=ns.interactive)
    if ns.command == "import":
        return cmd_import(cfg, ns.file, interactive=ns.interactive)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (new + existing CLI tests — the prior `cmd_import` tests still pass since `interactive` defaults to `None` and those tests run under pytest with no TTY → headless).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: --interactive/--no-interactive on ingest & import (TTY auto-detect)"
```

---

### Task 8: `cli.py` — `wiki review` + `wiki review answer`

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:
```python
from wiki_daemon.cli import _render_review, cmd_review_answer


def _seed_review(cfg, item_id="q1", status="open"):
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{item_id}.md").write_text(
        "---\ntype: review\nstatus: " + status + "\n"
        "source: raw/sources/x.md\nquestion: \"Same concept?\"\n"
        "tentative: \"t\"\ncreated: 2026-06-03\n---\nbody\n", encoding="utf-8")


def test_render_review_lists_open(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    _seed_review(cfg)
    out = _render_review(cfg)
    assert "q1" in out and "open" in out and "Same concept?" in out


def test_render_review_empty(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    out = _render_review(cfg)
    assert "no open" in out.lower()


def test_cmd_review_answer_runs_apply(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    _seed_review(cfg, "q1")

    import wiki_daemon.cli as cli
    from wiki_daemon.ops import ApplyResult
    seen = {}
    def fake_apply(cfg, rid):
        seen["id"] = rid
        # confirm the answer was persisted before apply ran
        from wiki_daemon.review import read_item
        seen["status"] = read_item(cfg, rid).status
        return ApplyResult(ok=True)
    monkeypatch.setattr(cli, "apply_clarification", fake_apply)

    rc = cli.cmd_review_answer(cfg, "q1", "they are the same")
    assert rc == 0 and seen["id"] == "q1" and seen["status"] == "answered"
    assert "resolved" in capsys.readouterr().out.lower()


def test_cmd_review_answer_unknown_id(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    import wiki_daemon.cli as cli
    rc = cli.cmd_review_answer(cfg, "nope", "x")
    assert rc == 1 and "no such" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL (`cannot import name '_render_review'`).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/cli.py`:

Add imports:
```python
from wiki_daemon.ops import apply_clarification
from wiki_daemon.review import list_items, write_answer
```

Add the `review` subparser in `build_parser()` (after the `serve` block, before `return p`):
```python
    rev = sub.add_parser("review", parents=[common],
                         help="list/answer ingest clarifications")
    rev_sub = rev.add_subparsers(dest="review_cmd")
    ans = rev_sub.add_parser("answer", parents=[common],
                             help="answer a clarification and apply it")
    ans.add_argument("id", help="review item id (filename without .md)")
    ans.add_argument("text", help="your answer")
```

Add the renderer + handlers:
```python
def _render_review(cfg: Config) -> str:
    items = list_items(cfg)
    if not items:
        return "no open clarifications"
    lines = []
    for it in items:
        lines.append(f"{it.id}  [{it.status}]  {it.source}\n    {it.question}")
    return "\n".join(lines)


def cmd_review_list(cfg: Config) -> int:
    print(_render_review(cfg))
    return 0


def cmd_review_answer(cfg: Config, item_id: str, text: str) -> int:
    try:
        write_answer(cfg, item_id, text)
    except FileNotFoundError as exc:
        print(f"review answer failed: {exc}", file=sys.stderr)
        return 1
    result = apply_clarification(cfg, item_id)
    if result.ok:
        print(f"resolved {item_id}")
        return 0
    print(f"apply failed: {result.reason}", file=sys.stderr)
    return 1
```

Add dispatch in `main()` (after the `serve` branch):
```python
    if ns.command == "review":
        if ns.review_cmd == "answer":
            return cmd_review_answer(cfg, ns.id, ns.text)
        return cmd_review_list(cfg)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: wiki review + wiki review answer commands"
```

---

### Task 9: `cli.py` — `review:` line in `wiki status`

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:
```python
def test_render_status_shows_review_count(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "q1.md").write_text(
        "---\ntype: review\nstatus: open\n---\nx\n", encoding="utf-8")
    (cfg.review / "q2.md").write_text(
        "---\ntype: review\nstatus: open\n---\nx\n", encoding="utf-8")
    out = _render_status(cfg)
    assert "review:" in out and "2 open" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_render_status_shows_review_count -v`
Expected: FAIL (no `review:` line).

- [ ] **Step 3: Implement**

In `src/wiki_daemon/cli.py`, inside `_render_status`, locate the `processed` line
construction:
```python
        f"processed:  {processed} sources",
```
Immediately after that string in the `lines` list, add a review line. Replace the
`lines = [...]` block's closing so it includes:
```python
    review_open = len(list(cfg.review.glob("*.md"))) if cfg.review.is_dir() else 0
    lines = [
        f"daemon:     {daemon}",
        f"auth:       {auth}",
        f"queue:      {pending} pending{ingesting}",
        f"processed:  {processed} sources",
        f"review:     {review_open} open",
    ]
```
(Keep the existing `last error` append block below it unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: show open clarification count in wiki status"
```

---

### Task 10: Docs — README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add `wiki review` to the command table**

In `README.md`, add this row right after the `wiki status` row:
```markdown
| `wiki review --vault <path>` | List open ingest clarifications. `wiki review answer <id> "…"` records your answer and applies it. |
```

- [ ] **Step 2: Note the interactive flag on ingest/import rows**

Replace the `wiki ingest` and `wiki import` rows with:
```markdown
| `wiki ingest --vault <path> [--interactive\|--no-interactive] <file>` | Ingest one source now. Interactive (the default in a terminal) asks clarifications live; headless queues them to `wiki/review/`. |
| `wiki import --vault <path> [--interactive\|--no-interactive] <file>` | Copy any UTF-8 text file into `raw/sources/` and ingest it (same interactive/headless behavior as `ingest`). |
```

- [ ] **Step 3: Add a "Clarifications" bullet to "How it works"**

Append to the "How it works (briefly)" list:
```markdown
- **Clarifications** = when a structural decision is ambiguous, an interactive
  ingest asks you live; otherwise (scripts, the daemon) the maintainer files an
  open question under `wiki/review/`. Resolve later with `wiki review` and
  `wiki review answer <id> "…"`, which runs a maintainer pass to apply it.
```

- [ ] **Step 4: Verify the suite still passes**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document ingest clarifications & wiki review"
```

---

## Self-Review Notes

- **Spec coverage:** review layer + Config.review (Task 1); template RAISE/APPLY (Task 2); `wiki/review/<slug>.md` format + review.py (Task 3); interactive vs headless prompts + apply prompt (Task 4); `run_claude_interactive` (Task 5); `ingest_interactive` + `apply_clarification` + best-effort completion (Task 6); `--interactive`/`--no-interactive` + TTY auto-detect on ingest & import (Task 7); `wiki review` + `wiki review answer` in-process apply (Task 8); `review:` status line (Task 9); README (Task 10). All spec sections mapped. Daemon "always queue" needs no code change (per spec) — covered by the headless `ingest_prompt` default in Task 4, which the daemon already uses.
- **Placeholder scan:** none — every code/test step is complete.
- **Type consistency:** `ReviewItem(id,path,status,source,question,tentative,answer)`, `list_items(cfg)`, `read_item(cfg,id)`, `write_answer(cfg,id,answer)`, `run_claude_interactive(prompt,cwd,allowed_tools,…)->int` with `InteractiveRunner=(cmd,cwd)->int`, `ingest_interactive(cfg,path,*,store,runner=None)->IngestResult`, `apply_clarification(cfg,id,*,runner=None)->ApplyResult`, `ApplyResult(ok,reason)`, `cmd_ingest(cfg,file,*,interactive=None)`, `cmd_import(cfg,file,*,interactive=None)`, `_want_interactive(flag)`, `_render_review(cfg)`, `cmd_review_answer(cfg,id,text)` — names/signatures consistent across tasks. Note the two runner shapes are intentionally different: headless `Runner=(cmd,cwd,timeout)->(code,out,err)` vs interactive `(cmd,cwd)->int`.
```
