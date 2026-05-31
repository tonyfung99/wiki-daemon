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
