# tests/test_logging_setup.py
import logging

import pytest

from wiki_daemon.config import Config
from wiki_daemon.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _reset_wiki_logger():
    logger = logging.getLogger("wiki_daemon")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    yield
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def test_writes_to_daemon_log(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    logger = configure_logging(cfg)
    logger.info("hello daemon")
    for h in logger.handlers:
        h.flush()
    log_path = cfg.state_dir / "daemon.log"
    assert log_path.exists()
    assert "hello daemon" in log_path.read_text(encoding="utf-8")


def test_configure_is_idempotent(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    logger = configure_logging(cfg)
    n = len(logger.handlers)
    logger2 = configure_logging(cfg)
    assert logger2 is logger
    assert len(logger2.handlers) == n  # not doubled
    assert n == 2  # stdout + rotating file
