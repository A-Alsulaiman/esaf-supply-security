"""Graphical abstract for the ECM:X submission, generated from model outputs.

Left panel: map of the two supply routes, the model's chosen domestic plant
sites (results_v8_2030) and the routed sea lanes from the three export
terminals (geometries extracted from the shipping graph of
import_case/shipping_kerosene.py, stored in sea_routes_geometry.json).
Right panel: delivered cost of mandate-sized domestic production per country
on a log axis against the imported-supply band, which is the paper's
supply-security premium read geometrically.

Inputs (all in this repository):
  results_v8_2030/summary_all_airports.csv      domestic delivered costs + sites
  import_case/import_best_by_country.csv        cheapest import per market
  presentation/sea_routes_geometry.json         routed lane polylines
  presentation/map_countries_110m.json          Natural Earth 110m countries (TopoJSON)

Output: presentation/graphical_abstract.png (and .pdf), 3320 x 1280 px,
above Elsevier's 1328 x 531 px minimum.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)

BLUE = "#2a78d6"    # domestic below import parity
RED = "#e34948"     # domestic above import parity
AQUA = "#1baf7a"    # receiving (import) ports
GRAY = "#52514e"    # export production sites, inland links
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#8a8880"
LAND = "#edebe6"
BORDER = "#ffffff"
LANE = "#b0aeaa"
SURF = "#ffffff"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "text.color": INK,
    "axes.edgecolor": "#d8d6d0",
    "axes.labelcolor": INK2,
    "xtick.color": INK2,
    "ytick.color": INK2,
})

# ---------------------------------------------------------------- data
dom = pd.read_csv(os.path.join(PKG, "results_v8_2030", "summary_all_airports.csv"))
dom = dom[dom["scenario_year"] == 2030][
    ["country_iso3", "delivered_cost_usd_per_tonne_saf", "best_lat", "best_lon",
     "airport_lat", "airport_lon"]
].rename(columns={"delivered_cost_usd_per_tonne_saf": "dom_usd_t"})

ports = pd.read_csv(os.path.join(PKG, "import_case", "import_ports.csv"))[
    ["country", "port_lat", "port_lon"]
].rename(columns={"country": "country_iso3"})

exp = pd.read_csv(os.path.join(PKG, "import_case", "export_production_raw.csv"))
exp = exp[exp["year"] == 2030][
    ["exporter", "export_port", "export_port_lat", "export_port_lon", "best_lat", "best_lon"]
]

imp = pd.read_csv(os.path.join(PKG, "import_case", "import_best_by_country.csv"))
imp = imp[imp["year"] == 2030][["country", "import_delivered_usd_t"]] \
    .rename(columns={"country": "country_iso3", "import_delivered_usd_t": "imp_usd_t"})

df = dom.merge(imp, on="country_iso3", how="inner").merge(ports, on="country_iso3", how="left")
df["premium_pct"] = (df["dom_usd_t"] - df["imp_usd_t"]) / df["imp_usd_t"] * 100.0
df = df.sort_values("dom_usd_t").reset_index(drop=True)

assert len(df) == 29, f"expected 29 markets, got {len(df)}"
fra = df.loc[df.country_iso3 == "FRA", "dom_usd_t"].iloc[0]
assert abs(fra - 5898) < 5, fra
below = int((df["premium_pct"] < 0).sum())
assert below == 9, below
band_lo, band_hi = df["imp_usd_t"].min(), df["imp_usd_t"].max()
assert 6990 < band_lo < band_hi < 7150, (band_lo, band_hi)

world = json.load(open(os.path.join(HERE, "map_countries_110m.json")))
routes = json.load(open(os.path.join(HERE, "sea_routes_geometry.json")))

# ---------------------------------------------------------------- topojson decode
def topo_polys(topo):
    tr = topo.get("transform")
    arcs_q = topo["arcs"]
    arcs = []
    for arc in arcs_q:
        pts, x, y = [], 0, 0
        for dx, dy in arc:
            x += dx; y += dy
            if tr:
                pts.append((tr["scale"][0] * x + tr["translate"][0],
                            tr["scale"][1] * y + tr["translate"][1]))
            else:
                pts.append((x, y))
        arcs.append(pts)
    def ring(idx_list):
        out = []
        for i in idx_list:
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(pts if not out else pts[1:])
        # clip to the drawing window so world-spanning rings (Russia) fill sanely
        out = [(x, y) for x, y in out if -40 <= x <= 85 and -8 <= y <= 78]
        return out
    polys = []
    for geom in topo["objects"]["countries"]["geometries"]:
        gt = geom.get("type")
        if gt == "Polygon":
            polys.append([ring(r) for r in geom["arcs"]])
        elif gt == "MultiPolygon":
            for pg in geom["arcs"]:
                polys.append([ring(r) for r in pg])
    return polys

# ---------------------------------------------------------------- figure
fig = plt.figure(figsize=(16.6, 6.4), dpi=200, facecolor=SURF)
gs = fig.add_gridspec(1, 2, left=0.035, right=0.985, top=0.845, bottom=0.115,
                      width_ratios=[1.12, 1.0], wspace=0.10)

fig.text(0.035, 0.955, "Domestic production versus imports under ReFuelEU Aviation",
         fontsize=17, fontweight="bold", color=INK, va="top")
fig.text(0.035, 0.895,
         "Delivered cost of mandate-sized eSAF at 29 European hub airports, 2030 · "
         "supply-security premium = domestic cost vs the cheapest import",
         fontsize=11.5, color=INK2, va="top")

# ---- left: map
ax = fig.add_subplot(gs[0, 0])
ax.set_facecolor(SURF)
# continental land behind the north-east corner, where the clipped Russia ring
# leaves the window unfilled
ax.add_patch(Rectangle((38, 48.5), 26, 17, fc=LAND, ec="none", zorder=0.5))
for poly in topo_polys(world):
    for r in poly:
        xs = [p[0] for p in r]; ys = [p[1] for p in r]
        ax.fill(xs, ys, color=LAND, ec=BORDER, lw=0.4, zorder=1)

for exp_key in routes:
    for port, line in routes[exp_key].items():
        xs = [p[0] for p in line]; ys = [p[1] for p in line]
        ax.plot(xs, ys, color=LANE, lw=0.8, alpha=0.85, zorder=2,
                solid_capstyle="round")

DASH = dict(color=GRAY, lw=0.65, alpha=0.55, ls=(0, (2.2, 2.2)), zorder=3)

# inland legs: production site -> hub airport, receiving port -> hub airport
for _, r in df.iterrows():
    ax.plot([r.best_lon, r.airport_lon], [r.best_lat, r.airport_lat], **DASH)
    if pd.notna(r.port_lon):
        ax.plot([r.port_lon, r.airport_lon], [r.port_lat, r.airport_lat], **DASH)

# export side: production site -> export terminal, both as points
TERM_LABEL = {"King Fahd Port": "Yanbu", "Mina Jabal Ali": "Jebel Ali", "Ad Dakhla": "Ad Dakhla"}
for _, r in exp.iterrows():
    ax.plot([r.best_lon, r.export_port_lon], [r.best_lat, r.export_port_lat], **DASH)
    ax.plot(r.best_lon, r.best_lat, "o", ms=4.6, color=GRAY, mec=SURF, mew=0.7, zorder=5)
    ax.plot(r.export_port_lon, r.export_port_lat, "o", ms=5.0, color=INK,
            mec=SURF, mew=0.7, zorder=5)
    name = TERM_LABEL[r.export_port]
    dy = -2.9 if name != "Jebel Ali" else 2.0
    ax.text(r.export_port_lon + 0.8, r.export_port_lat + dy, name, fontsize=8.5,
            color=INK, fontweight="bold", zorder=6)

# receiving ports and hub airports
for _, r in df.iterrows():
    if pd.notna(r.port_lon):
        ax.plot(r.port_lon, r.port_lat, "o", ms=4.0, color=AQUA, mec=SURF, mew=0.6, zorder=4)
    ax.plot(r.airport_lon, r.airport_lat, "o", ms=3.6, mfc=SURF, mec=INK, mew=0.9, zorder=6)

# domestic production sites, colored by parity with the cheapest import
for _, r in df.iterrows():
    c = BLUE if r.premium_pct < 0 else RED
    ax.plot(r.best_lon, r.best_lat, "o", ms=4.6, color=c,
            mec=SURF, mew=0.7, zorder=5)

handles_map = [
    Line2D([], [], marker="o", ls="", color=BLUE, ms=5.5, label="domestic plant, below import parity"),
    Line2D([], [], marker="o", ls="", color=RED, ms=5.5, label="domestic plant, above import parity"),
    Line2D([], [], marker="o", ls="", mfc=SURF, mec=INK, mew=1.0, ms=5.0, label="hub airport"),
    Line2D([], [], marker="o", ls="", color=AQUA, ms=5.0, label="receiving port"),
    Line2D([], [], marker="o", ls="", color=INK, ms=5.5, label="export terminal"),
    Line2D([], [], marker="o", ls="", color=GRAY, ms=5.5, label="export production site"),
    Line2D([], [], color=LANE, lw=1.2, label="routed sea lane"),
    Line2D([], [], color=GRAY, lw=1.0, ls=(0, (2.2, 2.2)), label="inland leg"),
]
ax.legend(handles=handles_map, loc="upper right", fontsize=7.3, frameon=False,
          bbox_to_anchor=(1.0, 1.0), borderaxespad=0.3, handletextpad=0.5,
          labelspacing=0.32)

ax.text(-24.5, 59.8, "29 mandate-sized plants,\none per country at its best site",
        fontsize=9.5, color=INK2, zorder=6)
ax.text(-25.5, 12.0, "3 export systems sized to half of\nEU demand, shipped on routed lanes",
        fontsize=9.5, color=INK2, zorder=6)

ax.set_xlim(-26, 62); ax.set_ylim(9, 65)
ax.set_aspect(1.42)
ax.set_xticks([]); ax.set_yticks([])
for s in ax.spines.values():
    s.set_visible(False)

# ---- right: cost dot plot vs import band
ax2 = fig.add_subplot(gs[0, 1])
ax2.set_facecolor(SURF)
y = np.arange(len(df))[::-1]
ax2.axvspan(band_lo, band_hi, color="#d8d6d0", alpha=0.9, zorder=1)
ax2.axvspan(band_lo, band_hi, color="#52514e", alpha=0.12, zorder=1)
for yi, (_, r) in zip(y, df.iterrows()):
    c = BLUE if r.premium_pct < 0 else RED
    ax2.plot([band_hi if r.premium_pct > 0 else r.dom_usd_t,
              r.dom_usd_t if r.premium_pct > 0 else band_lo],
             [yi, yi], color=c, lw=1.1, alpha=0.45, zorder=2)
    ax2.plot(r.dom_usd_t, yi, "o", ms=5.2, color=c, mec=SURF, mew=0.6, zorder=3)
ax2.set_yticks(y)
ax2.set_yticklabels(df["country_iso3"], fontsize=7.2, color=INK2)
ax2.set_xscale("log")
ax2.set_xlim(2500, 60000)
ticks = [3000, 5000, 7000, 10000, 20000, 40000]
ax2.set_xticks(ticks)
ax2.set_xticklabels([f"{t:,}" for t in ticks], fontsize=8.5)
ax2.set_xlabel("delivered cost, USD per tonne of eSAF (log scale)", fontsize=9.5)
ax2.grid(axis="x", color="#eceae6", lw=0.7, zorder=0)
ax2.tick_params(length=0)
for s in ("top", "right", "left"):
    ax2.spines[s].set_visible(False)

ax2.annotate("imports land at\n7,040–7,110 USD/t\nwherever they arrive",
             xy=(band_hi, y[2] + 0.4), xytext=(11000, y[1] - 1.6),
             fontsize=9, color=INK2,
             arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
ax2.text(2650, y[5] - 0.4,
         f"{below} countries produce\nbelow the import price", fontsize=9, color=BLUE,
         fontweight="bold")
ax2.text(12500, y[15] + 0.2,
         "smallest mandates pay\nmultiples of the import price", fontsize=9,
         color=RED, fontweight="bold", ha="left")

handles = [Line2D([], [], marker="o", ls="", color=BLUE, ms=6,
                  label="domestic below import parity"),
           Line2D([], [], marker="o", ls="", color=RED, ms=6,
                  label="domestic above import parity"),
           Rectangle((0, 0), 1, 1, fc="#c9c7c1", ec="none",
                     label="imported-supply band")]
ax2.legend(handles=handles, loc="upper right", fontsize=8.5, frameon=False,
           bbox_to_anchor=(1.0, 1.0), borderaxespad=0.2)

fig.text(0.035, 0.028,
         "All values real USD-2024 from the model in this repository, "
         "results_v8_2030 and import_case. The whole logistics chain adds 50–90 USD/t, "
         "financing and mandate scale, not renewable resources, set the ranking on both sides.",
         fontsize=9, color=MUTED)

fig.savefig(os.path.join(HERE, "graphical_abstract.png"), dpi=200, facecolor=SURF)
fig.savefig(os.path.join(HERE, "graphical_abstract.pdf"), facecolor=SURF)
print("saved graphical_abstract.png / .pdf")
print(f"markets {len(df)}, below parity {below}, import band {band_lo:.0f}-{band_hi:.0f}")
