# tests/conftest.py
import pytest


@pytest.fixture
def tmp_vault(tmp_path):
    """A minimal vault directory tree for tests."""
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    wiki = tmp_path / "wiki"
    for sub in ("entities", "concepts", "sources", "queries"):
        (wiki / sub).mkdir(parents=True)
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "log.md").write_text("# Log\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# schema\n", encoding="utf-8")
    return tmp_path
