"""Offline tests of CREST's ArcGIS side: the dashboard and web map JSON, and the upload against a stand-in portal."""
import csv
import json
import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crest import arcgis_dashboard, arcgis_push, schema  # noqa: E402

NAMES = {m: schema.SHORT[m] + " (feed name)" for m in schema.MAIN}
LAYERS = {k: {"item": f"{i:032x}", "url": f"https://services.example/arcgis/rest/services/{v[0].replace(' ', '_')}/FeatureServer"}
          for i, (k, v) in enumerate(schema.FEEDS.items(), 1)}
MAP = "f" * 32


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def test_dashboard_refers_only_to_what_exists():
    d = arcgis_dashboard.dashboard(LAYERS, MAP, names=NAMES)
    json.dumps(d, allow_nan=False)
    view = d["desktopView"]
    widgets = {w["id"]: w for w in view["widgets"]}
    selectors = {s["id"]: s for s in view["header"]["selectors"]}
    assert len(widgets) == len(view["widgets"]), "widget ids repeat"
    # every panel in the layout is a widget and every widget is laid out once
    placed = [n["id"] for n in walk(view["layout"]) if n.get("type") == "itemLayoutElement"]
    assert sorted(placed) == sorted(widgets)
    # every layer a panel reads is one of ours, and every column it names is in that feed
    by_item = {v["item"]: k for k, v in LAYERS.items()}
    for w in list(widgets.values()) + list(selectors.values()):
        for ds in w.get("datasets", []):
            if ds.get("type") != "serviceDataset":
                continue
            key = by_item[ds["dataSource"]["itemId"]]
            cols = set(schema.columns(key))
            made = {s["outStatisticFieldName"] for s in ds["statisticDefinitions"]}
            used = set(ds["groupByFields"]) | {s["onStatisticField"] for s in ds["statisticDefinitions"]}
            used |= {f for f in ds["outFields"] if f != "*"}
            used |= {r["field"]["name"] for r in walk(ds.get("filter", {})) if r.get("type") == "filterRule"}
            used |= {o.split()[0] for o in ds["orderByFields"]} - made
            used |= set(re.findall(r"\{field/(\w+)\}", json.dumps(w)))
            arcade = w.get("expression", "")    # Arcade reads the row as $datapoint (the tables call it d)
            used |= set(re.findall(r"(?:\$datapoint|\bd)\.(\w+)", arcade) + re.findall(r"\$datapoint\['(\w+)'\]", arcade)) - made
            assert used <= cols, (w["name"], key, sorted(used - cols))
    # every action points at a panel that exists and filters on a column of that panel's layer
    map_id = arcgis_dashboard.wid("Map")
    for src in list(widgets.values()) + list(selectors.values()) + widgets[map_id]["layers"]:
        for ev in src.get("events", []):
            for act in ev["actions"]:
                for t in act["targets"]:
                    tid = t["targetId"].split("#")[0]
                    assert tid in widgets, (src.get("name"), t["targetId"])
                    for fm in t.get("fieldMap", []):
                        ds = widgets[tid]["datasets"][0]
                        assert fm["targetName"] in schema.columns(by_item[ds["dataSource"]["itemId"]]), (widgets[tid]["name"], fm)
    # the split charts' series are the feeds' own guidance names, in CREST's colors
    lead = next(w for w in widgets.values() if w["name"] == "Skill by lead time")
    assert [s["id"] for s in lead["chartConfig"]["series"]] == [NAMES[m] for m in schema.MAIN]
    assert lead["chartConfig"]["series"][0]["lineSymbol"]["color"][:3] == [0x0b, 0x39, 0x54]
    # Arcade number patterns: a bare 0 is one digit, so Text(10.18, '0.00') would print 0.18
    for w in widgets.values():
        assert not re.search(r"'0(\.0+)?'", w.get("expression", "")), w["name"]
    assert arcgis_dashboard.DISCLAIMER in json.dumps(d)


def test_webmap_draws_the_stations_layer():
    m = arcgis_dashboard.webmap(LAYERS, (-77.5, 36.5, -75.2, 39.3))
    layer = m["operationalLayers"][0]
    assert layer["id"] == arcgis_dashboard.STATIONS_LAYER and layer["itemId"] == LAYERS["stations"]["item"]
    assert layer["url"].endswith("/FeatureServer/0")
    box = m["initialState"]["viewpoint"]["targetGeometry"]
    assert box["xmin"] < box["xmax"] and box["ymin"] < box["ymax"]
    assert set(re.findall(r"\{(\w+)\}", layer["popupInfo"]["description"])) <= set(schema.columns("stations"))


class Portal:
    """A stand-in for the few ArcGIS Online REST calls arcgis_push makes."""

    def __init__(self, refuse_types=()):
        self.items, self.folders, self.published, self.resources, self.n = [], [], {}, {}, 0
        self.refuse_types = set(refuse_types)   # layer titles the portal will not publish with other than its own field types
        self.limit = None    # the largest file (bytes) this portal takes in one request; None: any size
        self.lost = set()    # titles whose first addItem makes the item and then answers with an error

    def new(self, **item):
        self.n += 1
        item = dict(item, id=f"{self.n:032x}", access="private")
        self.items.append(item)
        return item

    def handle(self, method, url, params, files=None):
        assert params.get("f") == "json"
        if url.endswith("/generateToken"):
            return {"token": "T"}
        assert params.get("token") == "T", url
        item = next((i for i in self.items if f"/items/{i['id']}/" in url), None)
        if url.endswith("/community/self"):
            return {"username": "u"}
        if url.endswith("/content/users/u"):
            return {"folders": self.folders}
        if url.endswith("/createFolder"):
            self.folders.append({"id": "F", "title": params["title"]})
            return {"success": True, "folder": self.folders[0]}
        if url.endswith("/content/users/u/F"):
            return {"items": [dict(i) for i in self.items], "nextStart": -1}
        body = files["file"][1].read() if files and url.endswith(("/addItem", "/update")) else None
        if body is not None and self.limit is not None and len(body) > self.limit:
            return {"error": {"code": 413, "message": "Request Entity Too Large", "details": []}}
        whole = body is not None and dict(header=body.split(b"\n", 1)[0].decode().strip(), size=len(body), sent="whole")
        if url.endswith("/addItem"):
            if files:
                made = self.new(title=params["title"], type="CSV", **whole)
                if params["title"] in self.lost:
                    self.lost.discard(params["title"])
                    return {"error": {"code": 504, "message": "Gateway Timeout", "details": []}}
                return {"success": True, "id": made["id"]}
            if params.get("multipart") == "true":   # an item waiting for its file's parts
                assert params["type"] == "CSV" and params["fileName"].endswith(".csv")
                return {"success": True, "id": self.new(title=params["title"], type="CSV", parts={})["id"]}
            assert params["type"] in ("Web Map", "Dashboard") and params["typeKeywords"]
            return {"success": True, "id": self.new(title=params["title"], type=params["type"], text=params["text"], writes=1)["id"]}
        if url.endswith("/update"):
            if files:
                item.update(whole)
            elif params.get("multipart") == "true":
                assert params["fileName"].endswith(".csv") and "title" not in params
                item["parts"] = {}
            else:
                item.update(text=params["text"], writes=item["writes"] + 1)
            return {"success": True}
        if url.endswith("/addPart"):
            item["parts"][int(params["partNum"])] = files["file"][1]
            return {"success": True}
        if url.endswith("/commit"):
            assert params["id"] == item["id"] and params["type"] == "CSV" and params["async"] == "true"
            parts = [p for _, p in sorted(item.pop("parts").items())]
            assert parts and all(len(p) == arcgis_push.PART for p in parts[:-1])   # only the last part may be short
            body = b"".join(parts)
            item.update(header=body.split(b"\n", 1)[0].decode().strip(), size=len(body), sent="parts")
            item.update({k: params[k] for k in ("title", "tags", "snippet") if k in params})
            return {"success": True, "id": item["id"]}
        if url.endswith("/data"):
            return json.loads(next(i for i in self.items if f"/items/{i['id']}/data" in url)["text"])
        if url.endswith("/addResources") or url.endswith("/updateResources"):
            name = params["resourcesPrefix"] + "/" + params["fileName"]
            if url.endswith("/addResources") and name in self.resources.get(item["id"], {}):
                return {"error": {"code": 409, "message": "Resource already present", "details": []}}
            self.resources.setdefault(item["id"], {})[name] = files["file"][1]
            return {"success": True}
        if url.endswith("/analyze"):   # ArcGIS guesses the types; here it takes everything for text
            src = next(i for i in self.items if i["id"] == params["itemid"])
            return {"publishParameters": {"name": "x", "layerInfo": {"capabilities": "Create,Delete,Query,Update,Editing", "fields": [
                {"name": c, "type": "esriFieldTypeString", "alias": c, "sqlType": "sqlTypeNVarchar", "length": 256,
                 "nullable": True, "editable": True, "domain": None, "defaultValue": None} for c in src["header"].split(",")]}}}
        if url.endswith("/publish"):
            pp = json.loads(params["publishParameters"])
            src = next(i for i in self.items if i["id"] == params["itemid"])
            title = src["title"]
            svc = next((i for i in self.items if i["type"] == "Feature Service" and i["title"] == title), None)
            assert params["overwrite"] == ("true" if svc else "false") and pp["layerInfo"]["capabilities"] == "Query"
            types_ = {f["name"]: f["type"] for f in pp["layerInfo"]["fields"]}
            if title in self.refuse_types and set(types_.values()) != {"esriFieldTypeString"}:
                return {"services": [{"error": {"message": "field types not accepted"}}]}
            if not svc:
                svc = self.new(title=title, type="Feature Service",
                               url=f"https://services.example/arcgis/rest/services/{pp['name']}/FeatureServer")
            self.published[title] = {"fields": {f["name"]: f for f in pp["layerInfo"]["fields"]}, "location": pp["locationType"],
                                     "times": self.published.get(title, {}).get("times", 0) + 1}
            return {"services": [{"serviceItemId": svc["id"], "serviceurl": svc["url"], "jobId": "j"}]}
        if url.endswith("/status"):
            return {"status": "completed"}
        if url.endswith("/FeatureServer"):
            return {"capabilities": "Query"}
        if url.endswith("/FeatureServer/0"):
            return {"fields": [{"name": "date", "type": "esriFieldTypeDate"}]}
        if url.endswith("/updateDefinition"):
            assert "/rest/admin/services/" in url
            return {"success": True}
        raise AssertionError(url)

    def module(self):
        portal = self

        class Reply:
            def __init__(self, d):
                self.d = d

            def raise_for_status(self):
                pass

            def json(self):
                return self.d

        class Session:
            def __init__(self):
                self.headers = {}

            def get(self, url, params=None, timeout=None):
                return Reply(portal.handle("GET", url, params))

            def post(self, url, data=None, files=None, timeout=None):
                return Reply(portal.handle("POST", url, data, files))

        return types.SimpleNamespace(Session=Session, post=lambda url, data=None, timeout=None: Reply(
            portal.handle("POST", url, dict(data, token="T"))))


def write_feeds(folder):
    """Every feed with its header and one row (blank where the first rows of the real feeds are blank)."""
    folder.mkdir()
    sample = {"str": "x", "float": "", "int": "", "date": "2026-11-25T12:00:00Z"}
    for key, (_, name, _, cols) in schema.FEEDS.items():
        row = {c[0]: sample[c[1]] for c in cols}
        row.update({k: v for k, v in (("lon", "-76.33"), ("lat", "36.95"), ("station", "SWPT"), ("member", "primary"),
                                      ("member_name", NAMES["primary"])) if k in row})
        with open(folder / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[c[0] for c in cols])
            w.writeheader()
            w.writerow(row)
    return str(folder)


@pytest.fixture
def portal(monkeypatch):
    p = Portal()
    monkeypatch.setitem(sys.modules, "requests", p.module())
    monkeypatch.setenv("ARCGIS_URL", "https://vims.maps.arcgis.com/home/")
    monkeypatch.setenv("ARCGIS_USERNAME", "u")
    monkeypatch.setenv("ARCGIS_PASSWORD", "p")
    monkeypatch.delenv("ARCGIS_API_KEY", raising=False)
    monkeypatch.delenv("CREST_DASHBOARD", raising=False)
    return p


def test_upload_publishes_typed_layers_and_creates_the_dashboard_once(portal, tmp_path, monkeypatch, capsys):
    feeds = write_feeds(tmp_path / "feeds")
    assert arcgis_push.main(feeds) == 0
    # every feed is a layer with CREST's own field types, not ArcGIS's guess
    assert set(portal.published) == {v[0] for v in schema.FEEDS.values()}
    st, fc = portal.published["CREST Stations"], portal.published["CREST Forecasts"]
    assert st["fields"]["S_tidewatch"]["type"] == "esriFieldTypeDouble" and st["fields"]["runs"]["type"] == "esriFieldTypeInteger"
    assert st["fields"]["verdict"]["length"] == 1000 and st["fields"]["S_primary"]["alias"] == "Skill S: Published"
    assert fc["fields"]["issued"]["type"] == "esriFieldTypeDate" and st["location"] == "coordinates" and fc["location"] == "none"
    # the web map and the dashboard exist, on those layers, with the StormSense theme
    web = next(i for i in portal.items if i["type"] == "Web Map")
    dash = next(i for i in portal.items if i["type"] == "Dashboard")
    ids = {i["title"]: i["id"] for i in portal.items if i["type"] == "Feature Service"}
    data = json.loads(dash["text"])
    assert data["theme"] == {"id": "user", "type": "resource"} and "themes/user.json" in portal.resources[dash["id"]]
    assert json.loads(web["text"])["operationalLayers"][0]["itemId"] == ids["CREST Stations"]
    used = {n["itemId"] for n in walk(data) if n.get("type") == "layerDataSource"}
    assert used == {web["id"]} | {ids[t] for t in ("CREST Stations", "CREST Leaderboard", "CREST Lead Decay", "CREST Forecasts",
                                                   "CREST Flood Events", "CREST Changes")}
    assert NAMES["primary"] in dash["text"]     # series named as the feeds name the guidance
    out = capsys.readouterr().out
    assert f"https://vims.maps.arcgis.com/apps/dashboards/{dash['id']}" in out and "::notice title=CREST dashboard::Created" in out

    # the next day: layers overwritten in place, the dashboard and map left as they are
    writes = (web["writes"], dash["writes"])
    assert arcgis_push.main(feeds) == 0
    assert all(v["times"] == 2 for v in portal.published.values()) and len(portal.items) == 2 * len(schema.FEEDS) + 2
    assert (web["writes"], dash["writes"]) == writes and "left as it is" in capsys.readouterr().out

    # renamed in ArcGIS, they are still recognized (by the ids inside them) and not made a second time
    web["title"], dash["title"] = "Derek's map", "Derek's dashboard"
    assert arcgis_push.main(feeds) == 0
    assert len(portal.items) == 2 * len(schema.FEEDS) + 2 and (web["writes"], dash["writes"]) == writes
    capsys.readouterr()

    # on request they are written again from the code
    monkeypatch.setenv("CREST_DASHBOARD", "rebuild")
    assert arcgis_push.main(feeds) == 0
    assert web["writes"] == writes[0] + 1 and dash["writes"] > writes[1] and len(portal.items) == 2 * len(schema.FEEDS) + 2
    assert "::notice title=CREST dashboard::Rebuilt" in capsys.readouterr().out


def test_upload_falls_back_to_arcgis_types_and_reports_uneven_sharing(portal, tmp_path, capsys):
    portal.refuse_types = {"CREST Changes"}
    feeds = write_feeds(tmp_path / "feeds")
    assert arcgis_push.main(feeds) == 0
    assert portal.published["CREST Changes"]["fields"]["date"]["type"] == "esriFieldTypeString"
    assert portal.published["CREST Stations"]["fields"]["lon"]["type"] == "esriFieldTypeDouble"
    assert "field types were refused" in capsys.readouterr().out
    # the owner shares the layers; the new dashboard and map are still private, and the run says so
    for i in portal.items:
        if i["type"] == "Feature Service":
            i["access"] = "public"
    assert arcgis_push.main(feeds) == 0
    out = capsys.readouterr().out
    assert "::notice title=ArcGIS sharing::" in out and "private: " in out and arcgis_dashboard.DASH_TITLE in out


def big_forecasts(feeds, monkeypatch, rows=300):
    """Make the forecasts feed "large" (the real one is about 15 MB) by this test's small measure."""
    path = os.path.join(feeds, schema.FEEDS["forecasts"][1])
    row = open(path).read().splitlines()[1]
    with open(path, "a") as f:
        f.write("".join(row + "\n" for _ in range(rows)))
    monkeypatch.setattr(arcgis_push, "BIG", 4000)
    monkeypatch.setattr(arcgis_push, "PART", 5000)
    assert os.path.getsize(path) > 3 * 5000
    assert all(os.path.getsize(os.path.join(feeds, v[1])) < 4000 for k, v in schema.FEEDS.items() if k != "forecasts")
    return path, row


def test_a_large_feed_goes_up_in_parts_when_one_request_is_refused(portal, tmp_path, monkeypatch, capsys):
    feeds = write_feeds(tmp_path / "feeds")
    path, row = big_forecasts(feeds, monkeypatch)
    portal.limit = 6000
    assert arcgis_push.main(feeds) == 0
    csvs = {i["title"]: i for i in portal.items if i["type"] == "CSV"}
    fc = csvs.pop("CREST Forecasts")
    assert fc["sent"] == "parts" and fc["size"] == os.path.getsize(path) and fc["header"].split(",") == schema.columns("forecasts")
    assert len(csvs) == len(schema.FEEDS) - 1 and all(i["sent"] == "whole" for i in csvs.values())
    assert portal.published["CREST Forecasts"]["fields"]["S"]["type"] == "esriFieldTypeDouble"
    assert any(i["type"] == "Dashboard" for i in portal.items) and "sending it in parts" in capsys.readouterr().out
    # the next day the same item gets the new file, again in parts, and keeps the properties its owner gave it
    n = len(portal.items)
    assert fc["tags"] == arcgis_push.TAGS
    fc["tags"] = "the owner's own tags"
    with open(path, "a") as f:
        f.write(row + "\n")
    assert arcgis_push.main(feeds) == 0
    assert len(portal.items) == n and fc["size"] == os.path.getsize(path) and portal.published["CREST Forecasts"]["times"] == 2
    assert fc["tags"] == "the owner's own tags"


def test_a_large_feed_is_not_added_twice_when_the_first_request_fails_late(portal, tmp_path, monkeypatch):
    feeds = write_feeds(tmp_path / "feeds")
    path, _ = big_forecasts(feeds, monkeypatch)
    portal.lost = {"CREST Forecasts"}     # ArcGIS makes the item, but the answer is an error
    assert arcgis_push.main(feeds) == 0
    mine = [i for i in portal.items if i["type"] == "CSV" and i["title"] == "CREST Forecasts"]
    assert len(mine) == 1 and mine[0]["sent"] == "parts" and mine[0]["size"] == os.path.getsize(path)
    assert portal.published["CREST Forecasts"]["times"] == 1


def test_a_small_feed_that_is_refused_is_reported_not_sent_in_parts(portal, tmp_path, capsys):
    feeds = write_feeds(tmp_path / "feeds")
    portal.limit = 10
    assert arcgis_push.main(feeds) == 1
    out = capsys.readouterr().out
    assert not portal.published and "::warning title=ArcGIS CREST Stations::413" in out and "in parts" not in out
    assert "::warning title=ArcGIS dashboard::the dashboard needs layers that were not published" in out
