from __future__ import annotations
from pathlib import Path
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import LineString

from dataclasses import replace as dc_replace

from .airports import load_airports
from .process import make_default_process, ProcessDemand
from .economics import (
    EconomicAssumptions,
    annualized_process_cost,
    annualized_transport_cost,
    annualized_water_cost,
    annualized_electrolyser_unit_cost_usd_per_mw,
    make_economic_assumptions_from_literature,
    make_process_params_from_literature,
    load_country_finance,
)
from .geography import load_country_geometry, make_candidate_points_within_country, country_area_km2
from .resources import monthly_resource_profiles, infer_boundary_proximity, infer_island_factor
from .optimizer import optimise_mix_for_site, optimise_mix_for_site_v3
from .carbon import load_country_biogenic, biogenic_co2_supply
from .utils import log, haversine_km

PHI_GRID = (1.0, 1.05, 1.1, 1.15, 1.3, 1.5, 1.7, 1.9)


def load_airport_fuel(base_dir: Path) -> pd.DataFrame:
    fp = base_dir / "src" / "saf_eu_model" / "data" / "airport_fuel.csv"
    return pd.read_csv(fp) if fp.exists() else pd.DataFrame()


def load_policy_demand(base_dir: Path) -> dict:
    import json
    fp = base_dir / "src" / "saf_eu_model" / "data" / "policy_demand.json"
    if not fp.exists():
        return {}
    with fp.open("r", encoding="utf-8") as f:
        return json.load(f)


def mandate_tonnes_for_airport(base_dir: Path, iso3: str, scenario_year: int) -> tuple[float | None, dict]:
    """Hub-airport e-SAF demand: fuel uplift 2024 x growth(year) x synthetic share.
    Returns (tonnes or None if no mandate applies, diagnostics)."""
    fuel = load_airport_fuel(base_dir)
    pol = load_policy_demand(base_dir)
    if fuel.empty or not pol:
        return None, {}
    row = fuel[fuel.country_iso3 == iso3]
    if row.empty:
        return None, {}
    fuel_kt = float(row.iloc[0].fuel_kt_2024)
    growth = float(pol.get("fuel_growth_factor_vs_2024", {}).get(str(scenario_year), 1.0))
    shares = pol.get("synthetic_share", {})
    jur = iso3 if iso3 in shares else ("EU" if iso3 not in ("SAU", "ARE", "MAR") else None)
    if jur is None:
        return None, {"fuel_kt_2024": fuel_kt, "note": "no mandate (reference case)"}
    share = shares[jur].get(str(scenario_year))
    if share is None:
        return None, {"fuel_kt_2024": fuel_kt, "note": f"no share for {scenario_year}"}
    tonnes = fuel_kt * 1000.0 * growth * float(share)
    return tonnes, {
        "fuel_kt_2024": fuel_kt, "fuel_growth_factor": growth,
        "synthetic_share": float(share), "mandate_jurisdiction": jur,
    }

# ---------------------------------------------------------------------------
# v6 runner. Changes vs original:
# - model_version switch ("v2" recommended / "legacy" exact reproduction)
#   threads the corrected process accounting, energy-correct availability,
#   physically-closed optimiser, physical NASA conversion and modal transport
#   through the whole evaluation (findings P1-P8).
# - all_candidate_sites*.csv now REALLY contains all candidates: screened-out
#   rows are kept and labelled via candidate_validity_note instead of being
#   dropped before writing (bug B2).
# - candidates whose delivery line to the airport starts on a different
#   polygon (island) than the airport are flagged crosses_water (finding P17).
# - infeasible optimiser designs are screened out explicitly rather than
#   silently truncated.
# ---------------------------------------------------------------------------


def _currency_factor(currency: str, usd_to_eur: float) -> float:
    """Internal unit is real USD-2024; EUR reporting at the 2024 average rate."""
    if currency.upper() == "EUR":
        return usd_to_eur
    return 1.0


def _add_reporting_columns(df: pd.DataFrame, usd_to_eur: float) -> pd.DataFrame:
    df = df.copy()
    cost_cols = [c for c in df.columns if c.endswith("_usd") or c.endswith("_usd_per_tonne_saf")]
    for c in cost_cols:
        eur_c = c.replace("_usd_per_tonne_saf", "_eur_per_tonne_saf").replace("_usd", "_eur")
        df[eur_c] = df[c] * usd_to_eur
    return df


def _polygon_index_of_point(geom, lon: float, lat: float) -> int:
    """Index of the polygon (within a MultiPolygon) containing/nearest the point."""
    from shapely.geometry import Point
    p = Point(lon, lat)
    if geom.geom_type != "MultiPolygon":
        return 0
    best_i, best_d = 0, float("inf")
    for i, g in enumerate(geom.geoms):
        if g.contains(p):
            return i
        d = g.distance(p)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _flexible_share_from_process(process: ProcessDemand) -> float:
    """Share of electrical demand that can follow generation via the H2 buffer:
    electrolysis plus H2 compression (both upstream of the H2 store)."""
    h2_compression_kwh_per_kg = getattr(process, "h2_compression_kwh_per_kg_fuel", 0.0)
    if h2_compression_kwh_per_kg <= 0.0:
        h2_kg_per_kg_fuel = process.h2_tonnes_per_year * 1000.0 / process.fuel_kg_per_year
        h2_compression_kwh_per_kg = 0.66 * h2_kg_per_kg_fuel
    flex = (process.electrolysis_kwh_per_kg_fuel + h2_compression_kwh_per_kg) / max(process.elec_kwh_per_kg_fuel, 1e-9)
    return min(max(flex, 0.5), 0.95)


def evaluate_candidate(
    iso3: str,
    airport_row,
    geom,
    process: ProcessDemand,
    econ: EconomicAssumptions,
    lat: float,
    lon: float,
    resource_source: str = "auto",
    resource_cache_dir: Path | None = None,
    resource_timeout_s: float = 12.0,
    model_version: str = "v2",
    wind_method: str = "weibull",
    carbon_contexts: list | None = None,
) -> dict:
    from shapely.geometry import Point

    legacy = model_version == "legacy"
    p = Point(lon, lat)
    bprox = infer_boundary_proximity(geom, p)
    island = infer_island_factor(iso3)
    prof = monthly_resource_profiles(
        lat,
        lon,
        bprox,
        island,
        source=resource_source,
        cache_dir=resource_cache_dir,
        timeout_s=resource_timeout_s,
        calibration="legacy" if legacy else "none",
        wind_method="legacy" if legacy else wind_method,
    )

    if model_version == "v3":
        # v8: per site and per carbon mode, evaluate BOTH operating strategies -
        # (a) "flex_fuel_store": seasonally flexible plant + finished-fuel tanks
        #     (optimise_mix_for_site_v3; no seasonal H2 store), and
        # (b) "steady_h2_store": constant-output plant + seasonal H2 storage
        #     (the v2 optimiser, geology-priced caverns) -
        # and keep the cheaper. Winter-peaked wind sites often favour (b);
        # summer-peaked solar and biogenic-carbon sites favour (a).
        best = None
        gc_km_ = haversine_km(lat, lon, float(airport_row.airport_lat), float(airport_row.airport_lon))
        for ctx in (carbon_contexts or []):
            proc_obj = ctx["process"]
            trn_ = annualized_transport_cost(gc_km_, proc_obj.fuel_tonnes_per_year, econ, mode="auto")
            wat_ = annualized_water_cost(proc_obj, econ)
            const_terms = (
                ctx["annual_co2_purchase_usd"]
                + trn_["annual_transport_cost_usd"]
                + wat_["annual_water_cost_usd"]
            )
            # (a) flexible plant + fuel storage
            mix_a = optimise_mix_for_site_v3(
                proc_obj.average_power_mw,
                prof["solar_cf_monthly"],
                prof["wind_cf_monthly"],
                lat,
                econ,
                annual_tonnes=proc_obj.fuel_tonnes_per_year,
                process_cost_by_phi=ctx["process_cost_by_phi"],
                flexible_share=_flexible_share_from_process(proc_obj),
                plant_availability=proc_obj.availability,
            )
            cands = []
            if mix_a["mix_feasible"]:
                total_a = (mix_a["annual_renewable_cost_usd"]
                           + mix_a["annual_process_cost_usd_selected"] + const_terms)
                cands.append({"mix": mix_a, "strategy": "flex_fuel_store", "total": total_a})
            # (b) steady plant + seasonal H2 store (phi = 1 process cost)
            mix_b = optimise_mix_for_site(
                proc_obj.average_power_mw,
                prof["solar_cf_monthly"],
                prof["wind_cf_monthly"],
                lat,
                econ,
                flexible_share=_flexible_share_from_process(proc_obj),
                h2_path_efficiency=econ.h2_path_efficiency,
                electrolyser_annualized_usd_per_mw=annualized_electrolyser_unit_cost_usd_per_mw(econ),
                plant_availability=proc_obj.availability,
                method="v2",
            )
            if mix_b.get("mix_feasible", False):
                proc_b = ctx["process_cost_by_phi"][1.0]
                mix_b = dict(mix_b)
                mix_b["annual_process_cost_usd_selected"] = proc_b["annual_process_cost_usd"]
                mix_b["synthesis_oversize"] = 1.0
                mix_b["synthesis_load_min"] = 1.0
                mix_b["synthesis_load_max"] = 1.0
                mix_b["fuel_storage_tonnes"] = 0.0
                mix_b["fuel_storage_days"] = 0.0
                total_b = (mix_b["annual_renewable_cost_usd"]
                           + proc_b["annual_process_cost_usd"] + const_terms)
                cands.append({"mix": mix_b, "strategy": "steady_h2_store", "total": total_b})
            if not cands:
                cands = [{"mix": mix_a, "strategy": "infeasible", "total": float("inf")}]
            for cand in cands:
                cand.update({"ctx": ctx, "trn": trn_, "wat": wat_})
                if best is None or cand["total"] < best["total"]:
                    best = cand
        mix, ctx = best["mix"], best["ctx"]
        proc_obj = ctx["process"]
        if not mix["mix_feasible"]:
            gc_km = haversine_km(lat, lon, float(airport_row.airport_lat), float(airport_row.airport_lon))
            trn = annualized_transport_cost(gc_km, proc_obj.fuel_tonnes_per_year, econ, mode="auto")
            wat = annualized_water_cost(proc_obj, econ)
        else:
            gc_km, trn, wat = None, best["trn"], best["wat"]
            gc_km = haversine_km(lat, lon, float(airport_row.airport_lat), float(airport_row.airport_lon))
        distance = gc_km * econ.road_distance_factor
        proc_annual = mix["annual_process_cost_usd_selected"]
        gross_annual_cost = best["total"] if mix["mix_feasible"] else float("inf")
        annual_coproduct_credit = econ.coproduct_credit_usd_per_tonne_saf * proc_obj.fuel_tonnes_per_year
        annual_cost = gross_annual_cost - annual_coproduct_credit
        t = proc_obj.fuel_tonnes_per_year
        delivered = annual_cost / t
        supply = ctx["supply"]
        return {
            "lat": lat, "lon": lon,
            "distance_to_airport_km": distance, "great_circle_km": gc_km,
            "boundary_proximity": bprox,
            "crosses_water": bool(_polygon_index_of_point(geom, lon, lat) != _polygon_index_of_point(
                geom, float(airport_row.airport_lon), float(airport_row.airport_lat))),
            "solar_cf_annual": prof["solar_cf_annual"], "wind_cf_annual": prof["wind_cf_annual"],
            "resource_source": prof.get("resource_source", resource_source),
            "resource_error": prof.get("resource_error", ""),
            "raw_nasa_solar_cf_annual": prof.get("raw_nasa_solar_cf_annual", prof["solar_cf_annual"]),
            "raw_nasa_wind_cf_annual": prof.get("raw_nasa_wind_cf_annual", prof["wind_cf_annual"]),
            "nasa_ws50_annual_ms": prof.get("nasa_ws50_annual_ms", float("nan")),
            "solar_mw": mix["solar_mw"], "wind_mw": mix["wind_mw"],
            "renewable_capacity_mw": mix["renewable_capacity_mw"],
            "renewable_capacity_overbuild_ratio": mix["renewable_capacity_overbuild_ratio"],
            "battery_mwh": mix["battery_mwh"],
            "seasonal_h2_storage_mwh": mix.get("seasonal_h2_storage_mwh", 0.0),
            "operating_strategy": best["strategy"],
            "fuel_storage_tonnes": mix["fuel_storage_tonnes"],
            "fuel_storage_days": mix["fuel_storage_days"],
            "synthesis_oversize": mix["synthesis_oversize"],
            "synthesis_load_min": mix["synthesis_load_min"],
            "synthesis_load_max": mix["synthesis_load_max"],
            "carbon_source": ctx["mode"],
            "dac_fraction": proc_obj.dac_fraction,
            "co2_delivered_cost_usd_per_t": supply["co2_delivered_cost_usd_per_t"],
            "co2_mean_haul_km": supply["co2_mean_haul_km"],
            "co2_purchased_t_per_year": supply["co2_purchased_t_per_year"],
            "annual_co2_purchase_usd": ctx["annual_co2_purchase_usd"],
            "annual_generation_mwh": mix.get("annual_generation_mwh"),
            "annual_demand_mwh": mix.get("annual_demand_mwh"),
            "generation_to_demand_ratio": mix.get("generation_to_demand_ratio"),
            "curtailed_energy_frac": mix.get("curtailed_energy_frac", 0.0),
            "electrolyser_oversize_ratio": mix.get("electrolyser_oversize_ratio", 1.0),
            "mix_feasible": mix.get("mix_feasible", True),
            "effective_annual_cf": mix.get("effective_annual_cf"),
            "effective_low_month_cf": mix.get("effective_low_month_cf"),
            "adequacy_cf_used_for_sizing": mix.get("adequacy_cf_used_for_sizing"),
            "annual_renewable_cost_usd": mix["annual_renewable_cost_usd"],
            "annual_process_cost_usd": proc_annual,
            "annual_transport_cost_usd": trn["annual_transport_cost_usd"],
            "annual_water_cost_usd": wat["annual_water_cost_usd"],
            "net_water_m3_per_year": wat["net_water_m3_per_year"],
            "transport_mode": trn.get("transport_mode", "auto"),
            "gross_annual_cost_usd": gross_annual_cost,
            "coproduct_credit_usd_per_tonne_saf": econ.coproduct_credit_usd_per_tonne_saf,
            "annual_coproduct_credit_usd": annual_coproduct_credit,
            "total_annual_cost_usd": annual_cost,
            "delivered_cost_usd_per_tonne_saf": delivered,
            "delivered_cost_energy_allocated_usd_per_tonne_saf": delivered * proc_obj.jet_energy_share,
            "renewable_cost_share": mix["annual_renewable_cost_usd"] / max(annual_cost, 1e-9),
            "process_cost_share": proc_annual / max(annual_cost, 1e-9),
            "transport_cost_share": trn["annual_transport_cost_usd"] / max(annual_cost, 1e-9),
            "water_cost_share": wat["annual_water_cost_usd"] / max(annual_cost, 1e-9),
            "co2_purchase_cost_share": ctx["annual_co2_purchase_usd"] / max(annual_cost, 1e-9),
            "wacc_used": econ.wacc,
            "wacc_process_used": econ.wacc + econ.wacc_process_premium,
            "selected_main_resource": (
                "solar" if mix["solar_share"] >= 0.55 else ("wind" if mix["solar_share"] < 0.15 else "mixed")
            ),
            "solar_share": mix["solar_share"],
            "renewable_capex_usd": mix["renewable_capex_usd"],
            "process_capex_usd": ctx["process_cost_by_phi"][mix["synthesis_oversize"]]["process_capex_usd"]
            if mix["mix_feasible"] else float("inf"),
            "transport_capex_component_usd": trn["transport_capex_component_usd"],
            "fixed_logistics_cost_usd": trn["fixed_logistics_cost_usd"],
        }

    if legacy:
        mix = optimise_mix_for_site(
            process.average_power_mw, prof["solar_cf_monthly"], prof["wind_cf_monthly"], lat, econ,
            method="legacy",
        )
    else:
        mix = optimise_mix_for_site(
            process.average_power_mw,
            prof["solar_cf_monthly"],
            prof["wind_cf_monthly"],
            lat,
            econ,
            flexible_share=_flexible_share_from_process(process),
            h2_path_efficiency=econ.h2_path_efficiency,
            electrolyser_annualized_usd_per_mw=annualized_electrolyser_unit_cost_usd_per_mw(econ),
            plant_availability=process.availability,
            method="v2",
        )

    gc_km = haversine_km(lat, lon, float(airport_row.airport_lat), float(airport_row.airport_lon))
    distance = gc_km * econ.road_distance_factor  # reported road-routed distance (continuity)

    proc = annualized_process_cost(process, econ, basis="legacy" if legacy else "corrected")
    trn = annualized_transport_cost(gc_km, process.fuel_tonnes_per_year, econ, mode="legacy" if legacy else "auto")
    if legacy:
        wat = {"annual_water_cost_usd": 0.0, "water_demand_m3_per_year": 0.0,
               "dac_water_recovered_m3_per_year": 0.0, "net_water_m3_per_year": 0.0}
    else:
        wat = annualized_water_cost(process, econ)

    gross_annual_cost = (
        mix["annual_renewable_cost_usd"]
        + proc["annual_process_cost_usd"]
        + trn["annual_transport_cost_usd"]
        + wat["annual_water_cost_usd"]
    )
    annual_coproduct_credit = econ.coproduct_credit_usd_per_tonne_saf * process.fuel_tonnes_per_year
    annual_cost = gross_annual_cost - annual_coproduct_credit
    delivered = annual_cost / process.fuel_tonnes_per_year
    renewable_share = mix["annual_renewable_cost_usd"] / max(annual_cost, 1e-9)
    process_share = proc["annual_process_cost_usd"] / max(annual_cost, 1e-9)
    transport_share = trn["annual_transport_cost_usd"] / max(annual_cost, 1e-9)
    water_share = wat["annual_water_cost_usd"] / max(annual_cost, 1e-9)

    crosses_water = _polygon_index_of_point(geom, lon, lat) != _polygon_index_of_point(
        geom, float(airport_row.airport_lon), float(airport_row.airport_lat)
    )

    return {
        "lat": lat,
        "lon": lon,
        "distance_to_airport_km": distance,
        "great_circle_km": gc_km,
        "boundary_proximity": bprox,
        "crosses_water": bool(crosses_water),
        "solar_cf_annual": prof["solar_cf_annual"],
        "wind_cf_annual": prof["wind_cf_annual"],
        "resource_source": prof.get("resource_source", resource_source),
        "resource_error": prof.get("resource_error", ""),
        "raw_nasa_solar_cf_annual": prof.get("raw_nasa_solar_cf_annual", prof["solar_cf_annual"]),
        "raw_nasa_wind_cf_annual": prof.get("raw_nasa_wind_cf_annual", prof["wind_cf_annual"]),
        "nasa_ws50_annual_ms": prof.get("nasa_ws50_annual_ms", float("nan")),
        "solar_mw": mix["solar_mw"],
        "wind_mw": mix["wind_mw"],
        "renewable_capacity_mw": mix.get("renewable_capacity_mw", mix["solar_mw"] + mix["wind_mw"]),
        "renewable_capacity_overbuild_ratio": mix.get("renewable_capacity_overbuild_ratio", (mix["solar_mw"] + mix["wind_mw"]) / max(process.average_power_mw, 1e-9)),
        "battery_mwh": mix["battery_mwh"],
        "seasonal_h2_storage_mwh": mix["seasonal_h2_storage_mwh"],
        "annual_generation_mwh": mix.get("annual_generation_mwh"),
        "annual_demand_mwh": mix.get("annual_demand_mwh"),
        "generation_to_demand_ratio": mix.get("generation_to_demand_ratio"),
        "curtailed_energy_frac": mix.get("curtailed_energy_frac", 0.0),
        "electrolyser_oversize_ratio": mix.get("electrolyser_oversize_ratio", 1.0),
        "mix_feasible": mix.get("mix_feasible", True),
        "effective_annual_cf": mix.get("effective_annual_cf"),
        "effective_low_month_cf": mix.get("effective_low_month_cf"),
        "adequacy_cf_used_for_sizing": mix.get("adequacy_cf_used_for_sizing"),
        "annual_renewable_cost_usd": mix["annual_renewable_cost_usd"],
        "annual_process_cost_usd": proc["annual_process_cost_usd"],
        "annual_transport_cost_usd": trn["annual_transport_cost_usd"],
        "annual_water_cost_usd": wat["annual_water_cost_usd"],
        "net_water_m3_per_year": wat["net_water_m3_per_year"],
        "transport_mode": trn.get("transport_mode", "legacy"),
        "gross_annual_cost_usd": gross_annual_cost,
        "coproduct_credit_usd_per_tonne_saf": econ.coproduct_credit_usd_per_tonne_saf,
        "annual_coproduct_credit_usd": annual_coproduct_credit,
        "total_annual_cost_usd": annual_cost,
        "delivered_cost_usd_per_tonne_saf": delivered,
        "delivered_cost_energy_allocated_usd_per_tonne_saf": delivered * process.jet_energy_share,
        "renewable_cost_share": renewable_share,
        "process_cost_share": process_share,
        "transport_cost_share": transport_share,
        "water_cost_share": water_share,
        "wacc_used": econ.wacc,
        "wacc_process_used": econ.wacc + econ.wacc_process_premium,
        "selected_main_resource": (
            "solar" if mix["solar_share"] >= 0.55 else ("wind" if mix["solar_share"] < 0.15 else "mixed")
        ),
        "solar_share": mix["solar_share"],
        "renewable_capex_usd": mix["renewable_capex_usd"],
        "process_capex_usd": proc["process_capex_usd"],
        "transport_capex_component_usd": trn["transport_capex_component_usd"],
        "fixed_logistics_cost_usd": trn["fixed_logistics_cost_usd"],
    }


def plot_country_map(geom, airport_row, df: pd.DataFrame, out_png: Path, title: str, report_currency: str = "EUR", usd_to_eur: float = 0.9239):
    currency = report_currency.upper()
    factor = _currency_factor(currency, usd_to_eur)
    cost = df["delivered_cost_usd_per_tonne_saf"] * factor
    fig, ax = plt.subplots(figsize=(8, 8))
    gpd.GeoSeries([geom], crs=4326).plot(ax=ax, facecolor="#f2f2f2", edgecolor="black", linewidth=1.2)
    sc = ax.scatter(df["lon"], df["lat"], c=cost, s=38, cmap="viridis", label="Candidate sites")
    ax.scatter([airport_row.airport_lon], [airport_row.airport_lat], marker="*", s=250, color="red", edgecolor="black", linewidth=0.6, label=f"Airport ({airport_row.airport_iata})")
    best = df.iloc[0]
    ax.scatter([best.lon], [best.lat], marker="X", s=170, color="black", label="Selected production site")
    ax.plot([best.lon, airport_row.airport_lon], [best.lat, airport_row.airport_lat], color="black", linewidth=1.2, linestyle="--", label="Delivery route (geodesic proxy)")
    cbar = fig.colorbar(sc, ax=ax, shrink=0.75)
    cbar.set_label(f"Delivered SAF cost ({currency}/t)")
    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.legend(loc="best")
    plt.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)


def plot_summary_map(base_dir: Path, summaries: pd.DataFrame, out_png: Path, report_currency: str = "EUR", usd_to_eur: float = 0.9239):
    currency = report_currency.upper()
    factor = _currency_factor(currency, usd_to_eur)
    countries = gpd.read_file(base_dir / "src" / "saf_eu_model" / "data" / "countries.geojson")
    airports = load_airports(base_dir)
    sm = summaries.copy()
    if "airport_lat" not in sm.columns or "airport_lon" not in sm.columns:
        sm = sm.merge(airports[["country_iso3", "airport_lat", "airport_lon", "case_group"]], on="country_iso3", how="left")
    sm[f"delivered_cost_{currency.lower()}_per_tonne_saf"] = sm["delivered_cost_usd_per_tonne_saf"] * factor
    mapdf = countries.merge(sm[["country_iso3", f"delivered_cost_{currency.lower()}_per_tonne_saf"]], on="country_iso3", how="inner")
    pts = gpd.GeoDataFrame(sm, geometry=gpd.points_from_xy(sm["best_lon"], sm["best_lat"]), crs=4326)
    airports_gdf = gpd.GeoDataFrame(sm, geometry=gpd.points_from_xy(sm["airport_lon"], sm["airport_lat"]), crs=4326)

    minx = min(sm["best_lon"].min(), sm["airport_lon"].min()) - 5
    maxx = max(sm["best_lon"].max(), sm["airport_lon"].max()) + 5
    miny = min(sm["best_lat"].min(), sm["airport_lat"].min()) - 5
    maxy = max(sm["best_lat"].max(), sm["airport_lat"].max()) + 5

    fig, ax = plt.subplots(figsize=(13, 10))
    mapdf.plot(column=f"delivered_cost_{currency.lower()}_per_tonne_saf", ax=ax, cmap="YlGnBu", legend=True, edgecolor="black", linewidth=0.6)
    for _, r in sm.iterrows():
        ax.plot([r["best_lon"], r["airport_lon"]], [r["best_lat"], r["airport_lat"]], color="0.25", linewidth=0.7, alpha=0.7)
    airports_gdf.plot(ax=ax, marker="*", color="red", edgecolor="black", markersize=70, label="Airport")
    pts.plot(ax=ax, marker="o", color="black", markersize=28, label="Selected production site")
    for _, r in sm.iterrows():
        ax.annotate(r["country_iso3"], xy=(r["best_lon"], r["best_lat"]), xytext=(2, 2), textcoords="offset points", fontsize=7)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"Selected SAF production sites, airports, and delivery links ({currency}/t SAF)")
    ax.legend(loc="lower left")
    plt.tight_layout()
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def _case_countries(airports: pd.DataFrame, case_set: str) -> list[str]:
    if case_set == "core":
        return airports.loc[airports.get("case_group", "core_eu27_uk_ch") == "core_eu27_uk_ch", "country_iso3"].tolist()
    if case_set == "renewable_rich":
        return airports.loc[airports.get("case_group", "") == "renewable_rich_reference", "country_iso3"].tolist()
    if case_set == "all":
        return airports["country_iso3"].tolist()
    raise ValueError(f"Unknown case_set: {case_set}")


def run_country(
    base_dir: Path,
    iso3: str,
    step_deg: float = 0.5,
    output_root: str = "results",
    cell_size_km: float | None = None,
    max_candidates: int | None = None,
    resource_source: str = "auto",
    resource_timeout_s: float = 12.0,
    max_delivery_distance_km: float = 650.0,
    econ: EconomicAssumptions | None = None,
    scenario_label: str = "base",
    annual_saf_tonnes: float = 740000.0,
    plant_availability: float | None = None,
    report_currency: str = "EUR",
    usd_to_eur: float = 0.9239,
    write_files: bool = True,
    model_version: str = "v3",
    wind_method: str = "weibull",
    process_params: dict | None = None,
    wacc_mode: str = "country",
    sizing: str = "fixed",
    scenario_year: int = 2030,
    carbon_sourcing: str = "auto",
    destination_override: dict | None = None,
):
    """destination_override: optional {'name','iata','lat','lon'} replacing the
    hub airport as the delivery point (v8 import case: an export sea terminal).
    Default None reproduces all existing behaviour exactly."""
    econ = econ or EconomicAssumptions()
    legacy = model_version == "legacy"
    process_params = dict(process_params or {})
    if plant_availability is None:
        plant_availability = float(process_params.get("plant_availability", 0.90))
    process_kwargs = {k: v for k, v in process_params.items() if k != "plant_availability"}

    # --- v8 mandate-driven plant sizing (hub-airport e-SAF demand) ----------
    sizing_info: dict = {}
    if sizing == "mandate" and not legacy:
        tonnes, sizing_info = mandate_tonnes_for_airport(base_dir, iso3, scenario_year)
        if tonnes is not None:
            annual_saf_tonnes = tonnes
        else:
            sizing_info = dict(sizing_info)
            sizing_info.setdefault("note", "no mandate; fixed reference size retained")

    # --- v7 country finance: real pre-tax WACC and H2-storage geology -------
    # (never applied on the legacy path, which reproduces v5 exactly)
    h2_geology = "uniform"
    if not legacy:
        cf = load_country_finance(base_dir)
        entry = cf.get(iso3)
        if entry:
            updates = {}
            if wacc_mode == "country" and "wacc_real" in entry:
                updates["wacc"] = float(entry["wacc_real"]) + econ.wacc_country_delta
            h2_geology = entry.get("h2_geology", "uniform")
            if h2_geology == "salt":
                updates["h2_storage_capex_usd_per_kwh"] = econ.h2_storage_capex_cavern_usd_per_kwh
            elif h2_geology == "rock":
                updates["h2_storage_capex_usd_per_kwh"] = econ.h2_storage_capex_rock_usd_per_kwh
            if updates:
                econ = dc_replace(econ, **updates)

    airports = load_airports(base_dir)
    airport_row = airports[airports.country_iso3 == iso3].iloc[0]
    if destination_override is not None:
        airport_row = airport_row.copy()
        airport_row["airport_name"] = destination_override.get("name", airport_row["airport_name"])
        airport_row["airport_iata"] = destination_override.get("iata", airport_row["airport_iata"])
        airport_row["airport_lat"] = float(destination_override["lat"])
        airport_row["airport_lon"] = float(destination_override["lon"])
    process = make_default_process(
        fuel_tonnes_per_year=annual_saf_tonnes,
        availability=plant_availability,
        accounting="legacy" if legacy else "corrected",
        availability_mode="legacy" if legacy else "energy_correct",
        **({} if legacy else process_kwargs),
    )

    # --- v8 carbon sourcing contexts (dac vs purchased biogenic + DAC top-up)
    carbon_contexts: list[dict] = []
    geom = load_country_geometry(base_dir, iso3)
    area_km2 = country_area_km2(geom)
    if model_version == "v3":
        from .economics import annualized_process_cost as _apc
        no_supply = {
            "dac_fraction": 1.0, "co2_purchased_t_per_year": 0.0,
            "co2_delivered_cost_usd_per_t": 0.0, "co2_mean_haul_km": 0.0,
            "co2_supply_radius_km": 0.0, "co2_country_potential_mt": 0.0,
        }
        modes = []
        if carbon_sourcing in ("auto", "dac"):
            modes.append(("dac", 1.0, no_supply))
        if carbon_sourcing in ("auto", "market"):
            bio = load_country_biogenic(base_dir)
            supply = biogenic_co2_supply(process.co2_tonnes_per_year, iso3, area_km2, econ, bio)
            if supply["co2_purchased_t_per_year"] > 0.02 * process.co2_tonnes_per_year:
                modes.append(("market", supply["dac_fraction"], supply))
        for mode, f_dac, supply in modes:
            proc_obj = process if f_dac >= 0.999 else make_default_process(
                fuel_tonnes_per_year=annual_saf_tonnes,
                availability=plant_availability,
                accounting="corrected",
                availability_mode="energy_correct",
                dac_fraction=f_dac,
                **process_kwargs,
            )
            costs_by_phi = {
                phi: _apc(proc_obj, econ, basis="corrected", synthesis_oversize=phi)
                for phi in PHI_GRID
            }
            carbon_contexts.append({
                "mode": mode,
                "process": proc_obj,
                "process_cost_by_phi": costs_by_phi,
                "supply": supply,
                "annual_co2_purchase_usd": supply["co2_purchased_t_per_year"] * supply["co2_delivered_cost_usd_per_t"],
            })

    log(f"=== START {iso3} [{scenario_label}] model_version={model_version} ===")
    if sizing_info:
        log(f"{iso3}: sizing=mandate year={scenario_year} -> plant {annual_saf_tonnes:,.0f} t/y "
            f"(fuel {sizing_info.get('fuel_kt_2024', float('nan')):,.0f} kt x growth {sizing_info.get('fuel_growth_factor', 1.0):.2f} "
            f"x share {sizing_info.get('synthetic_share', 0.0):.3%} [{sizing_info.get('mandate_jurisdiction', '-')}])")
    if carbon_contexts:
        for ctx in carbon_contexts:
            sp = ctx["supply"]
            log(f"{iso3}: carbon mode={ctx['mode']} dac_fraction={ctx['process'].dac_fraction:.2f} "
                f"purchased={sp['co2_purchased_t_per_year']:,.0f} t/y @ {sp['co2_delivered_cost_usd_per_t']:.1f} USD/t "
                f"(haul {sp['co2_mean_haul_km']:.0f} km)")
    if not legacy:
        log(
            f"{iso3}: finance/geology -> wacc={econ.wacc:.3f} (mode={wacc_mode}, process premium={econ.wacc_process_premium:.3f}), "
            f"h2_storage_geology={h2_geology} @ {econ.h2_storage_capex_usd_per_kwh:.2f} USD/kWh"
        )
    log(f"{iso3}: target airport is {airport_row.airport_name} ({airport_row.airport_iata})")
    log(
        f"{iso3}: constant SAF output={process.fuel_tonnes_per_year:,.0f} t/y; availability={plant_availability:.2f}; "
        f"average power demand={process.average_power_mw:.1f} MW (rated {process.rated_power_mw:.1f} MW)"
    )
    log(
        f"{iso3}: process demand -> electrolysis={process.electrolyser_power_mw:.1f} MW, "
        f"DAC+HP={process.dac_power_mw + process.heat_pump_power_mw:.1f} MW, "
        f"compressor={process.compressor_power_mw:.1f} MW"
    )
    log(
        f"{iso3}: economics [{scenario_label}] wacc={econ.wacc:.3f} pv_capex={econ.pv_capex_usd_per_kw:.0f} wind_capex={econ.wind_capex_usd_per_kw:.0f} "
        f"electrolyser_capex={econ.electrolyser_capex_usd_per_kw:.0f} DAC={econ.dac_capex_usd_per_tco2_year:.0f} battery={econ.battery_capex_usd_per_kwh:.0f}"
    )

    chosen_cell_km = float(cell_size_km) if cell_size_km is not None else max(35.0, float(step_deg) * 111.0)
    grid = make_candidate_points_within_country(
        geom,
        cell_size_km=chosen_cell_km,
        max_candidates=max_candidates,
        airport_lat=float(airport_row.airport_lat),
        airport_lon=float(airport_row.airport_lon),
    )

    if area_km2 > 2_500 and (grid["kind"] != "airport_or_nearest_boundary").any():
        grid = grid[grid["kind"] != "airport_or_nearest_boundary"].reset_index(drop=True)

    log(
        f"{iso3}: country area={area_km2:,.0f} km2; created {len(grid)} projected in-country candidates "
        f"with cell_size={chosen_cell_km:.1f} km; resource_source={resource_source}; "
        f"candidate_kinds={dict(grid['kind'].value_counts())}; max_delivery_distance_km={max_delivery_distance_km:.0f}"
    )

    rows = []
    resource_cache_dir = base_dir / output_root / "resource_cache"
    for i, row in grid.reset_index(drop=True).iterrows():
        cand_id = f"C{i:05d}"
        res = evaluate_candidate(
            iso3,
            airport_row,
            geom,
            process,
            econ,
            float(row.lat),
            float(row.lon),
            resource_source=resource_source,
            resource_cache_dir=resource_cache_dir,
            resource_timeout_s=resource_timeout_s,
            model_version=model_version,
            wind_method=wind_method,
            carbon_contexts=carbon_contexts,
        )
        res["candidate_id"] = cand_id
        res["candidate_kind"] = row.get("kind", "grid")
        res["scenario"] = scenario_label
        log(
            f"{iso3}: {i+1}/{len(grid)} candidate={cand_id} kind={row.get('kind', 'grid')} lat={row.lat:.3f} lon={row.lon:.3f} "
            f"src={res['resource_source']} solar_cf={res['solar_cf_annual']:.3f} wind_cf={res['wind_cf_annual']:.3f} "
            f"solar_share={res['solar_share']:.2f} overbuild={res['renewable_capacity_overbuild_ratio']:.2f}x "
            f"W={res['wind_mw']:.1f}MW S={res['solar_mw']:.1f}MW B={res['battery_mwh']:.1f}MWh "
            f"dist={res['distance_to_airport_km']:.1f}km mode={res.get('transport_mode','-')} "
            f"delivered={res['delivered_cost_usd_per_tonne_saf']:.2f} USD/t"
        )
        rows.append(res)
    df = pd.DataFrame(rows)

    df["candidate_validity_note"] = "valid"
    apply_distance_cap = airport_row.get("case_group", "core_eu27_uk_ch") == "core_eu27_uk_ch"
    invalid = (
        (df["solar_cf_annual"] < 0.075) & (df["wind_cf_annual"] < 0.135)
        if not legacy
        else (df["solar_cf_annual"] < 0.075) | (df["wind_cf_annual"] < 0.135)
    )
    invalid = (
        invalid
        | (~df["mix_feasible"].astype(bool))
        | (df["renewable_capex_usd"] > 120_000_000_000)
        | ((df["distance_to_airport_km"] > max_delivery_distance_km) & (area_km2 > 10_000) & apply_distance_cap)
    )
    df.loc[invalid, "candidate_validity_note"] = "screened_out_resource_cost_or_distance_outlier"
    df.loc[~df["mix_feasible"].astype(bool), "candidate_validity_note"] = "screened_out_no_feasible_renewable_design"
    valid_df = df.loc[~invalid].copy()
    if valid_df.empty:
        log(f"{iso3}: WARNING all candidates tripped the plausibility screen; keeping unfiltered candidates for traceability")
        valid_df = df.copy()
    elif invalid.any():
        log(f"{iso3}: screened out {int(invalid.sum())}/{len(df)} candidates before final ranking (kept in all_candidate_sites with notes)")

    valid_df = valid_df.sort_values("delivered_cost_usd_per_tonne_saf").reset_index(drop=True)
    valid_df["resource_rank"] = valid_df["annual_renewable_cost_usd"].rank(method="min")
    valid_df["distance_rank"] = valid_df["distance_to_airport_km"].rank(method="min")
    valid_df["total_rank"] = valid_df["delivered_cost_usd_per_tonne_saf"].rank(method="min")

    # Full table (valid first, then screened-out) so the audit trail is complete.
    screened_df = df.loc[invalid].sort_values("delivered_cost_usd_per_tonne_saf")
    full_df = pd.concat([valid_df, screened_df], ignore_index=True)

    best = valid_df.iloc[0]
    min_resource = valid_df.sort_values("annual_renewable_cost_usd").iloc[0]
    nearest = valid_df.sort_values("distance_to_airport_km").iloc[0]
    log(
        f"{iso3}: diagnostics -> best={best.candidate_id} ({best.candidate_kind}), "
        f"min_resource={min_resource.candidate_id} renew={min_resource.annual_renewable_cost_usd/1e6:.1f}M, "
        f"nearest={nearest.candidate_id} dist={nearest.distance_to_airport_km:.1f}km; "
        f"best ranks: resource={best.resource_rank:.0f}, distance={best.distance_rank:.0f}, total={best.total_rank:.0f}"
    )

    full_df = _add_reporting_columns(full_df, usd_to_eur=usd_to_eur)
    valid_df = _add_reporting_columns(valid_df, usd_to_eur=usd_to_eur)

    if write_files:
        outdir = base_dir / output_root / iso3
        outdir.mkdir(parents=True, exist_ok=True)
        full_df.to_csv(outdir / f"all_candidate_sites_{scenario_label}.csv", index=False)
        valid_df.head(10).to_csv(outdir / f"top_sites_{scenario_label}.csv", index=False)
        if scenario_label == "base":
            full_df.to_csv(outdir / "all_candidate_sites.csv", index=False)
            valid_df.head(10).to_csv(outdir / "top_sites.csv", index=False)
            plot_country_map(geom, airport_row, valid_df, outdir / "map.png", f"{iso3} - delivered SAF cost to {airport_row.airport_iata}", report_currency=report_currency, usd_to_eur=usd_to_eur)
            log(f"Saved map to {outdir / 'map.png'}")
        log(f"Saved detailed results to {outdir / f'all_candidate_sites_{scenario_label}.csv'}")
        log(f"Saved top-10 results to {outdir / f'top_sites_{scenario_label}.csv'}")

    best = valid_df.iloc[0].to_dict()
    gross_annual_cost_usd = (
        best["annual_renewable_cost_usd"]
        + best["annual_process_cost_usd"]
        + best["annual_transport_cost_usd"]
        + best.get("annual_water_cost_usd", 0.0)
    )
    annual_coproduct_credit_usd = econ.coproduct_credit_usd_per_tonne_saf * process.fuel_tonnes_per_year
    annual_cost_usd = gross_annual_cost_usd - annual_coproduct_credit_usd
    summary = {
        "country_iso3": iso3,
        "country_name": airport_row.get("country_name", ""),
        "case_group": airport_row.get("case_group", "core_eu27_uk_ch"),
        "airport_name": airport_row.airport_name,
        "airport_iata": airport_row.airport_iata,
        "airport_lat": airport_row.airport_lat,
        "airport_lon": airport_row.airport_lon,
        "scenario": scenario_label,
        "model_version": model_version,
        "annual_saf_tonnes": process.fuel_tonnes_per_year,
        "plant_availability": plant_availability,
        "process_average_power_mw": process.average_power_mw,
        "process_rated_power_mw": process.rated_power_mw,
        "best_candidate_id": best["candidate_id"],
        "best_candidate_kind": best.get("candidate_kind", "grid"),
        "best_lat": best["lat"],
        "best_lon": best["lon"],
        "distance_to_airport_km": best["distance_to_airport_km"],
        "transport_mode": best.get("transport_mode", "legacy"),
        "crosses_water": best.get("crosses_water", False),
        "delivered_cost_usd_per_tonne_saf": best["delivered_cost_usd_per_tonne_saf"],
        "delivered_cost_eur_per_tonne_saf": best["delivered_cost_usd_per_tonne_saf"] * usd_to_eur,
        "delivered_cost_energy_allocated_usd_per_tonne_saf": best.get("delivered_cost_energy_allocated_usd_per_tonne_saf"),
        "annual_renewable_cost_usd": best["annual_renewable_cost_usd"],
        "annual_process_cost_usd": best["annual_process_cost_usd"],
        "annual_transport_cost_usd": best["annual_transport_cost_usd"],
        "annual_water_cost_usd": best.get("annual_water_cost_usd", 0.0),
        "gross_annual_cost_usd": gross_annual_cost_usd,
        "coproduct_credit_usd_per_tonne_saf": econ.coproduct_credit_usd_per_tonne_saf,
        "annual_coproduct_credit_usd": annual_coproduct_credit_usd,
        "renewable_cost_share": best["renewable_cost_share"],
        "process_cost_share": best["process_cost_share"],
        "transport_cost_share": best["transport_cost_share"],
        "water_cost_share": best.get("water_cost_share", 0.0),
        "wacc_used": best.get("wacc_used", econ.wacc),
        "wacc_process_used": best.get("wacc_process_used", econ.wacc),
        "h2_storage_geology": h2_geology,
        "h2_storage_capex_usd_per_kwh_used": econ.h2_storage_capex_usd_per_kwh,
        "sizing_mode": sizing,
        "scenario_year": scenario_year if sizing == "mandate" else None,
        "mandate_synthetic_share": sizing_info.get("synthetic_share"),
        "airport_fuel_kt_2024": sizing_info.get("fuel_kt_2024"),
        "carbon_source": best.get("carbon_source", "dac"),
        "dac_fraction": best.get("dac_fraction", 1.0),
        "co2_delivered_cost_usd_per_t": best.get("co2_delivered_cost_usd_per_t", 0.0),
        "co2_mean_haul_km": best.get("co2_mean_haul_km", 0.0),
        "annual_co2_purchase_usd": best.get("annual_co2_purchase_usd", 0.0),
        "operating_strategy": best.get("operating_strategy", "steady_h2_store"),
        "synthesis_oversize": best.get("synthesis_oversize", 1.0),
        "synthesis_load_min": best.get("synthesis_load_min"),
        "fuel_storage_tonnes": best.get("fuel_storage_tonnes", 0.0),
        "fuel_storage_days": best.get("fuel_storage_days", 0.0),
        "wind_mw": best["wind_mw"],
        "solar_mw": best["solar_mw"],
        "renewable_capacity_mw": best["renewable_capacity_mw"],
        "renewable_capacity_overbuild_ratio": best["renewable_capacity_overbuild_ratio"],
        "battery_mwh": best["battery_mwh"],
        "seasonal_h2_storage_mwh": best["seasonal_h2_storage_mwh"],
        "annual_generation_mwh": best["annual_generation_mwh"],
        "annual_demand_mwh": best["annual_demand_mwh"],
        "generation_to_demand_ratio": best["generation_to_demand_ratio"],
        "curtailed_energy_frac": best.get("curtailed_energy_frac", 0.0),
        "electrolyser_oversize_ratio": best.get("electrolyser_oversize_ratio", 1.0),
        "effective_annual_cf": best["effective_annual_cf"],
        "adequacy_cf_used_for_sizing": best["adequacy_cf_used_for_sizing"],
        "selected_main_resource": best["selected_main_resource"],
        "solar_share": best["solar_share"],
        "solar_cf_annual": best["solar_cf_annual"],
        "wind_cf_annual": best["wind_cf_annual"],
        "resource_source": best["resource_source"],
        "raw_nasa_solar_cf_annual": best.get("raw_nasa_solar_cf_annual", best["solar_cf_annual"]),
        "raw_nasa_wind_cf_annual": best.get("raw_nasa_wind_cf_annual", best["wind_cf_annual"]),
        "candidate_validity_note": best.get("candidate_validity_note", "valid"),
        "resource_rank": best["resource_rank"],
        "distance_rank": best["distance_rank"],
        "renewable_capex_usd": best["renewable_capex_usd"],
        "process_capex_usd": best["process_capex_usd"],
        "transport_capex_component_usd": best["transport_capex_component_usd"],
        "fixed_logistics_cost_usd": best["fixed_logistics_cost_usd"],
        "total_annual_cost_usd": annual_cost_usd,
        "gross_annual_cost_eur": gross_annual_cost_usd * usd_to_eur,
        "total_annual_cost_eur": annual_cost_usd * usd_to_eur,
        "n_candidates_screened_out": int(invalid.sum()),
        "n_candidates_total": int(len(df)),
    }
    log(f"{iso3}: best cell={best['candidate_id']} tech={best['selected_main_resource']} delivered_cost={best['delivered_cost_usd_per_tonne_saf']:.2f} USD/t")
    log(f"=== END {iso3} [{scenario_label}] ===")
    return full_df, summary


def run_batch(
    base_dir: Path,
    countries: list[str] | None = None,
    step_deg: float = 0.5,
    output_root: str = "results",
    scenario: str = "base",
    cell_size_km: float | None = None,
    max_candidates: int | None = None,
    resource_source: str = "auto",
    resource_timeout_s: float = 12.0,
    max_delivery_distance_km: float = 650.0,
    uncertainty_samples: int = 0,
    seed: int = 42,
    case_set: str = "core",
    annual_saf_tonnes: float = 740000.0,
    plant_availability: float | None = None,
    report_currency: str = "EUR",
    usd_to_eur: float = 0.9239,
    model_version: str = "v3",
    wind_method: str = "weibull",
    wacc_mode: str = "country",
    inputs_filename: str = "literature_inputs.json",
    sizing: str = "fixed",
    scenario_year: int = 2030,
    carbon_sourcing: str = "auto",
):
    airports = load_airports(base_dir)
    countries = countries or _case_countries(airports, case_set)

    summaries = []
    econ = make_economic_assumptions_from_literature(base_dir, scenario=scenario, inputs_filename=inputs_filename)
    process_params = make_process_params_from_literature(base_dir, scenario=scenario, inputs_filename=inputs_filename)
    for iso3 in countries:
        _, summary = run_country(
            base_dir,
            iso3,
            step_deg=step_deg,
            output_root=output_root,
            cell_size_km=cell_size_km,
            max_candidates=max_candidates,
            resource_source=resource_source,
            resource_timeout_s=resource_timeout_s,
            max_delivery_distance_km=max_delivery_distance_km,
            econ=econ,
            scenario_label=scenario,
            annual_saf_tonnes=annual_saf_tonnes,
            plant_availability=plant_availability,
            report_currency=report_currency,
            usd_to_eur=usd_to_eur,
            write_files=True,
            model_version=model_version,
            wind_method=wind_method,
            process_params=process_params,
            wacc_mode=wacc_mode,
            sizing=sizing,
            scenario_year=scenario_year,
            carbon_sourcing=carbon_sourcing,
        )
        summaries.append(summary)
    sdf = pd.DataFrame(summaries).sort_values("delivered_cost_usd_per_tonne_saf").reset_index(drop=True)
    out = base_dir / output_root / "summary_all_airports.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    sdf.to_csv(out, index=False)
    for grp, sub in sdf.groupby("case_group", dropna=False):
        sub.to_csv(base_dir / output_root / f"summary_{grp}.csv", index=False)
    try:
        plot_summary_map(base_dir, sdf, base_dir / output_root / "summary_map.png", report_currency=report_currency, usd_to_eur=usd_to_eur)
        log(f"Saved summary map with airport-site lines to {base_dir / output_root / 'summary_map.png'}")
    except Exception as e:
        log(f"WARNING: summary map could not be created: {e}")
    log(f"Saved batch summary to {out}")

    if uncertainty_samples > 0:
        log(f"=== START UNCERTAINTY ANALYSIS ({uncertainty_samples} samples) ===")
        records = []
        for sample in range(uncertainty_samples):
            econ_s = make_economic_assumptions_from_literature(
                base_dir, scenario="sample", seed=seed, sample_index=sample, inputs_filename=inputs_filename
            )
            process_params_s = make_process_params_from_literature(
                base_dir, scenario="sample", seed=seed, sample_index=sample, inputs_filename=inputs_filename
            )
            label = f"sample_{sample:03d}"
            for iso3 in countries:
                _, summary = run_country(
                    base_dir,
                    iso3,
                    step_deg=step_deg,
                    output_root=output_root,
                    cell_size_km=cell_size_km,
                    max_candidates=max_candidates,
                    resource_source=resource_source,
                    resource_timeout_s=resource_timeout_s,
                    max_delivery_distance_km=max_delivery_distance_km,
                    econ=econ_s,
                    scenario_label=label,
                    annual_saf_tonnes=annual_saf_tonnes,
                    plant_availability=plant_availability,
                    report_currency=report_currency,
                    usd_to_eur=usd_to_eur,
                    write_files=False,
                    model_version=model_version,
                    wind_method=wind_method,
                    process_params=process_params_s,
                    wacc_mode=wacc_mode,
                    sizing=sizing,
                    scenario_year=scenario_year,
                    carbon_sourcing=carbon_sourcing,
                )
                summary["sample_index"] = sample
                records.append(summary)
        udf = pd.DataFrame(records)
        (base_dir / output_root).mkdir(parents=True, exist_ok=True)
        udf.to_csv(base_dir / output_root / "uncertainty_samples_all.csv", index=False)
        agg = (
            udf.groupby(["country_iso3", "airport_name", "airport_iata", "case_group"], as_index=False)["delivered_cost_usd_per_tonne_saf"]
            .agg(
                p10=lambda s: s.quantile(0.10),
                p50="median",
                p90=lambda s: s.quantile(0.90),
                mean="mean",
                minimum="min",
                maximum="max",
            )
            .sort_values("p50")
            .reset_index(drop=True)
        )
        agg["p10_eur_per_tonne_saf"] = agg["p10"] * usd_to_eur
        agg["p50_eur_per_tonne_saf"] = agg["p50"] * usd_to_eur
        agg["p90_eur_per_tonne_saf"] = agg["p90"] * usd_to_eur
        agg.to_csv(base_dir / output_root / "uncertainty_summary_by_airport.csv", index=False)
        log(f"Saved uncertainty samples to {base_dir / output_root / 'uncertainty_samples_all.csv'}")
        log(f"Saved uncertainty summary to {base_dir / output_root / 'uncertainty_summary_by_airport.csv'}")

    log("=== BATCH COMPLETE ===")
    print("\n===== FINAL SUMMARY =====\n")
    print(sdf.to_string(index=False))
    return sdf
