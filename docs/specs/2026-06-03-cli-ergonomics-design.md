# Design: CLI ergonomics — vault discovery, help, version, init-in-CWD

**Date:** 2026-06-03
**Status:** Approved (pending spec review)

## Problem

Every `wiki` command hard-requires `--vault <path>` (`_config` exits 2 without
it), so daily use is verbose and the tool feels like a dev script rather than an
installed CLI. We want a `git`-like experience: install once, `cd` into a vault,
run `wiki status` with no flags.

Scope (one focused pass): the **vault-resolution chain** + small ergonomics
(`wiki` → help, `--version`, `wiki init` scaffolds CWD, `init --set-default`),
plus a README install guide. Deferred: `--json`, `--quiet/--verbose`, shell
completions, a full `wiki config` subcommand.

## Decisions (from brainstorming)

- **Resolution chain:** `--vault` flag > `WIKI_VAULT` env > upward search from CWD
  > `~/.config/wiki/config.toml` default > error.
- **Vault marker:** the existing scaffold — `CLAUDE.md` + `raw/` + `wiki/` — no
  new marker files.
- **`init --set-default`** included, so the config layer is populatable without
  hand-editing TOML.
- `--vault` stays on every command (now optional — it's layer 1).

## Architecture

### `vault.py` (new module) — pure, injectable resolution

```python
class VaultNotFound(Exception): ...

def is_vault(d: Path) -> bool:
    return (d / "CLAUDE.md").exists() and (d / "raw").is_dir() and (d / "wiki").is_dir()

def find_vault_upward(start: Path) -> Path | None:
    # start.resolve(), then walk parents to root; first is_vault() hit wins.

def read_config_vault(config_path: Path) -> Path | None:
    # tomllib.load; return Path(default_vault) if present; None on missing
    # file / missing key / malformed TOML (never raises).

def write_config_vault(config_path: Path, vault: Path) -> None:
    # mkdir -p parent; write `default_vault = "<abs path>"\n` (the only key v1 uses).

def resolve_vault(explicit, *, env, start_dir, config_path,
                  finder=find_vault_upward) -> Path:
    # 1 explicit → Path(explicit)
    # 2 env (WIKI_VAULT, non-empty) → Path(env)
    # 3 finder(start_dir) → that Path
    # 4 read_config_vault(config_path) → that Path
    # else raise VaultNotFound(<guidance message>)
```

All of `env`, `start_dir`, `config_path` are injected (no direct `os.environ` /
`Path.home()` reads inside the pure function) → unit-testable in isolation.

Guidance message: `"no vault found — run inside a vault, set WIKI_VAULT, or pass
--vault <path>"`.

### Two resolution modes (`cli.py`)

- **`init` creates a vault** → it must NOT discover one. Target = `--vault` if
  given, else **CWD**:
  ```python
  def _resolve_for_init(explicit) -> Path:
      return Path(explicit) if explicit else Path.cwd()
  ```
- **All other commands find an existing vault** via the chain:
  ```python
  def _resolve_vault_arg(ns) -> Config:
      cfg_path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) \
                 / "wiki" / "config.toml"
      try:
          vault = resolve_vault(ns.vault, env=os.environ.get("WIKI_VAULT"),
                                start_dir=Path.cwd(), config_path=cfg_path)
      except VaultNotFound as exc:
          print(f"error: {exc}", file=sys.stderr)
          raise SystemExit(2)
      return Config(vault=vault)
  ```
  `_config(ns)` dispatches: `init` → `Config(vault=_resolve_for_init(ns.vault))`;
  everything else → `_resolve_vault_arg(ns)`.

  (`XDG_CONFIG_HOME` honored if set, else `~/.config` — standard.)

### Small ergonomics (`cli.py`, same pass)

- **Bare `wiki` → help, exit 0.** Make `add_subparsers(dest="command")` **not
  required**; in `main()`, if `ns.command is None`: `parser.print_help()`;
  `return 0`. (`build_parser` returns the parser; `main` keeps a reference.)
- **`wiki --version`** → on the top-level parser:
  `p.add_argument("--version", action="version", version=f"wiki {__version__}")`
  importing `from wiki_daemon import __version__`.
- **`wiki init`** → with no `--vault`, scaffolds CWD (via `_resolve_for_init`).
- **`--set-default` flag on `init`** → after `init_vault`, call
  `write_config_vault(cfg_path, cfg.vault)` and print
  `set default vault → <path>`. `cmd_init` gains a `set_default: bool` param;
  dispatch passes `ns.set_default`.

### Error handling

- Malformed / unreadable `config.toml` → `read_config_vault` returns `None`
  (caught broadly: `FileNotFoundError`, `tomllib.TOMLDecodeError`, `OSError`),
  resolution falls through; never crashes.
- `--vault` / env pointing at a non-existent dir → passed through as-is (explicit
  is explicit; `Config.__post_init__` resolves the path, downstream commands
  surface a missing vault naturally — unchanged behavior).
- `write_config_vault` stores the **resolved absolute** vault path.

## Data flow

```
wiki status               → resolve_vault(None, env, cwd, config) → Config → cmd_status
wiki status --vault P     → explicit P wins
WIKI_VAULT=P wiki lint    → env layer
cd ~/MyWiki/sub && wiki q → upward-search finds ~/MyWiki
wiki init                 → scaffold CWD
wiki init --vault ./w --set-default → scaffold ./w + write default_vault
wiki / wiki --version     → help / version, no vault needed
```

## Testing

- `vault.py` (pure, injected inputs): explicit wins; env wins over discovery;
  upward search finds a vault from a nested subdir and returns None when none;
  config fallback returns the configured path; full precedence order; malformed
  TOML / missing file → None (no raise); `VaultNotFound` raised when all layers
  miss; `is_vault` true on a scaffold, false otherwise; `write_config_vault` →
  `read_config_vault` round-trip.
- `cli`: bare `wiki` prints help (exit 0); `--version` prints `wiki 0.1.0`;
  `init` with no `--vault` scaffolds CWD (monkeypatch `Path.cwd`); `init
  --set-default` writes the config (inject config path); a non-init command with
  no `--vault` but CWD inside a vault resolves and runs; missing-everywhere →
  exit 2 with guidance. Use monkeypatch for `os.environ`, `Path.cwd`, and the
  config path.
- **Backward compatibility:** every existing test passes `--vault` explicitly →
  layer 1 → unchanged. (The suite must stay green without edits beyond new tests.)

## Docs (README install guide — part of this change)

Replace the thin install section with:
- **Editable (dev):** clone, `python3 -m venv .venv`, `.venv/bin/pip install -e
  ".[dev]"`, run `.venv/bin/wiki …`.
- **pipx (recommended for daily use):** `pipx install <path-to-repo>` → `wiki` on
  PATH everywhere. Note the runtime requirement: an authenticated `claude` CLI.
- **Vault discovery** subsection: `cd` into a vault and run bare commands; or
  `export WIKI_VAULT=…`; or `wiki init --set-default`; `--vault` always overrides.
- Update the command-table intro: `--vault` is **optional** when the vault is
  discoverable.

## Out of scope (later)

- `--json` machine output; `--quiet` / `--verbose`; shell completions.
- A full `wiki config` subcommand (we only read config + the `init --set-default`
  writer; `config.toml` holds just `default_vault` in v1).
- Publishing to PyPI (`pipx install wiki-daemon` from the index).
