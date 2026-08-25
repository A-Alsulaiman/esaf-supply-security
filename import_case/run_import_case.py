"""Import case: SAU / ARE / MAR export e-SAF to every EU-27 hub airport.

Chain (all real USD-2024 per tonne of e-SAF):
  production at the best renewables site near the designated export terminal,
  plant sized to the WHOLE EU-27 synthetic mandate (economies of scale),
  delivered to the terminal by the v8 inland-transport model
  + export terminal handling
  + sea shipping on the routed lane network (kerosene product tanker;
    ammonia-model routing, emission anchors and 50/50 carbon treatment)
  + EU ETS on voyage emissions
  + import port handling
  + inland transport import port -> hub airport (v8 road tariff)
  + airport receiving fee (same as domestic).

Import port per EU country = the energy-capable WPI port nearest the hub
airport (landlocked countries use the nearest energy-capable port in any
neighbouring market).

Usage: python run_import_case.py --step ports|routes|production|assemble|all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE / "src"))

from shipping_kerosene import (SeaRouter, load_wpi, find_port, haversine_nm, KM_PER_NM,
                               kerosene_cost_usd_per_t, shipping_co2_t_per_t_cargo,
                               shipping_ets_usd_per_t, best_route_economics, EUR_TO_USD)

# ---------------------------------------------------------------- config ----
# Candidate export terminals per exporter (WPI rows). The production step runs
# the siting optimizer against each candidate and keeps the cheapest
# production + inland-to-terminal chain — the terminal follows the resource
# (e.g. Morocco's Dakhla green-fuel corridor vs the Mohammedia oil port).
EXPORT_TERMINAL_CANDIDATES = {
    "SAU": ["King Fahd Port"],                        # Yanbu industrial port, Red Sea (oil terminal 32 m)
    "ARE": ["Al Fujayrah", "Mina Jabal Ali"],         # outside vs inside Hormuz
    "MAR": ["Mohammedia", "Agadir", "Ad Dakhla"],     # Ad Dakhla = greenfield (Morocco H2 corridor)
}
EU_TOTAL_FUEL_MT_2024 = 32.2      # EASA ReFuelEU ATR 2025: 193 kt SAF = 0.6% of Union uplift
GROWTH = {2030: 1.02, 2050: 1.00}  # same growth factors as v8 policy_demand.json
SYNTH_SHARE = {2030: 0.012, 2050: 0.35}
# Hard cap on the exporters' addressable market: 50% of EU e-SAF demand is
# met by imports, mirroring the REPowerEU balance for renewable hydrogen
# (10 Mt domestic + 10 Mt imported by 2030). Exporters size to this share.
EXPORT_SHARE_OF_EU_DEMAND = 0.50
VESSEL_BY_YEAR = {2030: "MR", 2050: "LR2"}
EU_ETS_USD_PER_TCO2 = {2030: 141.0, 2050: 271.0}   # same as the Ticket impact sheet
ORIGIN_CARBON_USD = {"SAU": 0.0, "ARE": 0.0, "MAR": 0.0}
PORT_HANDLING_USD_PER_T = 5.0     # tank storage + (un)loading, each end (editable)
MIN_OIL_DEPTH_M = 9.0             # import/export port eligibility screen
LANDLOCKED = {"AUT", "CZE", "HUN", "LUX", "SVK", "CHE"}   # landlocked markets in set
# Import markets = every country with a modelled hub airport (EU-27 + UK + CH).
# Exporter plant sizing stays pegged to EU-27 demand (the mandate driving it).
EU27 = ["AUT","BEL","BGR","HRV","CYP","CZE","DNK","EST","FIN","FRA","DEU","GRC","HUN",
        "IRL","ITA","LVA","LTU","LUX","MLT","NLD","POL","PRT","ROU","SVK","SVN","ESP","SWE"]
MARKETS = EU27 + ["GBR", "CHE"]

ROAD_USD_PER_TKM = 0.092004
ROAD_FACTOR = 1.35
AIRPORT_FEE_USD_PER_T = 5.412


def eu_demand_tonnes(year):
    return (EU_TOTAL_FUEL_MT_2024 * 1e6 * GROWTH[year] * SYNTH_SHARE[year]
            * EXPORT_SHARE_OF_EU_DEMAND)


# ------------------------------------------------------------ step: ports ---

def energy_ports(wpi):
    od = pd.to_numeric(wpi["Oil Terminal Depth (m)"], errors="coerce").fillna(0.0)
    ot = wpi["Facilities - Oil Terminal"].astype(str).str.upper() == "YES"
    lb = wpi["Facilities - Liquid Bulk"].astype(str).str.upper() == "YES"
    big = wpi["Harbor Size"].isin(["Large", "Medium"])
    return wpi[(od >= MIN_OIL_DEPTH_M) | ot | (lb & big)].copy()

# WPI country names for EU coastal markets (import-port search space)
WPI_EU_NAMES = {
    "BEL": "Belgium", "BGR": "Bulgaria", "HRV": "Croatia", "CYP": "Cyprus",
    "DNK": "Denmark", "EST": "Estonia", "FIN": "Finland", "FRA": "France",
    "DEU": "Germany", "GRC": "Greece", "IRL": "Ireland", "ITA": "Italy",
    "LVA": "Latvia", "LTU": "Lithuania", "MLT": "Malta", "NLD": "Netherlands",
    "POL": "Poland", "PRT": "Portugal", "ROU": "Romania", "SVN": "Slovenia",
    "ESP": "Spain", "SWE": "Sweden", "GBR": "United Kingdom",
}


def select_import_ports():
    wpi = load_wpi()
    ep = energy_ports(wpi)
    airports = pd.read_csv(BASE / "src" / "saf_eu_model" / "data" / "major_airports.csv").set_index("country_iso3")
    eu_pool = ep[ep["Country Code"].isin(WPI_EU_NAMES.values())]
    rows = []
    for iso in MARKETS:
        a = airports.loc[iso]
        screen = "energy"
        if iso in LANDLOCKED:
            pool = eu_pool          # landlocked: nearest foreign energy port
        else:
            pool = ep[ep["Country Code"] == WPI_EU_NAMES.get(iso, "")]
            if pool.empty:
                # coastal country whose ports carry no WPI oil attributes
                # (SVN Koper, MLT Valletta): keep the NATIONAL port per the
                # selection rule, relaxed to any Small+ national harbor
                nat = wpi[(wpi["Country Code"] == WPI_EU_NAMES.get(iso, ""))
                          & wpi["Harbor Size"].isin(["Large", "Medium", "Small"])]
                pool, screen = nat, "relaxed"
        d_nm = pool.apply(lambda p: haversine_nm(p["Longitude"], p["Latitude"],
                                                 a["airport_lon"], a["airport_lat"]), axis=1)
        best = pool.loc[d_nm.idxmin()]
        gc_km = d_nm.min() * KM_PER_NM
        rows.append({
            "country": iso, "airport_iata": a["airport_iata"],
            "import_port": best["Main Port Name"], "port_country": best["Country Code"],
            "port_lat": best["Latitude"], "port_lon": best["Longitude"],
            "port_oil_depth_m": pd.to_numeric(best["Oil Terminal Depth (m)"], errors="coerce"),
            "gc_km_port_to_airport": round(gc_km, 1),
            "road_km_port_to_airport": round(gc_km * ROAD_FACTOR, 1),
            "foreign_port": best["Country Code"] != WPI_EU_NAMES.get(iso, ""),
            "port_screen": screen,
        })
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "import_ports.csv", index=False)
    print(out.to_string(index=False))
    print("written import_ports.csv")
    return out


# ----------------------------------------------------------- step: routes ---

def compute_routes():
    ports = pd.read_csv(HERE / "import_ports.csv")
    prod = pd.read_csv(HERE / "export_production_raw.csv")
    dests = ports[["import_port", "port_country", "port_lat", "port_lon"]].drop_duplicates("import_port")
    origins = prod[["exporter", "export_port", "export_port_lat", "export_port_lon"]].drop_duplicates(
        ["exporter", "export_port"])
    print(f"{len(dests)} unique import ports x {len(origins)} chosen export terminals")
    router = SeaRouter()
    rows = []
    for _, o in origins.iterrows():
        for _, d in dests.iterrows():
            routes = router.route(float(o["export_port_lon"]), float(o["export_port_lat"]),
                                  float(d["port_lon"]), float(d["port_lat"]))
            for r in routes:
                rows.append({"exporter": o["exporter"], "export_port": o["export_port"],
                             "import_port": d["import_port"], **r})
            print(f"  {o['exporter']} ({o['export_port']}) -> {d['import_port']}: "
                  + ", ".join(f"{r['scenario']}={r['distance_nm']:.0f}nm" for r in routes))
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "sea_routes.csv", index=False)
    print("written sea_routes.csv", len(out), "rows")
    return out


# ------------------------------------------------------- step: production ---

def run_export_production():
    from saf_eu_model.economics import (make_economic_assumptions_from_literature,
                                        make_process_params_from_literature)
    from saf_eu_model.runner import run_country
    wpi = load_wpi()
    recs = []
    for year, inputs in [(2030, "literature_inputs.json"), (2050, "literature_inputs_2050.json")]:
        econ = make_economic_assumptions_from_literature(BASE, scenario="base", inputs_filename=inputs)
        pp = make_process_params_from_literature(BASE, scenario="base", inputs_filename=inputs)
        tonnes = eu_demand_tonnes(year)
        for iso, candidates in EXPORT_TERMINAL_CANDIDATES.items():
            trials = []
            for port_name in candidates:
                o = find_port(wpi, port_name)
                dest = {"name": f"{o['Main Port Name']} (export terminal)", "iata": "SEA",
                        "lat": float(o["Latitude"]), "lon": float(o["Longitude"])}
                print(f"\n=== {iso} {year} via {o['Main Port Name']}: {tonnes/1e6:.2f} Mt/y ===")
                _full, s = run_country(
                    BASE, iso, output_root=str(HERE / f"results_export_{year}" / safe(port_name)),
                    cell_size_km=100.0, resource_source="heuristic",
                    econ=econ, scenario_label="base", annual_saf_tonnes=tonnes,
                    report_currency="USD", usd_to_eur=0.9239, write_files=True,
                    model_version="v3", process_params=pp, wacc_mode="country",
                    sizing="fixed", scenario_year=year, carbon_sourcing="dac",
                    destination_override=dest,
                )
                top = pd.read_csv(HERE / f"results_export_{year}" / safe(port_name) / iso / "top_sites_base.csv").iloc[0]
                trials.append({"exporter": iso, "year": year,
                               "export_port": str(o["Main Port Name"]),
                               "export_port_lat": float(o["Latitude"]), "export_port_lon": float(o["Longitude"]),
                               "delivered_cost_usd_per_tonne_saf": float(s["delivered_cost_usd_per_tonne_saf"]),
                               "annual_saf_tonnes": float(s["annual_saf_tonnes"]),
                               "best_lat": float(s["best_lat"]), "best_lon": float(s["best_lon"]),
                               "site_to_terminal_km": float(top["distance_to_airport_km"]),
                               "transport_mode": top.get("transport_mode"),
                               "solar_cf": float(top["solar_cf_annual"]), "wind_cf": float(top["wind_cf_annual"]),
                               "operating_strategy": s.get("operating_strategy"),
                               "wacc_used": float(s.get("wacc_used"))})
                print(f"    -> {trials[-1]['delivered_cost_usd_per_tonne_saf']:.0f} USD/t at terminal "
                      f"({trials[-1]['site_to_terminal_km']:.0f} km inland, {trials[-1]['transport_mode']})")
            best = min(trials, key=lambda t: t["delivered_cost_usd_per_tonne_saf"])
            best["terminals_tried"] = "; ".join(f"{t['export_port']}={t['delivered_cost_usd_per_tonne_saf']:.0f}" for t in trials)
            recs.append(best)
    out = pd.DataFrame(recs)
    out.to_csv(HERE / "export_production_raw.csv", index=False)
    print(out[["exporter", "year", "export_port", "delivered_cost_usd_per_tonne_saf",
               "site_to_terminal_km", "transport_mode", "terminals_tried"]].to_string(index=False))
    return out


def safe(name):
    return "".join(c if c.isalnum() else "_" for c in name)


# --------------------------------------------------------- step: assemble ---

def assemble():
    prod = pd.read_csv(HERE / "export_production_raw.csv")
    routes = pd.read_csv(HERE / "sea_routes.csv")
    ports = pd.read_csv(HERE / "import_ports.csv")
    dom30 = pd.read_csv(BASE / "results_v8_2030" / "summary_all_airports.csv").set_index("country_iso3")
    dom50 = pd.read_csv(BASE / "results_v8_2050t50" / "summary_all_airports.csv").set_index("country_iso3")

    rows = []
    for _, p in ports.iterrows():
        for year in (2030, 2050):
            dom = dom30 if year == 2030 else dom50
            vessel = VESSEL_BY_YEAR[year]
            for exp in EXPORT_TERMINAL_CANDIDATES:
                pr = prod[(prod.exporter == exp) & (prod.year == year)].iloc[0]
                # production delivered at terminal: swap airport fee -> terminal handling
                prod_at_terminal = (pr["delivered_cost_usd_per_tonne_saf"]
                                    - AIRPORT_FEE_USD_PER_T + PORT_HANDLING_USD_PER_T)
                rr = routes[(routes.exporter == exp) & (routes.export_port == pr["export_port"])
                            & (routes.import_port == p["import_port"])]
                best = best_route_economics(rr.to_dict("records"), year, vessel,
                                            EU_ETS_USD_PER_TCO2[year], ORIGIN_CARBON_USD[exp])
                inland = p["road_km_port_to_airport"] * ROAD_USD_PER_TKM
                total = (prod_at_terminal + best["cost_usd_per_t"] + best["ets_usd_per_t"]
                         + PORT_HANDLING_USD_PER_T + inland + AIRPORT_FEE_USD_PER_T)
                dom_cost = float(dom.loc[p["country"], "delivered_cost_usd_per_tonne_saf"]) \
                    if p["country"] in dom.index else None
                rows.append({
                    "country": p["country"], "year": year, "exporter": exp,
                    "export_port": pr["export_port"], "import_port": p["import_port"],
                    "route_scenario": best["scenario"], "distance_nm": round(best["distance_nm"], 0),
                    "uses_suez": best["uses_suez"], "vessel": vessel,
                    "production_usd_t": round(prod_at_terminal, 1),
                    "shipping_usd_t": round(best["cost_usd_per_t"], 1),
                    "ship_co2_kg_t": round(best["co2_t_per_t"] * 1000, 1),
                    "ets_usd_t": round(best["ets_usd_per_t"], 2),
                    "import_port_handling_usd_t": PORT_HANDLING_USD_PER_T,
                    "inland_usd_t": round(inland, 1),
                    "airport_fee_usd_t": AIRPORT_FEE_USD_PER_T,
                    "import_delivered_usd_t": round(total, 1),
                    "domestic_usd_t": None if dom_cost is None else round(dom_cost, 1),
                    "import_vs_domestic_pct": None if dom_cost is None else round(100 * (total / dom_cost - 1), 1),
                })
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "import_delivered_costs.csv", index=False)
    # headline: cheapest exporter per country/year
    best = out.loc[out.groupby(["country", "year"])["import_delivered_usd_t"].idxmin()]
    best.to_csv(HERE / "import_best_by_country.csv", index=False)
    print(best[["country", "year", "exporter", "import_delivered_usd_t", "domestic_usd_t",
                "import_vs_domestic_pct"]].to_string(index=False))
    print("written import_delivered_costs.csv /", len(out), "rows; import_best_by_country.csv")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=["ports", "routes", "production", "assemble", "all"], default="all")
    a = ap.parse_args()
    if a.step in ("ports", "all"):
        select_import_ports()
    if a.step in ("routes", "all"):
        compute_routes()
    if a.step in ("production", "all"):
        run_export_production()
    if a.step in ("assemble", "all"):
        assemble()
