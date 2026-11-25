/* CREST shared helpers: colors, units, pills, sortable tables, CSV download, theme for Highcharts. */
var COL = {primary:"#0b3954", stormsense:"#00b8c4", petss:"#d9480f", etss:"#e8a33d", stofs2d:"#5f3dc4", stofs3d:"#9c6ade",
  cbofs:"#2b8a3e", nwm_short:"#94a3b8", nwm_medium:"#64748b", nws:"#c2255c", psurge:"#a61e4d", tidewatch:"#16687E",
  ens_mean:"#f59f00", stormsense_raw:"#66d9e8", baseline:"#868e96", tide:"#adb5bd", obs:"#1f4fbf"};
var DISCLAIMER = "StormSense(TM) Predictive Plots are computer-generated operational storm tide forecasts; the user assumes any and all risk.";
var Q = new URLSearchParams(location.search);
var U = {datum: (Q.get("datum") || "MLLW").toUpperCase() === "NAVD" ? "NAVD" : "MLLW", units: Q.get("units") === "m" ? "m" : "ft"};
if (Q.get("embed") === "1") document.body.classList.add("embed");
function $(id){return document.getElementById(id)}
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()}
function col(m){return COL[m] || "#888"}
function k(){return U.units === "m" ? 0.3048 : 1}
function lvl(v, navd){ if (v == null) return null; var x = v - (U.datum === "NAVD" && navd != null ? navd : 0); return x * k(); }
function len(v){ return v == null ? null : v * k(); }
function ul(){ return U.units === "m" ? "m" : "ft"; }
function fx(v, n){ return v == null || isNaN(v) ? "–" : Number(v).toFixed(n == null ? 3 : n); }
function sgn(v, n){ return v == null ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(n == null ? 2 : n); }
function pill(v){ return v == null ? '<span class="pill n">–</span>' : '<span class="pill ' + (v >= 0.75 ? "g" : v >= 0.45 ? "f" : "p") + '">' + v.toFixed(3) + '</span>'; }
function sw(m){ return '<span class="sw" style="background:' + col(m) + '"></span>'; }
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"]/g, function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]}); }
function seg(groupId, values, cur, onPick){
  var g = $(groupId); g.innerHTML = values.map(function(v){return '<button data-v="' + v[0] + '" aria-pressed="' + (v[0] === cur) + '">' + v[1] + '</button>'}).join("");
  g.querySelectorAll("button").forEach(function(b){ b.onclick = function(){ g.querySelectorAll("button").forEach(function(x){x.setAttribute("aria-pressed", x === b)}); onPick(b.dataset.v); }; });
}
function hcTheme(){
  var muted = css("--muted"), line = css("--line"), ink = css("--ink");
  Highcharts.setOptions({
    chart: {backgroundColor: "transparent", style: {fontFamily: css("--body")}},
    title: {text: null}, credits: {enabled: false}, exporting: {enabled: false},
    legend: {itemStyle: {color: ink, fontWeight: "normal"}, itemHiddenStyle: {color: line}},
    xAxis: {labels: {style: {color: muted}}, lineColor: line, tickColor: line, gridLineColor: line, title: {style: {color: muted}}},
    yAxis: {labels: {style: {color: muted}}, gridLineColor: line, title: {style: {color: muted}}},
    tooltip: {backgroundColor: css("--panel"), borderColor: line, style: {color: ink}},
    plotOptions: {line: {marker: {enabled: false}}, arearange: {marker: {enabled: false}}},
    time: {useUTC: true}
  });
}
/* sortable table: cols = [{k, label, fmt(row), num, title}] */
function table(el, cols, rows, opt){
  opt = opt || {};
  var st = el._sort || {k: opt.sortKey, dir: opt.dir || -1};
  function val(r, c){ return c.val ? c.val(r) : r[c.k]; }
  var rs = rows.slice();
  if (st.k){ var c0 = cols.find(function(c){return c.k === st.k}); rs.sort(function(a, b){ var x = val(a, c0), y = val(b, c0);
    if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * st.dir; }); }
  el.innerHTML = "<thead><tr>" + cols.map(function(c){ return '<th class="sort' + (c.k === st.k ? " on" : "") + (c.t ? '" style="text-align:left' : "") + '" data-k="' + c.k + '"' + (c.title ? ' title="' + esc(c.title) + '"' : "") + ">" + c.label + (c.k === st.k ? (st.dir < 0 ? " ↓" : " ↑") : "") + "</th>"; }).join("") + "</tr></thead><tbody>" +
    rs.map(function(r){ return '<tr' + (opt.rowAttr ? opt.rowAttr(r) : "") + ">" + cols.map(function(c){ return '<td class="' + (c.t ? "t" : "") + '">' + (c.fmt ? c.fmt(r) : esc(val(r, c))) + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody>";
  el.querySelectorAll("th.sort").forEach(function(th){ th.onclick = function(){ var key = th.dataset.k; st = {k: key, dir: st.k === key ? -st.dir : (key === cols[0].k ? 1 : -1)}; el._sort = st; table(el, cols, rows, opt); }; });
  if (opt.onRow) el.querySelectorAll("tbody tr").forEach(function(tr, i){ tr.onclick = function(){ opt.onRow(rs[i]); }; tr.onkeydown = function(e){ if (e.key === "Enter") opt.onRow(rs[i]); }; });
  el._csv = {cols: cols, rows: rs, val: val};
}
function downloadCSV(el, name){
  var t = el._csv; if (!t) return;
  var lines = [t.cols.map(function(c){return c.label.replace(/<[^>]+>/g, "")}).join(",")].concat(t.rows.map(function(r){ return t.cols.map(function(c){ var v = t.val(r, c); return v == null ? "" : '"' + String(v).replace(/"/g, '""') + '"'; }).join(","); }));
  var a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([lines.join("\n")], {type: "text/csv"})); a.download = name; document.body.appendChild(a); a.click(); a.remove();
}
function getJSON(url, cb){ fetch(url).then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); }).then(cb).catch(function(e){ cb(null, e); }); }
