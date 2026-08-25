
from __future__ import annotations
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# v6 process module.
#
# Changes vs the original (all switchable back via accounting="legacy" /
# availability_mode="legacy" so the previous numbers remain reproducible):
#
# 1. CORRECTED SYNGAS ACCOUNTING (finding P1).
#    The original computed the syngas requirement as
#        m_syngas = (LHV_jet / kerosene_fraction) / LHV_syngas
#    i.e. syngas energy in = liquid energy out. The Fischer-Tropsch
#    conversion loss (FT_efficiency = 0.7667) was only used to split the
#    OUTPUT into "product" and "heat" streams, but was never applied to the
#    syngas requirement itself. Under the model's own convention
#    (1 kg of jet fuel leaves the plant; co-products receive no allocation)
#    the syngas requirement per kg jet is
#        E_syngas = LHV_jet / (jet_energy_share * ft_energy_efficiency)
#    which is 1/0.7667 = 1.30x larger. CO2, H2 and electrolysis energy scale
#    with it. The original therefore understated the upstream chain by ~23%
#    on a per-kg-jet basis.
#
# 2. H2 REQUIREMENT DERIVED FROM STOICHIOMETRY (not the hardcoded 0.6105):
#        m_H2 = 0.125 * m_syngas          (H2 mass fraction of 2:1 syngas)
#             + n_CO * M_H2               (RWGS: CO2 + H2 -> CO + H2O)
#    This reproduces 0.6105 exactly under legacy accounting and scales
#    consistently under corrected accounting.
#
# 3. CO2 COMPRESSION ELECTRICITY ADDED TO THE ENERGY BALANCE (finding P9).
#    The original sized compressor CAPEX for H2 AND CO2 compression
#    (0.66 kWh/kg H2 + 115 kWh/t CO2) but only counted the H2 part in the
#    electricity demand. The CO2 part (~0.5 kWh/kg jet) is now included.
#
# 4. AVAILABILITY DOUBLE-COUNT REMOVED (finding P2).
#    The original divided the ANNUAL ENERGY requirement by availability
#    (E * m_fuel / (8760 * availability)) and then asked the renewable
#    optimiser to serve that inflated power for ALL 8760 h of the year.
#    That procures 1/availability = 11% more energy than the plant can
#    consume. v6 distinguishes:
#      - average_power_mw : true average electrical demand = E*m/8760
#        (used for ENERGY procurement / renewable sizing), and
#      - rated_power_mw   : equipment nameplate = E*m/(8760*availability)
#        (used for EQUIPMENT capex sizing).
#
# 5. FT CAPEX BASIS (finding P8): liquid_output_mw_total covers the full
#    liquid slate (jet + naphtha + diesel), because the FT unit must be
#    sized for all liquids even if only jet is credited. The jet-only figure
#    is retained as liquid_output_mw_jet.
# ---------------------------------------------------------------------------

M_CO = 28.0e-3      # kg/mol
M_CO2 = 44.0e-3     # kg/mol
M_H2 = 2.016e-3     # kg/mol


@dataclass
class ProcessDemand:
    fuel_tonnes_per_year: float
    fuel_kg_per_year: float
    elec_kwh_per_kg_fuel: float
    heat_mj_per_kg_fuel: float
    average_power_mw: float          # true average demand (energy basis)
    rated_power_mw: float            # nameplate basis = average / availability
    liquid_output_mw: float          # jet-only, kept for backward compatibility
    liquid_output_mw_total: float    # full liquid slate (FT capex basis)
    co2_tonnes_per_year: float
    h2_tonnes_per_year: float
    dac_power_mw: float
    electrolyser_power_mw: float
    compressor_power_mw: float
    heat_pump_power_mw: float
    availability: float
    accounting: str
    jet_energy_share: float
    electrolysis_kwh_per_kg_fuel: float
    h2_compression_kwh_per_kg_fuel: float = 0.0   # electrolysis-side compression (flexible load)
    dac_fraction: float = 1.0                     # v8: on-site DAC share of the CO2 requirement


def saf_energy_balance_per_kg(
    carbon_mode: str = "LT",
    accounting: str = "corrected",
    ft_energy_efficiency: float = 0.7667,
    jet_energy_share: float = 0.57,
    electrolyser_kwh_per_kg_h2: float | None = None,
    h2_compression_kwh_per_kg: float | None = None,
    co2_compression_kwh_per_tco2: float | None = None,
    dac_electricity_kwh_per_tco2: float | None = None,
    dac_heat_kwh_per_tco2: float | None = None,
    heat_pump_cop: float | None = None,
    dac_fraction: float = 1.0,
) -> dict:
    """Energy/mass balance per kg of jet fuel delivered.

    dac_fraction (v8): share of the CO2 requirement captured by on-site DAC;
    the remainder is purchased biogenic CO2 delivered to the fence (its cost is
    a separate opex line in economics; its capture energy is NOT on this
    plant's meter). dac_fraction=1.0 reproduces v7 exactly.

    accounting="corrected": syngas requirement includes the FT conversion loss
    (recommended). accounting="legacy" reproduces the original numbers exactly.

    v7: the physical parameters (electrolyser specific consumption, compression
    energies, DAC energies, heat-pump COP) can be supplied from
    literature_inputs.json so they participate in scenarios and uncertainty
    sampling. When None, each falls back to its v6 constant, reproducing the
    previous corrected/legacy numbers exactly.
    Returns a dict so callers can size each unit explicitly.
    """
    LHV_jet = 44.0        # MJ/kg. The plant product is neat FT synthetic
    # paraffinic kerosene (aromatics-free): measured net heats 43.9-44.2 MJ/kg
    # (NRL 2009: Syntroleum S-8 43.9, Shell FT kerosene 44.2; RED II Annex III
    # lists 44.0 for synthetic paraffinic fuels). Finished Jet A-1 (with
    # aromatics) would be ~43.15; 44.0 is correct for the FT-SPK stream.
    LHV_syngas = 23.9     # MJ/kg for ~2:1 H2:CO syngas
    LHV_H2 = 120.0        # MJ/kg
    efficiency_AEM = 0.667
    pressure_H2_25bar_kwh_per_kg = 0.66 if h2_compression_kwh_per_kg is None else h2_compression_kwh_per_kg
    co2_compression_kwh_per_kg = (115.0 if co2_compression_kwh_per_tco2 is None else co2_compression_kwh_per_tco2) / 1000.0
    Delta_Hr_rwgs = 41.2  # kJ/mol CO (endothermic RWGS)

    liquids_energy = LHV_jet / jet_energy_share            # MJ per kg jet, full slate
    if accounting == "legacy":
        syngas_energy = liquids_energy                      # original: no FT loss on input
        ft_waste_heat = (1.0 - ft_energy_efficiency) * liquids_energy
    elif accounting == "corrected":
        syngas_energy = liquids_energy / ft_energy_efficiency
        ft_waste_heat = syngas_energy - liquids_energy
    else:
        raise ValueError(f"Unknown accounting: {accounting}")

    m_syngas = syngas_energy / LHV_syngas
    m_CO = 0.875 * m_syngas
    n_CO = m_CO / M_CO
    m_CO2 = n_CO * M_CO2

    # RWGS heat demand (endothermic), served from FT waste heat first as in
    # the original model. NOTE (documented limitation): RWGS runs at
    # 700-900 C while FT waste heat is ~200 C; this heat cascade is optimistic
    # and is retained only for continuity. See review report, finding P10.
    # Delta_Hr[kJ/mol] -> MJ per kg CO: (41.2e-3 MJ/mol) / (0.028 kg/mol) = 1.4714 MJ/kg CO
    Q_shift_per_kg_co = (Delta_Hr_rwgs * 1e-3) / M_CO
    Q_shift_total = Q_shift_per_kg_co * m_CO  # MJ per kg jet

    # H2 requirement: syngas H2 content + RWGS consumption (1 mol H2 per mol CO).
    m_H2 = 0.125 * m_syngas + n_CO * M_H2
    if accounting == "legacy":
        m_H2 = 0.6105  # original hardcoded value, kept for exact reproduction

    E_pressure = m_H2 * pressure_H2_25bar_kwh_per_kg * 3.6           # MJ
    E_co2_compression = m_CO2 * co2_compression_kwh_per_kg * 3.6      # MJ (new in v6)
    if electrolyser_kwh_per_kg_h2 is None:
        E_electrolysis = (m_H2 * LHV_H2) / efficiency_AEM             # MJ (v6 form)
    else:
        E_electrolysis = m_H2 * electrolyser_kwh_per_kg_h2 * 3.6      # MJ (v7 parameterised)

    E_DAC = 0.0
    Q_DAC = 0.0
    if carbon_mode == "LT":
        # v6 constants: 0.73 MJ/kg (~203 kWh_e/t, Fasihi et al. 2040 value) and
        # 4.62 MJ/kg (~1283 kWh_th/t). v7 supplies 2030 values via parameters.
        # v8: only the DAC-captured share of CO2 draws capture energy here.
        m_CO2_dac = m_CO2 * dac_fraction
        E_DAC = (0.73 if dac_electricity_kwh_per_tco2 is None else dac_electricity_kwh_per_tco2 * 3.6 / 1000.0) * m_CO2_dac
        Q_DAC = (4.62 if dac_heat_kwh_per_tco2 is None else dac_heat_kwh_per_tco2 * 3.6 / 1000.0) * m_CO2_dac
    elif carbon_mode == "HT":
        E_DAC = 4.986 * m_CO2 * dac_fraction

    E_heat_pump = 0.0
    available_waste_heat = ft_waste_heat - Q_shift_total
    if Q_DAC > available_waste_heat:
        COP_real = 2.5 if heat_pump_cop is None else heat_pump_cop
        E_heat_pump = (Q_DAC - available_waste_heat) / COP_real

    E_total_mj = E_pressure + E_co2_compression + E_electrolysis + E_DAC + E_heat_pump
    if accounting == "legacy":
        # Original excluded CO2 compression from the demand (kept for exact repro).
        E_total_mj = E_pressure + E_electrolysis + E_DAC + E_heat_pump
    Q_net_mj = Q_DAC + Q_shift_total - ft_waste_heat * 0.9

    return {
        "E_total_kwh_per_kg": E_total_mj / 3.6,
        "E_electrolysis_kwh_per_kg": E_electrolysis / 3.6,
        "E_dac_kwh_per_kg": E_DAC / 3.6,
        "E_heat_pump_kwh_per_kg": E_heat_pump / 3.6,
        "E_compression_kwh_per_kg": (E_pressure + (0.0 if accounting == "legacy" else E_co2_compression)) / 3.6,
        "E_co2_compression_kwh_per_kg": E_co2_compression / 3.6,
        "Q_net_mj_per_kg": Q_net_mj,
        "m_co2_kg_per_kg": m_CO2,
        "m_h2_kg_per_kg": m_H2,
        "liquids_energy_mj_per_kg_jet": liquids_energy,
        "syngas_energy_mj_per_kg_jet": syngas_energy,
    }


def make_default_process(
    fuel_tonnes_per_year: float = 740000.0,
    availability: float = 0.90,
    carbon_mode: str = "LT",
    accounting: str = "corrected",
    availability_mode: str = "energy_correct",
    ft_energy_efficiency: float = 0.7667,
    jet_energy_share: float = 0.57,
    electrolyser_kwh_per_kg_h2: float | None = None,
    h2_compression_kwh_per_kg: float | None = None,
    co2_compression_kwh_per_tco2: float | None = None,
    dac_electricity_kwh_per_tco2: float | None = None,
    dac_heat_kwh_per_tco2: float | None = None,
    heat_pump_cop: float | None = None,
    dac_fraction: float = 1.0,
) -> ProcessDemand:
    """Build the plant demand object.

    availability_mode="energy_correct" (recommended): renewables are sized to
    the true average power (annual energy / 8760 h); equipment is sized to the
    rated power (average / availability).
    availability_mode="legacy": both use the rated basis, reproducing the
    original 1/availability energy over-procurement.
    v7: physical parameters default to their v6 constants when None (exact
    reproduction); pass values from literature_inputs.json to use the sourced
    2030 ranges.
    """
    fuel_kg = fuel_tonnes_per_year * 1000.0
    bal = saf_energy_balance_per_kg(
        carbon_mode, accounting=accounting,
        ft_energy_efficiency=ft_energy_efficiency, jet_energy_share=jet_energy_share,
        electrolyser_kwh_per_kg_h2=electrolyser_kwh_per_kg_h2,
        h2_compression_kwh_per_kg=h2_compression_kwh_per_kg,
        co2_compression_kwh_per_tco2=co2_compression_kwh_per_tco2,
        dac_electricity_kwh_per_tco2=dac_electricity_kwh_per_tco2,
        dac_heat_kwh_per_tco2=dac_heat_kwh_per_tco2,
        heat_pump_cop=heat_pump_cop,
        dac_fraction=dac_fraction,
    )
    h2_comp_kwh_per_kg = 0.66 if h2_compression_kwh_per_kg is None else h2_compression_kwh_per_kg
    co2_comp_kwh_per_t = 115.0 if co2_compression_kwh_per_tco2 is None else co2_compression_kwh_per_tco2
    hours_rated = 8760.0 * availability
    rated_power_mw = bal["E_total_kwh_per_kg"] * fuel_kg / hours_rated / 1000.0
    average_power_mw = bal["E_total_kwh_per_kg"] * fuel_kg / 8760.0 / 1000.0
    if availability_mode == "legacy":
        average_power_mw = rated_power_mw
    elif availability_mode != "energy_correct":
        raise ValueError(f"Unknown availability_mode: {availability_mode}")

    def _rated(kwh_per_kg: float) -> float:
        return kwh_per_kg * fuel_kg / hours_rated / 1000.0

    electrolyser_power_mw = _rated(bal["E_electrolysis_kwh_per_kg"])
    dac_power_mw = _rated(bal["E_dac_kwh_per_kg"]) if carbon_mode == "LT" else 0.0
    heat_pump_power_mw = _rated(bal["E_heat_pump_kwh_per_kg"])
    compressor_power_mw = _rated(
        h2_comp_kwh_per_kg * bal["m_h2_kg_per_kg"] + (co2_comp_kwh_per_t / 1000.0) * bal["m_co2_kg_per_kg"]
    )
    liquid_output_mw_jet = (fuel_kg * 44.0 / 3600.0) / hours_rated
    liquid_output_mw_total = (fuel_kg * bal["liquids_energy_mj_per_kg_jet"] / 3600.0) / hours_rated

    return ProcessDemand(
        fuel_tonnes_per_year=fuel_tonnes_per_year,
        fuel_kg_per_year=fuel_kg,
        elec_kwh_per_kg_fuel=bal["E_total_kwh_per_kg"],
        heat_mj_per_kg_fuel=bal["Q_net_mj_per_kg"],
        average_power_mw=average_power_mw,
        rated_power_mw=rated_power_mw,
        liquid_output_mw=liquid_output_mw_jet,
        liquid_output_mw_total=liquid_output_mw_total,
        co2_tonnes_per_year=bal["m_co2_kg_per_kg"] * fuel_kg / 1000.0,
        h2_tonnes_per_year=bal["m_h2_kg_per_kg"] * fuel_kg / 1000.0,
        dac_power_mw=dac_power_mw,
        electrolyser_power_mw=electrolyser_power_mw,
        compressor_power_mw=compressor_power_mw,
        heat_pump_power_mw=heat_pump_power_mw,
        availability=availability,
        accounting=accounting,
        jet_energy_share=jet_energy_share,
        electrolysis_kwh_per_kg_fuel=bal["E_electrolysis_kwh_per_kg"],
        h2_compression_kwh_per_kg_fuel=h2_comp_kwh_per_kg * bal["m_h2_kg_per_kg"],
        dac_fraction=dac_fraction,
    )
