from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import math
from typing import Any

from .utils import annuity_factor
from .process import ProcessDemand

# ---------------------------------------------------------------------------
# v6 economics module. Changes vs original (each switchable to "legacy"):
#
# 1. TRANSPORT IS NOW MODAL (finding P7). The original ADDED a pipeline
#    linehaul (capex+opex per km) AND a road tariff (USD/t-km) AND pipeline
#    pump energy on the same tonnes over the same kilometres - i.e. it built
#    a pipeline and then also trucked the fuel alongside it. v6 selects the
#    cheaper feasible mode (road, or pipeline where throughput justifies it;
#    rail optional when a sourced tariff is supplied). The legacy behaviour
#    is retained via transport_mode="legacy".
#
# 2. H2 STORAGE COST BASIS UNIFIED (finding P11). The original priced the
#    seasonal store at 2.4 USD/kWh (= 80 CHF/kg H2) but the plant H2 buffer
#    at 12 CHF/kg - a 6.7x internal inconsistency for the same commodity.
#    Both now use h2_storage_capex_usd_per_kwh on an H2-LHV basis.
#
# 3. BUFFERS SIZED IN DAYS (finding P12). The original sized the CO2 buffer
#    at 0.25 YEARS (~ 1.1 Mt of stored CO2) and the H2 buffer at 0.5 YEARS
#    of throughput - physically implausible tank farms. v6 uses explicit
#    co2_buffer_days / h2_buffer_days (defaults 10 and 5 days).
#
# 4. ELECTROLYSER STACK REPLACEMENT (finding P13): stacks last well short of
#    the 25-year plant life; replacement capex (stack_replacement_frac of the
#    electrolyser capex every stack_life_years) is discounted and annualised.
#
# 5. The 60 MCHF/y fixed land/utilities term and the logistics electricity
#    price are now explicit, documented parameters (finding P14) and the
#    fixed term is included in literature_inputs.json so it participates in
#    scenarios and uncertainty sampling.
#
# 6. Uncertainty sampling now uses numpy's Generator instead of the homebrew
#    xorshift hash (finding P15), and can include every parameter present in
#    literature_inputs.json.
# ---------------------------------------------------------------------------


@dataclass
class EconomicAssumptions:
    wacc: float = 0.08
    life_renewables: int = 25
    life_process: int = 25
    life_pipeline: int = 30
    # v7 per-asset lives (defaults = v6 behaviour: everything at 25 y).
    # literature_inputs.json v7 sets life_pv=30, life_wind=25, life_battery=15,
    # life_h2_storage=30 per Fraunhofer ISE / DEA / NREL (see JSON notes).
    life_pv: float = 25.0
    life_wind: float = 25.0
    life_battery: float = 25.0
    life_h2_storage: float = 25.0
    # v7 country-finance extensions (defaults inactive).
    wacc_process_premium: float = 0.0   # process-plant WACC = wacc + premium
    wacc_country_delta: float = 0.0     # sampled perturbation on country WACC

    fom_pv: float = 0.020
    fom_wind: float = 0.035
    fom_battery: float = 0.025
    fom_h2_storage: float = 0.020

    pv_capex_usd_per_kw: float = 700.0
    wind_capex_usd_per_kw: float = 1800.0
    battery_capex_usd_per_kwh: float = 220.0
    h2_storage_capex_usd_per_kwh: float = 2.4
    # v7 geology-dependent seasonal H2 storage (Papadias & Ahluwalia 2021;
    # Caglayan et al. 2020 for the country assignment). The ACTIVE value above
    # is set per country by the runner from these two (salt cavern vs lined
    # rock cavern); both default to the v6 value so behaviour is unchanged
    # until the v7 inputs are loaded.
    h2_storage_capex_cavern_usd_per_kwh: float = 2.4
    h2_storage_capex_rock_usd_per_kwh: float = 2.4
    h2_path_efficiency: float = 0.90    # store-then-feed path (v7 JSON: 0.95)

    pipeline_capex_usd_per_km: float = 3_000_000.0
    pipeline_opex_frac: float = 0.020
    road_variable_usd_per_tkm: float = 0.075
    pump_energy_kwh_per_tkm: float = 0.05
    road_distance_factor: float = 1.35
    pipeline_routing_factor: float = 1.15
    rail_variable_usd_per_tkm: float = 0.0     # disabled unless a sourced tariff is supplied
    rail_routing_factor: float = 1.25
    rail_terminal_usd_per_tonne: float = 0.0   # v7: transloading at both ends
    rail_min_km: float = 150.0                 # v7: rail not offered below this haul
    pipeline_min_tonnes_per_year: float = 1_500_000.0
    electricity_price_usd_per_kwh_logistics: float = 0.10
    airport_terminal_and_tankage_usd_per_year: float = 0.0
    airport_fee_usd_per_tonne: float = 0.0     # v7: receiving/fuel-farm adder per tonne delivered
    # v7 water balance (all default 0 = v6 behaviour of not costing water).
    water_demand_kg_per_kg_h2: float = 0.0
    water_cost_usd_per_m3: float = 0.0
    dac_water_recovery_t_per_tco2: float = 0.0

    electrolyser_capex_usd_per_kw: float = 960.0
    dac_capex_usd_per_tco2_year: float = 600.0
    ft_capex_usd_per_kw_liquid: float = 1600.0
    heat_pump_capex_usd_per_kw: float = 620.0
    compressor_capex_usd_per_kw: float = 260.0
    co2_storage_capex_usd_per_tco2: float = 260.0
    h2_buffer_capex_usd_per_kg: float = 12.0   # legacy only; v6 uses h2_storage_capex
    co2_buffer_days: float = 10.0
    h2_buffer_days: float = 5.0
    stack_replacement_frac: float = 0.35
    stack_life_years: int = 10
    balance_of_plant_frac: float = 0.18
    owner_cost_frac: float = 0.08
    contingency_frac: float = 0.12
    process_opex_frac: float = 0.040
    fixed_land_and_utilities_usd_per_year: float = 60_000_000.0
    coproduct_credit_usd_per_tonne_saf: float = 0.0

    # ---- v8: purchased biogenic CO2 (RFNBO-eligible carbon sourcing) -------
    co2_capture_cost_usd_per_t: float = 75.0       # capture+conditioning at biogenic point sources
    co2_liquefaction_usd_per_t: float = 19.5
    co2_purity_polish_usd_per_t: float = 5.4
    co2_transfer_fixed_usd_per_t: float = 2.2
    co2_truck_usd_per_tkm: float = 0.13
    co2_contractable_share: float = 0.30           # share of mapped potential contractable for e-SAF
    co2_max_haul_km: float = 300.0                 # beyond this, DAC covers the remainder
    # ---- v8: flexible plant + finished-fuel storage ------------------------
    ft_min_load_frac: float = 0.5                  # synthesis minimum load, fraction of installed
    product_storage_usd_per_m3: float = 300.0      # installed jet tankage (API 650-class farm)
    fom_product_storage: float = 0.015
    life_product_storage: float = 30.0
    # ---- v8: capex scale exponents (see scale.py for anchors/sources) ------
    ft_scale_exponent: float = 0.67
    electrolyser_scale_exponent: float = 0.95
    dac_scale_exponent: float = 0.95
    compressor_scale_exponent: float = 0.78
    generic_scale_exponent: float = 0.70
    fixed_cost_scale_exponent: float = 0.60


H2_LHV_KWH_PER_KG = 33.33
CHF_TO_USD_2024 = 1.1362   # inverse of the 2024 ECB/ESTV average USD/CHF 0.8801


def literature_inputs_path(base_dir: Path, filename: str = "literature_inputs.json") -> Path:
    return base_dir / "src" / "saf_eu_model" / "data" / filename


def load_literature_inputs(base_dir: Path, filename: str = "literature_inputs.json") -> dict[str, Any]:
    with literature_inputs_path(base_dir, filename).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_country_finance(base_dir: Path) -> dict[str, Any]:
    """Per-country real pre-tax WACC and seasonal H2 storage geology
    (country_finance.json). Returns {} when the file is absent so v6-style
    uniform runs keep working."""
    fp = base_dir / "src" / "saf_eu_model" / "data" / "country_finance.json"
    if not fp.exists():
        return {}
    with fp.open("r", encoding="utf-8") as f:
        return json.load(f).get("countries", {})


# Physical/process parameters sampled from literature_inputs.json (v7).
# Keys absent from the file (e.g. the preserved v6 file) are simply omitted,
# so make_default_process falls back to its v6 constants.
PROCESS_PARAM_KEYS = (
    "electrolyser_kwh_per_kg_h2",
    "ft_energy_efficiency",
    "jet_energy_share",
    "h2_compression_kwh_per_kg",
    "co2_compression_kwh_per_tco2",
    "dac_electricity_kwh_per_tco2",
    "dac_heat_kwh_per_tco2",
    "heat_pump_cop",
    "plant_availability",
)


def make_process_params_from_literature(
    base_dir: Path,
    scenario: str = "base",
    seed: int | None = None,
    sample_index: int = 0,
    inputs_filename: str = "literature_inputs.json",
) -> dict[str, float]:
    """Scenario/sampled values for the physical process parameters (v7).

    Uses a random stream decoupled from the economics sampler (extra salt in
    the seed sequence) so adding process sampling does not change which
    economic draws a given sample_index receives.
    """
    data = load_literature_inputs(base_dir, inputs_filename)
    params = data.get("parameters", {})
    rng = None
    if scenario == "sample":
        import numpy as np
        rng = np.random.default_rng([abs(int(seed or 0)), int(sample_index), 7])
    out: dict[str, float] = {}
    for name in PROCESS_PARAM_KEYS:
        if name not in params:
            continue
        u = float(rng.random()) if rng is not None else None
        out[name] = _pick_value(params[name], scenario=scenario, u=u)
    return out


def _pick_value(spec: dict[str, float], scenario: str, u: float | None = None) -> float:
    lo = float(spec["min"])
    mid = float(spec["base"])
    hi = float(spec["max"])
    if scenario == "low":
        return lo
    if scenario == "high":
        return hi
    if scenario == "base":
        return mid
    if scenario == "sample":
        if u is None:
            raise ValueError("sample scenario requires a random value")
        if math.isclose(lo, hi):
            return lo
        c = (mid - lo) / (hi - lo)
        if u < c:
            return lo + math.sqrt(u * (hi - lo) * (mid - lo))
        return hi - math.sqrt((1.0 - u) * (hi - lo) * (hi - mid))
    raise ValueError(f"Unknown scenario: {scenario}")


def make_economic_assumptions_from_literature(
    base_dir: Path,
    scenario: str = "base",
    seed: int | None = None,
    sample_index: int = 0,
    inputs_filename: str = "literature_inputs.json",
) -> EconomicAssumptions:
    """Build assumptions from literature_inputs.json.

    FX handling: a parameter entry may carry "currency" and the file meta may
    carry "fx_to_usd": {"USD": .., "EUR": ..}. Values are converted to the
    internal USD unit at load time; without these keys behaviour is unchanged
    (the historical "CHF-equivalent" convention - see review finding P16).
    Sampling uses numpy's PCG64 generator seeded by (seed, sample_index).
    """
    data = load_literature_inputs(base_dir, inputs_filename)
    params = data["parameters"]
    meta = data.get("meta", {}) or {}
    fx = meta.get("fx_to_usd", {}) or {}
    if not fx and meta.get("fx_to_chf"):
        # v7-era file: values tagged in source currencies with CHF-based rates.
        # Chain through CHF->USD at the same 2024-average vintage (1/0.8801):
        # EUR 0.9526*1.1362=1.0824, USD 0.8801*1.1362=1.0000 - exactly the
        # direct 2024 source->USD rates, so v7 numerics carry over losslessly.
        fx = {cur: float(rate) * CHF_TO_USD_2024 for cur, rate in meta["fx_to_chf"].items() if not isinstance(rate, str)}
    fields = asdict(EconomicAssumptions())
    # Accept v6/v7-era parameter names (_chf_) for the renamed _usd_ fields.
    params = {name.replace("_chf", "_usd"): spec for name, spec in params.items()}

    rng = None
    if scenario == "sample":
        import numpy as np
        rng = np.random.default_rng([abs(int(seed or 0)), int(sample_index)])

    for name, spec in params.items():
        if name not in fields:
            continue
        u = float(rng.random()) if rng is not None else None
        val = _pick_value(spec, scenario=scenario, u=u)
        cur = spec.get("currency")
        if cur and cur in fx:
            val *= float(fx[cur])
        fields[name] = val

    return EconomicAssumptions(**fields)


def _wacc_process(econ: EconomicAssumptions) -> float:
    """Process-plant discount rate: country/renewables WACC plus the sampled
    technology-risk premium (v7; premium defaults to 0 = v6 behaviour)."""
    return econ.wacc + econ.wacc_process_premium


def annualized_electrolyser_unit_cost_usd_per_mw(econ: EconomicAssumptions) -> float:
    """Annualised cost of one MW of installed electrolyser capacity, including
    indirects, opex and discounted stack replacement. Used by the optimiser to
    price flexible-operation oversizing."""
    af = annuity_factor(_wacc_process(econ), econ.life_process)
    unit = econ.electrolyser_capex_usd_per_kw * 1000.0
    unit *= (1.0 + econ.balance_of_plant_frac) * (1.0 + econ.owner_cost_frac) * (1.0 + econ.contingency_frac)
    repl_npv_frac = _stack_replacement_npv_frac(econ)
    return unit * (1.0 + repl_npv_frac) * af + unit * econ.process_opex_frac


def _stack_replacement_npv_frac(econ: EconomicAssumptions) -> float:
    """NPV of future stack replacements as a fraction of initial electrolyser capex."""
    frac = 0.0
    t = econ.stack_life_years
    while t < econ.life_process:
        frac += econ.stack_replacement_frac / (1.0 + _wacc_process(econ)) ** t
        t += econ.stack_life_years
    return frac


def annualized_water_cost(process: ProcessDemand, econ: EconomicAssumptions) -> dict:
    """Annual cost of demineralised process water, net of DAC water co-production.

    v7 addition (was not costed in v6). Demand: water_demand_kg_per_kg_h2 x H2
    throughput (IRENA/Bluerisk 2023: withdrawal ~2x stoichiometric incl.
    purification reject and cooling make-up). Credit: solid-sorbent DAC
    co-produces 0.8-2 t H2O per tCO2 (IEA DAC 2022). 1 t = 1 m3.
    All parameters default to 0, reproducing v6 exactly.
    """
    demand_t = process.h2_tonnes_per_year * econ.water_demand_kg_per_kg_h2
    # Only the DAC-captured share co-produces water on site (v8).
    recovered_t = (
        process.co2_tonnes_per_year
        * getattr(process, "dac_fraction", 1.0)
        * econ.dac_water_recovery_t_per_tco2
    )
    net_t = max(demand_t - recovered_t, 0.0)
    annual = net_t * econ.water_cost_usd_per_m3
    return {
        "annual_water_cost_usd": annual,
        "water_demand_m3_per_year": demand_t,
        "dac_water_recovered_m3_per_year": recovered_t,
        "net_water_m3_per_year": net_t,
    }


def annualized_process_cost(
    process: ProcessDemand,
    econ: EconomicAssumptions,
    basis: str = "corrected",
    synthesis_oversize: float = 1.0,
) -> dict:
    """Annualised process-plant cost.

    basis="corrected": FT capex on the full liquid slate, buffers in days on a
    unified H2 storage cost basis, stack replacement included.
    basis="legacy": reproduces the original formula exactly (no v8 features).

    v8: (1) per-unit ECONOMIES OF SCALE - each unit group's capex carries a
    multiplier from scale.py at its effective capacity (plant size x
    synthesis_oversize), anchored so a 740 kt/y plant at oversize 1.0
    reproduces v7 exactly; (2) synthesis_oversize (phi >= 1) sizes the whole
    process train above the steady-state rating for seasonally-flexible
    operation (optimiser v3 decision variable); (3) only the DAC-captured
    share of CO2 (process.dac_fraction) carries DAC capex - purchased
    biogenic CO2 is an opex line handled in the runner.
    """
    from .scale import unit_scale_multiplier
    af = annuity_factor(_wacc_process(econ), econ.life_process)

    if basis == "legacy":
        direct_capex = (
            process.electrolyser_power_mw * 1000 * econ.electrolyser_capex_usd_per_kw
            + process.co2_tonnes_per_year * econ.dac_capex_usd_per_tco2_year
            + process.liquid_output_mw * 1000 * econ.ft_capex_usd_per_kw_liquid
            + process.heat_pump_power_mw * 1000 * econ.heat_pump_capex_usd_per_kw
            + process.compressor_power_mw * 1000 * econ.compressor_capex_usd_per_kw
            + process.co2_tonnes_per_year * 0.25 * econ.co2_storage_capex_usd_per_tco2
            + process.h2_tonnes_per_year * 0.50 * econ.h2_buffer_capex_usd_per_kg
        )
        capex = direct_capex * (1.0 + econ.balance_of_plant_frac)
        capex *= (1.0 + econ.owner_cost_frac)
        capex *= (1.0 + econ.contingency_frac)
        annualized = capex * af + capex * econ.process_opex_frac + econ.fixed_land_and_utilities_usd_per_year
        return {"process_capex_usd": capex, "annual_process_cost_usd": annualized}

    ft_basis_mw = getattr(process, "liquid_output_mw_total", process.liquid_output_mw)
    co2_buffer_t = process.co2_tonnes_per_year * econ.co2_buffer_days / 365.0
    h2_buffer_kg = process.h2_tonnes_per_year * 1000.0 * econ.h2_buffer_days / 365.0
    h2_buffer_capex = h2_buffer_kg * H2_LHV_KWH_PER_KG * econ.h2_storage_capex_usd_per_kwh

    phi = max(float(synthesis_oversize), 1.0)
    plant_kt = process.fuel_tonnes_per_year / 1000.0
    s_eff = plant_kt * phi
    m_ft = unit_scale_multiplier("ft", s_eff, econ)
    m_el = unit_scale_multiplier("electrolyser", s_eff, econ)
    m_dac = unit_scale_multiplier("dac", s_eff, econ)
    m_cmp = unit_scale_multiplier("compressor", s_eff, econ)
    m_gen = unit_scale_multiplier("generic", s_eff, econ)
    m_fix = unit_scale_multiplier("fixed", plant_kt, econ)
    dac_fraction = getattr(process, "dac_fraction", 1.0)

    electrolyser_capex = process.electrolyser_power_mw * 1000 * econ.electrolyser_capex_usd_per_kw * phi * m_el
    direct_capex = (
        electrolyser_capex
        + process.co2_tonnes_per_year * dac_fraction * econ.dac_capex_usd_per_tco2_year * phi * m_dac
        + ft_basis_mw * 1000 * econ.ft_capex_usd_per_kw_liquid * phi * m_ft
        + process.heat_pump_power_mw * 1000 * econ.heat_pump_capex_usd_per_kw * phi * m_gen
        + process.compressor_power_mw * 1000 * econ.compressor_capex_usd_per_kw * phi * m_cmp
        + (co2_buffer_t * econ.co2_storage_capex_usd_per_tco2 + h2_buffer_capex) * m_gen
    )
    capex = direct_capex * (1.0 + econ.balance_of_plant_frac)
    capex *= (1.0 + econ.owner_cost_frac)
    capex *= (1.0 + econ.contingency_frac)

    stack_repl_capex = (
        electrolyser_capex
        * (1.0 + econ.balance_of_plant_frac) * (1.0 + econ.owner_cost_frac) * (1.0 + econ.contingency_frac)
        * _stack_replacement_npv_frac(econ)
    )
    annualized = (
        (capex + stack_repl_capex) * af
        + capex * econ.process_opex_frac
        + econ.fixed_land_and_utilities_usd_per_year * m_fix
    )

    # Per-unit annualised components (v8 transparency): each unit's DIRECT
    # capex share of the annualised total, i.e. every line already includes
    # its proportional share of BoP/owner/contingency and fixed O&M; stack
    # replacement is folded into the electrolyser line; the fixed site term
    # is its own line. Sum of components == annual_process_cost_usd.
    ind = (1.0 + econ.balance_of_plant_frac) * (1.0 + econ.owner_cost_frac) * (1.0 + econ.contingency_frac)
    per_capex_rate = ind * (af + econ.process_opex_frac)   # annualised per unit of direct capex
    direct = {
        "electrolyser": process.electrolyser_power_mw * 1000 * econ.electrolyser_capex_usd_per_kw * phi * m_el,
        "dac": process.co2_tonnes_per_year * dac_fraction * econ.dac_capex_usd_per_tco2_year * phi * m_dac,
        "ft_rwgs_upgrading": ft_basis_mw * 1000 * econ.ft_capex_usd_per_kw_liquid * phi * m_ft,
        "heat_pump": process.heat_pump_power_mw * 1000 * econ.heat_pump_capex_usd_per_kw * phi * m_gen,
        "compressors": process.compressor_power_mw * 1000 * econ.compressor_capex_usd_per_kw * phi * m_cmp,
        "buffers": (co2_buffer_t * econ.co2_storage_capex_usd_per_tco2 + h2_buffer_capex) * m_gen,
    }
    components = {k: v * per_capex_rate for k, v in direct.items()}
    components["electrolyser"] += stack_repl_capex * af
    components["fixed_site_services"] = econ.fixed_land_and_utilities_usd_per_year * m_fix

    return {
        "process_capex_usd": capex,
        "annual_process_cost_usd": annualized,
        "stack_replacement_npv_usd": stack_repl_capex,
        "synthesis_oversize": phi,
        "scale_multiplier_ft": m_ft,
        "plant_kt": plant_kt,
        "annual_components_usd": components,
    }


def annualized_product_storage_cost(capacity_tonnes: float, econ: EconomicAssumptions) -> dict:
    """Finished-fuel (jet) tank farm bridging seasonal production and constant
    delivery (v8). Installed cost per m3 per IEA emergency-stockholding and
    Thunder Said tank-farm benchmarks; jet density 0.8 t/m3."""
    m3 = capacity_tonnes / 0.8
    capex = m3 * econ.product_storage_usd_per_m3
    af = annuity_factor(_wacc_process(econ), econ.life_product_storage)
    return {
        "product_storage_capex_usd": capex,
        "annual_product_storage_cost_usd": capex * (af + econ.fom_product_storage),
        "product_storage_m3": m3,
    }


def annualized_renewable_cost(
    wind_mw: float,
    solar_mw: float,
    battery_mwh: float,
    seasonal_h2_storage_mwh: float,
    econ: EconomicAssumptions,
) -> dict:
    """Annualised renewables + storage cost.

    v7: each asset is annualised over its own economic life (life_pv,
    life_wind, life_battery, life_h2_storage). All four default to 25 y, so
    with v6 inputs this reproduces the previous single-annuity behaviour
    exactly; the v7 inputs set PV 30 / wind 25 / battery 15 / cavern 30 y.
    """
    af_wind = annuity_factor(econ.wacc, econ.life_wind)
    af_pv = annuity_factor(econ.wacc, econ.life_pv)
    af_batt = annuity_factor(econ.wacc, econ.life_battery)
    af_h2 = annuity_factor(econ.wacc, econ.life_h2_storage)
    wind_capex = wind_mw * 1000 * econ.wind_capex_usd_per_kw
    solar_capex = solar_mw * 1000 * econ.pv_capex_usd_per_kw
    batt_capex = battery_mwh * 1000 * econ.battery_capex_usd_per_kwh
    h2_capex = seasonal_h2_storage_mwh * 1000 * econ.h2_storage_capex_usd_per_kwh
    annual = (
        wind_capex * (af_wind + econ.fom_wind)
        + solar_capex * (af_pv + econ.fom_pv)
        + batt_capex * (af_batt + econ.fom_battery)
        + h2_capex * (af_h2 + econ.fom_h2_storage)
    )
    return {
        "renewable_capex_usd": wind_capex + solar_capex + batt_capex + h2_capex,
        "annual_renewable_cost_usd": annual,
    }


def annualized_transport_cost_legacy(distance_km: float, fuel_tonnes_per_year: float, econ: EconomicAssumptions) -> dict:
    """Original transport model (pipeline capex + road tariff + pump on the
    SAME tonnes and kilometres). Kept only for A/B comparison."""
    af = annuity_factor(econ.wacc, econ.life_pipeline)
    fixed_logistics = econ.airport_terminal_and_tankage_usd_per_year
    linehaul_capex = distance_km * econ.pipeline_capex_usd_per_km * (af + econ.pipeline_opex_frac)
    variable = distance_km * fuel_tonnes_per_year * econ.road_variable_usd_per_tkm
    pump = distance_km * fuel_tonnes_per_year * econ.pump_energy_kwh_per_tkm * 0.10
    annual = fixed_logistics + linehaul_capex + variable + pump
    return {
        "annual_transport_cost_usd": annual,
        "transport_capex_component_usd": linehaul_capex,
        "fixed_logistics_cost_usd": fixed_logistics,
        "transport_mode": "legacy_pipeline_plus_road",
    }


def annualized_transport_cost(
    great_circle_km: float,
    fuel_tonnes_per_year: float,
    econ: EconomicAssumptions,
    mode: str = "auto",
) -> dict:
    """Modal fuel-delivery cost from plant gate to airport.

    great_circle_km is the UNROUTED distance; each mode applies its own
    routing factor. mode="auto" picks the cheapest feasible mode; pipeline is
    only feasible above pipeline_min_tonnes_per_year (a dedicated products
    pipeline is not credible for sub-Mt annual flows). mode="legacy" restores
    the original additive formula.
    """
    if mode == "legacy":
        return annualized_transport_cost_legacy(great_circle_km * econ.road_distance_factor, fuel_tonnes_per_year, econ)

    af = annuity_factor(econ.wacc, econ.life_pipeline)
    # v7: fixed logistics = legacy fixed term + the per-tonne airport
    # receiving/fuel-farm adder (Vienna Entgeltordnung / Perth JFI anchored;
    # 0 by default = v6 behaviour).
    fixed_logistics = (
        econ.airport_terminal_and_tankage_usd_per_year
        + econ.airport_fee_usd_per_tonne * fuel_tonnes_per_year
    )
    options: dict[str, dict] = {}

    road_km = great_circle_km * econ.road_distance_factor
    options["road"] = {
        "annual_transport_cost_usd": fixed_logistics + road_km * fuel_tonnes_per_year * econ.road_variable_usd_per_tkm,
        "transport_capex_component_usd": 0.0,
        "transport_distance_routed_km": road_km,
    }

    if econ.rail_variable_usd_per_tkm > 0.0:
        rail_km = great_circle_km * econ.rail_routing_factor
        if rail_km >= econ.rail_min_km:
            options["rail"] = {
                "annual_transport_cost_usd": (
                    fixed_logistics
                    + rail_km * fuel_tonnes_per_year * econ.rail_variable_usd_per_tkm
                    + econ.rail_terminal_usd_per_tonne * fuel_tonnes_per_year
                ),
                "transport_capex_component_usd": 0.0,
                "transport_distance_routed_km": rail_km,
            }

    if fuel_tonnes_per_year >= econ.pipeline_min_tonnes_per_year:
        pipe_km = great_circle_km * econ.pipeline_routing_factor
        linehaul = pipe_km * econ.pipeline_capex_usd_per_km * (af + econ.pipeline_opex_frac)
        pump = pipe_km * fuel_tonnes_per_year * econ.pump_energy_kwh_per_tkm * econ.electricity_price_usd_per_kwh_logistics
        options["pipeline"] = {
            "annual_transport_cost_usd": fixed_logistics + linehaul + pump,
            "transport_capex_component_usd": linehaul,
            "transport_distance_routed_km": pipe_km,
        }

    if mode != "auto":
        if mode not in options:
            raise ValueError(f"Transport mode '{mode}' not feasible/enabled for {fuel_tonnes_per_year:,.0f} t/y")
        chosen_name, chosen = mode, options[mode]
    else:
        chosen_name, chosen = min(options.items(), key=lambda kv: kv[1]["annual_transport_cost_usd"])

    return {
        "annual_transport_cost_usd": chosen["annual_transport_cost_usd"],
        "transport_capex_component_usd": chosen["transport_capex_component_usd"],
        "fixed_logistics_cost_usd": fixed_logistics,
        "transport_mode": chosen_name,
        "transport_distance_routed_km": chosen["transport_distance_routed_km"],
    }
