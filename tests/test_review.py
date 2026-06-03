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
    again = read_item(cfg, "calvin-vs-dark")
    assert again.status == "answered"
    assert again.answer == "They are the same; keep one page."
    assert "context body" in (cfg.review / "calvin-vs-dark.md").read_text(encoding="utf-8")


def test_write_answer_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_answer(_cfg(tmp_path), "nope", "x")


def test_empty_answer_string_distinct_from_none(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(cfg, answer="")           # answer key present but empty
    assert read_item(cfg, "calvin-vs-dark").answer == ""
    _seed(cfg, item_id="open-one")  # no answer key
    assert read_item(cfg, "open-one").answer is None
