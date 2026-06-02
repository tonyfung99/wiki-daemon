# src/wiki_daemon/logging_setup.py
"""Configure the `wiki_daemon` logger: stdout + a rotating daemon.log."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from wiki_daemon.config import Config

_FMT = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")


def configure_logging(cfg: Config, *, level: int = logging.INFO) -> logging.Logger:
    """Idempotent: wires a stdout handler and a rotating file handler
    (1 MB x 3) at state_dir/daemon.log. Returns the `wiki_daemon` logger."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wiki_daemon")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    sh = logging.StreamHandler()
    sh.setFormatter(_FMT)
    fh = RotatingFileHandler(
        cfg.state_dir / "daemon.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(_FMT)
    logger.addHandler(sh)
    logger.addHandler(fh)
    logger.propagate = False
    return logger
