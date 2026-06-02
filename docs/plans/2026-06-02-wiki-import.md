# `wiki import` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `wiki import --vault <path> <file>` CLI command that copies an arbitrary text file into the vault's `raw/sources/` as a well-formed Markdown source, then runs the existing ingest pipeline on it.

**Architecture:** A new pure module `importer.py` does the file-landing (`import_source(cfg, src_path) -> Path`): validate UTF-8, compute a normalized `YYYY-MM-DD-<slug>.md` name with collision suffixes, synthesize minimal frontmatter when absent, write, and return the destination path. The existing `ops.ingest` is reused unchanged. `cli.cmd_import` wires them together.

**Tech Stack:** Python 3.12, `argparse`, `pyyaml` (via existing `frontmatter` module), `pytest`.

**Reference spec:** `docs/specs/2026-06-02-wiki-import-design.md`

---

## File Structure

- **Create** `src/wiki_daemon/importer.py` — `import_source()` and its private helpers (`_slugify`, `_dest_name`). One responsibility: land a valid source file in the vault.
- **Create** `tests/test_importer.py` — pure unit tests for `import_source` (no LLM).
- **Modify** `src/wiki_daemon/cli.py` — add the `import` subparser, `cmd_import`, and dispatch.
- **Modify** `tests/test_cli.py` — add a `cmd_import` end-to-end test with a fake claude runner.
- **Modify** `README.md` — add `wiki import` to the Quickstart and the command table.

---

### Task 1: Filename normalization (`_slugify` + `_dest_name`)

**Files:**
- Create: `src/wiki_daemon/importer.py`
- Test: `tests/test_importer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_importer.py
from pathlib import Path

import pytest

from wiki_daemon.importer import _slugify, _dest_name


def test_slugify_basic():
    assert _slugify("My Cool Note!") == "my-cool-note"


def test_slugify_collapses_and_trims():
    assert _slugify("  --Foo__Bar.. ") == "foo-bar"


def test_slugify_empty_falls_back():
    assert _slugify("---") == "source"


def test_dest_name_adds_date_prefix():
    assert _dest_name("notes", "2026-06-02") == "2026-06-02-notes.md"


def test_dest_name_skips_double_date_prefix():
    # stem already starts with a YYYY-MM-DD- prefix -> don't prepend again
    assert _dest_name("2026-05-31-acme", "2026-06-02") == "2026-05-31-acme.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_importer.py -v`
Expected: FAIL with `ImportError` / `cannot import name '_slugify'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/wiki_daemon/importer.py
"""Land an arbitrary text file into the vault's raw/sources/ as a Markdown
source, then let ops.ingest process it. Pure file-handling: no LLM, no state."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import dump

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _slugify(stem: str) -> str:
    """Lowercase, non-alphanumeric runs -> single '-', trimmed. Empty -> 'source'."""
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug or "source"


def _dest_name(stem: str, today: str) -> str:
    """`YYYY-MM-DD-<slug>.md`, skipping the date prefix when the stem already
    starts with one (avoids `2026-06-02-2026-06-01-foo`)."""
    if _DATE_PREFIX.match(stem):
        return f"{stem}.md"
    return f"{today}-{_slugify(stem)}.md"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_importer.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/importer.py tests/test_importer.py
git commit -m "feat: filename normalization for wiki import"
```

---

### Task 2: `import_source` — validation, collision, frontmatter, write

**Files:**
- Modify: `src/wiki_daemon/importer.py`
- Test: `tests/test_importer.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_importer.py
from wiki_daemon.config import Config
from wiki_daemon.frontmatter import parse
from wiki_daemon.importer import import_source


def _cfg(tmp_path):
    return Config(vault=tmp_path / "v", state_root=tmp_path / "s")


def test_import_copies_and_leaves_original(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "Hello World.md"
    src.write_text("---\ntype: source\ntitle: Hi\n---\nbody\n", encoding="utf-8")

    dest = import_source(cfg, src)

    assert dest.parent == cfg.raw_sources
    assert dest.name.endswith("-hello-world.md")
    assert dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert src.exists()  # original untouched (copy, never move)


def test_import_collision_appends_suffix(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "2026-05-31-acme.md"
    src.write_text("---\ntype: source\n---\nbody\n", encoding="utf-8")
    (cfg.raw_sources / "2026-05-31-acme.md").write_text("existing", encoding="utf-8")

    dest = import_source(cfg, src)

    assert dest.name == "2026-05-31-acme-2.md"


def test_import_synthesizes_frontmatter_when_absent(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "raw-clip.md"
    src.write_text("just some text\n", encoding="utf-8")

    dest = import_source(cfg, src)

    meta, body = parse(dest.read_text(encoding="utf-8"))
    assert meta["type"] == "source"
    assert meta["title"] == "Raw Clip"
    assert "captured_at" in meta
    assert body == "just some text\n"


def test_import_keeps_existing_frontmatter_verbatim(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "clip.md"
    original = "---\ntype: source\ntitle: Keep\n---\nverbatim\n"
    src.write_text(original, encoding="utf-8")

    dest = import_source(cfg, src)

    assert dest.read_text(encoding="utf-8") == original


def test_import_missing_path_raises(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        import_source(cfg, tmp_path / "nope.md")


def test_import_non_utf8_raises(tmp_path):
    cfg = _cfg(tmp_path)
    src = tmp_path / "binary.md"
    src.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ValueError):
        import_source(cfg, src)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_importer.py -v`
Expected: FAIL with `cannot import name 'import_source'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/wiki_daemon/importer.py`:

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _has_frontmatter(text: str) -> bool:
    # Match frontmatter.parse's own rule for what counts as frontmatter.
    return text.startswith("---\n")


def import_source(cfg: Config, src_path: Path) -> Path:
    """Copy `src_path` into the vault's raw/sources/ as a Markdown source and
    return the destination path. Always copies (never moves). Synthesizes minimal
    frontmatter when the file has none. Raises FileNotFoundError for a missing/
    non-file path and ValueError for non-UTF-8 input."""
    src_path = Path(src_path)
    if not src_path.is_file():
        raise FileNotFoundError(f"not a file: {src_path}")
    try:
        text = src_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not a UTF-8 text file: {src_path}") from exc

    stem = src_path.stem
    today = _now_iso()[:10]  # YYYY-MM-DD
    name = _dest_name(stem, today)

    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    dest = cfg.raw_sources / name
    counter = 2
    while dest.exists():
        dest = cfg.raw_sources / f"{Path(name).stem}-{counter}.md"
        counter += 1

    if _has_frontmatter(text):
        out = text
    else:
        title = _slugify(stem).replace("-", " ").title()
        out = dump({"type": "source", "captured_at": _now_iso(), "title": title}, text)

    dest.write_text(out, encoding="utf-8")
    return dest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_importer.py -v`
Expected: PASS (11 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/importer.py tests/test_importer.py
git commit -m "feat: import_source lands a text file into raw/sources"
```

---

### Task 3: CLI wiring (`wiki import`)

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
from wiki_daemon.cli import cmd_import
from wiki_daemon.scaffold import init_vault


def test_parser_has_import_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["import", "--vault", "/tmp/v", "/tmp/clip.md"])
    assert ns.command == "import"
    assert ns.file == "/tmp/clip.md"


def _good_claude(cfg):
    """Fake runner: writes a compliant source summary for whatever lands in
    raw/sources/ (matches the pattern in tests/test_ops.py)."""
    def runner(cmd, cwd, timeout):
        src = next(cfg.raw_sources.glob("*.md"))
        rel = src.relative_to(cfg.vault).as_posix()
        (cfg.wiki / "sources").mkdir(parents=True, exist_ok=True)
        (cfg.wiki / "sources" / "clip.md").write_text(
            f"---\ntype: source\nsources: [{rel}]\n---\nsummary\n", encoding="utf-8")
        (cfg.wiki / "index.md").write_text("# Index\n", encoding="utf-8")
        (cfg.wiki / "log.md").write_text("# Log\n", encoding="utf-8")
        return 0, "ok\n", ""
    return runner


def test_cmd_import_lands_and_ingests(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    external = tmp_path / "outside.md"
    external.write_text("some clipped text\n", encoding="utf-8")

    # Route ops.run_claude through the fake runner without changing signatures.
    import wiki_daemon.ops as ops
    real_ingest = ops.ingest
    monkeypatch.setattr(ops, "ingest",
                        lambda cfg, path, *, store: real_ingest(
                            cfg, path, store=store, runner=_good_claude(cfg)))

    rc = cmd_import(cfg, str(external))

    assert rc == 0
    assert external.exists()  # original left in place
    assert list(cfg.raw_sources.glob("*-outside.md"))  # landed copy
    out = capsys.readouterr().out.lower()
    assert "imported" in out and "ingested" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `cannot import name 'cmd_import'`.

- [ ] **Step 3: Write minimal implementation**

In `src/wiki_daemon/cli.py`, add the import near the top:

```python
from wiki_daemon.importer import import_source
```

Add the subparser inside `build_parser()` (after the `ingest` parser block):

```python
    imp = sub.add_parser("import", parents=[common],
                         help="copy a file into the vault and ingest it")
    imp.add_argument("file", help="path to any UTF-8 text file to import")
```

Add `cmd_import` (after `cmd_ingest`). It calls `ops.ingest` via the module so
tests can monkeypatch it:

```python
def cmd_import(cfg: Config, file: str) -> int:
    from pathlib import Path

    import wiki_daemon.ops as ops

    try:
        dest = import_source(cfg, Path(file))
    except (FileNotFoundError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported {dest.name}")

    store = StateStore(cfg.processed_json)
    result = ops.ingest(cfg, dest, store=store)
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1
```

Add dispatch in `main()` (after the `ingest` branch):

```python
    if ns.command == "import":
        return cmd_import(cfg, ns.file)
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `.venv/bin/pytest -q`
Expected: PASS (all tests, including the new import tests).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: wiki import CLI command"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add to the Quickstart**

In `README.md`, after the step `# 4. Ingest a single clip by hand ...` block,
add:

```markdown
# 4b. Or import a file from anywhere — copies it into raw/sources/ then ingests.
wiki import --vault "$VAULT" ~/Downloads/some-note.md
```

- [ ] **Step 2: Add to the command table**

Add this row to the Commands table, right under the `wiki ingest` row:

```markdown
| `wiki import --vault <path> <file>` | Copy any UTF-8 text file into `raw/sources/` (adds frontmatter if missing) and ingest it. The original is left in place. |
```

- [ ] **Step 3: Verify the suite still passes**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document wiki import command"
```

---

## Self-Review Notes

- **Spec coverage:** copy-never-move (Task 2 `test_import_copies_and_leaves_original`, Task 3 original-left-in-place); command name `wiki import` (Task 3); any UTF-8 text → `.md` (Task 2 validation + `_dest_name` forces `.md`); collision suffix (Task 2 `test_import_collision_appends_suffix`); frontmatter synth vs verbatim (Task 2 two tests); `captured_at` = now UTC (Task 2 `_now_iso`); README rows (Task 4). All spec sections mapped.
- **Placeholder scan:** none — every code/test step is complete.
- **Type consistency:** `import_source(cfg, src_path) -> Path`, `_slugify(stem) -> str`, `_dest_name(stem, today) -> str`, `cmd_import(cfg, file) -> int` used consistently across tasks. `ops.ingest` called as `ingest(cfg, path, store=...)` matching its real signature.
```
