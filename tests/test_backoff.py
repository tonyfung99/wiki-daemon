# tests/test_backoff.py
from wiki_daemon.backoff import next_backoff


def test_first_failure_is_base():
    assert next_backoff(1) == 30


def test_doubles_each_failure():
    assert next_backoff(2) == 60
    assert next_backoff(3) == 120
    assert next_backoff(4) == 240


def test_capped():
    assert next_backoff(100) == 900


def test_zero_or_negative_is_base():
    assert next_backoff(0) == 30
    assert next_backoff(-5) == 30
