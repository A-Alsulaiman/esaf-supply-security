from __future__ import annotations
import numpy as np
from .resources import MONTH_HOURS, daylight_fraction_monthly
from .utils import clamp
from .economics import annualized_renewable_cost, EconomicAssumptions

# ---------------------------------------------------------------------------
# v6 optimiser.
#
# The original sizing routine had four ad hoc dials with no physical basis
# (findings P4-P6): a fixed reserve margin of 1.08; an "adequacy CF" blend
# (0.72*annual + 0.28*20th-percentile, floored at 0.135); a hard cap on
# seasonal storage at 18% of annual demand that silently violated the energy
# balance when it bound; minimum/maximum solar-share constraints that forced
# hybridisation by fiat; and a battery sized by an arbitrary hours formula
# (2 + 8*s + ...) that never entered any energy balance. In addition, the
# seasonal store was priced as a bare tank at an electricity-equivalent
# round-trip of 0.58, i.e. a power-to-power path whose conversion equipment
# (extra electrolysers / reconversion) was never costed.
#
# v6 replaces all of that with a small, physically closed screening design:
#
#   Decision variables: solar share s, generation oversize gamma,
#                       electrolyser oversize epsilon.
#   Demand split:  flexible load (electrolysis + H2 compression, ~90%)
#                  follows generation, buffered by H2 storage that feeds a
#                  steady FT unit -> storage carries HYDROGEN (a feedstock),
#                  so the "round trip" is compression/storage only
#                  (h2_path_efficiency ~= 0.90), not power-to-power;
#                  firm load (DAC, heat pump, CO2 compression, ~10%) must be
#                  met every month, with a day/night battery covering nights.
#   Sizing:        monthly energy balance with explicit curtailment; H2 store
#                  from the cumulative flexible balance; battery from the
#                  worst-month nightly firm+flex deficit vs wind; a mix that
#                  cannot close its annual balance is marked INFEASIBLE and
#                  excluded (never silently truncated).
#   Objective:     annualised renewables + storage + battery + the marginal
#                  electrolyser oversize above the availability-rated base.
#
# Overbuild now EMERGES from the curtailment-vs-storage trade-off instead of
# being imposed. The original routine is kept as optimise_mix_for_site_legacy
# for A/B comparison.
# ---------------------------------------------------------------------------


def storage_requirement_from_monthly_balance(net_mwh: np.ndarray, roundtrip_eff: float = 0.55) -> float:
    adjusted = net_mwh.copy()
    adjusted[adjusted < 0] = adjusted[adjusted < 0] / max(roundtrip_eff, 1e-6)
    cumulative = np.cumsum(adjusted)
    return float(cumulative.max() - cumulative.min())


def optimise_mix_for_site_legacy(
    demand_mw: float,
    solar_cf_monthly: np.ndarray,
    wind_cf_monthly: np.ndarray,
    lat: float,
    econ: EconomicAssumptions,
) -> dict:
    """Original v5 routine, unchanged, for backward comparison."""
    best = None
    reserve = 1.08
    annual_solar_cf_site = float(np.average(solar_cf_monthly, weights=MONTH_HOURS))
    annual_wind_cf_site = float(np.average(wind_cf_monthly, weights=MONTH_HOURS))

    min_solar_share = 0.0
    max_solar_share = 1.0
    if annual_solar_cf_site >= 0.165:
        min_solar_share = 0.25
    elif annual_solar_cf_site >= 0.145:
        min_solar_share = 0.15
    elif annual_solar_cf_site >= 0.125:
        min_solar_share = 0.075
    if lat >= 56.0 and annual_wind_cf_site >= 0.25:
        max_solar_share = min(max_solar_share, 0.45)

    solar_shares = np.linspace(min_solar_share, max_solar_share, 81)
    for s in solar_shares:
        blend = s * solar_cf_monthly + (1.0 - s) * wind_cf_monthly
        annual_cf = float(np.average(blend, weights=MONTH_HOURS))
        low_month_cf = float(np.quantile(blend, 0.20))
        adequacy_cf = 0.72 * annual_cf + 0.28 * low_month_cf
        adequacy_cf = max(adequacy_cf, 0.135)
        k = demand_mw * reserve / max(adequacy_cf, 1e-6)
        solar_mw = s * k
        wind_mw = (1.0 - s) * k

        monthly_gen = (solar_mw * solar_cf_monthly + wind_mw * wind_cf_monthly) * MONTH_HOURS
        monthly_demand = demand_mw * MONTH_HOURS
        net = monthly_gen - monthly_demand
        seasonal_store_raw = storage_requirement_from_monthly_balance(net, roundtrip_eff=0.58)
        seasonal_store = min(seasonal_store_raw, demand_mw * 8760.0 * 0.18)

        annual_solar_cf = float(np.mean(solar_cf_monthly))
        diurnal_hours = (
            2.0
            + 8.0 * s
            + 1.5 * clamp((lat - 50.0) / 12.0, 0.0, 1.0)
            + 1.0 * clamp((0.16 - annual_solar_cf) / 0.08, 0.0, 1.0)
        )
        battery_mwh = diurnal_hours * (0.65 * solar_mw + 0.15 * wind_mw)

        renew = annualized_renewable_cost(wind_mw, solar_mw, battery_mwh, seasonal_store, econ)
        annual_generation_mwh = float(monthly_gen.sum())
        annual_demand_mwh = float(monthly_demand.sum())
        renewable_capacity_mw = float(wind_mw + solar_mw)
        cand = {
            "solar_share": float(s),
            "wind_mw": float(wind_mw),
            "solar_mw": float(solar_mw),
            "battery_mwh": float(battery_mwh),
            "seasonal_h2_storage_mwh": float(seasonal_store),
            "monthly_generation_mwh": monthly_gen,
            "monthly_demand_mwh": monthly_demand,
            "annual_generation_mwh": annual_generation_mwh,
            "annual_demand_mwh": annual_demand_mwh,
            "generation_to_demand_ratio": annual_generation_mwh / max(annual_demand_mwh, 1e-9),
            "effective_annual_cf": float(annual_cf),
            "effective_low_month_cf": float(low_month_cf),
            "adequacy_cf_used_for_sizing": float(adequacy_cf),
            "renewable_capacity_mw": renewable_capacity_mw,
            "renewable_capacity_overbuild_ratio": renewable_capacity_mw / max(demand_mw, 1e-9),
            "annual_renewable_cost_usd": renew["annual_renewable_cost_usd"],
            "renewable_capex_usd": renew["renewable_capex_usd"],
            "curtailed_energy_frac": 0.0,
            "electrolyser_oversize_ratio": 1.0,
            "mix_feasible": True,
            "annual_electrolyser_oversize_cost_usd": 0.0,
        }
        if best is None or cand["annual_renewable_cost_usd"] < best["annual_renewable_cost_usd"]:
            best = cand
    return best


def optimise_mix_for_site(
    demand_mw: float,
    solar_cf_monthly: np.ndarray,
    wind_cf_monthly: np.ndarray,
    lat: float,
    econ: EconomicAssumptions,
    flexible_share: float = 0.90,
    h2_path_efficiency: float = 0.90,
    battery_efficiency: float = 0.90,
    electrolyser_annualized_usd_per_mw: float = 0.0,
    plant_availability: float = 0.90,
    min_battery_hours_firm: float = 4.0,
    method: str = "v2",
) -> dict:
    """Least-cost hybrid design with a feasibility-closed monthly balance.

    demand_mw is the TRUE AVERAGE electrical demand (energy basis).
    flexible_share: fraction of demand (electrolysis + H2 compression) that can
    follow generation via the H2 buffer; the rest is firm.
    electrolyser_annualized_usd_per_mw: annualised unit cost used to price
    oversize ABOVE the availability-rated base (1/plant_availability).
    """
    if method == "legacy":
        return optimise_mix_for_site_legacy(demand_mw, solar_cf_monthly, wind_cf_monthly, lat, econ)

    day_frac = daylight_fraction_monthly(lat)
    hours = MONTH_HOURS
    flex_mw = demand_mw * flexible_share
    firm_mw = demand_mw * (1.0 - flexible_share)
    annual_demand_mwh = demand_mw * 8760.0
    base_epsilon = 1.0 / max(plant_availability, 1e-6)

    solar_shares = np.linspace(0.0, 1.0, 41)
    oversizes = np.linspace(1.0, 2.0, 26)
    epsilons = np.array([base_epsilon, 1.2, 1.3, 1.45, 1.6])
    epsilons = np.unique(np.round(epsilons, 4))

    best = None
    for s in solar_shares:
        blend_cf = s * solar_cf_monthly + (1.0 - s) * wind_cf_monthly
        annual_cf = float(np.average(blend_cf, weights=hours))
        if annual_cf <= 0.02:
            continue
        k_base = demand_mw / annual_cf
        for gamma in oversizes:
            k = gamma * k_base
            solar_mw = s * k
            wind_mw = (1.0 - s) * k
            gen = (solar_mw * solar_cf_monthly + wind_mw * wind_cf_monthly) * hours

            firm_dem = firm_mw * hours
            # Firm load must be physically coverable every month.
            if np.any(gen < firm_dem):
                continue

            for eps in epsilons:
                flex_cap = eps * flex_mw * hours          # max monthly electrolyser intake
                intake = np.minimum(gen - firm_dem, flex_cap)
                curtailed = gen - firm_dem - intake       # >= 0 by construction
                flex_draw = flex_mw * hours               # steady FT-side requirement
                store_net = intake - flex_draw
                # Storage losses apply to energy passing through the store.
                stored_in = float(store_net[store_net > 0].sum())
                losses = stored_in * (1.0 - h2_path_efficiency)
                if float(intake.sum()) - losses < flex_draw.sum() * 0.9999:
                    continue  # infeasible: cannot close the flexible balance
                adj = store_net.copy()
                adj[adj < 0] = adj[adj < 0] / h2_path_efficiency
                cum = np.cumsum(adj)
                h2_store_el_mwh = float(cum.max() - cum.min())
                # Stored commodity is H2: convert electricity-equivalent to H2 LHV
                # for costing against the per-kWh(H2) storage capex.
                h2_store_lhv_mwh = h2_store_el_mwh * 0.667

                # Day/night battery for the worst month: at night solar is absent;
                # wind plus battery must carry firm + the night share of the
                # electrolyser's steady minimum operation. Screening assumption:
                # electrolyser may idle at night if the H2 store can cover the FT
                # draw, so the battery only firms the FIRM load at night.
                night_frac = 1.0 - day_frac
                wind_gen_night_rate = wind_mw * wind_cf_monthly  # MW average
                night_deficit_mw = np.maximum(firm_mw - wind_gen_night_rate, 0.0)
                nightly_mwh = night_deficit_mw * night_frac * 24.0
                battery_mwh = float(nightly_mwh.max()) / battery_efficiency
                # Minimum ride-through for the firm load: monthly means hide
                # hourly wind lulls, so never size the battery below
                # min_battery_hours_firm hours of firm demand.
                battery_mwh = max(battery_mwh, min_battery_hours_firm * firm_mw)

                renew = annualized_renewable_cost(wind_mw, solar_mw, battery_mwh, h2_store_lhv_mwh, econ)
                oversize_cost = max(eps - base_epsilon, 0.0) * flex_mw * electrolyser_annualized_usd_per_mw
                total = renew["annual_renewable_cost_usd"] + oversize_cost

                if best is None or total < best["_objective"]:
                    annual_gen = float(gen.sum())
                    best = {
                        "_objective": total,
                        "solar_share": float(s),
                        "wind_mw": float(wind_mw),
                        "solar_mw": float(solar_mw),
                        "battery_mwh": float(battery_mwh),
                        "seasonal_h2_storage_mwh": float(h2_store_lhv_mwh),
                        "monthly_generation_mwh": gen,
                        "monthly_demand_mwh": demand_mw * hours,
                        "annual_generation_mwh": annual_gen,
                        "annual_demand_mwh": float(annual_demand_mwh),
                        "generation_to_demand_ratio": annual_gen / max(annual_demand_mwh, 1e-9),
                        "effective_annual_cf": annual_cf,
                        "effective_low_month_cf": float(np.quantile(blend_cf, 0.20)),
                        "adequacy_cf_used_for_sizing": annual_cf,
                        "renewable_capacity_mw": float(k),
                        "renewable_capacity_overbuild_ratio": float(k) / max(demand_mw, 1e-9),
                        "annual_renewable_cost_usd": total,
                        "renewable_capex_usd": renew["renewable_capex_usd"],
                        "curtailed_energy_frac": float(curtailed.sum() / max(annual_gen, 1e-9)),
                        "electrolyser_oversize_ratio": float(eps),
                        "generation_oversize_ratio": float(gamma),
                        "mix_feasible": True,
                        "annual_electrolyser_oversize_cost_usd": float(oversize_cost),
                    }
    if best is None:
        # No feasible design on the scan grid: report the resource as unusable
        # rather than fabricating a design (runner screens these out explicitly).
        return {
            "solar_share": float("nan"), "wind_mw": 0.0, "solar_mw": 0.0,
            "battery_mwh": 0.0, "seasonal_h2_storage_mwh": 0.0,
            "monthly_generation_mwh": np.zeros(12), "monthly_demand_mwh": demand_mw * MONTH_HOURS,
            "annual_generation_mwh": 0.0, "annual_demand_mwh": float(annual_demand_mwh),
            "generation_to_demand_ratio": 0.0, "effective_annual_cf": 0.0,
            "effective_low_month_cf": 0.0, "adequacy_cf_used_for_sizing": 0.0,
            "renewable_capacity_mw": 0.0, "renewable_capacity_overbuild_ratio": 0.0,
            "annual_renewable_cost_usd": float("inf"), "renewable_capex_usd": float("inf"),
            "curtailed_energy_frac": 0.0, "electrolyser_oversize_ratio": 1.0,
            "generation_oversize_ratio": float("nan"),
            "mix_feasible": False, "annual_electrolyser_oversize_cost_usd": 0.0,
        }
    best.pop("_objective", None)
    return best


# ---------------------------------------------------------------------------
# v8 optimiser (v3): seasonally flexible plant + finished-fuel storage.
#
# The v2 design held the WHOLE plant at constant output and bridged seasonal
# renewable variability with hydrogen storage. That forces winter-firm
# renewable sizing and (in no-salt countries) expensive lined-rock caverns.
# Since (a) no EU rule constrains an islanded plant's production profile
# (Delegated Reg. (EU) 2023/1184: temporal correlation applies only to
# grid-PPA electricity, Art. 4/6; direct-connection plants qualify under
# Art. 3 alone), and (b) finished jet fuel stores at ~300 USD/m3 =
# ~0.03 USD/kWh - two orders of magnitude below hydrogen caverns - the
# cost-minimising strategy is to OVERSIZE the synthesis train (phi > 1), run
# it harder in the high-resource season, store FUEL, and deliver constantly.
#
# Decision variables: solar share s, generation scale gamma, synthesis
# oversize phi. Monthly operation u_m (electrical intake) obeys
#     ft_min_load_frac * phi * D * h_m  <=  u_m  <=  phi * D * h_m,
# (D = average electrical demand of the steady plant; capacity phi*D/avail
# derated by availability), follows generation up to capacity, and is
# level-trimmed so annual intake exactly meets the fuel target. Fuel storage
# is sized from the cumulative production-minus-delivery balance. The
# synthesis minimum load reflects FT turndown practice (Concawe: 80%
# planning near-term, 50% by 2050; 40% demonstrated at pilot scale - the
# base 0.5 assumes train-level modularity). Diurnal solar/electrolyser
# mismatch is carried by the day-scale H2 buffer and the 1/availability
# equipment headroom, consistent with the model's monthly resolution
# (limitation documented; hourly re-check recommended for selected sites).
# There is NO seasonal hydrogen store in this design.
# ---------------------------------------------------------------------------


def _trim_to_annual(u0: np.ndarray, floor: np.ndarray, hours: np.ndarray, target: float) -> np.ndarray | None:
    """Reduce u0 toward `floor` by a uniform per-hour rate so that sum(u) == target.
    Returns None if even at floor the total exceeds target is impossible to meet
    (i.e. sum(u0) < target)."""
    total0 = float(u0.sum())
    if total0 < target * 0.9999:
        return None
    if total0 <= target * 1.0001:
        return u0
    lo, hi = 0.0, float((u0 / hours).max())
    for _ in range(60):
        lam = 0.5 * (lo + hi)
        u = np.maximum(floor, u0 - lam * hours)
        if u.sum() > target:
            lo = lam
        else:
            hi = lam
    u = np.maximum(floor, u0 - hi * hours)
    # tiny residual: scale months above floor
    excess = float(u.sum()) - target
    room = u - floor
    if excess > 0 and room.sum() > 0:
        u = u - room * (excess / room.sum())
    return u


def optimise_mix_for_site_v3(
    demand_mw: float,
    solar_cf_monthly: np.ndarray,
    wind_cf_monthly: np.ndarray,
    lat: float,
    econ: EconomicAssumptions,
    annual_tonnes: float,
    process_cost_by_phi: dict[float, dict],
    flexible_share: float = 0.90,
    battery_efficiency: float = 0.90,
    plant_availability: float = 0.90,
    min_battery_hours_firm: float = 4.0,
) -> dict:
    """Least-cost hybrid + flexible-plant design with finished-fuel storage.

    demand_mw is the average electrical demand of the STEADY plant (energy
    basis); process_cost_by_phi maps each synthesis-oversize option to its
    annualised process cost (computed with economies of scale at the plant's
    actual size). The objective includes the process cost, so the returned
    design minimises the full delivered numerator except constant per-tonne
    terms (carbon opex, transport, water, airport fee)."""
    from .economics import annualized_product_storage_cost

    day_frac = daylight_fraction_monthly(lat)
    night_frac = 1.0 - day_frac
    hours = MONTH_HOURS
    e_annual = demand_mw * 8760.0
    e_spec_mwh_per_t = e_annual / max(annual_tonnes, 1e-9)
    firm_share = 1.0 - flexible_share

    solar_shares = np.linspace(0.0, 1.0, 21)
    gammas = np.linspace(1.0, 2.2, 16)
    phis = sorted(process_cost_by_phi.keys())

    best = None
    for s in solar_shares:
        blend_cf = s * solar_cf_monthly + (1.0 - s) * wind_cf_monthly
        annual_cf = float(np.average(blend_cf, weights=hours))
        if annual_cf <= 0.02:
            continue
        k_base = demand_mw / annual_cf
        for gamma in gammas:
            k = gamma * k_base
            solar_mw = s * k
            wind_mw = (1.0 - s) * k
            gen = (solar_mw * solar_cf_monthly + wind_mw * wind_cf_monthly) * hours
            for phi in phis:
                cap = phi * demand_mw * hours
                floor = econ.ft_min_load_frac * cap
                if np.any(gen < floor):
                    continue                       # cannot hold minimum load
                u0 = np.minimum(gen, cap)
                u = _trim_to_annual(u0, floor, hours, e_annual)
                if u is None:
                    continue                       # cannot meet annual target

                # finished-fuel storage from cumulative production vs delivery
                prod_t = u / e_spec_mwh_per_t
                deliv_t = annual_tonnes * hours / 8760.0
                cum = np.cumsum(prod_t - deliv_t)
                store_t = float(max(cum.max(), 0.0) - min(cum.min(), 0.0))
                storage = annualized_product_storage_cost(store_t, econ)

                # battery: firm night load at each month's operating rate
                firm_rate = firm_share * u / hours
                deficit = np.maximum(firm_rate - wind_mw * wind_cf_monthly, 0.0)
                nightly = deficit * night_frac * 24.0
                battery_mwh = float(nightly.max()) / battery_efficiency
                battery_mwh = max(battery_mwh, min_battery_hours_firm * float(firm_rate.max()))

                renew = annualized_renewable_cost(wind_mw, solar_mw, battery_mwh, 0.0, econ)
                proc = process_cost_by_phi[phi]
                total = (
                    renew["annual_renewable_cost_usd"]
                    + storage["annual_product_storage_cost_usd"]
                    + proc["annual_process_cost_usd"]
                )
                if best is None or total < best["_objective"]:
                    annual_gen = float(gen.sum())
                    monthly_load = u / cap
                    best = {
                        "_objective": total,
                        "solar_share": float(s),
                        "wind_mw": float(wind_mw),
                        "solar_mw": float(solar_mw),
                        "battery_mwh": float(battery_mwh),
                        "seasonal_h2_storage_mwh": 0.0,
                        "fuel_storage_tonnes": store_t,
                        "fuel_storage_days": store_t / max(annual_tonnes / 365.0, 1e-9),
                        "annual_product_storage_cost_usd": storage["annual_product_storage_cost_usd"],
                        "synthesis_oversize": float(phi),
                        "synthesis_load_min": float(monthly_load.min()),
                        "synthesis_load_max": float(monthly_load.max()),
                        "monthly_generation_mwh": gen,
                        "monthly_intake_mwh": u,
                        "monthly_demand_mwh": demand_mw * hours,
                        "annual_generation_mwh": annual_gen,
                        "annual_demand_mwh": float(e_annual),
                        "generation_to_demand_ratio": annual_gen / max(e_annual, 1e-9),
                        "effective_annual_cf": annual_cf,
                        "effective_low_month_cf": float(np.quantile(blend_cf, 0.20)),
                        "adequacy_cf_used_for_sizing": annual_cf,
                        "renewable_capacity_mw": float(k),
                        "renewable_capacity_overbuild_ratio": float(k) / max(demand_mw, 1e-9),
                        "annual_renewable_cost_usd": renew["annual_renewable_cost_usd"]
                        + storage["annual_product_storage_cost_usd"],
                        "annual_process_cost_usd_selected": proc["annual_process_cost_usd"],
                        "renewable_capex_usd": renew["renewable_capex_usd"]
                        + storage["product_storage_capex_usd"],
                        "curtailed_energy_frac": float((gen - u).sum() / max(annual_gen, 1e-9)),
                        "electrolyser_oversize_ratio": float(phi) / max(plant_availability, 1e-6),
                        "generation_oversize_ratio": float(gamma),
                        "mix_feasible": True,
                        "annual_electrolyser_oversize_cost_usd": 0.0,
                    }
    if best is None:
        return {
            "solar_share": float("nan"), "wind_mw": 0.0, "solar_mw": 0.0,
            "battery_mwh": 0.0, "seasonal_h2_storage_mwh": 0.0,
            "fuel_storage_tonnes": 0.0, "fuel_storage_days": 0.0,
            "annual_product_storage_cost_usd": 0.0,
            "synthesis_oversize": float("nan"),
            "synthesis_load_min": 0.0, "synthesis_load_max": 0.0,
            "monthly_generation_mwh": np.zeros(12), "monthly_intake_mwh": np.zeros(12),
            "monthly_demand_mwh": demand_mw * MONTH_HOURS,
            "annual_generation_mwh": 0.0, "annual_demand_mwh": float(e_annual),
            "generation_to_demand_ratio": 0.0, "effective_annual_cf": 0.0,
            "effective_low_month_cf": 0.0, "adequacy_cf_used_for_sizing": 0.0,
            "renewable_capacity_mw": 0.0, "renewable_capacity_overbuild_ratio": 0.0,
            "annual_renewable_cost_usd": float("inf"),
            "annual_process_cost_usd_selected": float("inf"),
            "renewable_capex_usd": float("inf"),
            "curtailed_energy_frac": 0.0, "electrolyser_oversize_ratio": 1.0,
            "generation_oversize_ratio": float("nan"),
            "mix_feasible": False, "annual_electrolyser_oversize_cost_usd": 0.0,
        }
    best.pop("_objective", None)
    return best
