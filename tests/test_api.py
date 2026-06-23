from wiki_daemon.api import extract_citations


def test_extract_simple_link():
    result = extract_citations("See [[Graph View Notes]] for details.")
    assert result == [{"wikiLink": "Graph View Notes", "title": "Graph View Notes"}]


def test_extract_aliased_link():
    result = extract_citations("See [[Graph View Notes|the notes]] for details.")
    assert result == [{"wikiLink": "Graph View Notes", "title": "the notes"}]


def test_extract_deduplicates():
    result = extract_citations("[[A]] and [[B]] and [[A]] again.")
    assert len(result) == 2
    assert result[0]["wikiLink"] == "A"
    assert result[1]["wikiLink"] == "B"


def test_extract_empty_on_no_links():
    assert extract_citations("No links here.") == []


def test_extract_empty_string():
    assert extract_citations("") == []


def test_extract_malformed_links_ignored():
    result = extract_citations("[[]] and [[ ]] and [[|alias]]")
    assert result == []


def test_extract_multiple_links():
    md = "Check [[Alpha]], [[Beta|B]], and [[Gamma]]."
    result = extract_citations(md)
    assert len(result) == 3
    assert result[0] == {"wikiLink": "Alpha", "title": "Alpha"}
    assert result[1] == {"wikiLink": "Beta", "title": "B"}
    assert result[2] == {"wikiLink": "Gamma", "title": "Gamma"}
