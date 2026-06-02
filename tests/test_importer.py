from pathlib import Path

import pytest

from wiki_daemon.importer import _slugify, _dest_name


def test_slugify_basic():
    assert _slugify("My Cool Note!") == "my-cool-note"


def test_slugify_collapses_and_trims():
    assert _slugify("  --Foo__Bar.. ") == "foo-bar"


def test_slugify_empty_falls_back():
    assert _slugify("---") == "source"


def test_dest_name_adds_date_prefix():
    assert _dest_name("notes", "2026-06-02") == "2026-06-02-notes.md"


def test_dest_name_skips_double_date_prefix():
    # stem already starts with a YYYY-MM-DD- prefix -> don't prepend again
    assert _dest_name("2026-05-31-acme", "2026-06-02") == "2026-05-31-acme.md"
