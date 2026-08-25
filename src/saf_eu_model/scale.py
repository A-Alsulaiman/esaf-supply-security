from __future__ import annotations

# ---------------------------------------------------------------------------
# v8 economies-of-scale module.
#
# The delivered cost was previously "nearly linear in tonnage by construction"
# (review finding P18). With mandate-driven plant sizing (hub-airport e-SAF
# demand: ~20-100 kt/y in 2030, 0.15-2.2 Mt/y in 2050) that linearity is no
# longer acceptable. Each process-unit group gets a capex scale multiplier
#
#     mult_u(S_eff) = g_u(S_eff) / g_u(S_REF)
#
# applied ON TOP of the linear throughput-proportional capex, where S_eff is
# the unit's effective capacity in kt/y of jet (plant size x synthesis
# oversize) and g_u encodes cost-per-unit vs size:
#   - FT/RWGS/upgrading: n=0.67 within a single train (Kreutz et al. 2008:
#     component exponents 0.60-0.70; NETL GTL baseline), single-train max
#     ~12,500 bbl/d of total liquids = ~340 kt/y of jet at a 0.57 jet share;
#     above that, number-up with n_eff=0.92 (shared offsites only).
#   - Electrolyser: modular above ~100 MW, n=0.95 (DEA 10/100/1000 MW
#     datasheets imply 0.80 below 100 MW, 0.96 above; Reksten et al. 2022).
#   - DAC (solid sorbent): container-modular, n=0.95 for shared BoP
#     (Fasihi et al. 2019 assume learning, not unit scale; NETL sorbent case).
#   - Compressors: n=0.78 (NETL QGESS 0.61-0.88; Towler & Sinnott 0.6-0.8).
#   - Heat pump / buffers / other: generic six-tenths-class n=0.70.
#   - Fixed land & utilities: n=0.60 (site services scale sub-linearly).
#   - Renewables and battery: modular, n=1.0 above ~100 MW (LBNL).
# Below MODULAR_FLOOR_KT the per-unit cost is held at the floor value:
# classical exponents are not valid at micro scale, where modular/numbered-up
# equipment sets a cost ceiling (documented screening choice).
#
# Everything is anchored at S_REF = 740 kt/y so that a 740 kt/y plant with
# oversize 1.0 reproduces the v7 economics exactly (multipliers = 1).
# ---------------------------------------------------------------------------

S_REF_KT = 740.0          # anchor plant size (kt jet / y)
MODULAR_FLOOR_KT = 30.0   # below this, per-unit cost held constant
FT_TRAIN_MAX_KT = 340.0   # single FT train ~12,500 bbl/d total liquids, as jet
ELECTROLYSER_BREAK_KT = 22.0   # ~100 MW electrolyser at v7 specific demand


def _per_unit_cost_rel(s_kt: float, n_small: float, n_large: float, break_kt: float) -> float:
    """Relative cost PER UNIT OF CAPACITY vs size, piecewise power law with a
    modular floor. Continuous across the break; value at break = 1."""
    s = max(s_kt, MODULAR_FLOOR_KT)
    if s <= break_kt:
        return (s / break_kt) ** (n_small - 1.0)
    return (s / break_kt) ** (n_large - 1.0)


def unit_scale_multiplier(unit: str, s_eff_kt: float, econ=None) -> float:
    """Capex multiplier for `unit` at effective capacity s_eff_kt (kt jet/y),
    normalised so multiplier(S_REF) = 1. econ supplies sampled exponents when
    present (fields ft_scale_exponent etc.); otherwise base values are used."""
    def _e(name: str, default: float) -> float:
        return float(getattr(econ, name, default)) if econ is not None else default

    if unit == "ft":
        n_in, n_up = _e("ft_scale_exponent", 0.67), 0.92
        f = _per_unit_cost_rel(s_eff_kt, n_in, n_up, FT_TRAIN_MAX_KT)
        f_ref = _per_unit_cost_rel(S_REF_KT, n_in, n_up, FT_TRAIN_MAX_KT)
        return f / f_ref
    if unit == "electrolyser":
        n_in, n_up = 0.80, _e("electrolyser_scale_exponent", 0.95)
        f = _per_unit_cost_rel(s_eff_kt, n_in, n_up, ELECTROLYSER_BREAK_KT)
        f_ref = _per_unit_cost_rel(S_REF_KT, n_in, n_up, ELECTROLYSER_BREAK_KT)
        return f / f_ref
    if unit == "dac":
        n = _e("dac_scale_exponent", 0.95)
        return (max(s_eff_kt, MODULAR_FLOOR_KT) / S_REF_KT) ** (n - 1.0)
    if unit == "compressor":
        n = _e("compressor_scale_exponent", 0.78)
        return (max(s_eff_kt, MODULAR_FLOOR_KT) / S_REF_KT) ** (n - 1.0)
    if unit == "generic":
        n = _e("generic_scale_exponent", 0.70)
        return (max(s_eff_kt, MODULAR_FLOOR_KT) / S_REF_KT) ** (n - 1.0)
    if unit == "fixed":
        # ABSOLUTE-term multiplier: the fixed land/utilities cost is a constant
        # (not throughput-proportional), so the whole term scales as (S/ref)^n.
        n = _e("fixed_cost_scale_exponent", 0.60)
        return (max(s_eff_kt, MODULAR_FLOOR_KT) / S_REF_KT) ** n
    return 1.0
