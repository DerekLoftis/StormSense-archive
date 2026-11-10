from datetime import datetime, timedelta, timezone
import importlib.util, os

spec = importlib.util.spec_from_file_location(
    "watchdog", os.path.join(os.path.dirname(__file__), "..", "archiver", "watchdog.py"))
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)


def run(t, conclusion="failure", jobs=0, status="completed", bot=False):
    return {"created": T(t), "status": status, "conclusion": conclusion, "jobs": jobs, "bot": bot}


def test_cycle_of():
    assert wd.cycle_of(T("2026-11-09T18:50")) == T("2026-11-09T18:20")
    assert wd.cycle_of(T("2026-11-10T00:10")) == T("2026-11-09T18:20")
    assert wd.cycle_of(T("2026-11-10T03:50")) == T("2026-11-10T00:20")


def test_run_that_never_started_is_redispatched():
    go, why = wd.decide(T("2026-11-09T18:50"), [run("2026-11-09T18:20")])
    assert go and "died without a job" in why


def test_missing_dispatch_is_redispatched():
    assert wd.decide(T("2026-11-09T21:50"), [])[0]


def test_started_queued_or_good_runs_are_left_alone():
    now = T("2026-11-09T18:50")
    assert not wd.decide(now, [run("2026-11-09T18:20", "success")])[0]
    assert not wd.decide(now, [run("2026-11-09T18:20", None, status="in_progress")])[0]
    assert not wd.decide(now, [run("2026-11-09T18:20", "failure", jobs=1)])[0]   # a real failure
    assert not wd.decide(now, [run("2026-11-09T18:20"), run("2026-11-09T18:40", "success", bot=True)])[0]


def test_too_soon_too_late_and_capped():
    assert not wd.decide(T("2026-11-09T18:40"), [])[0]          # inside the grace period
    assert not wd.decide(T("2026-11-09T23:40"), [])[0]          # next cycle under an hour away
    dead = [run("2026-11-09T18:20"), run("2026-11-09T18:50", bot=True), run("2026-11-09T21:50", bot=True)]
    assert not wd.decide(T("2026-11-09T22:30"), dead)[0]


def test_earlier_cycles_do_not_count():
    assert wd.decide(T("2026-11-09T18:50"), [run("2026-11-09T12:20", "success")])[0]
