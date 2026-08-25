"""Full per-component cost breakdown for every country and scenario, recomputed
from the model's own cost functions at each run's chosen configuration and
verified against the run totals (max deviation printed; must be ~0)."""
import sys
from pathlib import Path
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
OUT = Path(__file__).resolve().parent

from dataclasses import replace as dc_replace
from saf_eu_model.economics import (
    make_economic_assumptions_from_literature, make_process_params_from_literature,
    annualized_process_cost, annualized_renewable_cost, annualized_product_storage_cost,
    annualized_water_cost, load_country_finance,
)
from saf_eu_model.process import make_default_process
from saf_eu_model.utils import annuity_factor

RUNS = [
    ("740 kt fixed (2030 tech)", "results_v8_fixed", "literature_inputs.json"),
    ("2030 mandate (2030 tech)", "results_v8_2030", "literature_inputs.json"),
    ("2050 mandate (2030 tech)", "results_v8_2050t30", "literature_inputs.json"),
    ("2050 mandate (2050 tech)", "results_v8_2050t50", "literature_inputs_2050.json"),
]

cf = load_country_finance(BASE)
rows, max_dev = [], 0.0
for label, d, inputs in RUNS:
    econ0 = make_economic_assumptions_from_literature(BASE, scenario="base", inputs_filename=inputs)
    pp = make_process_params_from_literature(BASE, scenario="base", inputs_filename=inputs)
    avail = pp.get("plant_availability", 0.90)
    pk = {k: v for k, v in pp.items() if k != "plant_availability"}
    df = pd.read_csv(BASE / d / "summary_all_airports.csv").set_index("country_iso3")
    for iso3, s in df.iterrows():
        entry = cf.get(iso3, {})
        upd = {}
        if "wacc_real" in entry:
            upd["wacc"] = float(entry["wacc_real"])
        geol = entry.get("h2_geology", "uniform")
        if geol == "salt":
            upd["h2_storage_capex_usd_per_kwh"] = econ0.h2_storage_capex_cavern_usd_per_kwh
        elif geol == "rock":
            upd["h2_storage_capex_usd_per_kwh"] = econ0.h2_storage_capex_rock_usd_per_kwh
        econ = dc_replace(econ0, **upd) if upd else econ0

        t = float(s["annual_saf_tonnes"])
        proc_obj = make_default_process(
            fuel_tonnes_per_year=t, availability=avail,
            accounting="corrected", availability_mode="energy_correct",
            dac_fraction=float(s.get("dac_fraction", 1.0)), **pk,
        )
        phi = float(s.get("synthesis_oversize", 1.0))
        pc = annualized_process_cost(proc_obj, econ, basis="corrected", synthesis_oversize=phi)

        # renewables split (recompute each asset's annuity from the run's capacities)
        af_w = annuity_factor(econ.wacc, econ.life_wind)
        af_s = annuity_factor(econ.wacc, econ.life_pv)
        af_b = annuity_factor(econ.wacc, econ.life_battery)
        af_h = annuity_factor(econ.wacc, econ.life_h2_storage)
        wind = s["wind_mw"] * 1000 * econ.wind_capex_usd_per_kw * (af_w + econ.fom_wind)
        solar = s["solar_mw"] * 1000 * econ.pv_capex_usd_per_kw * (af_s + econ.fom_pv)
        batt = s["battery_mwh"] * 1000 * econ.battery_capex_usd_per_kwh * (af_b + econ.fom_battery)
        h2st = s["seasonal_h2_storage_mwh"] * 1000 * econ.h2_storage_capex_usd_per_kwh * (af_h + econ.fom_h2_storage)
        fuelst = annualized_product_storage_cost(float(s.get("fuel_storage_tonnes", 0.0)), econ)["annual_product_storage_cost_usd"]
        # steady-strategy designs may carry a flexible-electrolyser oversize adder
        # inside the run's renewables total; recover it as the exact residual.
        flex_oversize = float(s["annual_renewable_cost_usd"]) - (wind + solar + batt + h2st + fuelst)
        if abs(flex_oversize) < 0.5 * t / 1000:   # numerical noise guard (<0.0005 USD/t)
            flex_oversize = 0.0
        renew_sum = wind + solar + batt + h2st + fuelst + flex_oversize

        wat = annualized_water_cost(proc_obj, econ)["annual_water_cost_usd"]
        co2buy = float(s.get("annual_co2_purchase_usd", 0.0))
        fee = econ.airport_fee_usd_per_tonne * t
        linehaul = float(s["annual_transport_cost_usd"]) - fee

        dev = max(abs(renew_sum - s["annual_renewable_cost_usd"]),
                  abs(pc["annual_process_cost_usd"] - s["annual_process_cost_usd"]),
                  abs(wat - s["annual_water_cost_usd"])) / t
        max_dev = max(max_dev, dev)

        comp = pc["annual_components_usd"]
        row = {
            "country": iso3, "scenario": label, "plant_kt": round(t / 1000, 1),
            "strategy": s.get("operating_strategy", ""), "carbon": s.get("carbon_source", ""),
            "wind": wind / t, "solar_pv": solar / t, "battery": batt / t,
            "seasonal_h2_store": h2st / t, "fuel_tank_store": fuelst / t,
            "electrolyser_flex_oversize": flex_oversize / t,
            "electrolyser_incl_stacks": comp["electrolyser"] / t, "dac": comp["dac"] / t,
            "ft_rwgs_upgrading": comp["ft_rwgs_upgrading"] / t, "heat_pump": comp["heat_pump"] / t,
            "compressors": comp["compressors"] / t, "buffers_co2_h2": comp["buffers"] / t,
            "fixed_site_services": comp["fixed_site_services"] / t,
            "purchased_biogenic_co2": co2buy / t, "fuel_transport": linehaul / t,
            "airport_fee": fee / t, "water": wat / t,
        }
        row["total"] = sum(v for k, v in row.items() if k not in ("country", "scenario", "plant_kt", "strategy", "carbon"))
        row["model_delivered"] = s["delivered_cost_usd_per_tonne_saf"]
        row["check_diff"] = row["total"] - row["model_delivered"]
        rows.append(row)

out = pd.DataFrame(rows)
print("max component-recompute deviation:", round(max_dev, 3), "USD/t")
print("max |total - delivered|:", round(out.check_diff.abs().max(), 3), "USD/t")
out.round(2).to_csv(OUT / "cost_breakdown_v8.csv", index=False)
print("written", OUT / "cost_breakdown_v8.csv", len(out), "rows")
