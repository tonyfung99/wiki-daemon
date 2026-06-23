import time
from wiki_daemon.api import extract_citations, JobStore, QueryJob
from wiki_daemon.ops import QueryResult


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


def test_jobstore_create_and_get():
    store = JobStore()
    job_id = store.create("What is X?", save=True)
    assert job_id.startswith("qry_")
    job = store.get(job_id)
    assert isinstance(job, QueryJob)
    assert job.question == "What is X?"
    assert job.save is True
    assert job.status == "running"


def test_jobstore_get_unknown_returns_none():
    store = JobStore()
    assert store.get("qry_nonexistent") is None


def test_jobstore_complete_marks_done():
    store = JobStore()
    job_id = store.create("Q?", save=False)
    store.complete(job_id, QueryResult(ok=True, answer="A"))
    job = store.get(job_id)
    assert job.status == "done"
    assert job.result.answer == "A"


def test_jobstore_fail_marks_failed():
    store = JobStore()
    job_id = store.create("Q?", save=False)
    store.complete(job_id, QueryResult(ok=False, kind="auth", reason="401"))
    job = store.get(job_id)
    assert job.status == "failed"


def test_jobstore_expiry():
    store = JobStore(expiry_seconds=0.01)
    job_id = store.create("Q?", save=False)
    time.sleep(0.02)
    assert store.get(job_id) is None


def test_jobstore_evicts_oldest_when_full():
    store = JobStore(max_jobs=2)
    id1 = store.create("Q1", save=False)
    id2 = store.create("Q2", save=False)
    id3 = store.create("Q3", save=False)
    assert store.get(id1) is None  # evicted
    assert store.get(id2) is not None
    assert store.get(id3) is not None
