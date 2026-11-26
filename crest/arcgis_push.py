"""Load CREST's feeds into hosted feature layers in VIMS's ArcGIS Online org, for the ArcGIS
Experience Builder dashboard (Esri UC). Each build overwrites the layers in place, so the dashboard
and its maps keep pointing at the same items.

    python -m crest.arcgis_push site/crest/feeds

Credentials, from repository secrets (the step is skipped without them):
    ARCGIS_URL        the org's URL, e.g. https://vims.maps.arcgis.com
    ARCGIS_API_KEY    an API key credential with privileges to create and update items, or
    ARCGIS_USERNAME / ARCGIS_PASSWORD   a built-in ArcGIS Online account allowed to publish
Optional: ARCGIS_FOLDER (default "StormSense CREST").

Uses the ArcGIS REST API directly (only `requests`; the arcgis package needs Kerberos headers to
install). The first run adds each CSV to that folder and publishes it as a hosted feature layer
(points from the lon/lat columns, field types detected by ArcGIS); later runs replace the CSV and
publish again with overwrite, which keeps the layer's item and URL. Layers are read-only (editing
off), which ArcGIS requires before a layer can be shared publicly. Sharing stays as Derek sets it.
Daily Skill is time-enabled on its "date" field so the map's time slider can replay a period.
"""
import json
import os
import sys
import time

LAYERS = [
    # title, file, has lon/lat
    ("CREST Stations", "stations.csv", True),
    ("CREST Daily Skill", "daily_skill.csv", True),
    ("CREST Flood Events", "flood_events.csv", True),
    ("CREST Leaderboard", "leaderboard.csv", False),
    ("CREST Lead Decay", "lead_decay.csv", False),
    ("CREST Changes", "changes.csv", False),
]
TAGS = "StormSense, CREST, VIMS, storm tide, forecast skill"
SNIPPET = "StormSense CREST: archived storm tide forecasts scored against the gauges (VIMS)."


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


def wait(p, item, job):
    for _ in range(120):
        d = p.call("GET", f"{p.content}/items/{item}/status", jobId=job, jobType="publish")
        if d.get("status") == "completed":
            return
        if d.get("status") == "failed":
            raise RuntimeError(f"publish failed: {d.get('statusMessage')}")
        time.sleep(5)
    raise RuntimeError("publish did not finish in 10 minutes")


def push(p, folder, have, title, path, has_xy):
    """Add or replace the CSV item, then publish (overwriting the layer when it exists)."""
    csv = next((i for i in have if i["title"] == title and i["type"] == "CSV"), None)
    svc = next((i for i in have if i["title"] == title and i["type"] == "Feature Service"), None)
    upload = "crest_" + os.path.basename(path)
    with open(path, "rb") as fh:
        files = {"file": (upload, fh, "text/csv")}
        if csv is None:
            cid = p.call("POST", f"{p.content}/{folder}/addItem", files=files, type="CSV", title=title,
                         tags=TAGS, snippet=SNIPPET)["id"]
        else:
            cid = csv["id"]
            p.call("POST", f"{p.content}/{folder}/items/{cid}/update", files=files)
    pp = p.call("POST", f"{p.rest}/content/features/analyze", itemid=cid, filetype="csv",
                analyzeParameters={"enableGlobalGeocoding": False, "sourceLocale": "en"})["publishParameters"]
    pp["name"] = svc["url"].rstrip("/").split("/")[-2] if svc and svc.get("url") else title.replace(" ", "_")
    if isinstance(pp.get("layerInfo"), dict):
        pp["layerInfo"]["capabilities"] = "Query"  # read-only: ArcGIS won't share an editable layer publicly
    if has_xy:
        pp.update(locationType="coordinates", latitudeFieldName="lat", longitudeFieldName="lon")
    else:
        pp["locationType"] = "none"
    res = p.call("POST", f"{p.content}/publish", itemid=cid, filetype="csv", publishParameters=pp,
                 overwrite="true" if svc else "false")["services"][0]
    if res.get("error") or not res.get("serviceItemId"):
        raise RuntimeError(f"publish refused: {res.get('error') or res}")
    if res.get("jobId"):
        wait(p, res["serviceItemId"], res["jobId"])
    url = res.get("serviceurl") or (svc or {}).get("url")
    read_only(p, url)
    return res["serviceItemId"], url, "overwritten" if svc else "published"


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


def main(feeds):
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
    for title, name, xy in LAYERS:
        path = os.path.join(feeds, name)
        if not os.path.exists(path):
            print(f"{title}: {name} missing, skipped")
            continue
        try:
            item, url, what = push(p, folder, have, title, path, xy)
            print(f"{title}: {what}, item {item} {url}")
            if name == "daily_skill.csv" and url:
                time_enable(p, url)
        except Exception as e:
            failed += 1
            print(f"::warning title=ArcGIS {title}::{e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "site/crest/feeds"))
