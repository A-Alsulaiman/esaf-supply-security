from __future__ import annotations
import json
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# v8 carbon-sourcing module.
#
# Under the RFNBO GHG methodology (Delegated Regulation (EU) 2023/1185,
# Annex point 10) the carbon in compliant e-SAF may come from: (b) direct air
# capture (no end date), (c) biogenic CO2 from RED-compliant biofuel/biomass
# production or combustion (no end date), or (a) fossil ETS point sources -
# but only if incorporated before 2036 (power CO2) / 2041 (other ETS
# activities), inside a 25-year plant life. The model therefore offers two
# 2050-proof sourcing modes and picks the cheaper per site:
#
#   "dac"    - on-site DAC for 100% of the CO2 (v7 behaviour);
#   "market" - purchased biogenic CO2 from point sources within trucking
#              range, with on-site DAC topping up whatever the regional
#              biogenic supply cannot cover.
#
# Biogenic supply is modelled as a per-country SUPPLY RADIUS: country
# point-source potential (country_biogenic_co2.json; BEST/Bioenergy Europe
# 2025 inventory of >=5 MW bioenergy CHP/power/heat plants + pulp & paper +
# biogenic WtE, cross-checked against Rosa et al. 2021) times a contractable
# share is spread over the country area; procuring Q tonnes requires
# collecting from a disc of radius r = sqrt(Q / (pi * density)), and the mean
# road haul is ~2/3 r times a routing factor. Delivered cost =
# capture + liquefaction + purity polish + transfer + trucking x mean haul.
# Beyond co2_max_haul_km the marginal source is assumed uneconomic and DAC
# covers the remainder (dac_fraction > 0). This is a screening treatment -
# it prices "how close or far the approved carbon is" without a site-level
# source atlas; the sampled contractable share (15-50%) and haul cap carry
# the spatial uncertainty.
# ---------------------------------------------------------------------------


def load_country_biogenic(base_dir: Path) -> dict:
    fp = base_dir / "src" / "saf_eu_model" / "data" / "country_biogenic_co2.json"
    if not fp.exists():
        return {}
    with fp.open("r", encoding="utf-8") as f:
        return json.load(f).get("countries", {})


def biogenic_co2_supply(
    q_tonnes_per_year: float,
    iso3: str,
    country_area_km2: float,
    econ,
    biogenic_data: dict,
) -> dict:
    """Delivered-cost assessment for purchasing biogenic CO2.

    Returns a dict with dac_fraction (share left for on-site DAC), the
    delivered unit cost of the purchased share (USD/t), mean haul, supply
    radius and diagnostics. If the country has no usable biogenic potential,
    dac_fraction = 1 and no purchase takes place.
    """
    entry = biogenic_data.get(iso3, {})
    potential_mt = float(entry.get("biogenic_point_source_mt_per_year", 0.0))
    contractable = potential_mt * 1e6 * econ.co2_contractable_share
    if contractable <= 0.0 or country_area_km2 <= 0.0 or q_tonnes_per_year <= 0.0:
        return {
            "dac_fraction": 1.0, "co2_purchased_t_per_year": 0.0,
            "co2_delivered_cost_usd_per_t": 0.0, "co2_mean_haul_km": 0.0,
            "co2_supply_radius_km": 0.0, "co2_country_potential_mt": potential_mt,
        }

    density = contractable / country_area_km2               # t/y per km2
    r_max = econ.co2_max_haul_km
    q_within_reach = density * math.pi * r_max ** 2
    q_bio = min(q_tonnes_per_year, contractable, q_within_reach)

    radius = math.sqrt(q_bio / (math.pi * density))
    mean_haul = (2.0 / 3.0) * radius * 1.35 + 10.0          # routed; 10 km local floor
    unit = (
        econ.co2_capture_cost_usd_per_t
        + econ.co2_liquefaction_usd_per_t
        + econ.co2_purity_polish_usd_per_t
        + econ.co2_transfer_fixed_usd_per_t
        + econ.co2_truck_usd_per_tkm * mean_haul
    )
    return {
        "dac_fraction": 1.0 - q_bio / q_tonnes_per_year,
        "co2_purchased_t_per_year": q_bio,
        "co2_delivered_cost_usd_per_t": unit,
        "co2_mean_haul_km": mean_haul,
        "co2_supply_radius_km": radius,
        "co2_country_potential_mt": potential_mt,
    }


def annual_purchased_co2_cost(supply: dict) -> float:
    return supply["co2_purchased_t_per_year"] * supply["co2_delivered_cost_usd_per_t"]
