"""What the eSAF mandate does to an airline ticket, 2030 and 2050.

Logic (all real USD-2024, linear pass-through as specified):
  blend premium per t of total fuel  = synthetic share x (eSAF delivered - fossil jet)
  extra cost per passenger           = share x premium x fuel burn/pax x cabin factor
  ticket increase (%)                = extra cost per pax / representative fare
Counterfactual = same flight burning 100% fossil jet at the projected price
(bio-SAF blending exists in both worlds and cancels out; only the synthetic
share is priced here). A net-of-ETS variant credits the EU ETS allowances the
fossil counterfactual would need on intra-EEA (short-haul) routes; long-haul
extra-EU routes sit outside the EU ETS (CORSIA prices are negligible).

Sources for the fixed inputs are given in the workbook sheet notes.
"""
from pathlib import Path
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent

EU27 = {"AUT","BEL","BGR","HRV","CYP","CZE","DNK","EST","FIN","FRA","DEU","GRC","HUN",
        "IRL","ITA","LVA","LTU","LUX","MLT","NLD","POL","PRT","ROU","SVK","SVN","ESP","SWE"}

# --- eSAF price faced by airlines: 50% domestic + 50% imported ---------------
# Supply balance mirrors REPowerEU's renewable-hydrogen split (10 Mt domestic
# + 10 Mt imported by 2030): half of each hub's mandate is met by national
# production (v8 mandate runs), half by the cheapest exporter (import case,
# exporters sized to 50% of EU demand). Blend is computed PER HUB, then
# demand-weighted; the band is the weighted p10-p90 of the per-hub blends.
IMPORT_SHARE = 0.50


def weighted_blend(run, year):
    dom = pd.read_csv(BASE / run / "summary_all_airports.csv").set_index("country_iso3")
    dom = dom[dom.index.isin(EU27)]
    imp = pd.read_csv(BASE / "import_case" / "import_best_by_country.csv")
    imp = imp[imp.year == year].set_index("country")["import_delivered_usd_t"]
    blend = (1 - IMPORT_SHARE) * dom["delivered_cost_usd_per_tonne_saf"] + IMPORT_SHARE * imp.reindex(dom.index)
    w = dom["annual_saf_tonnes"]
    s = blend.sort_values()
    cw = w.reindex(s.index).cumsum() / w.sum()
    p10 = s[cw.values >= 0.10].iloc[0]
    p90 = s[cw.values >= 0.90].iloc[0]
    return (blend * w).sum() / w.sum(), p10, p90


ESAF = {2030: weighted_blend("results_v8_2030", 2030), 2050: weighted_blend("results_v8_2050t50", 2050)}

# --- fixed assumptions (mirrored as editable cells in the workbook) ----------
JET_USD_T = 780.0          # IATA: 2024 avg jet 99 USD/bbl x 7.88 bbl/t; held flat in real terms
FUEL_SHARE_OPEX = 0.288    # IATA Global Outlook Dec-2025: fuel = 28.8% of opex (2024)
SHARE = {2030: 0.012, 2050: 0.35}   # ReFuelEU Annex I synthetic shares (avg 2030-31; 2050)
ETS_USD_T_CO2 = {2030: 141.0, 2050: 271.0}   # EUR 130 / 250 x 1.0824 (BNEF-style forecasts)
CO2_T_PER_T_FUEL = 3.16
EFF = {2030: 1.0, 2050: 1.0}        # fleet fuel-burn factor vs 2024 (1.0 = conservative)

SEGMENTS = [  # haul, class, sector km, economy fuel kg/pax, cabin factor, fare USD one-way
    ("Short haul", "Economy",  1250, 30.0, 1.0, 110.0),
    ("Short haul", "Business", 1250, 30.0, 1.5, 330.0),
    ("Long haul",  "Economy",  7000, 182.0, 1.0, 450.0),
    ("Long haul",  "Business", 7000, 182.0, 2.9, 2200.0),
]
# fuel: DEFRA-consistent ~24 g/pax-km short (x1250 km) and ~26 g/pax-km long (x7000 km,
# ICCT transatlantic avg 34 pax-km/L = 23.5 g); cabin factors = DEFRA seat-area multipliers.

rows = []
for year in (2030, 2050):
    mean, lo, hi = ESAF[year]
    sh = SHARE[year]
    for tag, cost in (("central", mean), ("p10", lo), ("p90", hi)):
        prem = cost - JET_USD_T
        prem_net = prem - CO2_T_PER_T_FUEL * ETS_USD_T_CO2[year]  # intra-EEA only
        fuel_pct = 100 * sh * prem / JET_USD_T
        rows.append({"year": year, "band": tag, "segment": "Airline fuel bill", "class": "-",
                     "esaf_usd_t": round(cost), "premium_usd_t": round(prem),
                     "extra_usd_per_pax": None, "ticket_increase_pct": round(fuel_pct, 1),
                     "net_of_ets_pct": None,
                     "note": "increase in the operator's total fuel cost"})
        rows.append({"year": year, "band": tag, "segment": "Uniform pass-through", "class": "-",
                     "esaf_usd_t": round(cost), "premium_usd_t": round(prem),
                     "extra_usd_per_pax": None,
                     "ticket_increase_pct": round(FUEL_SHARE_OPEX * fuel_pct, 1),
                     "net_of_ets_pct": round(FUEL_SHARE_OPEX * 100 * sh * prem_net / JET_USD_T, 1),
                     "note": "same % on every ticket if spread pro-rata over opex"})
        for haul, cls, km, burn, fac, fare in SEGMENTS:
            kg = burn * EFF[year] * fac
            extra = sh * prem * kg / 1000.0
            pct = 100 * extra / fare
            net = 100 * sh * prem_net * kg / 1000.0 / fare if haul == "Short haul" else None
            rows.append({"year": year, "band": tag, "segment": haul, "class": cls,
                         "esaf_usd_t": round(cost), "premium_usd_t": round(prem),
                         "extra_usd_per_pax": round(extra, 2),
                         "ticket_increase_pct": round(pct, 1),
                         "net_of_ets_pct": None if net is None else round(net, 1),
                         "note": f"{km} km, {kg:.0f} kg fuel/pax, fare {fare:.0f} USD"})

out = pd.DataFrame(rows)
out.to_csv(OUT / "ticket_impact_v8.csv", index=False)
pc = out[(out.band == "central")]
print(pc[["year", "segment", "class", "extra_usd_per_pax", "ticket_increase_pct", "net_of_ets_pct"]].to_string(index=False))
print("\neSAF EU-27 demand-weighted USD/t:", {y: tuple(round(v) for v in ESAF[y]) for y in ESAF})
