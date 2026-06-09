# src/wiki_daemon/config.py
"""Runtime configuration. Daemon state lives OUTSIDE the vault (never iCloud)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


def _default_state_root() -> Path:
    return Path.home() / ".wiki-daemon"


@dataclass
class Config:
    vault: Path
    state_root: Path = field(default_factory=_default_state_root)
    claude_bin: str = "claude"

    def __post_init__(self) -> None:
        self.vault = Path(self.vault).expanduser().resolve()
        self.state_root = Path(self.state_root).expanduser()

    @property
    def vault_id(self) -> str:
        return hashlib.sha256(str(self.vault).encode("utf-8")).hexdigest()[:12]

    @property
    def raw_sources(self) -> Path:
        return self.vault / "raw" / "sources"

    @property
    def raw_originals(self) -> Path:
        """Where original binaries are archived after conversion to Markdown."""
        return self.vault / "raw" / "originals"

    @property
    def wiki(self) -> Path:
        return self.vault / "wiki"

    @property
    def review(self) -> Path:
        return self.vault / "wiki" / "review"

    @property
    def claude_md(self) -> Path:
        return self.vault / "CLAUDE.md"

    @property
    def state_dir(self) -> Path:
        return self.state_root / self.vault_id

    @property
    def processed_json(self) -> Path:
        return self.state_dir / "processed.json"

    @property
    def queue_dir(self) -> Path:
        return self.state_dir / "queue"
