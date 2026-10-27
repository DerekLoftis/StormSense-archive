"""Read the StormSense run archives on GitHub releases.

Since the move to one pipeline (Oct 2026) every run is archived on this private repository's
releases; the ensemble runs before it are on the public StormSense-ensemble's releases. Both
are read, this repository's first. Uploads happen in the workflow (gh release upload).
"""
import json
import os
import time
import urllib.request

PRIVATE = os.environ.get("STORMSENSE_ARCHIVE_REPO", "DerekLoftis/StormSense-archive")
PUBLIC_ENSEMBLE = "DerekLoftis/StormSense-ensemble"
REPOS = (PRIVATE, PUBLIC_ENSEMBLE)
UA = {"User-Agent": "StormSense (VIMS)"}


def _get(url, accept=None, tries=3, timeout=180):
    head = dict(UA)
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok and url.startswith("https://api.github.com/"):
        head["Authorization"] = f"Bearer {tok}"
    if accept:
        head["Accept"] = accept
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def assets(prefix, repos=REPOS, max_pages=3):
    """{tag: {asset name: api url}} for the releases whose tag starts with prefix, newest tag first;
    for an asset in more than one repository the first repository listed wins."""
    out = {}
    for repo in repos:
        for page in range(1, max_pages + 1):
            try:
                js = json.loads(_get(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"))
            except Exception as e:
                print(f"releases of {repo} unavailable: {e}")
                break
            for r in js:
                if r["tag_name"].startswith(prefix):
                    d = out.setdefault(r["tag_name"], {})
                    for a in r["assets"]:
                        d.setdefault(a["name"], a["url"])
            if len(js) < 100:
                break
    return dict(sorted(out.items(), reverse=True))


def download(url, path):
    """Save one release asset (by its API url) to path, unless it is already there."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = _get(url, accept="application/octet-stream")
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    return path


def newest_asset(prefix, name, repos=REPOS):
    """API url of the newest release asset called `name` under tags starting with prefix, or None."""
    for tag, a in assets(prefix, repos).items():
        if name in a:
            return a[name]
    return None
