"""Presentation figures for the v8 results (print quality, 320 dpi PNG + vector PDF).

Palette per the validated reference instance (dataviz method):
  dumbbell pair  #5598e7 / #1c5cab  (validated PASS, light mode)
  scatter slots  #2a78d6 / #eb6834 / #1baf7a (all-pairs PASS) + gray context
  stack slots    #2a78d6 / #eb6834 / #1baf7a / #eda100 / #e87ba4 (adjacent PASS)
Contrast WARNs on light slots are relieved by direct labels + the accompanying
Excel table (relief rule).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patheffects as pe
import pandas as pd
import numpy as np
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
    "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
    "axes.labelcolor": INK2, "font.size": 9,
})

CLEAN = True  # suppress in-figure titles, subtitles and notes; that text lives in the report as footnotes

runs = {k: pd.read_csv(BASE / d / "summary_all_airports.csv").set_index("country_iso3")
        for k, d in [("fixed", "results_v8_fixed"), ("m2030", "results_v8_2030"),
                     ("t30", "results_v8_2050t30"), ("t50", "results_v8_2050t50")]}
REF3 = ["SAU", "ARE", "MAR"]
eu = [c for c in runs["fixed"].index if c not in REF3]


def style_ax(ax):
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


# ---------------------------------------------------------------- Figure 1
d30 = runs["m2030"].loc[eu, "delivered_cost_usd_per_tonne_saf"]
d50 = runs["t50"].loc[eu, "delivered_cost_usd_per_tonne_saf"]
iata = runs["fixed"].loc[eu, "airport_iata"]
order = d50.sort_values(ascending=False).index[::-1]  # cheapest 2050 at TOP row

C30, C50 = "#5598e7", "#1c5cab"
fig, ax = plt.subplots(figsize=(7.4, 9.2), dpi=320)
y = np.arange(len(order))[::-1]
for yi, c in zip(y, order):
    ax.plot([d50[c], d30[c]], [yi, yi], color=GRID, lw=2, zorder=1, solid_capstyle="round")
ax.scatter(d30[order], y, s=62, color=C30, edgecolor=SURFACE, linewidth=1.6, zorder=3, label="2030 mandate (plant = 1.2% of hub fuel)")
ax.scatter(d50[order], y, s=62, color=C50, edgecolor=SURFACE, linewidth=1.6, zorder=3, label="2050 mandate, 2050 technology (35% of hub fuel)")
ax.set_yticks(y)
ax.set_yticklabels([f"{c} · {iata[c]}" for c in order], fontsize=8.2, color=INK2)
ax.set_xscale("log")
ax.set_xlim(2300, 62000)
ticks = [3000, 5000, 10000, 20000, 50000]
ax.set_xticks(ticks)
ax.get_xaxis().set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
ax.xaxis.set_minor_locator(mticker.NullLocator())
ax.grid(axis="x", color=GRID, lw=0.8)
ax.set_axisbelow(True)
style_ax(ax)
ax.set_xlabel("Delivered cost of e-SAF at the hub airport (USD-2024 per tonne, log scale)")

# selective direct labels: extremes only
for c, col, dy in [(order[0], C50, 0)]:
    pass
lab = lambda c, series, color: ax.annotate(f"{series[c]:,.0f}", (series[c], y[list(order).index(c)]),
    textcoords="offset points", xytext=(0, 7), ha="center", fontsize=7.5, color=INK2)
best50 = d50.idxmin(); worst30 = d30.idxmax()
lab(best50, d50, C50); lab(worst30, d30, C30); lab(best50, d30, C30); lab(worst30, d50, C50)

ax.legend(loc="upper right", frameon=False, fontsize=8.4, handletextpad=0.4, borderaxespad=0.4)
if not CLEAN:
    fig.suptitle("Mandate-sized e-SAF plants: 2030 vs 2050 delivered cost by country",
                 fontsize=12.5, fontweight="bold", x=0.012, y=0.988, ha="left", color=INK)
    fig.text(0.012, 0.962, "One plant per country, sized to the hub airport's ReFuelEU/UK synthetic-fuel demand; cheapest feasible site,",
             fontsize=8.4, color=INK2)
    fig.text(0.012, 0.949, "operating strategy and RFNBO-eligible carbon source chosen per country. EU-27 + UK + CH.",
             fontsize=8.4, color=INK2)
    fig.text(0.012, 0.030, "Small-country hubs in 2030 (SVN, SVK, EST, LTU, HRV, LVA, BGR: plants of 0.2–2 kt/y) sit far below minimum",
             fontsize=6.9, color=MUTED)
    fig.text(0.012, 0.020, "economic scale — their mandate volumes favour imports or multi-airport supply. In 2050, 23 of 29 countries choose the",
             fontsize=6.9, color=MUTED)
    fig.text(0.012, 0.010, "flexible plant + fuel-storage design with DAC. Model: PtL-SAF siting v8; offline heuristic resources, 100 km grid; USD-2024.",
             fontsize=6.9, color=MUTED)
fig.tight_layout(rect=(0, 0.01, 1, 0.99) if CLEAN else (0, 0.042, 1, 0.945))
fig.savefig(OUT / "fig1_2030_vs_2050_by_country.png")
fig.savefig(OUT / "fig1_2030_vs_2050_by_country.pdf")
plt.close(fig)

# ---------------------------------------------------------------- Figure 2
fig, ax = plt.subplots(figsize=(9.2, 6.4), dpi=320)
series = [
    ("m2030", "#2a78d6", "2030 mandate demand, 2030 technology"),
    ("t30", "#eb6834", "2050 mandate demand, 2030 technology"),
    ("t50", "#1baf7a", "2050 mandate demand, 2050 technology"),
]
# two worked examples, the SAME airports in every series (distinct marker shapes)
HIGHLIGHT = [("FRA", "D", "Paris CDG"), ("SVN", "^", "Ljubljana")]
hl_iso = [h[0] for h in HIGHLIGHT]
for key, color, label in series:
    df = runs[key].loc[[c for c in eu if c not in hl_iso]]
    ax.scatter(df["annual_saf_tonnes"] / 1000, df["delivered_cost_usd_per_tonne_saf"],
               s=52, color=color, edgecolor=SURFACE, linewidth=1.4, zorder=3, label=label)
for iso3, marker, name in HIGHLIGHT:
    xs = [runs[k].loc[iso3, "annual_saf_tonnes"] / 1000 for k, _, _ in series]
    ys = [runs[k].loc[iso3, "delivered_cost_usd_per_tonne_saf"] for k, _, _ in series]
    ax.plot(xs, ys, color=AXIS, lw=0.9, ls=(0, (4, 3)), zorder=2)   # trajectory across scenarios
    for (k, color, _), x, yv in zip(series, xs, ys):
        ax.scatter([x], [yv], s=100, marker=marker, color=color,
                   edgecolor=INK, linewidth=0.9, zorder=4)
ax.set_xscale("log"); ax.set_yscale("log")
ax.set_xlim(0.15, 2600); ax.set_ylim(2300, 62000)
ax.set_xticks([0.2, 1, 5, 20, 100, 500, 2000])
ax.get_xaxis().set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
ax.set_yticks([3000, 5000, 10000, 20000, 50000])
ax.get_yaxis().set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
ax.xaxis.set_minor_locator(mticker.NullLocator()); ax.yaxis.set_minor_locator(mticker.NullLocator())
ax.grid(color=GRID, lw=0.8); ax.set_axisbelow(True)
style_ax(ax)
ax.set_xlabel("Plant size (kt of e-SAF per year, log scale)")
ax.set_ylabel("Delivered cost (USD-2024 per tonne, log scale)")

def _fmt_kt(v):
    return f"{v/1e6:,.1f} Mt/y" if v >= 1e6 else (f"{v/1000:.0f} kt/y" if v >= 1e3 else f"{v/1000:.1f} kt/y")

SCEN_TAG = {"m2030": "2030", "t30": "2050 @2030 tech", "t50": "2050"}
ANN_POS = {  # preferred (offset points, ha) per airport x series; auto-nudged if covering points
    "SVN": {"m2030": ((-9, 12), "right"), "t30": ((12, 0), "left"), "t50": ((13, -24), "left")},
    "FRA": {"m2030": ((11, 8), "left"), "t30": ((-9, -26), "right"), "t50": ((2, -26), "center")},
}
ann = []
for iso3, marker, name in HIGHLIGHT:
    for key in ("m2030", "t30", "t50"):
        r = runs[key].loc[iso3]
        off, ha = ANN_POS[iso3][key]
        ann.append((iso3, key,
                    f"{name} {SCEN_TAG[key]}:\n{_fmt_kt(r['annual_saf_tonnes'])}, "
                    f"{r['delivered_cost_usd_per_tonne_saf']:,.0f} USD/t",
                    off, ha))

# annotations are allergic to covering plotted points: measure each candidate
# text box with the real renderer and take the first placement clear of every
# marker (and of previously placed labels); the preferred offset is tried first.
# Layout is FROZEN before measuring — tight_layout afterwards would compress
# point spacing and silently re-introduce overlaps.
fig.tight_layout(rect=(0, 0.01, 1, 0.99) if CLEAN else (0, 0.025, 1, 0.91))
fig.canvas.draw()
_renderer = fig.canvas.get_renderer()
_all_pts = np.vstack([runs[k].loc[eu][["annual_saf_tonnes", "delivered_cost_usd_per_tonne_saf"]].values
                      for k, _, _ in series])
_pts_disp = ax.transData.transform(np.column_stack([_all_pts[:, 0] / 1000, _all_pts[:, 1]]))
_placed_bbs = []

def _hits(bb, pad=17):
    n = sum(1 for p in _placed_bbs
            if bb.x0 < p.x1 + 4 and bb.x1 > p.x0 - 4 and bb.y0 < p.y1 + 4 and bb.y1 > p.y0 - 4) * 10
    m = ((_pts_disp[:, 0] > bb.x0 - pad) & (_pts_disp[:, 0] < bb.x1 + pad)
         & (_pts_disp[:, 1] > bb.y0 - pad) & (_pts_disp[:, 1] < bb.y1 + pad))
    return n + int(m.sum())

_ax_bb = ax.get_window_extent(_renderer)
for iso3, key, text, off, ha in ann:
    r = runs[key].loc[iso3]
    xy = (r["annual_saf_tonnes"] / 1000, r["delivered_cost_usd_per_tonne_saf"])
    cands = [(off, ha), ((18, 2), "left"), ((-18, 2), "right"), ((0, 16), "center"),
             ((0, -26), "center"), ((26, 12), "left"), ((-26, 12), "right"),
             ((26, -22), "left"), ((-26, -22), "right"), ((36, 0), "left"), ((-36, 0), "right"),
             ((0, 30), "center"), ((0, -40), "center"),
             ((48, 6), "left"), ((-48, 6), "right"), ((48, -30), "left"), ((-48, -30), "right"),
             ((56, 24), "left"), ((-56, 24), "right"), ((0, 48), "center"), ((0, -58), "center"),
             ((74, 0), "left"), ((-74, 0), "right")]
    best = None   # (hits, annotation, bbox); annotations must stay inside the axes
    for cand_off, cand_ha in cands:
        a = ax.annotate(text, xy, textcoords="offset points", xytext=cand_off, ha=cand_ha,
                        fontsize=7.4, color=INK2,
                        arrowprops=dict(arrowstyle="-", color=AXIS, lw=0.7, shrinkB=0, shrinkA=4))
        bb = a.get_window_extent(_renderer)
        inside = (bb.x0 >= _ax_bb.x0 - 40 and bb.x1 <= _ax_bb.x1 + 40
                  and bb.y0 >= _ax_bb.y0 and bb.y1 <= _ax_bb.y1 + 30)
        h = _hits(bb) + (0 if inside else 100)
        if h == 0:
            best = (0, a, bb)
            break
        if best is None or h < best[0]:
            if best is not None:
                best[1].remove()
            best = (h, a, bb)
        else:
            a.remove()
    _placed_bbs.append(best[2])
from matplotlib.lines import Line2D
handles, labels = ax.get_legend_handles_labels()
handles += [Line2D([], [], marker=m, linestyle="none", markersize=7.5, markerfacecolor=MUTED,
                   markeredgecolor=INK, markeredgewidth=0.9) for _, m, _ in HIGHLIGHT]
labels += [f"{n} — same airport in every series" for _, _, n in HIGHLIGHT]
ax.legend(handles, labels, loc="upper right", frameon=False, fontsize=8.4)
if not CLEAN:
    fig.suptitle("Economies of scale: delivered cost against mandate-driven plant size",
                 fontsize=12.5, fontweight="bold", x=0.012, y=0.985, ha="left", color=INK)
    fig.text(0.012, 0.945, "Each dot is one country's best site. Capex scale exponents per unit group (FT 0.67 within a train, number-up 0.92 above;",
             fontsize=8.4, color=INK2)
    fig.text(0.012, 0.925, "electrolyser/DAC ~0.95; per-unit costs held constant below 30 kt/y). EU-27 + UK + CH; USD-2024.",
             fontsize=8.4, color=INK2)
    fig.text(0.012, 0.012, "Model: PtL-SAF siting v8; offline heuristic resource layer, 100 km grid.",
             fontsize=6.8, color=MUTED)
# NOTE: no tight_layout here — layout was frozen before annotation placement
fig.savefig(OUT / "fig2_scale_curve.png")
fig.savefig(OUT / "fig2_scale_curve.pdf")
plt.close(fig)

# ---------------------------------------------------------------- Figure 3
# Two variants of the cost-breakdown exhibit sharing one engine:
#   condensed  - three top-level systems only (no sub-components)
#   detailed   - Renewables System split on a validated blue ordinal ramp and
#                eSAF Plant System on the orange ramp (both dark -> light)
# Legend renders as THREE separate bracketed groups: Renewables System,
# eSAF Plant System, Other. Percent labels + two-sided leader callouts as
# established; totals float clear of everything.
from matplotlib.patches import Patch

bdf = pd.read_csv(OUT / "cost_breakdown_v8.csv")
bdf["renewables"] = (bdf["wind"] + bdf["solar_pv"] + bdf["battery"] + bdf["seasonal_h2_store"]
                     + bdf["fuel_tank_store"] + bdf["electrolyser_flex_oversize"])
bdf["process"] = (bdf["electrolyser_incl_stacks"] + bdf["dac"] + bdf["ft_rwgs_upgrading"]
                  + bdf["heat_pump"] + bdf["compressors"] + bdf["buffers_co2_h2"]
                  + bdf["fixed_site_services"])

RENEW_UNITS = [                                # blue ordinal ramp, dark -> light
    ("Wind", "wind", "#11458c"),
    ("Solar PV", "solar_pv", "#1d5ab0"),
    ("Battery", "battery", "#3372cd"),
    ("Seasonal H2 store", "seasonal_h2_store", "#518ce3"),
    ("Fuel tank store", "fuel_tank_store", "#72a6f2"),
]
PLANT_UNITS = [                                # orange ordinal ramp, dark -> light
    ("Electrolyser (incl. stacks)", "electrolyser_incl_stacks", "#6c3000"),
    ("DAC (on-site share)", "dac", "#883c00"),
    ("FT + RWGS + upgrading", "ft_rwgs_upgrading", "#a44800"),
    ("Heat pump", "heat_pump", "#bc5900"),
    ("Compressors", "compressors", "#d06e14"),
    ("Buffers (CO2 + H2)", "buffers_co2_h2", "#e2843b"),
    ("Fixed site services", "fixed_site_services", "#f29b5d"),
]
OTHER_UNITS = [
    ("Purchased biogenic CO2", "purchased_biogenic_co2", "#1baf7a"),
    ("Fuel transport to airport", "fuel_transport", "#eda100"),
    ("Water (net of DAC recovery)", "water", "#e87ba4"),
    ("Airport receiving fee", "airport_fee", "#008300"),
]
SMALL = OTHER_UNITS[1:]
hubs = ["ESP", "FRA", "DEU", "FIN", "CHE", "NLD", "GBR", "SWE"]
panels = [("2030 mandate (2030 tech)", "2030 mandate (2030 technology)"),
          ("2050 mandate (2050 tech)", "2050 mandate (2050 technology)")]
BARW = 0.44


def fmt_pct(p):
    return f"{p:.0f}" if p >= 1.0 else f"{p:.2f}"


def luminance(color):
    return sum(w * int(color[i:i+2], 16) for w, i in ((0.2126, 1), (0.7152, 3), (0.0722, 5))) / 255


def draw_stack(ax, df, comps, thr, gap, ymax_lab, label_fs=6.6, callout_fs=5.8, suppress=()):
    x = np.arange(len(df))
    totals = df["total"].values
    bottoms = np.zeros(len(df))
    callouts = {i: [] for i in range(len(df))}
    for label, colname, color in comps:
        vals = df[colname].values
        ax.bar(x, vals, bottom=bottoms, width=BARW, color=color, linewidth=0, zorder=3)
        if colname in suppress:      # drawn but labelled only in the zoom panel below
            bottoms += vals
            continue
        txtc = "#ffffff" if luminance(color) < 0.55 else INK
        for xi, (v, b) in enumerate(zip(vals, bottoms)):
            if v <= 0:
                continue
            pct = 100.0 * v / totals[xi]
            if v > thr:
                ax.text(xi, b + v / 2, fmt_pct(pct), ha="center", va="center",
                        fontsize=label_fs, color=txtc, zorder=4)
            else:
                callouts[xi].append((b + v / 2, color, fmt_pct(pct)))
        bottoms += vals

    ladder_top = {i: 0.0 for i in range(len(df))}

    def _ladder(xi, items, side):
        if not items:
            return
        if side == "right":
            ys = []
            for mid, _, _ in items:
                y = mid if not ys else max(mid, ys[-1] + gap)
                ys.append(y)
            over = ys[-1] - ymax_lab
            if over > 0:
                ys = [y - over for y in ys]
            ladder_top[xi] = max(ladder_top[xi], ys[-1])
        else:
            ys = []
            for mid, _, _ in reversed(items):
                y = mid if not ys else min(mid, ys[-1] - gap)
                ys.append(y)
            under = gap * 0.6 - ys[-1]
            if under > 0:
                ys = [y + under for y in ys]
            ys = list(reversed(ys))
        sgn = 1.0 if side == "right" else -1.0
        for (mid, _color, txt), ylab in zip(items, ys):
            ax.plot([xi + sgn * BARW / 2, xi + sgn * (BARW / 2 + 0.055)], [mid, ylab],
                    color=INK, lw=0.6, zorder=5, clip_on=False)
            ax.text(xi + sgn * (BARW / 2 + 0.075), ylab, txt, fontsize=callout_fs, va="center",
                    ha="left" if side == "right" else "right", color=INK, zorder=6,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground=SURFACE)])

    for xi, items in callouts.items():
        if not items:
            continue
        items.sort(key=lambda it: it[0])
        if len(items) <= 2:
            _ladder(xi, items, "right")
        else:
            half = len(items) // 2
            _ladder(xi, items[:half], "left")
            _ladder(xi, items[half:], "right")
    return totals, ladder_top


def make_breakdown_fig(detailed: bool, fname: str):
    if detailed:
        comps = RENEW_UNITS + PLANT_UNITS + OTHER_UNITS
        groups = [("Renewables System", RENEW_UNITS),
                  ("eSAF Plant System", PLANT_UNITS),
                  ("Other", OTHER_UNITS)]
        figh, rect_top, variant = 9.5, 0.788, "component detail"
    else:
        comps = ([("Renewables System", "renewables", "#2a78d6"),
                  ("eSAF Plant System", "process", "#eb6834")] + OTHER_UNITS)
        groups = [("Renewables System", comps[:1]),
                  ("eSAF Plant System", comps[1:2]),
                  ("Other", OTHER_UNITS)]
        figh, rect_top, variant = 8.8, 0.836, "condensed"

    fig, axes = plt.subplots(2, 2, figsize=(11.4, figh), dpi=320,
                             gridspec_kw={"height_ratios": [2.1, 1.0], "hspace": 0.34, "wspace": 0.12})
    for col, (scen, title) in enumerate(panels):
        df = bdf[(bdf.scenario == scen) & (bdf.country.isin(hubs))].set_index("country").loc[hubs]
        x = np.arange(len(hubs))

        ax = axes[0, col]
        # transport / water / fee are fully labelled in the zoom panel below,
        # so their values are never repeated in the main panel
        sup = tuple(c for _, c, _ in SMALL)
        totals, ltop = draw_stack(ax, df, comps, thr=330, gap=260, ymax_lab=8260, suppress=sup)
        for xi, tot in enumerate(totals):
            ax.text(xi, max(tot, ltop[xi]) + 165, f"{tot:,.0f}", ha="center", va="bottom",
                    fontsize=8, color=INK, fontweight="bold",
                    path_effects=[pe.withStroke(linewidth=2.6, foreground=SURFACE)])
        strat = df["strategy"].map({"flex_fuel_store": "flex", "steady_h2_store": "stdy"})
        carb = df["carbon"].map({"market": "bio", "dac": "DAC"})
        ax.set_xticks(x)
        ax.set_xticklabels([f"{c}\n{df.loc[c, 'plant_kt']:,.0f} kt\n{strat[c]}·{carb[c]}" for c in hubs],
                           fontsize=6.8, color=INK2)
        ax.set_title(title, fontsize=10, color=INK, loc="left", pad=8)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        style_ax(ax)
        ax.set_ylim(0, 8600)
        ax.set_xlim(-0.5, len(hubs) - 0.2)
        if col == 0:
            ax.set_ylabel("Delivered cost (USD-2024 / t e-SAF)")
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        else:
            ax.tick_params(labelleft=False)

        axz = axes[1, col]
        totz, ltopz = draw_stack(axz, df, SMALL, thr=13, gap=11.5, ymax_lab=124, label_fs=6.4, callout_fs=6.0)
        small_tot = df[[c for _, c, _ in SMALL]].sum(axis=1).values
        for xi, tot in enumerate(small_tot):
            axz.text(xi, max(tot, ltopz[xi]) + 3.2, f"{tot:,.0f}", ha="center", va="bottom",
                     fontsize=7.2, color=INK,
                     path_effects=[pe.withStroke(linewidth=2.2, foreground=SURFACE)])
        axz.set_xticks(x)
        axz.set_xticklabels(hubs, fontsize=7.6, color=INK2)
        axz.set_title(f"Zoom ×65 ({'2030' if col == 0 else '2050'})" if CLEAN else
                      f"Zoom ×65 — the small lines are real but negligible ({'2030' if col == 0 else '2050'})",
                      fontsize=9, color=INK, loc="left", pad=6)
        axz.grid(axis="y", color=GRID, lw=0.8)
        axz.set_axisbelow(True)
        style_ax(axz)
        axz.set_ylim(0, 132)
        axz.set_xlim(-0.5, len(hubs) - 0.2)
        if col == 0:
            axz.set_ylabel("USD-2024 / t")
            if not CLEAN:
                axz.text(0.99, 0.96, "purchased biogenic CO2 (10–12% of total)\nis large enough to appear in the main panel",
                         transform=axz.transAxes, ha="right", va="top", fontsize=7.0, color=MUTED)
        else:
            axz.tick_params(labelleft=False)
            if not CLEAN:
                axz.text(0.99, 0.96, "purchased CO2 = 0 in 2050:\nall eight hubs choose on-site DAC",
                         transform=axz.transAxes, ha="right", va="top", fontsize=7.0, color=MUTED)

    # --- three bracketed legend groups across the top -----------------------
    anchors = [(0.185, 2), (0.505, 2), (0.825, 2)] if detailed else [(0.16, 1), (0.44, 1), (0.76, 2)]
    for (gtitle, gitems), (ax_x, ncol) in zip(groups, anchors):
        handles = [Patch(facecolor=c, edgecolor="none") for _, _, c in gitems]
        labels = [l for l, _, _ in gitems]
        leg = fig.legend(handles, labels, title=gtitle, loc="upper center",
                         bbox_to_anchor=(ax_x, 0.972), ncol=ncol, frameon=False,
                         fontsize=7.2, columnspacing=0.9, handletextpad=0.5,
                         handlelength=1.3, borderaxespad=0.0)
        leg.get_title().set_fontsize(8.2)
        leg.get_title().set_fontweight("bold")
        leg._legend_box.align = "left"

    if not CLEAN:
        fig.suptitle(f"What a delivered tonne is made of: eight major hubs, 2030 vs 2050 — {variant}",
                     fontsize=12.5, fontweight="bold", x=0.012, ha="left", y=0.99, color=INK)
        ramp_note = ("Renewables System (blue) and eSAF Plant System (orange) sub-units each share one ordinal colour ramp, dark to light (shade = unit, not rank); "
                     "each process line carries its share of indirects and fixed O&M.\n"
                     if detailed else
                     "Renewables System includes battery and the chosen seasonal store; eSAF Plant System includes electrolysis, DAC, FT and site services with indirects and fixed O&M. See the component-detail companion figure.\n")
        fig.text(0.012, 0.058, "Figures in and beside the columns are PERCENT of the delivered cost per tonne "
                               "(two decimals below 1); bold caps above the bars are the USD-2024 totals.",
                 fontsize=7.8, color=INK2, style="italic")
        callout_note = ("Thin segments are called out with a leader line, split left/right of the column; "
                        "fuel transport, water and airport fee appear in the zoom panels only. Exact values in the 'Cost breakdown' sheet.\n"
                        if detailed else
                        "Fuel transport, water and airport fee appear in the zoom panels only; segments too thin for an inside figure "
                        "are called out with a thin leader line. Exact values in the 'Cost breakdown' workbook sheet.\n")
        fig.text(0.012, 0.006,
                 "Bar caption: country · plant size (mandate demand at the hub) · strategy (flex = oversized synthesis + fuel tanks; stdy = constant plant + H2 cavern) · carbon source (bio = purchased biogenic CO2).\n"
                 + ramp_note + callout_note +
                 "Electrolyser flexible-oversize is zero at these hubs. Model: PtL-SAF siting v8; heuristic resources, 100 km grid; USD-2024.",
                 fontsize=6.8, color=MUTED)
    fig.tight_layout(rect=(0, 0.008, 1, rect_top) if CLEAN else (0, 0.075, 1, rect_top))
    fig.savefig(OUT / f"{fname}.png")
    fig.savefig(OUT / f"{fname}.pdf")
    plt.close(fig)


make_breakdown_fig(detailed=False, fname="fig3a_cost_breakdown_condensed")
make_breakdown_fig(detailed=True, fname="fig3b_cost_breakdown_detailed")

# ---------------------------------------------------------------- Figure 4
# Ticket impact: % increase vs a no-eSAF world, by segment, 2030 vs 2050
tk = pd.read_csv(OUT / "ticket_impact_v8.csv")
seg_rows = tk[tk["class"].isin(["Economy", "Business"])].copy()
seg_rows["seg"] = seg_rows["segment"] + "\n" + seg_rows["class"]
SEG_ORDER = ["Short haul\nEconomy", "Short haul\nBusiness", "Long haul\nEconomy", "Long haul\nBusiness"]

fig, ax = plt.subplots(figsize=(8.8, 5.6), dpi=320)
x = np.arange(len(SEG_ORDER))
for off, year, color in [(-0.19, 2030, C30), (0.19, 2050, C50)]:
    sub = seg_rows[seg_rows.year == year].set_index(["seg", "band"])
    cen = np.array([sub.loc[(s, "central"), "ticket_increase_pct"] for s in SEG_ORDER])
    lo = np.array([sub.loc[(s, "p10"), "ticket_increase_pct"] for s in SEG_ORDER])
    hi = np.array([sub.loc[(s, "p90"), "ticket_increase_pct"] for s in SEG_ORDER])
    ax.bar(x + off, cen, width=0.34, color=color, linewidth=0, zorder=3)
    ax.errorbar(x + off, cen, yerr=[cen - lo, hi - cen], fmt="none",
                ecolor=INK, elinewidth=0.9, capsize=2.5, capthick=0.9, zorder=4)
    for xi, (c_, h_) in enumerate(zip(cen, hi)):
        ax.text(xi + off, h_ + 0.9, f"+{c_:.1f}%", ha="center", va="bottom",
                fontsize=8, color=INK, fontweight="bold",
                path_effects=[pe.withStroke(linewidth=2.4, foreground=SURFACE)])

opr = tk[(tk.band == "central") & (tk.segment == "Airline fuel bill")].set_index("year")["ticket_increase_pct"]
pas = tk[(tk.band == "central") & (tk.segment == "Uniform pass-through")].set_index("year")["ticket_increase_pct"]
if not CLEAN:
    ax.text(0.99, 0.985,
            "Airline-average view\n"
            f"total fuel bill:  +{opr[2030]:.1f}% (2030)   +{opr[2050]:.0f}% (2050)\n"
            f"spread evenly over every ticket:  +{pas[2030]:.1f}%   +{pas[2050]:.0f}%",
            transform=ax.transAxes, ha="right", va="top", fontsize=7.6, color=INK2, linespacing=1.5)

ax.set_xticks(x)
ax.set_xticklabels(SEG_ORDER, fontsize=8.6, color=INK2)
ax.set_ylabel("Ticket price increase vs no-eSAF baseline (%)")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
ax.set_ylim(0, 50)
ax.grid(axis="y", color=GRID, lw=0.8)
ax.set_axisbelow(True)
style_ax(ax)
leg = ax.legend(handles=[Patch(facecolor=C30, label="2030 — 1.2% e-SAF share"),
                         Patch(facecolor=C50, label="2050 — 35% e-SAF share")],
                loc="upper left", frameon=False, fontsize=8.2, handlelength=1.3)
if not CLEAN:
    fig.suptitle("What the e-SAF mandate adds to a ticket — EU average, full pass-through",
                 fontsize=11.5, fontweight="bold", x=0.012, ha="left", y=0.99, color=INK)
    fig.text(0.012, 0.916, "Extra cost per passenger = e-SAF share x (delivered e-SAF cost - fossil jet price) x fuel per passenger x cabin factor, "
                           "as % of a representative one-way fare.\nWhiskers span the demand-weighted p10-p90 of EU-27 hub delivered costs; bars use the demand-weighted mean.",
             fontsize=7.6, color=INK2)
    fig.text(0.012, 0.005,
             "Assumptions (editable in the 'Ticket impact' workbook sheet): fossil jet 780 USD/t (IATA 2024 avg, held real); e-SAF price = 50% domestic + 50% imported (REPowerEU-style balance):\n"
             "7,066 (2030) / 3,124 (2050) USD/t, per-hub blends demand-weighted; fuel 30 kg/pax (1,250 km short haul) and 182 kg/pax (7,000 km long haul, economy); DEFRA cabin factors\n"
             "(business 1.5x short, 2.9x long); fares 110 / 330 / 450 / 2,200 USD one-way. Crediting EU ETS allowances saved on intra-EEA routes trims short-haul economy to +1.9% (2030) and\n"
             "+14% (2050); long-haul extra-EU sits outside the EU ETS. Isolates the synthetic share only; no producer margin or certificate value. Model: PtL-SAF siting v8; USD-2024.",
             fontsize=6.6, color=MUTED)
fig.tight_layout(rect=(0, 0.012, 1, 0.99) if CLEAN else (0, 0.115, 1, 0.885))
fig.savefig(OUT / "fig4_ticket_impact.png")
fig.savefig(OUT / "fig4_ticket_impact.pdf")
plt.close(fig)

# ---------------------------------------------------------------- Figure 5
# Import vs domestic at every modelled hub (EU-27 + UK + CH), all three
# exporters shown as gradient shades of one green (validated ordinal ramp)
imp_path = BASE / "import_case" / "import_delivered_costs.csv"
if imp_path.exists():
    impall = pd.read_csv(imp_path)
    C_DOM = "#2a78d6"
    EXP_SHADES = [("SAU", "#076b46"), ("ARE", "#18a06f"), ("MAR", "#4cc998")]  # dark -> light, ordinal PASS
    dom_by_year = {2030: runs["m2030"], 2050: runs["t50"]}
    countries5 = [c for c in eu if c in set(impall.country)]          # the fig-1 airport set
    order5 = (dom_by_year[2030].loc[countries5, "delivered_cost_usd_per_tonne_saf"]
              .sort_values(ascending=True).index.tolist())
    # identical x-range on both panels so the 2030 -> 2050 shift reads directly
    allvals = pd.concat([impall["import_delivered_usd_t"],
                         pd.concat([dom_by_year[y].loc[order5, "delivered_cost_usd_per_tonne_saf"]
                                    for y in (2030, 2050)])])
    XLIM = (allvals.min() * 0.88, allvals.max() * 1.10)
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 8.8), dpi=320, sharey=True)
    for ax, year in zip(axes, (2030, 2050)):
        dom = dom_by_year[year].loc[order5, "delivered_cost_usd_per_tonne_saf"]
        sub = impall[impall.year == year].pivot(index="country", columns="exporter",
                                                values="import_delivered_usd_t").loc[order5]
        y = np.arange(len(order5))
        for yi, c in zip(y, order5):
            vals = [dom[c]] + [sub.loc[c, e] for e, _ in EXP_SHADES]
            ax.plot([min(vals), max(vals)], [yi, yi], color=GRID, lw=2, zorder=1, solid_capstyle="round")
        for e, shade in EXP_SHADES:
            ax.scatter(sub[e], y, s=30, color=shade, zorder=3, label=f"Import from {e}")
        ax.scatter(dom, y, s=34, color=C_DOM, zorder=4, label="Domestic production")
        for yi, c in zip(y, order5):
            best = min(sub.loc[c, e] for e, _ in EXP_SHADES)
            pct = 100 * (best / dom[c] - 1)
            ax.text(XLIM[1] * 1.03, yi, f"{pct:+.0f}%", fontsize=6.4, va="center", ha="left",
                    color=INK2 if pct < 0 else "#b3261e")
        ax.set_yticks(y)
        ax.set_yticklabels(order5, fontsize=7.2, color=INK2)
        ax.set_xscale("log")
        ax.set_xlim(*XLIM)
        ax.set_xticks([3000, 5000, 10000, 20000, 40000])
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
        ax.set_title(f"{year} mandate ({year} technology)", fontsize=10, color=INK, loc="left", pad=8)
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        style_ax(ax)
        ax.set_xlabel("Delivered cost at hub airport (USD-2024 / t, log scale)")
        ax.margins(y=0.012)
    h, l = axes[0].get_legend_handles_labels()
    h, l = [h[3], h[0], h[1], h[2]], [l[3], l[0], l[1], l[2]]   # domestic first
    fig.legend(h, l, loc="upper center", bbox_to_anchor=(0.5, 0.995), ncol=4, frameon=False,
               fontsize=8.0, columnspacing=1.6, handletextpad=0.5)
    if not CLEAN:
        fig.suptitle("Importing e-SAF beats most domestic mandates — and nearly all of them by 2050",
                     fontsize=11.5, fontweight="bold", x=0.012, ha="left", y=0.988, color=INK)
        fig.text(0.012, 0.955,
                 "Every modelled hub (EU-27 + UK + CH). Exporters site at their best renewables near a designated\n"
                 "export terminal and size production to 50% of the EU-27 synthetic mandate (197 kt 2030; 5.6 Mt\n"
                 "2050) — the REPowerEU-style import cap. Both panels share one axis: the whole cloud shifting\n"
                 "left is the 2030 → 2050 cost decline.",
                 fontsize=7.4, color=INK2, va="top", linespacing=1.45)
        fig.text(0.012, 0.006,
                 "Import dots: one green, shaded dark → light = SAU → ARE → MAR (Yanbu / Jebel Ali / Ad Dakhla). Right-margin labels: cheapest import vs domestic (red = domestic cheaper). The UAE wins\n"
                 "everywhere (WACC 8.0% vs SAU 8.7%, MAR 10.0% — financing, not resource or distance, decides; the whole sea leg incl. ETS is 10-72 USD/t). Routing, emission anchors (9 g CO2/t-nm 2030,\n"
                 "5 in 2050) and the 50% EU / 50% origin carbon split are ported from the ammonia trade model; kerosene product-tanker costs (MR 2030, LR2 2050) replace the ammonia curve. No producer\n"
                 "margin or certificate value on either side. UK domestic runs carry the UK PtL mandate; Switzerland mirrors ReFuelEU. Model: PtL-SAF siting v8 import case; USD-2024.",
                 fontsize=6.6, color=MUTED)
    fig.tight_layout(rect=(0, 0.01, 0.965, 0.93) if CLEAN else (0, 0.062, 0.965, 0.885))
    fig.savefig(OUT / "fig5_import_vs_domestic.png")
    fig.savefig(OUT / "fig5_import_vs_domestic.pdf")
    plt.close(fig)

# sanity: breakdown components vs delivered
print("max |total - delivered| in breakdown:", bdf["check_diff"].abs().max(), "USD/t")
print("figures written to", OUT)
