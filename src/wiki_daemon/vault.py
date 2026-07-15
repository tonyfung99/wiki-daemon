# src/wiki_daemon/vault.py
"""Discover which vault a `wiki` command should act on.

Resolution order (first hit wins): explicit --vault, WIKI_VAULT env, an upward
search from the current directory for a scaffold, then a configured default.
Pure and injectable — the CLI passes real env/cwd/config-path values in."""
from __future__ import annotations

import re
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


def read_config_token(config_path: Path) -> str | None:
    """Read `api_token` from the TOML config, or None."""
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    v = data.get("api_token")
    return v if isinstance(v, str) and v.strip() else None


def read_config_api(config_path: Path) -> dict:
    """Read api_port / api_bind / query_timeout from config. Returns a dict of
    present keys only."""
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}
    result = {}
    if isinstance(data.get("api_port"), int):
        result["api_port"] = data["api_port"]
    if isinstance(data.get("api_bind"), str):
        result["api_bind"] = data["api_bind"]
    qt = data.get("query_timeout")
    # Reject non-int, bool (isinstance(True, int) is True), and non-positive
    # values: a 0/negative timeout would make every agent run TimeoutExpired
    # almost immediately, silently failing all queries. Fall back to default.
    if isinstance(qt, int) and not isinstance(qt, bool) and qt > 0:
        result["query_timeout"] = qt
    return result


def write_config_token(config_path: Path, token: str) -> None:
    """Write or replace `api_token` in the TOML config, preserving other keys."""
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    line = f'api_token = "{escaped}"\n'
    if p.exists():
        text = p.read_text(encoding="utf-8")
        if re.search(r'^api_token\s*=', text, re.MULTILINE):
            text = re.sub(r'^api_token\s*=.*$', line.rstrip(), text,
                          flags=re.MULTILINE)
        else:
            text = text.rstrip("\n") + "\n" + line
        p.write_text(text, encoding="utf-8")
    else:
        p.write_text(line, encoding="utf-8")
