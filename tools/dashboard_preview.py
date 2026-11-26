"""Preview CREST's ArcGIS dashboard without publishing anything: a small local stand-in for ArcGIS
Online that the real ArcGIS Dashboards app can be pointed at (tools/dashboard_preview.js opens it in a
headless browser and takes screenshots).

    python tools/dashboard_preview.py --site <a built CREST site, with feeds/> [--port 8765] [--theme user]

It serves, on 127.0.0.1:
    /sharing/rest/content/items/<id>[/data|/resources/themes/user.json]
        the items: the dashboard and web map that crest/arcgis_dashboard.py writes (built again on every
        request, so an edit to that file shows on the next page load) and one item per feed
    /<org>/arcgis/rest/services/<name>/FeatureServer[/0[/query]]
        each feed as a hosted feature layer: the layer's description with the field types of
        crest/schema.py, and queries (where, object ids, out fields, order, paging, distinct, count, ids,
        statistics with group by and having, an envelope filter), answered from the feed CSVs in SQLite
    /site/...   the built site, for the pages the dashboard embeds
    /ids        the item ids and URLs, for the browser script

Only what ArcGIS Dashboards asks for is emulated; answers are JSON (the layers say they have no PBF
and no quantization, so the map asks for plain JSON too).
"""
import argparse
import csv
import hashlib
import importlib
import json
import math
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crest import arcgis_dashboard, schema  # noqa: E402

ORG = "QHK2l9cSYhemgg5s"
SERVICES = f"https://services1.arcgis.com/{ORG}/arcgis/rest/services"
SR = {"wkid": 102100, "latestWkid": 3857}
OID = "ObjectId"
MAX = 1000
SQL = {"str": "TEXT", "float": "REAL", "int": "INTEGER", "date": "INTEGER"}


def fake_id(name):
    return hashlib.md5(("crest preview " + name).encode()).hexdigest()


def epoch_ms(text):
    t = text.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    d = datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


PARTS = {"YEAR": "%Y", "MONTH": "%m", "DAY": "%d", "HOUR": "%H", "MINUTE": "%M", "SECOND": "%S"}
UNIT_S = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}


def to_sqlite(sql):
    """ArcGIS's standardized SQL -> SQLite, for what Dashboards sends: timestamp and interval literals (dates
    are epoch milliseconds here), N'text', CURRENT_TIMESTAMP and EXTRACT(part FROM date expression)."""
    if ";" in sql:
        raise ValueError("unsupported SQL")
    sql = sql.replace("'ABC'='abc'", "1=1")  # Dashboards' probe for a case-insensitive database (ArcGIS Online is)
    sql = re.sub(r"(?i)\bINTERVAL\s*'(\d+):(\d+):(\d+)'\s*HOUR\s+TO\s+SECOND",
                 lambda m: str((int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])) * 1000), sql)
    sql = re.sub(r"(?i)\bINTERVAL\s*'(\d+)'\s*(DAY|HOUR|MINUTE|SECOND)", lambda m: str(int(m[1]) * UNIT_S[m[2].lower()] * 1000), sql)
    sql = re.sub(r"(?i)\b(?:timestamp|date)\s*'([^']*)'", lambda m: str(epoch_ms(m.group(1))), sql)
    sql = re.sub(r"(?<![A-Za-z0-9_'])N'", "'", sql)
    sql = re.sub(r"(?i)\bCURRENT_TIMESTAMP\b", str(int(time.time() * 1000)), sql)
    while True:
        m = re.search(r"(?i)\bEXTRACT\s*\(", sql)
        if not m:
            return sql
        j, depth = m.end(), 1
        while depth:
            depth += (sql[j] == "(") - (sql[j] == ")")
            j += 1
        part, expr = re.split(r"(?i)\s+FROM\s+", sql[m.end():j - 1], maxsplit=1)
        sql = sql[:m.start()] + f"CAST(strftime('{PARTS[part.strip().upper()]}', ({expr}) / 1000, 'unixepoch') AS INTEGER)" + sql[j:]


def split_top(text):
    """Split a comma list, leaving commas inside parentheses and quotes alone."""
    out, depth, quote, cur = [], 0, False, ""
    for c in text:
        if c == "'":
            quote = not quote
        elif not quote:
            depth += (c == "(") - (c == ")")
        if c == "," and depth == 0 and not quote:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    return [x for x in out + [cur.strip()] if x]


class Layer:
    def __init__(self, db, key, folder):
        self.key = key
        self.title, self.file, self.xy, self.cols = schema.FEEDS[key]
        self.name = self.title.replace(" ", "_") + "_preview"
        self.item = fake_id(self.title)
        self.url = f"{SERVICES}/{self.name}/FeatureServer"
        self.table = "t_" + key
        self.kinds = {c[0]: c[1] for c in self.cols}
        self.esri = schema.esri_fields(key)
        defs = ", ".join(f'"{c[0]}" {SQL[c[1]]}' + (" COLLATE NOCASE" if c[1] == "str" else "") for c in self.cols)
        db.execute(f'CREATE TABLE {self.table} ("{OID}" INTEGER PRIMARY KEY, {defs}, "_x" REAL, "_y" REAL)')
        path = os.path.join(folder, self.file)
        rows = []
        if os.path.exists(path):
            for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
                vals = [self.cast(r.get(c[0], ""), c[1]) for c in self.cols]
                x = y = None
                if self.xy and r.get("lon") not in (None, "") and r.get("lat") not in (None, ""):
                    x, y = arcgis_dashboard.mercator(float(r["lon"]), float(r["lat"]))
                rows.append(vals + [x, y])
        marks = ", ".join("?" * (len(self.cols) + 2))
        names = ", ".join(f'"{c[0]}"' for c in self.cols)
        db.executemany(f'INSERT INTO {self.table} ({names}, "_x", "_y") VALUES ({marks})', rows)
        self.count = len(rows)
        ext = db.execute(f'SELECT MIN(_x), MIN(_y), MAX(_x), MAX(_y) FROM {self.table}').fetchone()
        self.extent = dict(zip(("xmin", "ymin", "xmax", "ymax"), ext if ext[0] is not None else (0, 0, 0, 0)), spatialReference=SR)

    @staticmethod
    def cast(v, kind):
        if v is None or v == "":
            return None
        if kind == "float":
            return float(v)
        if kind == "int":
            return int(float(v))
        if kind == "date":
            return epoch_ms(v)
        return v

    def field(self, name):
        if name.lower() == OID.lower():
            return {"name": OID, "type": "esriFieldTypeOID", "alias": OID, "sqlType": "sqlTypeInteger", "domain": None, "defaultValue": None}
        for c in self.cols:
            if c[0].lower() == name.lower():
                return dict({"name": c[0]}, **self.esri[c[0]], domain=None, defaultValue=None)
        return None

    def info(self):
        fields = [dict(self.field(c[0]), nullable=True, editable=False) for c in self.cols]
        fields.append(dict(self.field(OID), nullable=False, editable=False))
        d = {
            "currentVersion": 12, "id": 0, "name": self.name, "type": "Feature Layer" if self.xy else "Table",
            "serviceItemId": self.item, "displayField": "", "description": "", "copyrightText": "", "defaultVisibility": True,
            "relationships": [], "isDataVersioned": False, "supportsStatistics": True, "supportsAdvancedQueries": True,
            "supportsExceedsLimitStatistics": True, "supportsValidateSql": False, "supportsCoordinatesQuantization": False,
            "supportsReturningQueryGeometry": False, "useStandardizedQueries": True,
            "advancedQueryCapabilities": {
                "supportsPagination": True, "supportsPaginationOnAggregatedQueries": True, "supportsQueryWithDistance": False,
                "supportsReturningQueryExtent": True, "supportsStatistics": True, "supportsOrderBy": True, "supportsDistinct": True,
                "supportsQueryWithResultType": False, "supportsSqlExpression": True, "supportsAdvancedQueryRelated": False,
                "supportsCountDistinct": True, "supportsPercentileStatistics": False, "supportsHavingClause": True,
                "supportsOutFieldSQLExpression": False, "supportsMaxRecordCountFactor": False, "supportsTopFeaturesQuery": False,
                "supportsDisjointSpatialRel": False, "supportsQueryWithCacheHint": False, "supportsQueryAnalytic": False,
                "supportsQueryBins": False, "supportsDefaultSR": True, "supportsLod": False, "supportsFullTextSearch": False},
            "hasAttachments": False, "htmlPopupType": "esriServerHTMLPopupTypeNone", "hasM": False, "hasZ": False,
            "objectIdField": OID, "uniqueIdField": {"name": OID, "isSystemMaintained": True}, "globalIdField": "", "typeIdField": "",
            "collation": {"locale": "neutral", "caseSensitive": False, "accentSensitive": True},
            "dateFieldsTimeReference": {"timeZone": "UTC", "timeZoneIANA": "Etc/UTC", "respectsDaylightSaving": False},
            "preferredTimeReference": None, "fields": fields, "indexes": [], "types": [], "templates": [],
            "supportedQueryFormats": "JSON", "hasStaticData": True, "maxRecordCount": MAX, "standardMaxRecordCount": 32000,
            "maxIdsCount": 1000000, "tileMaxRecordCount": 8000, "maxRecordCountFactor": 1, "capabilities": "Query",
        }
        if self.xy:
            d.update(geometryType="esriGeometryPoint", minScale=0, maxScale=0, extent=self.extent, spatialReference=SR,
                     drawingInfo={"renderer": {"type": "simple", "symbol": {
                         "type": "esriSMS", "style": "esriSMSCircle", "color": [11, 57, 84, 255], "size": 8,
                         "outline": {"color": [255, 255, 255, 255], "width": 1}}}, "transparency": 0})
        return d

    def service(self):
        entry = {"id": 0, "name": self.name, "parentLayerId": -1, "defaultVisibility": True, "subLayerIds": None,
                 "minScale": 0, "maxScale": 0}
        if self.xy:
            entry.update(type="Feature Layer", geometryType="esriGeometryPoint")
        return {"currentVersion": 12, "serviceItemId": self.item, "serviceDescription": "", "hasVersionedData": False,
                "supportsDisconnectedEditing": False, "hasStaticData": True, "maxRecordCount": MAX, "supportedQueryFormats": "JSON",
                "capabilities": "Query", "description": "", "copyrightText": "", "spatialReference": SR,
                "initialExtent": self.extent, "fullExtent": self.extent, "units": "esriMeters", "syncEnabled": False,
                "layers": [entry] if self.xy else [], "tables": [] if self.xy else [entry]}

    def item_info(self):
        return {"id": self.item, "owner": "crest_preview", "orgId": ORG, "created": 1795654329000, "modified": 1795655396000,
                "title": self.title, "type": "Feature Service", "name": self.name,
                "typeKeywords": ["ArcGIS Server", "Data", "Feature Access", "Feature Service", "Service", "Singlelayer", "Hosted Service"],
                "description": None, "tags": [], "snippet": None, "thumbnail": None, "extent": [], "spatialReference": "102100",
                "url": self.url, "access": "public", "size": 1, "culture": "english", "properties": None, "categories": []}

    # --- queries ---------------------------------------------------------------------------------------------
    def where(self, q):
        parts = [f"({to_sqlite((q.get('where') or '').strip() or '1=1')})"]
        ids = (q.get("objectIds") or "").strip()
        if ids:
            parts.append(f'"{OID}" IN ({",".join(str(int(i)) for i in ids.split(",") if i.strip())})')
        g = (q.get("geometry") or "").strip()
        if g and self.xy and (q.get("geometryType") or "esriGeometryEnvelope") == "esriGeometryEnvelope":
            try:
                if g.startswith("{"):
                    e = json.loads(g)
                    box, wkid = [e["xmin"], e["ymin"], e["xmax"], e["ymax"]], (e.get("spatialReference") or {}).get("wkid")
                else:
                    box, wkid = [float(v) for v in g.split(",")], None
                wkid = wkid or _wkid(q.get("inSR")) or 102100
                if wkid in (4326, 4269):
                    box = list(arcgis_dashboard.mercator(box[0], max(box[1], -85))) + list(arcgis_dashboard.mercator(box[2], min(box[3], 85)))
                parts.append(f"(_x >= {box[0]} AND _x <= {box[2]} AND _y >= {box[1]} AND _y <= {box[3]})")
            except (ValueError, KeyError, TypeError):
                pass
        return " AND ".join(parts)

    def order(self, q, allowed=None):
        o = (q.get("orderByFields") or "").strip()
        if not o:
            return ""
        out = []
        for part in o.split(","):
            bits = part.strip().split()
            if not bits or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bits[0]):
                continue
            if allowed is not None and bits[0].lower() not in allowed:
                continue
            out.append(f'"{bits[0]}" {"DESC" if len(bits) > 1 and bits[1].upper() == "DESC" else "ASC"}')
        return " ORDER BY " + ", ".join(out) if out else ""

    def query(self, db, q):
        where = self.where(q)
        true = lambda k: str(q.get(k, "")).lower() == "true"  # noqa: E731
        offset = int(q.get("resultOffset") or 0)
        limit = min(int(q.get("resultRecordCount") or MAX), 32000)
        stats = json.loads(q["outStatistics"]) if q.get("outStatistics") else None
        head = {"objectIdFieldName": OID, "uniqueIdField": {"name": OID, "isSystemMaintained": True}, "globalIdFieldName": ""}

        if stats:
            if stats[0]["statisticType"].lower() == "exceedslimit":   # the map asking whether the layer is too big to load whole
                name = stats[0].get("outStatisticFieldName") or "exceedslimit"
                return dict(head, fields=[{"name": name, "type": "esriFieldTypeInteger", "alias": name, "sqlType": "sqlTypeInteger",
                                           "domain": None, "defaultValue": None}], features=[{"attributes": {name: 0}}])
            # group by fields, or SQL expressions (date parts for a chart's time bins), which ArcGIS names EXPR_1, EXPR_2...
            groups, gsel, gfields = [], [], []
            for g in split_top(q.get("groupByFieldsForStatistics") or ""):
                if self.field(g):
                    groups.append(self.field(g)["name"])
                    gsel.append(f'"{groups[-1]}"')
                    gfields.append(self.field(g))
                else:
                    groups.append(f"EXPR_{sum(n.startswith('EXPR_') for n in groups) + 1}")
                    gsel.append(f'{to_sqlite(g)} AS "{groups[-1]}"')
                    gfields.append({"name": groups[-1], "type": "esriFieldTypeInteger", "alias": groups[-1], "sqlType": "sqlTypeOther",
                                    "domain": None, "defaultValue": None})
            sel, fields = [], []
            for s in stats:
                kind, on, name = s["statisticType"].lower(), s["onStatisticField"], s.get("outStatisticFieldName") or s["onStatisticField"]
                fn = {"count": "COUNT", "sum": "SUM", "min": "MIN", "max": "MAX", "avg": "AVG", "stddev": "STDEV", "var": "VARIANCE"}[kind]
                arg = "*" if on in ("*", "1") else f'"{on}"'
                sel.append(f'{fn}({arg}) AS "{name}"')
                src = self.field(on) or {}
                same = kind in ("min", "max") and src
                fields.append({"name": name, "alias": name, "domain": None, "defaultValue": None,
                               "type": src["type"] if same else "esriFieldTypeInteger" if kind == "count" else "esriFieldTypeDouble",
                               "sqlType": src["sqlType"] if same else "sqlTypeInteger" if kind == "count" else "sqlTypeFloat"})
            sql = f'SELECT {", ".join(gsel + sel)} FROM {self.table} WHERE {where}'
            if groups:
                sql += " GROUP BY " + ", ".join(f'"{g}"' for g in groups)
            if q.get("having"):
                sql += " HAVING " + to_sqlite(q["having"])
            if true("returnCountOnly"):
                return {"count": db.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]}
            sql += self.order(q)
            rows = db.execute(sql + f" LIMIT {limit + 1} OFFSET {offset}").fetchall()
            names = groups + [f["name"] for f in fields]
            return dict(head, fields=fields + gfields, exceededTransferLimit=len(rows) > limit,
                        features=[{"attributes": dict(zip(names, r))} for r in rows[:limit]])

        if true("returnCountOnly") and not true("returnDistinctValues"):
            return {"count": db.execute(f"SELECT COUNT(*) FROM {self.table} WHERE {where}").fetchone()[0]}
        if true("returnIdsOnly"):
            rows = db.execute(f'SELECT "{OID}" FROM {self.table} WHERE {where}{self.order(q)}').fetchall()
            return {"objectIdFieldName": OID, "objectIds": [r[0] for r in rows]}
        if true("returnExtentOnly"):
            e = db.execute(f"SELECT MIN(_x), MIN(_y), MAX(_x), MAX(_y), COUNT(*) FROM {self.table} WHERE {where}").fetchone()
            out = {"count": e[4]}
            if e[0] is not None:
                out["extent"] = dict(zip(("xmin", "ymin", "xmax", "ymax"), e[:4]), spatialReference=SR)
            return out

        want = [f.strip() for f in (q.get("outFields") or "").split(",") if f.strip()]
        if not want or "*" in want:
            names = [c[0] for c in self.cols] + [OID]
        else:
            names = [self.field(f)["name"] for f in want if self.field(f)]
        cols = ", ".join(f'"{n}"' for n in names)
        if true("returnDistinctValues"):
            sql = f"SELECT DISTINCT {cols} FROM {self.table} WHERE {where}"
            if true("returnCountOnly"):
                return {"count": db.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]}
            rows = db.execute(sql + self.order(q) + f" LIMIT {limit + 1} OFFSET {offset}").fetchall()
            return dict(head, fields=[self.field(n) for n in names], exceededTransferLimit=len(rows) > limit,
                        features=[{"attributes": dict(zip(names, r))} for r in rows[:limit]])
        geometry = self.xy and str(q.get("returnGeometry", "true")).lower() != "false"
        order = self.order(q) or f' ORDER BY "{OID}" ASC'
        rows = db.execute(f"SELECT {cols}, _x, _y FROM {self.table} WHERE {where}{order} LIMIT {limit + 1} OFFSET {offset}").fetchall()
        out_wkid = _wkid(q.get("outSR")) or 102100
        feats = []
        for r in rows[:limit]:
            f = {"attributes": dict(zip(names, r[:-2]))}
            if geometry and r[-2] is not None:
                f["geometry"] = {"x": r[-2], "y": r[-1]} if out_wkid not in (4326, 4269) else dict(zip(("x", "y"), _lonlat(r[-2], r[-1])))
            feats.append(f)
        out = dict(head)
        if self.xy:
            out.update(geometryType="esriGeometryPoint", spatialReference=SR if out_wkid not in (4326, 4269) else {"wkid": 4326, "latestWkid": 4326})
        out.update(fields=[self.field(n) for n in names], exceededTransferLimit=len(rows) > limit, features=feats)
        return out


def _wkid(v):
    if not v:
        return None
    v = str(v).strip()
    if v.startswith("{"):
        try:
            d = json.loads(v)
            return d.get("latestWkid") and 102100 if d.get("wkid") in (102100, 3857) else d.get("wkid")
        except ValueError:
            return None
    try:
        n = int(v)
    except ValueError:
        return None
    return 102100 if n == 3857 else n


def _lonlat(x, y):
    return x / 20037508.34 * 180.0, math.degrees(2 * math.atan(math.exp(y / 20037508.34 * math.pi)) - math.pi / 2)


class _Spread:
    """STDEV and VARIANCE for SQLite (sample statistics, as ArcGIS's stddev and var)."""
    root = True

    def __init__(self):
        self.v = []

    def step(self, x):
        if x is not None:
            self.v.append(float(x))

    def finalize(self):
        n = len(self.v)
        if n < 2:
            return None
        m = sum(self.v) / n
        var = sum((x - m) ** 2 for x in self.v) / (n - 1)
        return math.sqrt(var) if self.root else var


class _Variance(_Spread):
    root = False


class Preview:
    def __init__(self, site, theme="user"):
        self.site = os.path.abspath(site)
        self.theme = theme
        self.lock = threading.Lock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.create_aggregate("STDEV", 1, _Spread)
        self.db.create_aggregate("VARIANCE", 1, _Variance)
        self.layers = {k: Layer(self.db, k, os.path.join(self.site, "feeds")) for k in schema.FEEDS}
        self.by_name = {la.name.lower(): la for la in self.layers.values()}
        self.by_item = {la.item: la for la in self.layers.values()}
        self.map_id, self.dash_id = fake_id("web map"), fake_id("dashboard")
        self.log = []

    def refs(self):
        return {k: {"item": la.item, "url": la.url} for k, la in self.layers.items()}

    def ids(self):
        return {"dashboard": self.dash_id, "map": self.map_id, "items": sorted(list(self.by_item) + [self.map_id, self.dash_id]),
                "services": SERVICES, "names": sorted(self.by_name), "base": arcgis_dashboard.BASE,
                "open": f"https://www.arcgis.com/apps/dashboards/{self.dash_id}"}

    def built(self):
        importlib.reload(schema)
        d = importlib.reload(arcgis_dashboard)
        st = self.layers["stations"]
        e = st.extent
        lo, hi = _lonlat(e["xmin"], e["ymin"]), _lonlat(e["xmax"], e["ymax"])
        return d, d.webmap(self.refs(), (lo[0], lo[1], hi[0], hi[1])), d.dashboard(self.refs(), self.map_id, theme=self.theme)

    def item(self, iid, rest):
        """(status, body) for /sharing/rest/content/items/<iid><rest>."""
        if iid in self.by_item:
            la = self.by_item[iid]
            return (200, la.item_info()) if not rest else (200, {}) if rest == "/data" else (404, None)
        if iid not in (self.map_id, self.dash_id):
            return 404, None
        d, wm, dash = self.built()
        is_map = iid == self.map_id
        e = self.layers["stations"].extent
        box = [list(_lonlat(e["xmin"], e["ymin"])), list(_lonlat(e["xmax"], e["ymax"]))]
        if not rest:
            return 200, {
                "id": iid, "owner": "crest_preview", "orgId": ORG, "created": 1795654329000, "modified": int(time.time() * 1000),
                "title": d.MAP_TITLE if is_map else d.DASH_TITLE, "type": "Web Map" if is_map else "Dashboard", "name": None,
                "typeKeywords": (["ArcGIS Online", "Explorer Web Map", "Map", "Online Map", "Web Map"] if is_map else
                                 ["ArcGIS Dashboards", "Dashboard", "Operations Dashboard"]),
                "description": None, "tags": [], "snippet": None, "thumbnail": None, "extent": box if is_map else [],
                "spatialReference": None, "url": None, "access": "public", "size": 1, "culture": "en-us", "properties": None,
                "categories": []}
        if rest == "/data":
            return 200, wm if is_map else dash
        if rest == "/resources/themes/user.json" and not is_map:
            return 200, d.THEME
        if rest == "/resources":
            mine = [] if is_map or self.theme != "user" else [
                {"resource": "themes/user.json", "created": 1795654329000, "size": len(json.dumps(d.THEME)), "access": "inherit"}]
            return 200, {"total": len(mine), "start": 1, "num": len(mine), "nextStart": -1, "resources": mine}
        return 404, None

    def feature(self, name, rest, q):
        la = self.by_name.get(name.lower())
        if la is None:
            return 404, None
        if rest in ("", "/"):
            return 200, la.service()
        if rest == "/layers":
            return 200, {"layers": [la.info()] if la.xy else [], "tables": [] if la.xy else [la.info()]}
        if rest == "/0":
            return 200, la.info()
        if rest == "/0/query":
            with self.lock:
                try:
                    return 200, la.query(self.db, q)
                except Exception as e:  # what ArcGIS answers to a query it can't run
                    return 200, {"error": {"code": 400, "message": "Unable to complete operation.", "details": [f"{type(e).__name__}: {e}"]}}
        return 200, {"error": {"code": 400, "message": "Invalid URL", "details": [rest]}}


def handler(pv):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def send(self, status, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def params(self):
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                body = self.rfile.read(n).decode("utf-8", "replace")
                q.update({k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()})
            return u.path, q

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            path, q = self.params()
            if path == "/ids":
                return self.send(200, pv.ids())
            if path == "/log":
                return self.send(200, pv.log[-int(q.get("n", 200)):])
            m = re.match(r"^/sharing/rest/content/items/([0-9a-f]{32})(/.*)?$", path)
            if m:
                status, body = pv.item(m.group(1), (m.group(2) or "").rstrip("/"))
                return self.send(status, body if body is not None else {"error": {"code": 404, "message": "Not found", "details": []}})
            m = re.match(r"^/[^/]+/arcgis/rest/services/([^/]+)/FeatureServer(/.*)?$", path)
            if m:
                t0 = time.time()
                status, body = pv.feature(m.group(1), (m.group(2) or "").rstrip("/"), q)
                if (m.group(2) or "").endswith("/query"):
                    n = len(body.get("features", [])) if isinstance(body, dict) and "features" in body else body
                    pv.log.append({"layer": m.group(1), "ms": round((time.time() - t0) * 1000),
                                   "q": {k: v for k, v in q.items() if k not in ("f", "token") and v not in ("", None)},
                                   "answer": n if isinstance(n, int) else {k: v for k, v in body.items() if k in ("count", "error", "objectIds")}})
                return self.send(status, body if body is not None else {"error": {"code": 404, "message": "Not found", "details": []}})
            if path.startswith("/site/"):
                f = os.path.normpath(os.path.join(pv.site, path[len("/site/"):] or "index.html"))
                if os.path.isdir(f):
                    f = os.path.join(f, "index.html")
                if f.startswith(pv.site) and os.path.isfile(f):
                    return self.send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
            return self.send(404, {"error": {"code": 404, "message": "Not found", "details": [path]}})
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True, help="a built CREST site (the folder with index.html and feeds/)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--theme", default="user", choices=["user", "light"])
    a = ap.parse_args(argv)
    pv = Preview(a.site, a.theme)
    for k, la in pv.layers.items():
        print(f"{la.title}: {la.count} rows, item {la.item}")
    print("dashboard:", pv.ids()["open"])
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), handler(pv))
    print(f"serving on http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
