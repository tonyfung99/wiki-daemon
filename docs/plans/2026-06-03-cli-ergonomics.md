# CLI Ergonomics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `--vault` optional via a resolution chain (flag > `WIKI_VAULT` env > upward CWD search > `~/.config/wiki/config.toml`), plus `wiki`→help, `wiki --version`, `wiki init` scaffolding CWD, and `init --set-default`.

**Architecture:** A new pure module `vault.py` owns discovery (`is_vault`, `find_vault_upward`, config read/write, `resolve_vault`). `cli.py` wires it: `init` resolves to an explicit path or CWD (it *creates*), every other command *discovers* via the chain; `main()` shows help on no subcommand and supports `--version`.

**Tech Stack:** Python 3.12, stdlib `tomllib`/`argparse`/`os`, `pytest`. Run tests with `.venv/bin/pytest`.

**Reference spec:** `docs/specs/2026-06-03-cli-ergonomics-design.md`

---

## File Structure

- **Create** `src/wiki_daemon/vault.py` — `VaultNotFound`, `is_vault`, `find_vault_upward`, `read_config_vault`, `write_config_vault`, `resolve_vault`.
- **Modify** `src/wiki_daemon/cli.py` — `--version`, optional subcommand + help, mode-aware `_config`, `init --set-default`, `cmd_init(set_default=)`.
- **Modify** `README.md` — install guide + vault discovery.
- **Tests:** new `tests/test_vault.py`; extend `tests/test_cli.py`.

End every commit body with:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 1: `vault.py` — discovery + resolution

**Files:**
- Create: `src/wiki_daemon/vault.py`
- Test: `tests/test_vault.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vault.py
import pytest

from pathlib import Path
from wiki_daemon.vault import (
    VaultNotFound, is_vault, find_vault_upward, read_config_vault,
    write_config_vault, resolve_vault,
)


def _make_vault(root: Path) -> Path:
    (root / "raw").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    return root


def test_is_vault_true_for_scaffold(tmp_path):
    v = _make_vault(tmp_path / "v")
    assert is_vault(v) is True


def test_is_vault_false_for_plain_dir(tmp_path):
    assert is_vault(tmp_path) is False


def test_find_vault_upward_from_nested_subdir(tmp_path):
    v = _make_vault(tmp_path / "v")
    nested = v / "wiki" / "concepts"
    nested.mkdir(parents=True, exist_ok=True)
    assert find_vault_upward(nested) == v.resolve()


def test_find_vault_upward_none_when_absent(tmp_path):
    assert find_vault_upward(tmp_path) is None


def test_config_roundtrip(tmp_path):
    cfg = tmp_path / "c" / "config.toml"
    write_config_vault(cfg, tmp_path / "myvault")
    assert read_config_vault(cfg) == (tmp_path / "myvault").resolve()


def test_read_config_missing_or_malformed_returns_none(tmp_path):
    assert read_config_vault(tmp_path / "nope.toml") is None
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml", encoding="utf-8")
    assert read_config_vault(bad) is None


def test_resolve_precedence_explicit_wins(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault("/explicit", env="/env", start_dir=v,
                        config_path=tmp_path / "c.toml")
    assert got == Path("/explicit")


def test_resolve_env_beats_discovery(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault(None, env="/env", start_dir=v, config_path=tmp_path / "c.toml")
    assert got == Path("/env")


def test_resolve_upward_search(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault(None, env=None, start_dir=v / "wiki",
                        config_path=tmp_path / "c.toml")
    assert got == v.resolve()


def test_resolve_config_fallback(tmp_path):
    cfg = tmp_path / "c.toml"
    write_config_vault(cfg, tmp_path / "cfgvault")
    got = resolve_vault(None, env=None, start_dir=tmp_path / "empty",
                        config_path=cfg)
    assert got == (tmp_path / "cfgvault").resolve()


def test_resolve_raises_when_nothing(tmp_path):
    with pytest.raises(VaultNotFound):
        resolve_vault(None, env=None, start_dir=tmp_path / "empty",
                      config_path=tmp_path / "nope.toml")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_vault.py -v`
Expected: FAIL (`ModuleNotFoundError: wiki_daemon.vault`).

- [ ] **Step 3: Implement**

```python
# src/wiki_daemon/vault.py
"""Discover which vault a `wiki` command should act on.

Resolution order (first hit wins): explicit --vault, WIKI_VAULT env, an upward
search from the current directory for a scaffold, then a configured default.
Pure and injectable — the CLI passes real env/cwd/config-path values in."""
from __future__ import annotations

import tomllib
from pathlib import Path


class VaultNotFound(Exception):
    """No vault could be resolved from any layer."""


def is_vault(d: Path) -> bool:
    """A directory is a vault if it has the scaffold `wiki init` creates."""
    d = Path(d)
    return (d / "CLAUDE.md").exists() and (d / "raw").is_dir() and (d / "wiki").is_dir()


def find_vault_upward(start: Path) -> Path | None:
    """Walk from `start` up to the filesystem root; first vault wins."""
    d = Path(start).resolve()
    for cand in [d, *d.parents]:
        if is_vault(cand):
            return cand
    return None


def read_config_vault(config_path: Path) -> Path | None:
    """`default_vault` from the TOML config, or None (missing/malformed → None)."""
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    v = data.get("default_vault")
    return Path(v) if v else None


def write_config_vault(config_path: Path, vault: Path) -> None:
    """Record the absolute vault path as the config default (the only v1 key)."""
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'default_vault = "{Path(vault).resolve()}"\n', encoding="utf-8")


def resolve_vault(explicit, *, env=None, start_dir=None, config_path=None,
                  finder=find_vault_upward) -> Path:
    if explicit:
        return Path(explicit)
    if env:
        return Path(env)
    if start_dir is not None:
        found = finder(Path(start_dir))
        if found is not None:
            return found
    if config_path is not None:
        cv = read_config_vault(config_path)
        if cv is not None:
            return cv
    raise VaultNotFound(
        "no vault found — run inside a vault, set WIKI_VAULT, or pass --vault <path>")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_vault.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/vault.py tests/test_vault.py
git commit -m "feat: vault discovery + resolution chain"
```

---

### Task 2: `cli` — `--version` + bare `wiki` shows help

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
import pytest
from wiki_daemon.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_bare_wiki_prints_help(capsys):
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "usage" in out and "ingest" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "version or bare" -v`
Expected: FAIL (bare `wiki` currently raises SystemExit; no `--version`).

- [ ] **Step 3: Implement in `src/wiki_daemon/cli.py`**

Add the version import near the top imports:
```python
from wiki_daemon import __version__
```

In `build_parser()`, add `--version` to the top parser and make the subcommand
optional. Replace:
```python
    p = argparse.ArgumentParser(prog="wiki")
    sub = p.add_subparsers(dest="command", required=True)
```
with:
```python
    p = argparse.ArgumentParser(prog="wiki")
    p.add_argument("--version", action="version", version=f"wiki {__version__}")
    sub = p.add_subparsers(dest="command")
```

In `main()`, keep the parser reference and handle the no-command case. Replace the
first two lines of `main`:
```python
def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    cfg = _config(ns)
```
with:
```python
def main(argv=None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    cfg = _config(ns)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS. (If a pre-existing test asserted that bare `main([])` raises
SystemExit, update it to expect the help/return-0 behavior — there should be none,
but check.)

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: wiki --version and bare-wiki help"
```

---

### Task 3: `cli` — mode-aware vault resolution

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
import os


def test_command_resolves_vault_from_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))  # keep daemon state off real home
    cfg0 = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg0)
    # no --vault; CWD inside the vault → resolve + run status
    monkeypatch.setattr("pathlib.Path.cwd", lambda: cfg0.vault / "wiki")
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "noconfig.toml")
    rc = main(["status"])
    assert rc == 0
    assert "processed:" in capsys.readouterr().out


def test_command_no_vault_anywhere_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path / "empty")
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "noconfig.toml")
    with pytest.raises(SystemExit) as e:
        main(["status"])
    assert e.value.code == 2
    assert "no vault found" in capsys.readouterr().err.lower()


def test_explicit_vault_still_works(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg0 = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg0)
    rc = main(["status", "--vault", str(cfg0.vault)])
    assert rc == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "resolves_vault or no_vault_anywhere or explicit_vault" -v`
Expected: FAIL (`_config_path` doesn't exist; `_config` still requires `--vault`).

- [ ] **Step 3: Implement in `src/wiki_daemon/cli.py`**

Add `import os` near the top imports (if not already present).

Replace the whole `_config` function:
```python
def _config(ns) -> Config:
    if not ns.vault:
        print("error: --vault is required", file=sys.stderr)
        raise SystemExit(2)
    return Config(vault=Path(ns.vault))
```
with:
```python
def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "wiki" / "config.toml"


def _config(ns) -> Config:
    # `init` CREATES a vault — target an explicit path or the current directory.
    if ns.command == "init":
        return Config(vault=Path(ns.vault) if ns.vault else Path.cwd())
    # every other command DISCOVERS an existing vault via the resolution chain.
    from wiki_daemon.vault import VaultNotFound, resolve_vault
    try:
        vault = resolve_vault(ns.vault, env=os.environ.get("WIKI_VAULT"),
                              start_dir=Path.cwd(), config_path=_config_path())
    except VaultNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return Config(vault=vault)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v` then full suite `.venv/bin/pytest -q`
Expected: PASS (existing tests pass `--vault` explicitly → unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: discover vault from CWD/env/config when --vault omitted"
```

---

### Task 4: `cli` — `wiki init` in CWD + `--set-default`

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli.py
def test_init_scaffolds_cwd_without_vault(tmp_path, monkeypatch, capsys):
    target = tmp_path / "newvault"
    target.mkdir()
    monkeypatch.setattr("pathlib.Path.cwd", lambda: target)
    rc = main(["init"])
    assert rc == 0
    assert (target / "CLAUDE.md").exists() and (target / "wiki").is_dir()


def test_init_set_default_writes_config(tmp_path, monkeypatch, capsys):
    target = tmp_path / "v"
    cfg_path = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr("wiki_daemon.cli._config_path", lambda: cfg_path)
    rc = main(["init", "--vault", str(target), "--set-default"])
    assert rc == 0
    from wiki_daemon.vault import read_config_vault
    assert read_config_vault(cfg_path) == target.resolve()
    out = capsys.readouterr().out.lower()
    assert "default vault" in out


def test_init_parser_has_set_default():
    parser = build_parser()
    ns = parser.parse_args(["init", "--vault", "/v", "--set-default"])
    assert ns.set_default is True
    ns2 = parser.parse_args(["init", "--vault", "/v"])
    assert ns2.set_default is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -k "init_scaffolds_cwd or set_default" -v`
Expected: FAIL (no `--set-default`; `cmd_init` has no `set_default`).

- [ ] **Step 3: Implement in `src/wiki_daemon/cli.py`**

In `build_parser()`, replace the init parser line:
```python
    sub.add_parser("init", parents=[common], help="scaffold a new vault")
```
with:
```python
    ini = sub.add_parser("init", parents=[common],
                         help="scaffold a new vault (defaults to the current dir)")
    ini.add_argument("--set-default", action="store_true",
                     help="record this vault as the default in ~/.config/wiki/config.toml")
```

Replace `cmd_init`:
```python
def cmd_init(cfg: Config) -> int:
    init_vault(cfg)
    print(f"initialized vault at {cfg.vault}")
    return 0
```
with:
```python
def cmd_init(cfg: Config, *, set_default: bool = False) -> int:
    init_vault(cfg)
    print(f"initialized vault at {cfg.vault}")
    if set_default:
        from wiki_daemon.vault import write_config_vault
        write_config_vault(_config_path(), cfg.vault)
        print(f"set default vault → {cfg.vault}")
    return 0
```

Update the `init` dispatch in `main()`:
```python
    if ns.command == "init":
        return cmd_init(cfg)
```
to:
```python
    if ns.command == "init":
        return cmd_init(cfg, set_default=ns.set_default)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v` then full suite `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: wiki init defaults to CWD; --set-default records config"
```

---

### Task 5: Docs — README install guide + vault discovery

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the Install section**

In `README.md`, find the `## Install` section and its fenced block (the
`git clone …` / venv / `pip install -e ".[dev]"` lines and the "This installs the
**`wiki`** console script…" sentence). Replace that whole section body with:
```markdown
## Install

**Editable (development):**

```bash
git clone https://github.com/tonyfung99/wiki-daemon.git
cd wiki-daemon
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
# run it as: .venv/bin/wiki …
```

**pipx (recommended for daily use)** — puts `wiki` on your PATH everywhere:

```bash
pipx install ~/workspace/wiki-daemon     # path to the cloned repo
wiki --version
```

Either way you also need the **[`claude` CLI](https://docs.claude.com/en/docs/claude-code)**
installed and authenticated — `wiki` shells out to headless `claude -p` for
ingest/query/lint (for an unattended daemon use `claude setup-token`).
```

- [ ] **Step 2: Add a "Vault discovery" note before the Commands table**

In `README.md`, immediately before the `## Commands` heading, add:
```markdown
## Choosing the vault

`--vault <path>` works on every command, but is optional — `wiki` finds the
vault by, in order: the `--vault` flag, the `WIKI_VAULT` environment variable, an
upward search from the current directory (so just `cd` into a vault), then a
`default_vault` in `~/.config/wiki/config.toml` (set it with
`wiki init --set-default`).

```bash
cd "$VAULT" && wiki status        # discovered from the current directory
export WIKI_VAULT="$VAULT"        # or set it once in your shell profile
```
```

- [ ] **Step 3: Note `--vault` is optional in the command table intro**

Find the line introducing the command table (just after `## Commands`). If there
is a sentence there, append "`--vault` is optional when the vault is discoverable
(see above)."; if the table starts immediately, add this line right under the
`## Commands` heading:
```markdown
`--vault` is optional when the vault is discoverable (see *Choosing the vault*).
```

- [ ] **Step 4: Verify the suite still passes**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: install guide (pipx) + vault discovery"
```

---

## Self-Review Notes

- **Spec coverage:** resolution chain flag>env>upward>config (Task 1 `resolve_vault`); `is_vault` scaffold marker (Task 1); config read/write incl. malformed-TOML tolerance (Task 1); two modes — init=CWD/explicit, others=discover (Task 3 `_config`); `VaultNotFound`→exit 2 with guidance (Task 3); bare `wiki`→help + `--version` (Task 2); `wiki init` CWD + `--set-default` writer (Task 4); XDG_CONFIG_HOME honored (Task 3 `_config_path`); README install + discovery (Task 5); `--vault` stays optional layer-1 and existing tests unchanged (Tasks 2–4 keep explicit `--vault` working). All spec sections mapped. Out-of-scope (`--json`, completions, full `wiki config`) absent.
- **Placeholder scan:** none — every code/test step is complete.
- **Type consistency:** `is_vault(d)->bool`, `find_vault_upward(start)->Path|None`, `read_config_vault(path)->Path|None`, `write_config_vault(path, vault)->None`, `resolve_vault(explicit, *, env, start_dir, config_path, finder)->Path`, `VaultNotFound`, `_config_path()->Path`, `_config(ns)->Config`, `cmd_init(cfg, *, set_default=False)->int` — consistent across tasks. `_config` dispatches on `ns.command == "init"`; tests monkeypatch `wiki_daemon.cli._config_path` and `pathlib.Path.cwd`.
```
