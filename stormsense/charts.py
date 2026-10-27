"""StormSense chart pages: self-contained Highcharts 6 pages that load jQuery and Highcharts from
the site's own jslib/ (no CDN), with the StormSense styling that used to be added to Tidewatch's
pages afterwards (styler/add_datums.py in StormSense-latest):

  * gray tidal datum lines (HAT+n ... LAT, NAVD88) on MLLW charts; NWS Action/Minor/Moderate/Major
    plus the MLLW line on NAVD88 charts; Boon's 30-day mean water level (m30) in VIMS teal on the
    72-hour forecasts. A flood line may carry the forecast's chance of reaching it within 72 h,
    shown in parentheses after its label, e.g. "Action 4.50 (12%)".
  * every reference line's label hides or shows the line when clicked
  * the residual in StormSense cyan, the disclaimer and guidance line under the time axis, and
    the StormSense logo faded into the first corner no series crosses
  * forecast high waters labelled "(XHW: 4.23)" (a series point with a dataLabel)
"""
import json
import os
import struct
from zoneinfo import ZoneInfo

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
JSLIB = os.path.join(HERE, "data", "jslib")
LOGO = os.path.join(HERE, "data", "stormsense_logo.png")
LOCAL = ZoneInfo("America/New_York")
DISCLAIMER = ("Disclaimer: StormSense™ Predictive Plots are computer-generated operational storm tide "
              "forecasts; the user assumes any and all risk.")
RESIDUAL = "#00E5EE"
CONE = "rgba(20, 38, 96, 0.5)"
TEAL = "#16687E"
FLOOD = (("action", "Action", "#FFFF00"), ("minor", "Minor", "#FF9900"),
         ("moderate", "Moderate", "#FF0000"), ("major", "Major", "#CC33FF"))
# reference line styles: (dash style, width, color)
STYLE = {
    "HAT": ("Dash", 1, "#8a8a8a"), "MHHW": ("ShortDash", 1, "#8a8a8a"), "MSL": ("LongDash", 1, "#8a8a8a"),
    "LAT": ("Dash", 1, "#8a8a8a"), "MLLW": ("Solid", 1, "#8a8a8a"), "NAVD88": ("Solid", 1, "#8a8a8a"),
    "m30": ("DashDot", 1.5, TEAL), "HAT+": ("Dot", 1, "#b0b0b0"),
}
STYLE.update({name: ("Solid", 2, color) for _, name, color in FLOOD})
SCRIPTS = ("jquery.min.js", "highcharts.js", "highcharts-more.js", "exporting.js")


def ms(t):
    return int(t.timestamp() * 1000)


def pts(times, vals):
    return [[ms(t), round(float(v), 3)] for t, v in zip(times, vals) if not np.isnan(v)]


def local_label(t):
    """'2026-10-27 07:36:00 EDT' and the chart's timezoneOffset (minutes west of UTC)."""
    lt = t.astimezone(LOCAL)
    return lt.strftime("%Y-%m-%d %H:%M:%S %Z"), int(-lt.utcoffset().total_seconds() // 60)


def logo_aspect():
    raw = open(LOGO, "rb").read(24)
    w, h = struct.unpack(">II", raw[16:24])
    return h / w


def datum_lines(d, datum, navd88, flood=None, chances=None, m30=None):
    """Reference lines for one chart. d: datums ft above MLLW; datum: 'MLLW' or 'NAVD'; navd88: NAVD88
    zero above MLLW; flood: {category: ft MLLW}; chances: {category: 0-1}; m30: ft MLLW.
    Returns (lines, hat, hat name) in the chart's datum."""
    shift = 0.0 if datum == "MLLW" else -(navd88 or 0.0)
    est = set(d.get("_estimated", []))
    lines = []
    if datum == "MLLW":
        for name in ("HAT", "MHHW", "MSL", "MLLW", "LAT"):
            if d.get(name) is not None:
                lines.append({"name": name + (" (est.)" if name in est else ""), "key": name, "value": round(d[name], 2)})
        if navd88 is not None:
            lines.append({"name": "NAVD88", "key": "NAVD88", "value": round(navd88, 2)})
    else:
        lines.append({"name": "MLLW", "key": "MLLW", "value": round(shift, 2)})
        for key, name, _ in FLOOD:
            v = (flood or {}).get(key)
            if v is not None:
                line = {"name": name, "key": name, "value": round(v + shift, 2)}
                if chances and chances.get(key) is not None:
                    line["chance"] = chances[key]
                lines.append(line)
    if m30 is not None:
        lines.append({"name": "m30", "key": "m30", "value": round(m30 + shift, 2)})
    hk = next((k for k in ("HAT", "MHHW", "MSL") if d.get(k) is not None), None) if datum == "MLLW" else None
    return lines, (round(d[hk], 2) if hk else None), hk


def xhw_points(times, vals, idx):
    """Line data with the points at idx labelled '(XHW: x.xx)'."""
    keep = set(int(i) for i in idx)
    out = []
    for i, (t, v) in enumerate(zip(times, vals)):
        if np.isnan(v):
            continue
        if i in keep:
            out.append({"x": ms(t), "y": round(float(v), 3), "marker": {"enabled": True, "radius": 3},
                        "dataLabels": {"enabled": True, "format": "(XHW: %.2f)" % v, "y": -8,
                                       "style": {"fontSize": "11px", "fontWeight": "bold", "color": "#b00000",
                                                 "textOutline": "2px #ffffff"}}})
        else:
            out.append([ms(t), round(float(v), 3)])
    return out


STYLER = r"""
(function () {
    var cfg = %(cfg)s;
    chart.series.forEach(function (s) {
        if (s.name === 'Residual' || s.name === 'Residual Forecast') s.update({color: cfg.residual}, false);
    });
    var dmax = -1e9, dmin = 1e9;
    chart.series.forEach(function (s) {  // residuals set the floor, not the HAT+n steps
        var res = /^Residual/.test(s.name);
        s.yData.forEach(function (v) {
            (Array.isArray(v) ? v : [v]).forEach(function (y) {
                if (y !== null && !isNaN(y)) { if (!res) dmax = Math.max(dmax, y); dmin = Math.min(dmin, y); }
            });
        });
    });
    var hiLine = cfg.lines.reduce(function (a, l) { return Math.max(a, l.value); }, -1e9);
    var top = cfg.hat === null ? 0 : Math.max(2, Math.ceil(dmax - cfg.hat));
    var pl = [];
    function add(name, value, key, label) {
        var st = cfg.style[key], flood = /^(Action|Minor|Moderate|Major)$/.test(key);
        pl.push({id: 'ref-' + name, value: value, dashStyle: st[0], width: st[1], color: st[2], zIndex: flood ? 2 : 1,
                 label: {text: label, align: 'right', textAlign: 'left', x: 6, y: 3,
                         style: {color: key === 'Action' ? '#a39700' : (key === 'm30' || flood ? st[2] : '#6f6f6f'),
                                 fontSize: '10px', fontWeight: flood ? 'bold' : 'normal'}}});
    }
    cfg.lines.forEach(function (l) {
        var text = l.name + ' ' + l.value.toFixed(2);
        if (l.chance !== undefined && l.chance !== null) text += ' (' + Math.round(l.chance * 100) + '%%)';
        add(l.name, l.value, l.key || l.name, text);
    });
    for (var n = 1; cfg.hat !== null && n <= top; n++) {
        var v = cfg.hat + n;
        add(cfg.hatName + '+' + n, v, 'HAT+', cfg.hatName + '+' + n + ' ' + v.toFixed(2));
    }
    var lo = Math.min(dmin, cfg.lines.reduce(function (a, l) { return Math.min(a, l.value); }, 1e9));
    var ax = chart.yAxis[0];
    chart.update({chart: {marginRight: cfg.marginRight}}, false);
    var ymax = cfg.hat === null ? Math.ceil(Math.max(dmax, hiLine) * 2) / 2 + 0.25 : cfg.hat + top + 0.25;
    ax.update({max: ymax, min: Math.floor(lo * 2) / 2 - 0.25, tickInterval: 1, startOnTick: false, endOnTick: false});
    // labels sit in the right margin; nudge apart any that would overlap
    var order = pl.slice().sort(function (a, b) { return b.value - a.value; });
    var prev = -1e9, gap = 11;
    order.forEach(function (p) {
        var py = ax.toPixels(p.value), ly = Math.max(py, prev + gap);
        p.label.y = 3 + (ly - py);
        prev = ly;
    });
    ax.update({plotLines: pl});

    // click a line's label to hide or show the line (the label stays, faded)
    var hidden = {};
    var applyToggles = function () {
        ax.plotLinesAndBands.forEach(function (p) {
            var id = p.options.id, off = !!hidden[id];
            if (!id) return;
            if (p.svgElem) p.svgElem.attr({visibility: off ? 'hidden' : 'visible'});
            if (p.label) {
                p.label.attr({opacity: off ? 0.35 : 1}).css({cursor: 'pointer'});
                if (!p.label.twToggle) {
                    p.label.twToggle = true;
                    p.label.on('click', function () { hidden[id] = !hidden[id]; applyToggles(); });
                }
            }
        });
    };
    applyToggles();
    Highcharts.addEvent(chart, 'redraw', applyToggles);

    // disclaimer and guidance under the "Latest Observation" axis title
    var xa = chart.xAxis[0];
    xa.setTitle({text: xa.options.title.text + '<br/><span style="font-size:10px;color:#6f6f6f">' + cfg.disclaimer +
                 '</span>' + (cfg.guidance ? '<br/><span style="font-size:10px;color:#14285a">' + cfg.guidance + '</span>' : '')});
    if (cfg.note) chart.setTitle(null, {text: cfg.note, style: {color: '#a05a00', fontSize: '11px'}});

    // faded logo in the first corner that no visible series crosses
    if (cfg.logo) {
        var logoEl = null;
        var placeLogo = function () {
            if (logoEl) { logoEl.destroy(); logoEl = null; }
            var w = Math.min(170, chart.plotWidth * 0.22), h = w * cfg.logoAspect, pad = 8;
            var L = chart.plotLeft, T = chart.plotTop, R = L + chart.plotWidth, B = T + chart.plotHeight;
            var corners = [[R - pad - w, T + pad], [L + pad, T + pad], [R - pad - w, B - pad - h], [L + pad, B - pad - h]];
            var best = null, bestHits = 1e9;
            corners.forEach(function (c) {
                if (best && bestHits === 0) return;
                var hits = 0, x0 = c[0] - 4, x1 = c[0] + w + 4, y0 = c[1] - 4, y1 = c[1] + h + 4;
                chart.series.forEach(function (s) {
                    if (!s.visible) return;
                    s.points.forEach(function (pt) {
                        if (pt.y === null || pt.plotX === undefined) return;
                        var px = L + pt.plotX, py = T + pt.plotY;
                        if (px >= x0 && px <= x1 && py >= y0 && py <= y1) hits++;
                    });
                });
                if (hits < bestHits) { best = c; bestHits = hits; }
            });
            logoEl = chart.renderer.image(cfg.logo, best[0], best[1], w, h).attr({opacity: 0.5, zIndex: 0}).add();
        };
        placeLogo();
        Highcharts.addEvent(chart, 'redraw', placeLogo);
    }
})();
"""


def page(path, title, t_last, datum, series, lines, hat=None, hat_name=None, guidance=None, note=None,
         jsdir="../jslib", after_html="", page_title=None):
    """Write one chart page. t_last: time of the latest observation (x-axis title); datum: 'MLLW' or
    'NAVD'; series: Highcharts series dicts; lines: datum_lines() output; after_html: HTML placed
    below the chart (e.g. skill tables)."""
    label, tz = local_label(t_last)
    option = {
        "chart": {"renderTo": "container", "zoomType": "x", "height": 560},
        "credits": {"enabled": False},
        "plotOptions": {"series": {"turboThreshold": 0, "marker": {"enabled": False}, "lineWidth": 1.5,
                                   "states": {"hover": {"lineWidth": 2}}}},
        "title": {"text": title},
        "tooltip": {"valueDecimals": 2, "shared": False},
        "xAxis": {"type": "datetime", "title": {"text": f"Latest Observation {label}"}},
        "yAxis": {"title": {"text": "Water Level (feet above %s)" % ("MLLW" if datum == "MLLW" else "NAVD88")}},
    }
    cfg = {"lines": lines, "hat": hat, "hatName": hat_name, "style": STYLE, "disclaimer": DISCLAIMER,
           "residual": RESIDUAL, "guidance": guidance, "note": note, "marginRight": 112,
           "logo": f"{jsdir}/stormsense_logo.png", "logoAspect": logo_aspect()}
    scripts = "\n".join(f'<script src="{jsdir}/{s}"></script>' for s in SCRIPTS)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title or title}</title>
{scripts}
<style>
body{{margin:0;padding:0;font-family:Arial,'Liberation Sans',sans-serif;color:#1b2533;background:#fff}}
.below{{padding:8px 16px 24px}}
.below h2{{font-size:1.05rem;color:#14285a;margin:18px 0 4px}}
.below p{{margin:4px 0;font-size:.85rem;color:#555;line-height:1.4}}
.wrap{{overflow-x:auto}}
.below table{{border-collapse:collapse;font-size:.8rem;margin-top:4px}}
.below th,.below td{{border:1px solid #d5dbe3;padding:3px 6px;text-align:right;white-space:nowrap}}
.below thead th{{background:#eef6fa;text-align:center}}
.below td.l,.below th.l{{text-align:left}}
.below tr.ss td{{background:#e0fbfc}}
</style>
</head>
<body>
<div id="container">Loading....</div>
<script>
$(function () {{
    Highcharts.setOptions({{global: {{timezoneOffset: {tz}}}}});
    var chart = new Highcharts.Chart({json.dumps(option)});
    var data = {json.dumps(series, separators=(",", ":"))};
    for (var ix = 0; ix < data.length; ix++) chart.addSeries(data[ix], false);
    chart.redraw();
{STYLER % {"cfg": json.dumps(cfg)}}
}});
</script>
{('<div class="below">' + after_html + '</div>') if after_html else ''}
</body>
</html>
"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def copy_jslib(site):
    """jQuery, Highcharts and the logo into site/jslib, shared by every page."""
    import shutil
    d = os.path.join(site, "jslib")
    os.makedirs(d, exist_ok=True)
    for n in os.listdir(JSLIB):
        shutil.copy(os.path.join(JSLIB, n), os.path.join(d, n))
    shutil.copy(LOGO, os.path.join(d, "stormsense_logo.png"))
    shutil.copy(LOGO, os.path.join(site, "stormsense_logo.png"))
