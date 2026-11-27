"""Load CREST's feeds into hosted feature layers in VIMS's ArcGIS Online org and keep the ArcGIS
dashboard on them (crest/arcgis_dashboard.py; Esri UC). Each build overwrites the layers in place, so
the dashboard and its map keep pointing at the same items.

    python -m crest.arcgis_push site/crest/feeds

Credentials, from repository secrets (the step is skipped without them):
    ARCGIS_URL        the org's URL, e.g. https://vims.maps.arcgis.com
    ARCGIS_API_KEY    an API key credential with privileges to create and update items, or
    ARCGIS_USERNAME / ARCGIS_PASSWORD   a built-in ArcGIS Online account allowed to publish
Optional: ARCGIS_FOLDER (default "StormSense CREST"); CREST_DASHBOARD=rebuild writes the web map and
the dashboard again from arcgis_dashboard.py (otherwise they are created once and then left alone, so
edits made in ArcGIS are kept).

Uses the ArcGIS REST API directly (only `requests`; the arcgis package needs Kerberos headers to
install). The first run adds each CSV to that folder and publishes it as a hosted feature layer
(points from the lon/lat columns); later runs replace the CSV and publish again with overwrite, which
keeps the layer's item and URL. Every column is published with its type from crest/schema.py: left to
itself ArcGIS guesses from the first rows and took numbers with blank leading cells for text (if ArcGIS
refuses those types, the layer is published with its own guesses and a warning says so). A publish that
ArcGIS has not finished in WAIT_MIN minutes is reported and left to finish there: it is not sent again.
Layers are read-only (editing off), which ArcGIS requires before a layer can be shared publicly.
Daily Skill is time-enabled on its "date" field so a map's time slider can replay a period.
Sharing stays as the owner sets it; a notice lists the items when they are not all shared alike,
because the dashboard only works for people who can see the dashboard, its map and every layer.
"""
import copy
import csv
import json
import os
import sys
import time

from . import schema

TAGS = "StormSense, CREST, VIMS, storm tide, forecast skill"
SNIPPET = "StormSense CREST: archived storm tide forecasts scored against the gauges (VIMS)."
BIG = 5 * 2 ** 20     # a file larger than this has a second way up, in parts (put_file)
PART = 50 * 2 ** 20   # the size of a part; ArcGIS wants every part but the last to be 5 MB or more
WAIT_MIN = 20         # how long one publish or upload job may take (a 2,000-row layer once took 7 minutes)


class Slow(RuntimeError):
    """ArcGIS has not finished a job in the time allowed. The job may still finish there, so it is not sent again."""


class Portal:
    def __init__(self, url, token):
        import requests
        self.s = requests.Session()
        self.rest = url.rstrip("/") + "/sharing/rest"
        self.token = token
        self.s.headers["Referer"] = url  # tokens from generateToken are bound to this referer
        self.user = self.call("GET", f"{self.rest}/community/self")["username"]

    def call(self, method, url, files=None, **params):
        params = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
        params.update(f="json", token=self.token)
        if method == "GET":
            r = self.s.get(url, params=params, timeout=120)
        else:
            r = self.s.post(url, data=params, files=files, timeout=600)
        r.raise_for_status()
        d = r.json()
        if isinstance(d, dict) and d.get("error"):
            e = d["error"]
            raise RuntimeError(f"{e.get('code')} {e.get('message')} {'; '.join(e.get('details') or [])}".strip())
        return d

    @property
    def content(self):
        return f"{self.rest}/content/users/{self.user}"

    def folder(self, title):
        for f in self.call("GET", self.content).get("folders", []):
            if f["title"] == title:
                return f["id"]
        return self.call("POST", f"{self.content}/createFolder", title=title)["folder"]["id"]

    def items(self, folder):
        out, start = [], 1
        while start > 0:
            d = self.call("GET", f"{self.content}/{folder}", start=start, num=100)
            out += d.get("items", [])
            start = d.get("nextStart", -1)
        return out


def connect():
    url = os.environ.get("ARCGIS_URL", "").strip()
    key = os.environ.get("ARCGIS_API_KEY", "").strip()
    user, pw = os.environ.get("ARCGIS_USERNAME", "").strip(), os.environ.get("ARCGIS_PASSWORD", "")
    if not url or not (key or (user and pw)):
        return None
    if not url.startswith("http"):
        url = "https://" + url
    url = url.rstrip("/").split("/home")[0].split("/sharing")[0]
    if not key:
        import requests
        r = requests.post(url + "/sharing/rest/generateToken", timeout=120, data={
            "username": user, "password": pw, "client": "referer", "referer": url, "expiration": 120, "f": "json"})
        d = r.json()
        if "token" not in d:
            e = d.get("error", {})
            raise RuntimeError(f"sign-in failed: {e.get('message')} {'; '.join(e.get('details') or [])}".strip())
        key = d["token"]
    return Portal(url, key)


def wait(p, item, job=None):
    """Wait for a publish job, or without a job for the item itself (a file sent in parts being put together)."""
    what = "publish" if job else "upload"
    for _ in range(WAIT_MIN * 12):
        d = p.call("GET", f"{p.content}/items/{item}/status", **({"jobId": job, "jobType": "publish"} if job else {}))
        if d.get("status") == "completed":
            return
        if d.get("status") == "failed":
            raise RuntimeError(f"{what} failed: {d.get('statusMessage')}")
        time.sleep(5)
    raise Slow(f"ArcGIS had not finished the {what} after {WAIT_MIN} minutes; the layer is left for it to finish")


def put_file(p, folder, cid, path, name, **props):
    """Add the file as a new item (cid None) or replace the item's file; returns the item's id.
    One request does it. A file over 5 MB (the Forecasts CSV is about 15 MB) that ArcGIS does not take that way is
    sent again in parts and committed, which is how Esri's own Python client adds anything over 5 MB."""
    base = f"{p.content}/{folder}"

    def whole(cid):
        with open(path, "rb") as fh:
            files = {"file": (name, fh, "text/csv")}
            if cid is None:
                return p.call("POST", f"{base}/addItem", files=files, **props)["id"]
            p.call("POST", f"{base}/items/{cid}/update", files=files)
            return cid

    def parts(cid):
        new = cid is None
        if new:
            cid = p.call("POST", f"{base}/addItem", multipart="true", fileName=name, **props)["id"]
        else:
            p.call("POST", f"{base}/items/{cid}/update", multipart="true", fileName=name)
        with open(path, "rb") as fh:
            for n, chunk in enumerate(iter(lambda: fh.read(PART), b""), 1):
                p.call("POST", f"{base}/items/{cid}/addPart", files={"file": (f"split{n}.split", chunk)}, partNum=n)
        # a new item's properties go with the commit, as Esri's client sends them; an existing item keeps its own
        p.call("POST", f"{base}/items/{cid}/commit", id=cid, **{"async": "true"},
               **(props if new else {"type": props["type"]}))
        wait(p, cid)
        return cid

    try:
        return whole(cid)
    except Exception as e:
        if os.path.getsize(path) <= BIG:
            raise
        print(f"  {name} was not taken in one request ({e}); sending it in parts")
        if cid is None:   # a request can fail after ArcGIS has made the item: use that item, do not make a second
            cid = next((i["id"] for i in p.items(folder)
                        if i["title"] == props["title"] and i["type"] == props["type"]), None)
        return parts(cid)


def typed(pp, key):
    """Give ArcGIS's publish parameters CREST's own field types, lengths and labels (schema.py)."""
    want = schema.esri_fields(key)
    for f in (pp.get("layerInfo") or {}).get("fields") or []:
        w = want.get(f.get("name"))
        if w:
            for k in ("length", "precision"):
                f.pop(k, None)
            f.update(w)
    return pp


def push(p, folder, have, key, path):
    """Add or replace the feed's CSV item, then publish it (overwriting the layer when it exists)."""
    title, _, has_xy, _ = schema.FEEDS[key]
    csv_item = next((i for i in have if i["title"] == title and i["type"] == "CSV"), None)
    svc = next((i for i in have if i["title"] == title and i["type"] == "Feature Service"), None)
    cid = put_file(p, folder, csv_item["id"] if csv_item else None, path, "crest_" + os.path.basename(path),
                   type="CSV", title=title, tags=TAGS, snippet=SNIPPET)
    pp = p.call("POST", f"{p.rest}/content/features/analyze", itemid=cid, filetype="csv",
                analyzeParameters={"enableGlobalGeocoding": False, "sourceLocale": "en"})["publishParameters"]
    pp["name"] = svc["url"].rstrip("/").split("/")[-2] if svc and svc.get("url") else title.replace(" ", "_")
    if isinstance(pp.get("layerInfo"), dict):
        pp["layerInfo"]["capabilities"] = "Query"  # read-only: ArcGIS won't share an editable layer publicly
    if has_xy:
        pp.update(locationType="coordinates", latitudeFieldName="lat", longitudeFieldName="lon")
    else:
        pp["locationType"] = "none"

    def publish(params, overwrite):
        res = p.call("POST", f"{p.content}/publish", itemid=cid, filetype="csv", publishParameters=params,
                     overwrite="true" if overwrite else "false")["services"][0]
        if res.get("error") or not res.get("serviceItemId"):
            raise RuntimeError(f"publish refused: {res.get('error') or res}")
        if res.get("jobId"):
            wait(p, res["serviceItemId"], res["jobId"])
        return res

    what = "overwritten" if svc else "published"
    try:
        res = publish(typed(copy.deepcopy(pp), key), bool(svc))
    except Slow:             # slow is not refused: publishing again would start a second job on the same layer
        raise
    except Exception as e:   # keep the layer updating with ArcGIS's own field types rather than not at all
        print(f"::warning title=ArcGIS {title}::CREST's field types were refused ({e}); published with ArcGIS's own")
        again = bool(svc) or any(i["title"] == title and i["type"] == "Feature Service" for i in p.items(folder))
        res = publish(pp, again)
        what += " with ArcGIS's field types"
    url = res.get("serviceurl") or (svc or {}).get("url")
    read_only(p, url)
    return res["serviceItemId"], url, what


def read_only(p, service_url):
    """Turn editing off on the service if publishing left it on, so public sharing keeps working."""
    try:
        caps = p.call("GET", service_url).get("capabilities", "")
        if any(c in caps for c in ("Create", "Update", "Delete", "Editing")):
            admin = service_url.replace("/rest/services/", "/rest/admin/services/")
            p.call("POST", f"{admin}/updateDefinition", updateDefinition={"capabilities": "Query"})
            print("  editing turned off")
    except Exception as e:
        print("  could not turn editing off:", e)


def time_enable(p, service_url, field="date"):
    try:
        info = p.call("GET", f"{service_url}/0")
        if (info.get("timeInfo") or {}).get("startTimeField"):
            return
        if not any(f["name"] == field and f["type"] == "esriFieldTypeDate" for f in info.get("fields", [])):
            print(f"  not time-enabled: '{field}' was not published as a date field")
            return
        admin = service_url.replace("/rest/services/", "/rest/admin/services/")
        p.call("POST", f"{admin}/0/updateDefinition", updateDefinition={"timeInfo": {
            "startTimeField": field, "endTimeField": None, "trackIdField": "station",
            "timeReference": {"timeZone": "UTC"}, "timeInterval": 1, "timeIntervalUnits": "esriTimeUnitsDays"}})
        print("  time-enabled on", field)
    except Exception as e:
        print("  could not time-enable:", e)


def feed_names(feeds):
    """{member id: member_name} as the feeds write them (the dashboard's series are named by these)."""
    names = {}
    path = os.path.join(feeds, schema.FEEDS["leaderboard"][1])
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                names.setdefault(r.get("member"), r.get("member_name"))
    return {k: v for k, v in names.items() if k and v}


def stations_extent(feeds):
    """(lon min, lat min, lon max, lat max) of the stations feed, or None."""
    path = os.path.join(feeds, schema.FEEDS["stations"][1])
    pts = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    pts.append((float(r["lon"]), float(r["lat"])))
                except (KeyError, TypeError, ValueError):
                    pass
    if not pts:
        return None
    return min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts), max(y for _, y in pts)


def dashboard_items(p, folder, have, layers, feeds):
    """The web map and the dashboard on the layers (crest/arcgis_dashboard.py). Returns their ids, or None."""
    from . import arcgis_dashboard
    extent = stations_extent(feeds)
    if extent is None:
        print("dashboard: no stations in the feeds, skipped")
        return None
    rebuild = os.environ.get("CREST_DASHBOARD", "").strip().lower() == "rebuild"
    res = arcgis_dashboard.ensure(p, folder, have, layers, extent, names=feed_names(feeds), rebuild=rebuild)
    org = p.rest[:-len("/sharing/rest")]
    url = f"{org}/apps/dashboards/{res['dashboard']}"
    for what in ("created", "updated"):
        if res[what]:
            print(f"dashboard: {what} {', '.join(res[what])}")
    if not res["created"] and not res["updated"]:
        print("dashboard: already there, left as it is (CREST_DASHBOARD=rebuild writes it again)")
    print("dashboard:", url, "| web map item", res["map"])
    if res["created"] or res["updated"]:
        print(f"::notice title=CREST dashboard::{'Created' if res['created'] else 'Rebuilt'} {url}")
    return res


def sharing(p, folder, ids):
    """Say so when the dashboard, its map and the layers are not all shared alike (sharing is the owner's call)."""
    access = {i["id"]: (i.get("title"), i.get("access") or "private") for i in p.items(folder) if i["id"] in ids}
    levels = sorted({a for _, a in access.values()})
    if len(levels) > 1:
        by = "; ".join(f"{lv}: " + ", ".join(sorted(t for t, a in access.values() if a == lv)) for lv in levels)
        print(f"::notice title=ArcGIS sharing::The dashboard works only for people who can see the dashboard, its map and "
              f"every layer. Now shared as {by}")
    elif levels:
        print("sharing: every item is", levels[0])


def main(feeds):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)   # each line as it happens, so the job log times every layer
    try:
        p = connect()
    except Exception as e:
        print(f"::warning title=ArcGIS sign-in::{e}")
        return 1
    if p is None:
        print("ArcGIS credentials not set (ARCGIS_URL and ARCGIS_API_KEY or ARCGIS_USERNAME/PASSWORD); nothing pushed")
        return 0
    print("signed in to", p.rest, "as", p.user)
    failed = 0
    try:
        folder = p.folder(os.environ.get("ARCGIS_FOLDER", "StormSense CREST"))
        have = p.items(folder)
    except Exception as e:
        print(f"::warning title=ArcGIS folder::{e}")
        return 1
    layers = {}
    for key, (title, name, _, _) in schema.FEEDS.items():
        path = os.path.join(feeds, name)
        if not os.path.exists(path):
            print(f"{title}: {name} missing, skipped")
            continue
        began = time.monotonic()
        try:
            item, url, what = push(p, folder, have, key, path)
            layers[key] = {"item": item, "url": url}
            print(f"{title}: {what} in {time.monotonic() - began:.0f} s, item {item} {url}")
            if key == "daily" and url:
                time_enable(p, url)
        except Exception as e:
            failed += 1
            print(f"::warning title=ArcGIS {title}::{e} (after {time.monotonic() - began:.0f} s)")
    ids = {v["item"] for v in layers.values()}
    try:
        res = dashboard_items(p, folder, have, layers, feeds)
        if res:
            ids |= {res["map"], res["dashboard"]}
    except Exception as e:
        failed += 1
        print(f"::warning title=ArcGIS dashboard::{e}")
    try:
        sharing(p, folder, ids)
    except Exception as e:
        print("sharing not checked:", e)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "site/crest/feeds"))
