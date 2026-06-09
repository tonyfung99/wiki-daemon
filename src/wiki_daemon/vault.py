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
    return Path(v) if isinstance(v, str) and v.strip() else None


def read_config_provider(config_path: Path) -> str | None:
    """`provider` from the TOML config, or None (missing/malformed → None)."""
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    v = data.get("provider")
    return v if isinstance(v, str) and v.strip() else None


def write_config_vault(config_path: Path, vault: Path) -> None:
    """Record the absolute vault path as the config default (the only v1 key)."""
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(Path(vault).resolve()).replace("\\", "\\\\").replace('"', '\\"')
    p.write_text(f'default_vault = "{escaped}"\n', encoding="utf-8")


def resolve_vault(explicit, *, env=None, start_dir=None, config_path=None,
                  finder=find_vault_upward) -> Path:
    if explicit and str(explicit).strip():
        return Path(explicit)
    if env and env.strip():
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
