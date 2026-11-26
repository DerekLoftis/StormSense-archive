"""The StormSense CREST dashboard in ArcGIS Online: a web map of the stations and an ArcGIS Dashboards
item on the hosted layers that arcgis_push.py loads, so the forecast archive can be browsed in ArcGIS
(and shown at the Esri UC).

    webmap(layers, extent)        the web map's JSON: stations colored by the published forecast's skill
    dashboard(layers, map_id)     the dashboard's JSON (ArcGIS Dashboards 5.x)
    ensure(portal, folder, ...)   create both in the "StormSense CREST" folder if they are not there

What the dashboard does: clicking a station on the map (or picking it in the header) filters
everything to that gauge; "Guidance" picks whose forecasts are listed and scored; "Lead time" picks
the hours the skill numbers cover. Tabs: the forecast archive (every forecast issued, newest first,
and the chosen forecast charted against the gauge by its CREST page), skill by lead time, skill over
time, floods, the station's full CREST report, and notes.

The two items are created once and then left alone, so edits made in ArcGIS are kept; set
CREST_DASHBOARD=rebuild (the workflow's "dashboard" input) to write them again from this file.
Sharing is left to the owner: share the dashboard, the map and the layers the same way.

The JSON follows what ArcGIS Dashboards itself saves (version 5.2.0); tools/dashboard_preview.py opens
it in the real Dashboards app against a local copy of the feeds to check it before it is published.
What that showed, for whoever edits this file:
  - a list, table or tile with Arcade on shows only what its expression returns;
  - Arcade's Text(10.18, '0.00') gives "0.18": number patterns need '#,##0.00';
  - date fields print in the viewer's time zone unless Arcade formats them (ToUTC);
  - a chart split by a field colors a series by its id, which must be the field's value (member_name);
  - a panel can wait for a selection from one control only ("requiresSelection"), not from either of two;
  - the header's logo is at most 48 px square, so the logos are in a panel of their own;
  - a custom theme is the item's resource themes/user.json.
"""
import json
import math
import os
import uuid

from . import schema

BASE = os.environ.get("CREST_BASE_URL", "https://derekloftis.github.io/StormSense-archive/crest/")
MAP_TITLE = "StormSense CREST Map"
DASH_TITLE = "StormSense CREST Forecast Archive"
STATIONS_LAYER = "crest-stations"   # the stations layer's id inside the web map
DISCLAIMER = ("StormSense(TM) Predictive Plots are computer-generated operational storm tide forecasts; "
              "the user assumes any and all risk.")
NS = uuid.UUID("5c7e2f6a-0d4b-4a59-9d0e-3b1f6c2a7e10")

NAVY, INK, MUTED, ACCENT = "#0b3954", "#0f2230", "#566a78", "#00a9b5"
GOOD, FAIR, POOR, NONE = "#1f7a4d", "#e8a33d", "#b3261e", "#93a1ab"
WARN = "#8a5a00"   # "fair" as text: the map's amber is too light to read on white
FLOOD = {"Action": "#8a5a00", "Minor": "#b35300", "Moderate": "#c2410c", "Major": "#b0174f"}   # NWS categories, as text
SEGMENTS = [f"{lo:02d}-{lo + 6:02d} h" for lo in range(0, 72, 6)]


def wid(name):
    return str(uuid.uuid5(NS, name))


def rgb(hex_color, alpha=255):
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [alpha]


def mercator(lon, lat):
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) * 20037508.34 / math.pi
    return x, y


# --- the web map -------------------------------------------------------------------------------------------
def _marker(color, size=11):
    return {"type": "esriSMS", "style": "esriSMSCircle", "color": rgb(color), "size": size, "angle": 0, "xoffset": 0,
            "yoffset": 0, "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [255, 255, 255, 255], "width": 1.5}}


RATING = """var s = $feature.S_primary;
if ($feature.suspect_obs == 1) { return "Suspect gauge"; }
if (IsEmpty(s)) { return "Not scored yet"; }
if (s >= 0.75) { return "Good"; }
if (s >= 0.45) { return "Fair"; }
return "No skill";"""

POPUP = """<p>{verdict}</p>
<p><strong>Skill S over 0-72 hours, last 30 days</strong><br>Published forecast {S_primary} · StormSense model {S_stormsense} · P-ETSS {S_petss} · NWS {S_nws} · Tidewatch {S_tidewatch}</p>
<p>Best guidance: {best_name}<br>Forecasts scored: {runs} · Floods observed: {events} · Gauge record: {obs_coverage_pct}%</p>
<p><a href="{crest_url}" target="_blank" rel="noopener">Open the CREST station report</a> · <a href="{forecast_url}" target="_blank" rel="noopener">Latest StormSense forecast</a></p>"""


def webmap(layers, extent):
    """extent: (lon min, lat min, lon max, lat max) of the stations."""
    st = layers["stations"]
    x0, y0 = mercator(extent[0], extent[1])
    x1, y1 = mercator(extent[2], extent[3])
    px, py = 0.08 * (x1 - x0) + 2000, 0.08 * (y1 - y0) + 2000
    infos = [{"fieldName": name, "label": label, "visible": name in ("name", "station", "best_name", "runs", "events"),
              "isEditable": False, **({"format": {"places": 3, "digitSeparator": False}} if name.startswith("S_") else {}),
              **({"format": {"places": 0, "digitSeparator": False}} if name in ("obs_coverage_pct", "cone_pct") else {})}
             for name, _, label, *_ in schema.STATIONS]
    renderer = {
        "type": "uniqueValue", "valueExpression": RATING, "valueExpressionTitle": "Published forecast, skill S over 0-72 h",
        "defaultSymbol": _marker(NONE, 9), "defaultLabel": "Other",
        "uniqueValueInfos": [
            {"value": "Good", "label": "Good (S 0.75 or more)", "symbol": _marker(GOOD)},
            {"value": "Fair", "label": "Fair (S 0.45 to 0.75)", "symbol": _marker(FAIR)},
            {"value": "No skill", "label": "No skill (S under 0.45)", "symbol": _marker(POOR)},
            {"value": "Suspect gauge", "label": "Suspect gauge (scores not trusted)", "symbol": _marker(NONE, 9)},
            {"value": "Not scored yet", "label": "Not scored yet", "symbol": _marker("#c9d2d8", 8)},
        ]}
    label = {
        "labelExpressionInfo": {"expression": "$feature.station"}, "labelPlacement": "esriServerPointLabelPlacementAboveRight",
        "minScale": 1200000, "maxScale": 0, "useCodedValues": True, "deconflictionStrategy": "static",
        "symbol": {"type": "esriTS", "color": rgb(INK), "haloColor": [255, 255, 255, 255], "haloSize": 1.5,
                   "font": {"family": "Arial", "size": 9, "weight": "bold", "style": "normal", "decoration": "none"}}}
    return {
        "operationalLayers": [{
            "id": STATIONS_LAYER, "title": "StormSense stations", "url": st["url"].rstrip("/") + "/0", "itemId": st["item"],
            "layerType": "ArcGISFeatureLayer", "opacity": 1, "visibility": True, "showLabels": True, "disablePopup": False,
            "layerDefinition": {"minScale": 0, "maxScale": 0, "drawingInfo": {"renderer": renderer, "labelingInfo": [label]}},
            "popupInfo": {"title": "{name} ({station})", "description": POPUP, "fieldInfos": infos, "showAttachments": False,
                          "mediaInfos": [], "popupElements": [{"type": "text", "text": POPUP}]},
        }],
        "baseMap": {"title": "Light Gray Canvas", "baseMapLayers": [
            {"id": "light-gray-base", "title": "Light Gray Base", "layerType": "VectorTileLayer", "opacity": 1, "visibility": True,
             "styleUrl": "https://cdn.arcgis.com/sharing/rest/content/items/291da5eab3a0412593b66d384379f89f/resources/styles/root.json"},
            {"id": "light-gray-reference", "title": "Light Gray Reference", "layerType": "VectorTileLayer", "opacity": 1,
             "visibility": True, "isReference": True,
             "styleUrl": "https://cdn.arcgis.com/sharing/rest/content/items/1768e8369a214dfab4e2167d5c5f2454/resources/styles/root.json"}]},
        "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        "initialState": {"viewpoint": {"targetGeometry": {
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
            "xmin": x0 - px, "ymin": y0 - py, "xmax": x1 + px, "ymax": y1 + py}}},
        "authoringApp": "StormSense CREST", "authoringAppVersion": "1", "timeZone": "system", "version": "2.31",
    }


# --- dashboard pieces ----------------------------------------------------------------------------------------
def dataset(item, layer=0, fields=("*",), group=(), stats=(), order=(), limit=None, where=None):
    d = {"type": "serviceDataset", "name": "main", "dataSource": {"type": "layerDataSource", "itemId": item, "layerId": layer}}
    if where:
        d["filter"] = {"type": "filterGroup", "condition": "OR",
                       "rules": [{"type": "filterGroup", "condition": "AND", "rules": list(where)}]}
    d.update(groupByFields=list(group), orderByFields=list(order),
             statisticDefinitions=[{"onStatisticField": f, "statisticType": t, "outStatisticFieldName": n} for f, t, n in stats])
    if limit:
        d["maxFeatures"] = limit
    d.update(outFields=list(fields), returnDistinctValues=False, allowSourceDownload=False, allowSummaryDownload=False)
    return d


def eq(field, value, kind="string"):
    return {"type": "filterRule", "field": {"name": field, "type": kind}, "operator": "equal",
            "constraint": {"type": "value", "value": value}}


def among(field, values, kind="string"):
    return {"type": "filterRule", "field": {"name": field, "type": kind}, "operator": "is_in",
            "constraint": {"type": "values", "values": list(values)}}


def widget(name, kind, **more):
    return {"id": wid(name), "dashboardItemType": "widget", "name": name, "noDataState": {}, "noFilterState": {},
            "focusModeEnabled": False, "type": kind, **more}


def number(digits, least=None, prefix=False):
    f = {"name": "value", "prefix": prefix, "style": "decimal", "useGrouping": True, "maximumFractionDigits": digits}
    if least is not None:
        f["minimumFractionDigits"] = least
    return f


def targets(names, field=None, source="station", require=False):
    """Filter targets (widgets by name) for an action; field = the target layers' column to match."""
    out = []
    for n in names:
        t = {"targetId": n if "#" in n else wid(n) + "#main", "by": "whereClause", "requiresSelection": require}
        if field:
            t["fieldMap"] = [{"sourceName": source, "targetName": field}]
        out.append(t)
    return out


# Arcade ("advanced formatting"): what a tile, list row or table cell shows, computed from its data.
# Number patterns need '#,##0': Arcade's Text(10.18, '0.00') gives "0.18" (a 0 is one digit, no more).
TONE = f"""function tone(s) {{
  return IIf(IsEmpty(s), '{MUTED}', IIf(s >= 0.75, '{GOOD}', IIf(s >= 0.45, '{WARN}', '{POOR}')));
}}
function num(v, pattern) {{
  return IIf(IsEmpty(v), '–', Text(v, pattern));
}}
function signed(v, pattern) {{
  if (IsEmpty(v)) {{ return '–'; }}
  var t = Text(Abs(v), pattern);
  if (Number(Replace(t, ',', '')) == 0) {{ return t; }}
  return IIf(v > 0, '+', '-') + t;
}}
"""


def indicator(name, ds, label, caption, digits, least=None, arcade=None):
    """A number tile. arcade: Arcade statements that set `value` (the text of the number) and may set `color`
    and `note` (the line under it); with Arcade on, the tile shows only what the expression returns."""
    sizes = {"top": 24, "middle": 46, "bottom": 30}
    w = widget(name, "indicatorWidget", datasets=[ds], defaultSettings={
        "topSection": {"maxHeight": sizes["top"], "textInfo": {"text": label, "weight": "normal"}},
        "middleSection": {"maxHeight": sizes["middle"], "textInfo": {"text": "{calculated/value}", "weight": "bold", "fillColor": NAVY}},
        "bottomSection": {"maxHeight": sizes["bottom"], "textInfo": {"text": caption, "weight": "normal"}}},
        verticalAlignment="middle", comparison="none", valueFormat=number(digits, least),
        percentageFormat={"name": "percentage", "prefix": False, "style": "percent", "useGrouping": True},
        ratioFormat={"name": "ratio", "prefix": False, "style": "decimal", "useGrouping": True, "maximumFractionDigits": 2},
        valueType="statistic", noValueState={}, showFeatureMenu=False)
    if arcade:
        w.update(arcadeEnabled=True, expression=TONE + f"""var color = '{NAVY}';
var note = '{caption}';
var value = '';
{arcade}
return {{
  topText: '{label}', topTextColor: '', topTextOutlineColor: '', topTextWeight: 'normal', topTextMaxHeight: {sizes["top"]},
  middleText: value, middleTextColor: color, middleTextOutlineColor: '', middleTextWeight: 'bold', middleTextMaxHeight: {sizes["middle"]},
  bottomText: note, bottomTextColor: '', bottomTextOutlineColor: '', bottomTextWeight: 'normal', bottomTextMaxHeight: {sizes["bottom"]}
}};""")
    return w


def _text(angle=0, weight="normal", text=None, visible=True):
    c = {"type": "esriTS", "angle": angle, "font": {"family": "inherit", "style": "normal", "weight": weight}}
    if text:
        c["text"] = text
    return {"type": "chartText", "visible": visible, "content": c}


def _line(width=1, color=None, style="esriSLSSolid"):
    s = {"type": "esriSLS", "style": style, "width": width}
    if color:
        s["color"] = color
    return s


DATE_FORMAT = {"type": "date", "intlOptions": {}, "formatPerDateTimeUnit": {
    "year": {"year": "numeric"}, "month": {"month": "short"}, "day": {"day": "numeric", "month": "short"},
    "hour": {"minute": "2-digit", "hour": "2-digit", "hour12": False},
    "minute": {"minute": "2-digit", "hour": "2-digit", "hour12": False},
    "second": {"second": "2-digit", "minute": "2-digit", "hour": "2-digit", "hour12": False}}}


def guide(value, text, color, label=True):
    g = {"type": "chartGuide", "name": text, "visible": True, "start": value, "above": False,
         "style": _line(1.5, rgb(color, 200), "esriSLSDash")}
    if label:
        g["label"] = {"type": "esriTS", "text": text, "color": rgb(color), "horizontalAlignment": "right",
                      "font": {"family": "inherit", "size": 9, "style": "normal", "weight": "normal"}}
    return g


def skill_guides(label=True):
    return [guide(0.75, "good 0.75", GOOD, label), guide(0.45, "no skill 0.45", POOR, label)]


def axes(title_x, title_y, date=False, rotated=False, limit=40, lo=0, hi=1, guides=()):
    x = {"type": "chartAxis", "visible": True, "title": _text(270 if rotated else 0, "bold", title_x, bool(title_x)),
         "valueFormat": DATE_FORMAT if date else {"type": "category", "characterLimit": limit},
         "lineSymbol": _line(), "labels": _text(), "grid": _line(0), "guides": [],
         "scrollbar": {"visible": False, "width": 15, "gripSize": 22}}
    y = {"type": "chartAxis", "visible": True, "title": _text(0 if rotated else 270, "bold", title_y, bool(title_y)),
         "valueFormat": {"type": "number", "intlOptions": {"style": "decimal", "notation": "standard",
                                                           "minimumFractionDigits": 0, "maximumFractionDigits": 2}},
         "minimum": lo, "maximum": hi, "lineSymbol": _line(), "labels": _text(), "grid": _line(1), "guides": list(guides),
         "isLogarithmic": False, "buffer": lo is None}
    return [x, y]


def line_series(key, x, color, width=2.0, days=False, y=None):
    s = {"type": "lineSeries", "id": key, "name": key, "x": x, "dataLabels": _text(visible=False),
         "dataTooltipVisible": True, "dataTooltipReverseColor": True, "y": y or key}
    if days:
        s.update(binTemporalData=True, temporalBinning={"size": 1, "unit": "days", "nullPolicy": "null"})
    s.update(lineSmoothed=False, lineSymbol=_line(width, rgb(color)), markerVisible=True,
             markerSymbol={"type": "esriSMS", "style": "esriSMSCircle", "color": rgb(color), "size": 5,
                           "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": rgb(color), "width": 0}},
             showArea=False, areaColor=rgb(color, 40), connectLines=True)
    return s


def chart(name, title, ds, series, ax, legend=True, rotated=False, split=None, dates=False, labels=number(3, 3),
          order=None, crosshair=False, **more):
    w = widget(name, "serialChartWidget", title=title, datasets=[ds], actionMode="monoSelection", categoryType="groupByValues",
               valueFormat=number(3, 3), labelFormat=dict(labels, name="label"),
               category={"labelOverrides": [], "nullLabel": "none", "blankLabel": "none"},
               parseDates=dates, minPeriod="DD", categoryAxisLabelsBehavior="wrap", **more)
    if split:
        w["splitBy"] = {"fieldName": split[0], "missingSeriesTemplate": split[1]}
    w["chartConfig"] = {
        "version": "25.2.0", "type": "chart", "orderOptions": order or {}, "colorMatch": False, "axes": ax, "series": series,
        "legend": {"type": "chartLegend", "visible": legend, "body": _text()["content"], "position": "bottom"},
        "horizontalAxisLabelsBehavior": "wrap", "verticalAxisLabelsBehavior": "hide", "rotated": rotated,
        "cursorCrosshair": {"type": "cursorCrosshair", "verticalLineVisible": crosshair, "horizontalLineVisible": False},
        "stackedType": "sideBySide"}
    return w


def column(title, field, stat=None, digits=None, align="right"):
    c = {"title": title, "fieldName": field, "textAlign": align, "isBold": False, "isItalic": False, "isUnderlined": False}
    if stat:
        c["outStatisticFieldName"] = stat
        c["valueFormat"] = number(digits, digits)
    return c


def cells(spec):
    """The Arcade return for a table: spec = [(cell key, text expression, align, text color expression, fill expression)]."""
    rows = [f"    {key}: {{ displayText: {text}, textColor: {color or EMPTY}, backgroundColor: {fill or EMPTY}, textAlign: '{align}', "
            f"iconName: '', iconAlign: '', iconColor: '', iconOutlineColor: '' }}" for key, text, align, color, fill in spec]
    return "return {\n  cells: {\n" + ",\n".join(rows) + "\n  }\n};"


EMPTY = "''"


def table(name, title, ds, settings, columns, arcade=None, **more):
    w = widget(name, "tableWidget", title=title, datasets=[ds], dataSettings=settings, tableSettings={
        "layout": "fit-data-stretch", "scale": "medium", "hoverText": True, "rowStriping": True, "selectionMode": "single",
        "verticalGrid": {"thickness": 0}, "horizontalGrid": {"thickness": 1}, "headerSettings": {"rule": {"thickness": 2}}},
        columnSettings=columns, editingEnabled=False, editColumnPlacement="end", **more)
    if arcade:
        w.update(arcadeEnabled=True, expression=TONE + arcade)
    return w


def listing(name, title, ds, html, arcade=None, **more):
    w = widget(name, "listWidget", title=title, datasets=[ds], selectionMode="single", iconType="none", showFilter=False,
               text=html, **more)
    if arcade:
        w.update(arcadeEnabled=True, expression=arcade)
    return w


def rich(name, html, **more):
    return widget(name, "richTextWidget", datasets=[], text=html, valueType="none", showFeatureMenu=False, **more)


def embed(name, ds, url, **more):
    return widget(name, "embeddedContentWidget", datasets=[ds], url=url, contentType="document", imageRefreshInterval=0,
                  videoSettings={"controls": True, "autoplay": False, "loop": False, "muted": False, "controlsList": "nodownload"},
                  showFeatureMenu=False, **more)


def static_selector(name, label, values, field, to, default="1", bar=False):
    return {"id": wid(name), "dashboardItemType": "selector", "name": name, "noDataState": {}, "noFilterState": {},
            "focusModeEnabled": False, "label": label,
            "events": [{"type": "selectionChanged", "actions": [
                {"type": "filter", "targets": targets(to, field, "filterField")}]}],
            "datasets": [{"type": "staticDataset", "name": "main", "data": {
                "type": "staticValues", "dataType": "string",
                "values": [{"type": "labelledValue", "id": str(i + 1), "label": lab, "value": val}
                           for i, (lab, val) in enumerate(values)]}}],
            "type": "categorySelectorWidget", "category": {"type": "static"},
            "selection": {"type": "single", "operator": "equal", "requireDefault": True, "defaultSelection": default},
            "showFilter": False, "displayType": "button_bar" if bar else "list", "maxSize": "compact",
            "presentationMode": "inline" if bar else "dropdown"}


# layout
def cell(name, w=1.0, h=1.0, tab=None):
    e = {"type": "itemLayoutElement", "id": wid(name), "width": w, "height": h}
    if tab:
        e["tabName"] = tab
    return e


def box(key, orientation, elements, w=1.0, h=1.0, tab=None, kind="stackLayoutElement"):
    """orientation "col": elements side by side; "row": elements stacked top to bottom."""
    e = {"type": kind, "id": wid("layout " + key), "orientation": orientation, "elements": elements, "width": w, "height": h}
    if tab:
        e["tabName"] = tab
    return e


def tabs(key, elements, w=1.0, h=1.0):
    return {"type": "tabsLayoutElement", "id": wid("layout " + key), "elements": elements, "width": w, "height": h}


THEME = {
    "colorScheme": {
        "id": "light", "type": "custom", "mode": "light", "brandColor": NAVY, "brandHoverColor": "#072a3f", "brandPressColor": "#051f2f",
        "backgroundColor": "#f3f6f8", "foregroundColor1": "#ffffff", "foregroundColor2": "#f3f6f8", "foregroundColor3": "#e9f0f4",
        "textColor1": INK, "textColor2": "#3c5261", "textColor3": MUTED, "textInverseColor": "#ffffff", "textLinkColor": "#00727b",
        "borderColor1": "#c5d2db", "borderColor2": "#d6e0e7", "borderColor3": "#e3eaef", "borderInputColor": "#8fa3b0",
        "statusInfoColor": "#00727b", "statusSuccessColor": GOOD, "statusWarningColor": "#9a6400", "statusDangerColor": POOR,
        "statusDangerHoverColor": "#8f1e18", "statusDangerPressColor": "#701712",
        "panelHeaderBackgroundColor": "#ffffff", "panelHeadingTextColor": INK},
    "corners": "soft", "spacing": "default", "fontFamily": "Avenir Next", "headingFontFamily": "Avenir Next", "headingScaleFactor": 1,
}

P = '<p style="margin:0 0 10px 0">'
ABOUT = f"""<h3 style="margin:0 0 10px 0">StormSense CREST: Chart Review and Evaluation Statistics Tool</h3>
{P}Every 72-hour storm tide forecast that StormSense archives is scored against the water level the gauge then observed: the published StormSense forecast and the guidance behind it (the StormSense model, NWS P-ETSS and ETSS, the NWS official forecast, NOAA STOFS and VIMS Tidewatch). This dashboard reads the same numbers as the CREST station reports and is rebuilt every day.</p>
{P}<strong>How to use it.</strong> Click a station on the map, or pick it under <em>Station</em>, and every panel follows that gauge; click an empty part of the map (or clear <em>Station</em>) to return to all stations. <em>Guidance</em> chooses whose forecasts are listed and scored in the tiles. <em>Lead time</em> chooses the hours of each forecast that the skill numbers cover.</p>
{P}<strong>Forecast archive.</strong> Every forecast issued in the last 30 days, newest first, and within one issue time the highest water relative to the NWS Action stage first. Each row gives the forecast's own peak and the flood category it reaches, then how it verified. Click a row to chart that forecast against the gauge. The dot is the forecast's skill: green good, amber fair, red no skill, gray not verified yet.</p>
{P}<strong>Skill S</strong> = 0.25 [R² + e<sup>−|Bias|/σ</sup> + e<sup>−MAE/σ</sup> + e<sup>−RMSE/σ</sup>], where σ is the standard deviation of the observations in the scored window. S runs from 0 to 1: about 0.45 is no skill, 0.75 and above is good. Bias is forecast minus observed. Lead time counts from the gauge's last observation when the forecast was issued.</p>
{P}<strong>Floods</strong> are the times the observed water reached the NWS Action stage or higher at gauges with NWS flood categories. Peak error is the forecast peak minus the observed peak, averaged over the forecasts that covered it. Times are UTC and water levels are feet above MLLW.</p>
{P}__DISCLAIMER__</p>
{P}<a href="__BASE__" target="_blank" rel="noopener">CREST station reports</a> · <a href="https://www.vims.edu/people/loftis_jd/StormSense/" target="_blank" rel="noopener">StormSense at VIMS</a> · <a href="https://www.vims.edu/ccrm/research/modeling/" target="_blank" rel="noopener">W&amp;M VIMS Center for Coastal Resources Management</a></p>"""

BRAND = """<p style="text-align:center;margin:0"><a href="https://www.vims.edu/people/loftis_jd/StormSense/" target="_blank" rel="noopener"><img src="__BASE__img/stormsense_logo.png" alt="StormSense: forecasting flooding from storm surge, rain, and tides" style="height:44px;width:153px"></a>&nbsp; &nbsp; &nbsp;<a href="https://www.vims.edu/ccrm/research/modeling/" target="_blank" rel="noopener"><img src="__BASE__img/ccrm_logo.png" alt="William &amp; Mary VIMS, Batten School of Coastal &amp; Marine Sciences, Center for Coastal Resources Management" style="height:44px;width:133px"></a></p>"""


def guidance_names():
    """{member id: the name the feeds give it (member_name)} for the main guidance."""
    from .stats import NAMES
    return {m: NAMES.get(m, m) for m in schema.MAIN}


def dashboard(layers, map_id, base=BASE, theme="light", names=None):
    """layers: {key: {"item": item id, "url": service url}} for schema.FEEDS' keys; map_id: the web map's item id.
    theme: "light", or "user" once the item's themes/user.json resource (THEME) is in place."""
    st, lb, dec, fc, fl, ch = (layers[k]["item"] for k in ("stations", "leaderboard", "decay", "forecasts", "floods", "changes"))
    names = dict(names or {})
    if any(m not in names for m in schema.MAIN):
        names = {**guidance_names(), **names}
    guidance = [(names[m], m) for m in schema.MAIN]
    last30 = eq("period", "30d")
    main = among("member", schema.MAIN)

    # which panels follow which control
    by_station = ["Forecasts scored", "Mean skill", "Mean RMSE", "Mean bias", "Floods observed", "Band coverage",
                  "Skill by guidance", "Forecast list", "Forecast chart", "Skill by lead time", "Leaderboard table",
                  "Skill over time", "Flood table", "Station report"]
    by_guidance = ["Forecasts scored", "Mean skill", "Mean RMSE", "Mean bias", "Forecast list", "Forecast chart"]
    by_band = ["Mean skill", "Mean RMSE", "Mean bias", "Skill by guidance", "Leaderboard table"]

    w = []
    # tiles
    w.append(indicator("Forecasts scored", dataset(fc, stats=[("cycle", "count", "N")], fields=["cycle"]),
                       "Forecasts archived", "in the last 30 days", 0))
    w.append(indicator("Mean skill", dataset(lb, stats=[("S", "avg", "AVG_S")], fields=["S"], where=[last30]),
                       "Mean skill S", "0.75 and up is good", 3, 3, arcade="""var s = $datapoint.AVG_S;
value = num(s, '#,##0.000');
color = tone(s);
note = IIf(IsEmpty(s), 'nothing scored yet', IIf(s >= 0.75, 'good (0.75 and up)', IIf(s >= 0.45, 'fair (0.45 to 0.75)', 'no skill (under 0.45)')));"""))
    w.append(indicator("Mean RMSE", dataset(lb, stats=[("RMSE", "avg", "AVG_RMSE")], fields=["RMSE"], where=[last30]),
                       "RMSE (ft)", "root-mean-square error", 2, 2))
    w.append(indicator("Mean bias", dataset(lb, stats=[("Bias", "avg", "AVG_BIAS")], fields=["Bias"], where=[last30]),
                       "Bias (ft)", "forecast minus observed", 2, 2, arcade="value = signed($datapoint.AVG_BIAS, '#,##0.00');"))
    w.append(indicator("Floods observed", dataset(fl, stats=[("station", "count", "N")], fields=["station"]),
                       "Floods observed", "NWS Action or higher", 0))
    w.append(indicator("Band coverage", dataset(st, stats=[("cone_pct", "avg", "AVG_CONE")], fields=["cone_pct"]),
                       "In the 10-90% band", "80% is calibrated", 0, arcade="""var c = $datapoint.AVG_CONE;
value = IIf(IsEmpty(c), '–', Text(c, '#,##0') + '%');"""))

    # logos, and the map: a click on a station filters every panel
    w.append(rich("Brand", BRAND.replace("__BASE__", base)))
    w.append(widget("Map", "mapWidget", flashRepeats=3, itemId=map_id, linkedMapWidgetIds=[],
                    mapTools=[{"type": "legendTool"}, {"type": "basemapGalleryTool"}],
                    showMeasureTool=False, showNavigation=True, showPanRotate=False, showLocate=False, showCompass=False,
                    showPopup=True, scalebarStyle="none", editingEnabled=False, trackedFeatureRadius=60, pointZoomScale=350000,
                    layers=[{"type": "layer", "dataSource": {"type": "layerDataSource", "itemId": map_id, "layerId": STATIONS_LAYER},
                             "events": [{"type": "selectionChanged", "actions": [
                                 {"type": "filter", "targets": targets(by_station, "station")}]}]}],
                    selectTool={"type": "selectTool", "clickToSelect": True, "advanceSketchTools": []}))

    # skill by guidance (bars) and its table
    bars = [{"type": "barSeries", "id": "AVG_S", "name": "Mean skill S", "x": "member_name", "dataLabels": _text(),
             "dataTooltipVisible": True, "dataTooltipReverseColor": True, "y": "AVG_S",
             "fillSymbol": {"type": "esriSFS", "style": "esriSFSSolid", "color": rgb(NAVY),
                            "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": rgb(NAVY), "width": 0}}}]
    w.append(chart("Skill by guidance", "Skill by guidance (mean S, last 30 days)",
                   dataset(lb, group=["member_name"], stats=[("S", "avg", "AVG_S")], order=["AVG_S DESC"],
                           fields=["member_name", "S"], limit=50, where=[last30, main]),
                   bars, axes(None, None, rotated=True, guides=skill_guides(label=False)), legend=False, rotated=True,
                   bottomCaption='<p style="margin:0"><span style="font-size:12px">Dashed lines: 0.45 is no skill, 0.75 and up '
                                 'is good. All archived guidance is in the leaderboard under Skill by lead time.</span></p>'))
    w.append(table("Leaderboard table", "Leaderboard (last 30 days, chosen lead time)",
                   dataset(lb, group=["member_name"], order=["AVG_S DESC"], where=[last30],
                           stats=[("S", "avg", "AVG_S"), ("Bias", "avg", "AVG_BIAS"), ("MAE", "avg", "AVG_MAE"),
                                  ("RMSE", "avg", "AVG_RMSE"), ("R2", "avg", "AVG_R2"), ("n", "sum", "SUM_N")]),
                   {"type": "groupByValues", "fieldName": "member_name", "labelOverrides": []},
                   [column("Guidance", "member_name", align="left"), column("Skill S", "S", "AVG_S", 3),
                    column("Bias (ft)", "Bias", "AVG_BIAS", 2), column("MAE (ft)", "MAE", "AVG_MAE", 2),
                    column("RMSE (ft)", "RMSE", "AVG_RMSE", 2), column("R²", "R2", "AVG_R2", 2),
                    column("Forecasts scored", "n", "SUM_N", 0)],
                   arcade="""var d = $datapoint;
function wash(s) { return IIf(IsEmpty(s), '', IIf(s >= 0.75, '#e3f1e9', IIf(s >= 0.45, '#fbefd8', '#f8e1df'))); }
""" + cells([("member_name", "d.member_name", "left", None, None),
             ("AVG_S", "num(d.AVG_S, '#,##0.000')", "right", None, "wash(d.AVG_S)"),
             ("AVG_BIAS", "signed(d.AVG_BIAS, '#,##0.00')", "right", None, None),
             ("AVG_MAE", "num(d.AVG_MAE, '#,##0.00')", "right", None, None),
             ("AVG_RMSE", "num(d.AVG_RMSE, '#,##0.00')", "right", None, None),
             ("AVG_R2", "num(d.AVG_R2, '#,##0.00')", "right", None, None),
             ("SUM_N", "num(d.SUM_N, '#,##0')", "right", None, None)])))

    # forecast archive: the list, and the chosen forecast charted by its CREST page (the list's first row until one is clicked)
    newest = ["issued DESC", "fc_over_action_ft DESC", "name ASC"]
    w.append(listing("Forecast list", "Forecasts issued, newest first",
                     dataset(fc, order=newest, limit=400),
                     '<p style="margin:0"><span style="color:{expression/dot}">&#9679;</span>&nbsp; <strong>{field/issued_label}</strong> '
                     '&nbsp;{field/name}</p><p style="margin:0"><span style="font-size:13px">{field/summary}</span></p>',
                     arcade=f"""var dot = Decode($datapoint.rating, 'Good', '{GOOD}', 'Fair', '{FAIR}', 'No skill', '{POOR}', '#b8c4cc');
return {{ attributes: {{ dot: dot }} }};""",
                     events=[{"type": "selectionChanged", "actions": [
                         {"type": "filter", "targets": targets(["Forecast chart"])}]}]))
    w.append(embed("Forecast chart", dataset(fc, order=newest, limit=1), "{field/chart_url}",
                   title="Forecast against the gauge"))

    # skill by lead time and over time: one line per guidance, colored as on the CREST pages
    def lines(x, days=False):
        ss = [line_series(nm, x, schema.COLORS[m], 3.0 if m == "primary" else 2.0, days) for nm, m in guidance]
        return ss, line_series("__split-by-template__", x, "#868e96", 1.5, days, y="AVG_S")
    ss, template = lines("lead")
    w.append(chart("Skill by lead time", "Skill by lead time (mean S of each 6-hour segment, last 30 days)",
                   dataset(dec, group=["lead", "member_name"], stats=[("S", "avg", "AVG_S")], order=["lead ASC"],
                           where=[last30, main]),
                   ss, axes("Hours after the forecast was issued", "Skill S", lo=None, hi=None, guides=skill_guides()),
                   split=("member_name", template), crosshair=True))
    ss, template = lines("issued_day", days=True)
    w.append(chart("Skill over time", "Skill over time (daily mean S of the first 36 hours of the forecasts issued each day)",
                   dataset(fc, group=["issued_day", "member_name"], stats=[("S_0_36", "avg", "AVG_S")], order=["issued_day ASC"]),
                   ss, axes(None, "Skill S", date=True, lo=None, hi=None, guides=skill_guides()),
                   split=("member_name", template), dates=True, crosshair=True))
    w.append(listing("Changes", "Model and pipeline changes", dataset(ch, order=["date DESC"], limit=50),
                     '<p style="margin:0"><strong>{expression/day}</strong> &nbsp;{field/label}</p>'
                     '<p style="margin:0"><span style="font-size:13px">{field/detail}</span></p>',
                     arcade="""var day = $datapoint['date'];
return { attributes: { day: IIf(IsEmpty(day), '', Text(ToUTC(day), 'D MMM Y')) } };"""))

    # floods
    hue = "Decode(d.category, " + ", ".join(f"'{k}', '{v}'" for k, v in FLOOD.items()) + ", '')"
    w.append(table("Flood table", "Observed floods (NWS Action or higher), newest first",
                   dataset(fl, order=["peak_time DESC"], limit=500,
                           fields=["name", "peak_time", "peak_ft_mllw", "category", "peak_err_primary", "peak_err_petss",
                                   "peak_err_nws"]),
                   {"type": "features", "valueFields": ["name", "peak_time", "peak_ft_mllw", "category", "peak_err_primary",
                                                        "peak_err_petss", "peak_err_nws"]},
                   [column("Station", "name", align="left"), column("Peak (UTC)", "peak_time", align="left"),
                    column("Peak (ft MLLW)", "peak_ft_mllw"), column("Category", "category", align="left"),
                    column("Published (ft)", "peak_err_primary"), column("P-ETSS (ft)", "peak_err_petss"),
                    column("NWS (ft)", "peak_err_nws")],
                   arcade="""var d = $datapoint;
""" + cells([("name", "d.name", "left", None, None),
             ("peak_time", "IIf(IsEmpty(d.peak_time), '–', Text(ToUTC(d.peak_time), 'D MMM Y HH:mm') + 'Z')", "left", None, None),
             ("peak_ft_mllw", "num(d.peak_ft_mllw, '#,##0.00')", "right", None, None),
             ("category", "d.category", "left", hue, None),
             ("peak_err_primary", "signed(d.peak_err_primary, '#,##0.00')", "right", None, None),
             ("peak_err_petss", "signed(d.peak_err_petss, '#,##0.00')", "right", None, None),
             ("peak_err_nws", "signed(d.peak_err_nws, '#,##0.00')", "right", None, None)]),
                   bottomCaption="<p>Published, P-ETSS and NWS: each one's mean peak error (forecast peak minus observed peak) "
                                 "over the forecasts that covered the flood.</p>",
                   events=[{"type": "selectionChanged", "actions": [
                       {"type": "flashGeometry", "targets": [{"targetId": wid("Map"), "by": "geometry", "requiresSelection": False}]}]}]))

    # the station's own CREST report, and notes
    # (until a station is chosen it shows the one with the most floods observed: a panel can wait for a choice
    # from one control, not from either of two, the map and the Station list)
    w.append(embed("Station report", dataset(st, order=["events DESC", "runs DESC", "name ASC"], limit=1),
                   "{field/crest_url}?embed=1&name=1&theme=light"))
    w.append(rich("About", ABOUT.replace("__DISCLAIMER__", DISCLAIMER).replace("__BASE__", base)))
    w.append(rich("Footer", f"<p style=\"text-align:center;margin:0\"><span style=\"font-size:11px\">{DISCLAIMER} "
                            "Guidance shown: every archived guidance, scored by StormSense CREST (VIMS).</span></p>"))

    # header: station, guidance, lead time
    station = {
        "id": wid("Station"), "dashboardItemType": "selector", "name": "Station", "noDataState": {}, "noFilterState": {},
        "focusModeEnabled": False, "label": "Station", "placeholderText": "All stations",
        "events": [{"type": "selectionChanged", "actions": [
            {"type": "filter", "targets": targets(by_station, "station")},
            {"type": "zoom", "targets": [{"targetId": wid("Map"), "by": "geometry", "requiresSelection": False}]},
            {"type": "flashGeometry", "targets": [{"targetId": wid("Map"), "by": "geometry", "requiresSelection": False}]}]}],
        "datasets": [dataset(st, order=["name ASC"], limit=500)],
        "type": "categorySelectorWidget", "category": {"type": "features", "itemText": "{field/name}"},
        "selection": {"type": "single", "requireDefault": False}, "showFilter": True, "displayType": "list",
        "maxSize": "compact", "presentationMode": "dropdown"}
    header = {
        "type": "header", "dashboardItemType": "panel", "title": "StormSense CREST Forecast Archive",
        "subtitle": "Every archived 72-hour storm tide forecast, scored against the gauges",
        "backgroundColor": "#ffffff", "textColor": INK, "titleTextColor": NAVY,
        "backgroundImageSizing": "fit-height", "normalBackgroundImagePlacement": "left",
        "horizontalBackgroundImagePlacement": "top", "showSignOutMenu": False, "subtitlePlacement": "below", "showMargin": True,
        "menuContents": [{"key": "CREST station reports", "link": base},
                         {"key": "StormSense at VIMS", "link": "https://www.vims.edu/people/loftis_jd/StormSense/"}],
        "selectors": [
            station,
            static_selector("Guidance", "Guidance", guidance, "member", by_guidance),
            static_selector("Lead time", "Lead time", [("All 72 h", "0-72"), ("0-12 h", "0-12"), ("12-36 h", "12-36"),
                                                       ("36-72 h", "36-72")], "band", by_band, bar=True)]}

    layout = box("root", "row", [
        box("main", "col", [
            box("left", "row", [cell("Brand", h=0.09), cell("Map", h=0.53), cell("Skill by guidance", h=0.38)], w=0.34),
            box("right", "row", [
                box("tiles", "col", [cell(n, w=1 / 6) for n in ("Forecasts scored", "Mean skill", "Mean RMSE", "Mean bias",
                                                                "Floods observed", "Band coverage")], h=0.15),
                tabs("tabs", [
                    box("archive", "col", [cell("Forecast list", w=0.3), cell("Forecast chart", w=0.7)],
                        tab="Forecast archive", kind="groupLayoutElement"),
                    box("lead", "row", [cell("Skill by lead time", h=0.55), cell("Leaderboard table", h=0.45)],
                        tab="Skill by lead time", kind="groupLayoutElement"),
                    box("time", "col", [cell("Skill over time", w=0.7), cell("Changes", w=0.3)],
                        tab="Skill over time", kind="groupLayoutElement"),
                    cell("Flood table", tab="Floods"),
                    cell("Station report", tab="Station report"),
                    cell("About", tab="About"),
                ], h=0.85)], w=0.66)], h=0.955),
        cell("Footer", h=0.045)])

    return {
        "version": "5.2.0", "authoringApp": "ArcGIS Dashboards", "authoringAppVersion": "5.2.0",
        "maxPaginationRecords": 50000, "maxChartRecords": 10000, "timeZone": "system",
        "theme": {"id": "user", "type": "resource"} if theme == "user" else {"id": "light", "type": "defined"},
        "numberPrefixOverrides": [{"key": k, "symbol": s, "enabled": True} for k, s in (
            ("yotta", "Y"), ("zeta", "Z"), ("exa", "E"), ("peta", "P"), ("tera", "T"), ("giga", "G"), ("mega", "M"),
            ("kilo", "k"), ("base", ""), ("deci", "d"), ("centi", "c"), ("milli", "m"), ("micro", "µ"), ("nano", "n"))],
        "desktopView": {"type": "desktop", "header": header, "layout": {"type": "dockingLayout", "rootElement": layout},
                        "widgets": w, "settings": {"allowElementResizing": True, "allowReset": True}},
        "elementMappings": {},
    }


# --- the items in ArcGIS Online ------------------------------------------------------------------------------
TAGS = "StormSense, CREST, VIMS, storm tide, forecast skill"
MAP_KEYWORDS = "ArcGIS Online,Explorer Web Map,Map,Online Map,Web Map"
DASH_KEYWORDS = "ArcGIS Dashboards,Dashboard,Operations Dashboard"
MAP_SNIPPET = "StormSense gauges colored by the skill of the published 72-hour storm tide forecast over the last 30 days (VIMS)."
DASH_SNIPPET = ("StormSense CREST: every archived 72-hour storm tide forecast scored against the gauges, by station, "
                "guidance and lead time (VIMS).")
NEEDS = ("stations", "leaderboard", "decay", "forecasts", "floods", "changes")


def _theme(p, item):
    """Save THEME as the dashboard's themes/user.json resource (added, or replaced when it is there)."""
    body = json.dumps(THEME, separators=(",", ":")).encode()
    err = None
    for op in ("addResources", "updateResources"):
        try:
            p.call("POST", f"{p.content}/items/{item}/{op}", files={"file": ("user.json", body, "application/json")},
                   fileName="user.json", resourcesPrefix="themes")
            return
        except Exception as e:
            err = e
    raise err


def ensure(p, folder, have, layers, extent, base=BASE, names=None, rebuild=False):
    """Create the web map and the dashboard in the portal folder when they are not there; with rebuild, write
    both again from this file. p: arcgis_push.Portal; have: the folder's items; layers, extent as for webmap().
    Returns {"map": id, "dashboard": id, "created": [titles], "updated": [titles]}."""
    out = {"created": [], "updated": []}

    def find(kind, title, marks):
        """The folder's item of this kind with our title, or else one whose JSON carries one of our ids, so that
        a dashboard or map that was renamed in ArcGIS is still found and not made a second time."""
        mine = [i for i in have if i.get("type") == kind]
        hit = next((i["id"] for i in mine if i.get("title") == title), None)
        for i in ([] if hit else mine):
            try:
                text = json.dumps(p.call("GET", f"{p.rest}/content/items/{i['id']}/data"))
            except Exception:
                continue
            if any(m in text for m in marks):
                return i["id"]
        return hit

    def save(item, kind, title, data, **more):
        text = json.dumps(data, separators=(",", ":"))
        if item is None:
            item = p.call("POST", f"{p.content}/{folder}/addItem", type=kind, title=title, tags=TAGS, text=text, **more)["id"]
            out["created"].append(title)
        else:
            p.call("POST", f"{p.content}/{folder}/items/{item}/update", text=text)
            if title not in out["created"] + out["updated"]:
                out["updated"].append(title)
        return item

    mid = find("Web Map", MAP_TITLE, [f'"id": "{STATIONS_LAYER}"'])
    did = find("Dashboard", DASH_TITLE, [wid("Forecast list"), wid("Map")])
    if rebuild or mid is None or did is None:
        missing = [k for k in NEEDS if k not in layers]
        if missing:
            raise RuntimeError("the dashboard needs layers that were not published: " + ", ".join(missing))
    if mid is None or rebuild:
        mid = save(mid, "Web Map", MAP_TITLE, webmap(layers, extent), typeKeywords=MAP_KEYWORDS, snippet=MAP_SNIPPET,
                   extent=",".join(f"{v:.4f}" for v in extent))
    if did is None or rebuild:
        did = save(did, "Dashboard", DASH_TITLE, dashboard(layers, mid, base, "light", names), typeKeywords=DASH_KEYWORDS,
                   snippet=DASH_SNIPPET)
        try:   # the StormSense colors: the theme file first, then the dashboard is pointed at it
            _theme(p, did)
            save(did, "Dashboard", DASH_TITLE, dashboard(layers, mid, base, "user", names))
        except Exception as e:
            print(f"  the dashboard keeps ArcGIS's light theme (its own could not be saved: {e})")
    out.update(map=mid, dashboard=did)
    return out
