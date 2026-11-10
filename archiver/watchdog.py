"""Re-dispatch a StormSense cycle whose run never started.

    python archiver/watchdog.py [--dry]

cron-job.org dispatches .github/workflows/stormsense.yml at :20 past 00, 06, 12 and 18 UTC. Now and
then GitHub creates the run but never gives it a runner: it fails within a minute or two with no
jobs and an empty log (2026-11-09 18:20 UTC). This checks the newest cycle and dispatches
stormsense.yml again when, at least 25 minutes after the cycle's :20,

    - no run of the cycle has started (none at all, or every one failed or was cancelled without a job), and
    - fewer than MAX_REDISPATCH re-dispatches have been made for it, and
    - the next cycle is more than an hour away (otherwise the next run will do).

A run that started a job and then failed is a real failure and is left alone. Runs inside the
archive-latest workflow (every 3 hours at :50), so it costs no extra Actions minutes. Needs GH_TOKEN
(github.token with actions: write) and GITHUB_REPOSITORY.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

WORKFLOW = "stormsense.yml"
CYCLE_HOURS = (0, 6, 12, 18)
DISPATCH_MIN = 20          # cron-job.org's dispatch, minutes past the cycle hour
GRACE = timedelta(minutes=25)
CUTOFF = timedelta(hours=1)  # no re-dispatch this close to the next cycle
MAX_REDISPATCH = 2
DEAD = {"failure", "cancelled", "startup_failure", "timed_out"}


def cycle_of(now):
    """The newest cycle's dispatch time at or before now."""
    day = now.replace(minute=0, second=0, microsecond=0)
    for d in (0, 1):
        for h in reversed(CYCLE_HOURS):
            t = (day - timedelta(days=d)).replace(hour=h, minute=DISPATCH_MIN)
            if t <= now:
                return t
    raise ValueError(now)


def decide(now, runs):
    """runs: dicts with created (datetime), status, conclusion, jobs (int), bot (bool: dispatched by
    this watchdog). Returns (dispatch?, reason)."""
    cyc = cycle_of(now)
    if now < cyc + GRACE:
        return False, f"cycle {cyc:%Y-%m-%dT%H:%MZ} dispatched under {GRACE} ago"
    if cyc + timedelta(hours=6) - now < CUTOFF:
        return False, f"next cycle is under {CUTOFF} away"
    mine = [r for r in runs if r["created"] >= cyc - timedelta(minutes=10)]
    for r in mine:
        if r["status"] != "completed":
            return False, f"a run of the cycle is {r['status']}"
        if r["conclusion"] not in DEAD or r["jobs"] > 0:
            return False, f"a run of the cycle started ({r['conclusion']}, {r['jobs']} job(s))"
    tries = sum(r["bot"] for r in mine)
    if tries >= MAX_REDISPATCH:
        return False, f"already re-dispatched {tries} times for this cycle"
    what = f"{len(mine)} run(s) died without a job" if mine else "no run was dispatched"
    return True, f"cycle {cyc:%Y-%m-%dT%H:%MZ}: {what}"


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read()
    return json.loads(txt) if txt else None


def when(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="report only, never dispatch")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    since = cycle_of(now) - timedelta(minutes=10)
    got = api(f"actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=20"
              f"&created=%3E%3D{since:%Y-%m-%dT%H:%M:%SZ}")
    runs = []
    for r in got["workflow_runs"]:
        jobs = api(f"actions/runs/{r['id']}/jobs?filter=all&per_page=1")["total_count"]
        runs.append({"created": when(r["created_at"]), "status": r["status"], "conclusion": r["conclusion"],
                     "jobs": jobs, "bot": (r.get("triggering_actor") or {}).get("login") == "github-actions[bot]"})
    go, why = decide(now, runs)
    print(f"watchdog: {why}")
    if go and not a.dry:
        api(f"actions/workflows/{WORKFLOW}/dispatches", "POST", {"ref": "main"})
        print(f"::warning title=StormSense run re-dispatched::{why}; dispatched {WORKFLOW} again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
