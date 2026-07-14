from wiki_daemon import icloud


class FakeStat:
    def __init__(self, st_flags, st_size):
        self.st_flags = st_flags
        self.st_size = st_size
        self.st_mtime = 1.0


def test_is_dataless_reads_sf_dataless_bit(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    dataless = icloud.is_dataless(p, stat_fn=lambda _p: FakeStat(icloud.SF_DATALESS, 10))
    materialized = icloud.is_dataless(p, stat_fn=lambda _p: FakeStat(0, 10))
    assert dataless is True
    assert materialized is False


def test_materialize_uses_brctl_then_clears(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return 0  # success

    flags = iter([icloud.SF_DATALESS, 0])  # dataless, then materialized
    icloud.ensure_materialized(
        p,
        stat_fn=lambda _p: FakeStat(next(flags), 10),
        run_fn=fake_run,
        sleep_fn=lambda _s: None,
    )
    assert calls[0][0] == "brctl"
    assert calls[0][1] == "download"


def test_materialize_falls_back_to_fileproviderctl(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return 0 if cmd[0] == "fileproviderctl" else 1  # brctl fails

    flags = iter([icloud.SF_DATALESS, 0])
    icloud.ensure_materialized(
        p,
        stat_fn=lambda _p: FakeStat(next(flags), 10),
        run_fn=fake_run,
        sleep_fn=lambda _s: None,
    )
    assert any(c[0] == "fileproviderctl" for c in calls)


def test_wait_stable_true_when_size_unchanged(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    stable = icloud.wait_stable(
        p, window_checks=2, interval=0,
        stat_fn=lambda _p: FakeStat(0, 100),
        sleep_fn=lambda _s: None,
    )
    assert stable is True


def test_wait_stable_false_when_size_changes(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    sizes = iter([100, 200, 300, 400, 500, 600])
    stable = icloud.wait_stable(
        p, window_checks=2, interval=0, max_checks=3,
        stat_fn=lambda _p: FakeStat(0, next(sizes)),
        sleep_fn=lambda _s: None,
    )
    assert stable is False


def test_prepare_source_materialized_and_stable(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    ready = icloud.prepare_source(
        p,
        stat_fn=lambda _p: FakeStat(0, 100),   # materialized + constant size
        run_fn=lambda cmd: 0,
        sleep_fn=lambda _s: None,
    )
    assert ready is True


# --- ensure_tree_materialized: recursively materialize a whole vault tree ---
_TREE = [("/vault", ["wiki"], ["AGENTS.md"]), ("/vault/wiki", [], ["a.md"])]


def test_ensure_tree_downloads_recursively_then_clears():
    state = {"dataless": True}

    def fake_run(cmd):
        if cmd[0] == "brctl":
            state["dataless"] = False  # recursive download completes
        return 0

    def fake_stat(_p):
        return FakeStat(icloud.SF_DATALESS if state["dataless"] else 0, 10)

    report = icloud.ensure_tree_materialized(
        "/vault",
        stat_fn=fake_stat,
        walk_fn=lambda _root: list(_TREE),
        run_fn=fake_run,
        sleep_fn=lambda _s: None,
        max_polls=5,
        interval=0,
    )
    assert report.scanned == 2
    assert report.materialized == 2
    assert report.still_dataless == 0
    assert report.timed_out is False


def test_ensure_tree_download_targets_root_once():
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return 0

    flip = {"n": 0}

    def fake_stat(_p):
        # dataless on the initial scan, materialized on the first poll
        flip["n"] += 1
        return FakeStat(icloud.SF_DATALESS if flip["n"] <= 2 else 0, 10)

    icloud.ensure_tree_materialized(
        "/vault",
        stat_fn=fake_stat,
        walk_fn=lambda _root: list(_TREE),
        run_fn=fake_run,
        sleep_fn=lambda _s: None,
        max_polls=5,
        interval=0,
    )
    brctl = [c for c in calls if c[0] == "brctl"]
    assert len(brctl) == 1
    assert brctl[0] == ["brctl", "download", "/vault"]


def test_ensure_tree_local_tree_is_noop():
    calls = []
    report = icloud.ensure_tree_materialized(
        "/vault",
        stat_fn=lambda _p: FakeStat(0, 10),   # nothing dataless
        walk_fn=lambda _root: [("/vault", [], ["a.md"])],
        run_fn=lambda cmd: calls.append(cmd) or 0,
        sleep_fn=lambda _s: None,
    )
    assert calls == []                 # never shells out
    assert report.scanned == 1
    assert report.materialized == 0
    assert report.still_dataless == 0
    assert report.timed_out is False


def test_ensure_tree_stays_dataless_is_bounded():
    runs = []
    sleeps = []
    report = icloud.ensure_tree_materialized(
        "/vault",
        stat_fn=lambda _p: FakeStat(icloud.SF_DATALESS, 10),  # never clears
        walk_fn=lambda _root: [("/vault", [], ["a.md", "b.md"])],
        run_fn=lambda cmd: runs.append(cmd) or 0,
        sleep_fn=lambda s: sleeps.append(s),
        max_polls=4,
        interval=0.1,
    )
    assert report.timed_out is True
    assert report.still_dataless == 2
    assert report.materialized == 0
    # strictly bounded: at most max_polls sleeps, and none right before giving up
    assert len(sleeps) == 3
    assert sum(1 for c in runs if c[0] == "brctl") == 1


def test_ensure_tree_falls_back_to_fileproviderctl():
    state = {"dataless": True}
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        if cmd[0] == "fileproviderctl":
            state["dataless"] = False
        return 1 if cmd[0] == "brctl" else 0     # brctl fails

    def fake_stat(_p):
        return FakeStat(icloud.SF_DATALESS if state["dataless"] else 0, 10)

    report = icloud.ensure_tree_materialized(
        "/vault",
        stat_fn=fake_stat,
        walk_fn=lambda _root: [("/vault", [], ["a.md"])],
        run_fn=fake_run,
        sleep_fn=lambda _s: None,
        max_polls=5,
        interval=0,
    )
    assert any(c[0] == "fileproviderctl" for c in calls)
    assert report.still_dataless == 0


def test_ensure_tree_skips_unreadable_paths():
    # A broken symlink makes os.stat raise; the scan must not blow up.
    def boom_stat(_p):
        raise FileNotFoundError("broken symlink")

    report = icloud.ensure_tree_materialized(
        "/vault",
        stat_fn=boom_stat,
        walk_fn=lambda _root: [("/vault", [], ["dangling"])],
        run_fn=lambda cmd: 0,
        sleep_fn=lambda _s: None,
    )
    assert report.still_dataless == 0
    assert report.timed_out is False
