"""Product 5: the combined PowerPoint for one forecast cycle."""
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from . import config
from .data import station_name

NAVY = RGBColor(0x12, 0x2B, 0x4A)
GRAY = RGBColor(0x55, 0x55, 0x55)
HEADER_FILL = RGBColor(0xEE, 0xF2, 0xF7)


def _title(slide, text, sub=None):
    tb = slide.shapes.add_textbox(Inches(0.4), Inches(0.2), Inches(12.5), Inches(0.6))
    p = tb.text_frame.paragraphs[0]
    p.text = text
    p.font.size = Pt(22)
    p.font.bold = True
    p.font.color.rgb = NAVY
    if sub:
        p2 = tb.text_frame.add_paragraph()
        p2.text = sub
        p2.font.size = Pt(12)
        p2.font.color.rgb = GRAY


def _fmt(col, v):
    if pd.isna(v):
        return "–"
    if col == "Coverage":
        return f"{v:.0%}"
    if "Skill" in col:
        return f"{v:.3f}"
    if col.startswith("Bias"):
        return f"{v:+.3f}"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _table(slide, df, left, top, width, height, font=9, rel_widths=None):
    rows, cols = df.shape[0] + 1, df.shape[1]
    shape = slide.shapes.add_table(rows, cols, left, top, width, height)
    tbl = shape.table
    if rel_widths:
        total = sum(rel_widths)
        for j, rw in enumerate(rel_widths):
            tbl.columns[j].width = int(width * rw / total)
    for j, c in enumerate(df.columns):
        cell = tbl.cell(0, j)
        cell.text = str(c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = HEADER_FILL
        cell.margin_top = cell.margin_bottom = Pt(1)
        para = cell.text_frame.paragraphs[0]
        para.font.size = Pt(font)
        para.font.bold = True
        para.font.color.rgb = NAVY
    for i, row in enumerate(df.itertuples(index=False), start=1):
        for j, v in enumerate(row):
            cell = tbl.cell(i, j)
            cell.text = _fmt(df.columns[j], v)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if i % 2 else RGBColor(0xF7, 0xF8, 0xFA)
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(font)
            para.font.color.rgb = RGBColor(0x22, 0x22, 0x22)
            cell.margin_top = cell.margin_bottom = Pt(1)
    for r in range(rows):
        tbl.rows[r].height = int(height / rows)
    return tbl


SLIDE_COLS = ["Issued (UTC)", "R2", "Bias (ft)", "MAE (ft)", "RMSE (ft)", "Tidewatch Skill", "Site Skill"]
AGG_COLS = ["Station", "Name", "R2", "Bias (ft)", "MAE (ft)", "RMSE (ft)",
            "Tidewatch Skill", "Site Skill", "Coverage"]


def build(issued, station_items, aggregate, path):
    """station_items: list of dicts with station, timeseries_png, regression_png,
    recent (DataFrame from tables.to_table for the past SLIDE_TABLE_DAYS)."""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Inches(0.8), Inches(2.3), Inches(11.7), Inches(3))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "Tidewatch Automated Statistical Analysis"
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = NAVY
    for text, size in [
        (f"36-hour forecast issued {issued:%A, %B %d, %Y at %H:%M} UTC ({issued:%Y-%m-%d %H%MZ})", 20),
        (f"Validation window {issued:%m/%d %H:%MZ} to {(issued + pd.Timedelta(hours=36)):%m/%d %H:%MZ}"
         f"  ·  {len(station_items)} stations  ·  water levels in ft {config.DATUM}", 14),
    ]:
        q = tf.add_paragraph()
        q.text = text
        q.font.size = Pt(size)
        q.font.color.rgb = GRAY

    for item in station_items:
        st = item["station"]
        s = prs.slides.add_slide(blank)
        _title(s, f"{st} · {station_name(st)}",
               f"Forecast issued {issued:%Y-%m-%d %H:%MZ}  ·  Tidewatch px30 vs observed")
        s.shapes.add_picture(str(item["timeseries_png"]), Inches(0.3), Inches(1.15), height=Inches(4.0))
        s.shapes.add_picture(str(item["regression_png"]), Inches(7.85), Inches(1.0), height=Inches(4.3))
        recent = item["recent"][SLIDE_COLS].tail(12)
        _table(s, recent, Inches(0.4), Inches(5.35), Inches(12.5),
               Inches(min(2.0, 0.17 * (len(recent) + 1) + 0.05)), font=8)
        cap = s.shapes.add_textbox(Inches(0.4), Inches(7.1), Inches(12), Inches(0.3))
        cap.text_frame.paragraphs[0].text = (
            f"Forecast statistics at {st} for the past {config.SLIDE_TABLE_DAYS} days")
        cap.text_frame.paragraphs[0].font.size = Pt(9)
        cap.text_frame.paragraphs[0].font.color.rgb = GRAY

    agg = aggregate[AGG_COLS]
    n = config.STATIONS_PER_SLIDE
    pages = max(1, -(-len(agg) // n))
    for k in range(pages):
        part = agg.iloc[k * n:(k + 1) * n]
        s = prs.slides.add_slide(blank)
        suffix = f" ({k + 1} of {pages})" if pages > 1 else ""
        _title(s, f"All stations · forecast issued {issued:%Y-%m-%d %H:%MZ}{suffix}")
        _table(s, part, Inches(0.4), Inches(0.95), Inches(12.5),
               Inches(0.235 * (len(part) + 1)), font=9,
               rel_widths=[0.7, 4.2, 0.8, 0.9, 0.9, 0.9, 1.1, 0.9, 0.9])
    prs.save(path)
