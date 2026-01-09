#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate polar track SVG from almurnet-like text logs.

Usage:
  python plot_polar_from_log.py /path/to/R2M6_..._rec.txt -o out.svg

The geometry & styling follow the D3 implementation found in the paired HTML:
- polar projection formulas
- grid (circles + azimuth rays every 30°)
- track path, time markers, signal markers & labels
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import svgwrite


@dataclass
class Row:
    ts_raw: str
    dt: datetime
    az: float
    el: float
    level: float  # "Level" column
    snr: float  # "SNR" column
    # you can add more fields if needed


DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(\.\d+)?$")


def parse_dt(s: str) -> datetime:
    # examples:
    # 2026-01-09 15:47:19.12
    # 2026-01-09 15:47:19
    s = s.strip()
    if not DT_RE.match(s):
        # fallback: cut to first 19 chars
        s = s[:19]
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

    if "." in s:
        # python expects microseconds
        base, frac = s.split(".", 1)
        frac = (frac + "000000")[:6]
        return datetime.strptime(base, "%Y-%m-%d %H:%M:%S").replace(microsecond=int(frac))
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def read_log(path: str) -> Tuple[List[Row], Dict[str, str], List[str]]:
    """
    Returns:
      rows: parsed data
      meta: extracted #Key: Value
      header_cols: parsed column names (Time, Az, El, Level, ...)
    """
    meta: Dict[str, str] = {}
    header_cols: List[str] = []
    rows: List[Row] = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # meta and header
    for ln in lines:
        s = ln.strip("\n")
        if s.startswith("#"):
            # #Key: Value
            m = re.match(r"^#\s*([^:]+):\s*(.*)\s*$", s)
            if m:
                meta[m.group(1).strip()] = m.group(2).strip()
            # header line
            if s.startswith("#Time"):
                header_cols = s.lstrip("#").strip().split("\t")
            continue

        if not s.strip():
            continue

        # data line (tab-separated)
        if not header_cols:
            continue

        parts = s.split("\t")
        if len(parts) < len(header_cols):
            continue

        rec = dict(zip(header_cols, parts))
        ts_raw = rec.get("Time", "").strip()
        az = float(rec.get("Az", "nan"))
        el = float(rec.get("El", "nan"))
        level = float(rec.get("Level", "nan"))
        snr = float(rec.get("SNR", "nan"))

        try:
            dt = parse_dt(ts_raw)
        except Exception:
            # skip unparseable
            continue

        rows.append(Row(ts_raw=ts_raw, dt=dt, az=az, el=el, level=level, snr=snr))

    return rows, meta, header_cols


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def mix_color(c1: str, c2: str, t: float) -> str:
    """
    Linear mix in RGB (D3 uses HCL, but RGB mix is acceptable visually close).
    """
    r1, g1, b1 = hex_to_rgb(c1)
    r2, g2, b2 = hex_to_rgb(c2)
    t = clamp(t, 0.0, 1.0)
    r = int(round(lerp(r1, r2, t)))
    g = int(round(lerp(g1, g2, t)))
    b = int(round(lerp(b1, b2, t)))
    return rgb_to_hex((r, g, b))


def d3_like_color(value: float) -> str:
    """
    Approximate the D3 linear scale:
      domain: [0, 3, 6, 6, 9, 12, 16, 24, 34]
      range : ['grey','grey','red','red','yellow','green','blue','violet','#ebdef0']
    From HTML. :contentReference[oaicite:5]{index=5}
    """
    dom = [0, 3, 6, 6, 9, 12, 16, 24, 34]
    rng = ["#808080", "#808080", "#ff0000", "#ff0000", "#ffff00", "#008000", "#0000ff", "#ee82ee", "#ebdef0"]

    if math.isnan(value):
        return "#808080"

    if value <= dom[0]:
        return rng[0]
    if value >= dom[-1]:
        return rng[-1]

    # find segment
    for i in range(len(dom) - 1):
        a, b = dom[i], dom[i + 1]
        if a <= value <= b or (a == b and value == a):
            if a == b:
                return rng[i + 1]  # degenerate segment
            t = (value - a) / (b - a)
            return mix_color(rng[i], rng[i + 1], t)

    return rng[-1]


def project(az_deg: float, el_deg: float, radius: float, min_el: float) -> Tuple[float, float]:
    """
    Same projection as in the HTML:
      x = radius *(90- el)*sin(pi*az/180)/(90-min_el)
      y = -radius *(90- el)*cos(pi*az/180)/(90-min_el)
    :contentReference[oaicite:6]{index=6}
    """
    az = math.radians(az_deg)
    denom = (90.0 - min_el) if (90.0 - min_el) != 0 else 1.0
    k = radius * (90.0 - el_deg) / denom
    x = k * math.sin(az)
    y = -k * math.cos(az)
    return x, y


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log_txt", help="Input text log (*.txt)")
    ap.add_argument("-o", "--out", default=None, help="Output SVG path")
    ap.add_argument("--size", type=int, default=600, help="SVG width/height (px)")
    ap.add_argument("--margin", type=int, default=30, help="Chart margin like in HTML (radius = size/2 - margin)")
    ap.add_argument("--min-el", type=float, default=None, help="Override min elevation (default: computed from data)")
    ap.add_argument("--bg", default="#ffffff", help="Background color")
    args = ap.parse_args()

    rows, meta, header_cols = read_log(args.log_txt)
    if not rows:
        raise SystemExit("No data rows parsed. Check file format / encoding.")

    out_path = args.out
    if out_path is None:
        base = os.path.splitext(os.path.basename(args.log_txt))[0]
        out_path = base + ".svg"

    size = args.size
    cx = cy = size / 2.0
    radius = min(size, size) / 2.0 - args.margin

    # min_el: in HTML it's taken from track_table.el_min; we approximate with data min.
    el_min_data = min(r.el for r in rows)
    min_el = args.min_el if args.min_el is not None else float(math.floor(el_min_data))
    min_el = clamp(min_el, 0.0, 89.0)

    # base_snr: for local file we use min(SNR).
    snr_values = [r.snr for r in rows if math.isfinite(r.snr)]
    base_snr = min(snr_values) if snr_values else 0.0

    # Drawing
    dwg = svgwrite.Drawing(out_path, size=(size, size), profile="full")

    # Background + border similar to the screenshot
    dwg.add(dwg.rect(insert=(0, 0), size=(size, size), fill=args.bg, stroke="#000000", stroke_width=1))

    g = dwg.g(transform=f"translate({cx},{cy})")
    dwg.add(g)

    # Styles (close to CSS in HTML)
    grid_stroke = "#777777"
    grid_dash = "1,4"
    grid_stroke_last = "#333333"
    track_stroke = "#cccccc"
    text_font = "Arial, Helvetica, sans-serif"

    # --- Frame: concentric circles + elevation labels (like r.ticks(10).slice(1)) ---
    # D3 r-scale maps elevation in [90..min_el] to radius [0..R]; circles at tick elevations.
    # We'll draw elevation circles at 10-tick steps between 90 and min_el.
    tick_count = 10
    ticks: List[float] = []
    if tick_count > 0:
        step = (90.0 - min_el) / tick_count
        for i in range(1, tick_count + 1):  # slice(1) => skip first (center)
            ticks.append(90.0 - step * i)

    for i, el_tick in enumerate(ticks):
        r_px = radius * (90.0 - el_tick) / (90.0 - min_el)
        is_last = (i == len(ticks) - 1)
        circle_kwargs = {
            "center": (0, 0),
            "r": r_px,
            "fill": "none",
            "stroke": (grid_stroke_last if is_last else grid_stroke),
            "stroke_width": 1,
        }
        if not is_last:
            circle_kwargs["stroke_dasharray"] = grid_dash
        g.add(dwg.circle(**circle_kwargs))

        # label like in HTML: y = -r(d) - 4; rotate(15)
        # Here: label uses value of tick (elevation degrees)
        label = f"{int(round(el_tick))}"
        # rotate around center by 15 deg then translate by y
        # easier: place at (0, -r_px-4) and rotate whole text
        t = dwg.text(label,
                     insert=(0, -r_px - 4),
                     text_anchor="middle",
                     font_size=10,
                     font_family=text_font)
        t.rotate(15)
        g.add(t)

    # --- Azimuth rays every 30° with degree labels (0..330) ---
    for az in range(0, 360, 30):
        # In HTML they rotate group by (d-90), and line to x2=radius.
        # We'll draw ray at angle (az-90) in SVG coords:
        ang = math.radians(az - 90)
        x2 = radius * math.cos(ang)
        y2 = radius * math.sin(ang)

        g.add(dwg.line(start=(0, 0), end=(x2, y2),
                       stroke=grid_stroke, stroke_width=1, stroke_dasharray=grid_dash))

        # label at radius+6 along ray
        lx = (radius + 6) * math.cos(ang)
        ly = (radius + 6) * math.sin(ang)
        txt = dwg.text(f"{az}°", insert=(lx, ly),
                       font_size=10, font_family=text_font)

        # text-anchor behavior like in HTML: end for az in (90..270)
        if 90 < az < 270:
            txt["text-anchor"] = "end"
            # rotate 180 around (lx,ly) for readability on left side
            txt.rotate(180, center=(lx, ly))
        else:
            txt["text-anchor"] = "start"

        # dy=".35em"
        txt["dy"] = "0.35em"
        g.add(txt)

    # --- Track path (all points as polyline/path) ---
    # Build SVG path "M x y L ..."
    pts = [project(r.az, r.el, radius, min_el) for r in rows]
    if pts:
        d = ["M {:.3f} {:.3f}".format(pts[0][0], pts[0][1])]
        for x, y in pts[1:]:
            d.append("L {:.3f} {:.3f}".format(x, y))
        g.add(dwg.path(d=" ".join(d),
                       fill="none", stroke=track_stroke, stroke_width=2))

    # --- Time markers (like HTML timestep logic) ---
    n = len(rows)
    timestep = 5
    if n > 100:
        timestep = 5 * (n // 100)
        if timestep <= 0:
            timestep = 5

    for i in range(0, n, timestep):
        r = rows[i]
        x, y = project(r.az, r.el, radius, min_el)

        g.add(dwg.circle(center=(x, y), r=2.5, fill="#cccccc", stroke="#cccccc", stroke_width=1))
        # label: x+12, y+8
        ts_label = r.dt.strftime("%H:%M:%S")
        g.add(dwg.text(ts_label,
                       insert=(x + 12, y + 8),
                       font_size=12, font_family=text_font, fill="#000000"))

    # --- Signal markers (colored circles) + numeric labels ---
    # step = floor(n/40)+1 :contentReference[oaicite:8]{index=8}
    step_sig = (n // 60) + 1
    for i in range(0, n, step_sig):
        r = rows[i]
        x, y = project(r.az, r.el, radius, min_el)

        val = r.snr - base_snr if math.isfinite(r.snr) else float("nan")
        color = d3_like_color(val)

        g.add(dwg.circle(center=(x, y), r=5.0, fill=color, stroke="none"))

        # every 4*step: label floor(val) at x-20, y+4 :contentReference[oaicite:9]{index=9}
        if (i % (4 * step_sig)) == 0 and math.isfinite(val):
            g.add(dwg.text(str(int(math.floor(val))),
                           insert=(x - 20, y + 4),
                           font_size=12, font_family=text_font, fill="#000000"))

    # --- Title (filename) at top-left-ish (как на скрине) ---
    title = os.path.basename(args.log_txt)
    dwg.add(dwg.text(title,
                     insert=(10, 18),
                     font_size=14,
                     font_family=text_font,
                     fill="#6A00FF"))  # близко к фиолетовому заголовку на примере

    # Save
    dwg.save()
    print(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
