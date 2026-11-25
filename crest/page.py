"""Write CREST's pages: index.html (map) and crest_<st>.html, each self-contained (Highcharts, Leaflet,
logos, styles and the default period's data inlined; other periods load from data/ beside the page).
No CDN: Derek's browser hung on CDN scripts, and the pages must work inside VIMS's iframes."""
import base64
import json
import os

from stormsense import stations as S

HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.join(HERE, "templates")
JSLIB = os.path.join(S.DATA, "jslib")
DISCLAIMER = ("StormSense(TM) Predictive Plots are computer-generated operational storm tide forecasts; "
              "the user assumes any and all risk.")


def _read(*p):
    return open(os.path.join(*p), encoding="utf-8").read()


def _uri(path):
    return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()


def _js(obj):
    # safe inside <script>: no "</" sequences
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


def _common():
    return {
        "__CSS__": _read(T, "crest.css"),
        "__SS_LOGO__": _uri(os.path.join(HERE, "branding", "stormsense_logo.png")),
        "__VIMS_LOGO__": _uri(os.path.join(HERE, "branding", "vims_logo.png")),
        "__HIGHCHARTS__": _read(JSLIB, "highcharts.js"),
        "__COMMON__": _read(T, "common.js"),
        "__DISCLAIMER__": DISCLAIMER,
    }


def _fill(template, subs):
    # longest keys first so __HIGHCHARTS__ never eats __HIGHCHARTS_MORE__
    for k in sorted(subs, key=len, reverse=True):
        template = template.replace(k, subs[k])
    return template


def write_site(out, manifest, table):
    com = _common()
    default = manifest["default"]
    ids = [p["id"] for p in manifest["periods"]]
    pid = default if default in ids else (ids[0] if ids else None)
    if pid is None:
        print("no periods built; no pages")
        return
    st_t = _read(T, "station.html")
    more = _read(JSLIB, "highcharts-more.js")
    n = 0
    for st in manifest["stations"]:
        # the page opens on the default period if the station has it, else its newest period
        have = [p["id"] for p in manifest["periods"] if st in p["stations"]]
        if not have:
            continue
        use = pid if pid in have else have[0]
        doc = json.load(open(os.path.join(out, "data", f"{st}_{use}.json")))
        html = _fill(st_t, dict(com, __TITLE__=f"StormSense CREST: {doc['name']}", __HIGHCHARTS_MORE__=more,
                                __MANIFEST__=_js(manifest), __DOC__=_js(doc)))
        open(os.path.join(out, f"crest_{st.lower()}.html"), "w", encoding="utf-8").write(html)
        n += 1
    net_id = pid
    net = json.load(open(os.path.join(out, "data", f"network_{net_id}.json")))
    html = _fill(_read(T, "index.html"), dict(com, __MANIFEST__=_js(manifest), __NET__=_js(net),
                                               __LEAFLET__=_read(HERE, "jslib", "leaflet.js"),
                                               __LEAFLET_CSS__=_read(HERE, "jslib", "leaflet.css"),
                                               __COAST__=_read(HERE, "branding", "coast.geojson")))
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
    print(f"pages: index.html and {n} station pages -> {out}")
