from __future__ import annotations
import json
import math
import time
from pathlib import Path
from typing import Literal

import numpy as np

from .utils import clamp, log

MONTH_HOURS = np.array([744,672,744,720,744,720,744,744,720,744,720,744], dtype=float)
MONTH_DAYS = MONTH_HOURS / 24.0
MONTH_KEYS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_MID_DOY = np.array([15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349], dtype=float)

# ---------------------------------------------------------------------------
# v6 resource module.
#
# Key change (finding P3): the original converted NASA POWER MONTHLY-MEAN
# 50 m wind speeds to capacity factors by evaluating a power curve AT the
# mean speed. Because turbine power is strongly convex in wind speed below
# rated, CF(mean(v)) severely underestimates mean(CF(v)) (Jensen's
# inequality); e.g. inland Finland came out at CF ~0.03-0.09. The original
# then compensated with heuristic blending, floors and clips
# (wind = max(0.55*raw+0.45*heuristic, 0.82*heuristic), clip [0.135,0.50]),
# which meant the published "NASA-based" capacity factors were, in poor-wind
# months, ~90% heuristic and ~10% NASA.
#
# v6 instead integrates a normalised power curve over a Weibull distribution
# whose monthly mean equals the NASA monthly-mean speed (shape k=2, Rayleigh,
# the standard screening assumption when only the mean is known), with
# terrain-aware shear extrapolation to hub height. This produces realistic
# CFs directly from the data with no floors:
#   FIN best site: raw 0.089 -> 0.333 ; IRL west coast: raw 0.236 -> 0.452.
# The legacy blending path is retained via calibration="legacy".
# ---------------------------------------------------------------------------

WIND_CUT_IN_MS = 3.0
WIND_RATED_MS = 11.0        # modern low specific-power onshore turbine (~300 W/m2)
WIND_CUT_OUT_MS = 25.0
WIND_LOSS_FACTOR = 0.85     # wakes, electrical, availability, icing (screening)
WIND_HUB_HEIGHT_M = 125.0   # typical new-build onshore hub height
WIND_WEIBULL_K = 2.0        # Rayleigh; supply monthly k values if available

_V_GRID = np.arange(0.0, 32.0, 0.05)
_P_NORM = np.where(
    _V_GRID < WIND_CUT_IN_MS, 0.0,
    np.where(
        _V_GRID < WIND_RATED_MS,
        (_V_GRID ** 3 - WIND_CUT_IN_MS ** 3) / (WIND_RATED_MS ** 3 - WIND_CUT_IN_MS ** 3),
        np.where(_V_GRID < WIND_CUT_OUT_MS, 1.0, 0.0),
    ),
)


def _seasonal_shape_solar(lat: float) -> np.ndarray:
    """Stylised monthly PV shape with stronger winter penalty at high latitude."""
    phi = math.radians(lat)
    vals = []
    for m in range(12):
        angle = 2.0 * math.pi * (m - 5.5) / 12.0
        declination_factor = 0.5 * (1.0 + math.cos(angle))
        winter_floor = clamp(0.10 - 0.06 * max(lat - 50.0, 0.0) / 15.0, 0.02, 0.10)
        base = winter_floor + (1.0 - winter_floor) * declination_factor
        geometric = max(0.10, math.cos(phi - 0.72))
        vals.append(base * geometric)
    arr = np.array(vals, dtype=float)
    return arr / max(arr.mean(), 1e-9)


def _seasonal_shape_wind(lat: float, boundary_proximity: float, island_factor: float) -> np.ndarray:
    """Stylised monthly wind shape, stronger in winter/coastal/maritime areas."""
    vals = []
    for m in range(12):
        angle = 2.0 * math.pi * (m - 0.5) / 12.0
        winter = 1.0 + 0.24 * math.cos(angle)
        shoulder = 1.0 + 0.05 * math.sin(2.0 * angle)
        maritime = 1.0 + 0.05 * max(boundary_proximity, island_factor) * math.cos(angle)
        vals.append(winter * shoulder * maritime)
    arr = np.array(vals, dtype=float)
    return arr / max(arr.mean(), 1e-9)


def _heuristic_resource_profiles(lat: float, lon: float, boundary_proximity: float, island_factor: float) -> dict:
    """Fallback European resource model (unchanged from the original)."""
    southness = clamp((45.0 - lat) / 15.0, -1.0, 1.2)
    mediterranean_bonus = clamp((42.0 - lat) / 10.0, 0.0, 1.2)
    continental_south_east_bonus = clamp((lon - 5.0) / 25.0, -0.6, 0.8) * clamp((48.0 - lat) / 15.0, 0.0, 1.0)
    atlantic_cloud_penalty = clamp((lat - 48.0) / 14.0, 0.0, 1.0) * clamp((5.0 - lon) / 16.0, 0.0, 1.0)
    alpine_penalty = 0.025 * clamp((lat - 44.0) / 4.0, 0.0, 1.0) * clamp((12.0 - abs(lon - 10.0)) / 12.0, 0.0, 1.0)

    solar_annual = (
        0.130
        + 0.100 * southness
        + 0.055 * mediterranean_bonus
        + 0.020 * continental_south_east_bonus
        - 0.030 * atlantic_cloud_penalty
        - alpine_penalty
        + (0.012 if island_factor > 0 and lat < 42 else 0.0)
    )
    solar_annual = clamp(solar_annual, 0.080, 0.300)

    northerliness = clamp((lat - 48.0) / 14.0, 0.0, 1.2)
    atlantic = clamp((2.0 - lon) / 18.0, 0.0, 1.0)
    north_sea = clamp((15.0 - abs(lon - 5.0)) / 15.0, 0.0, 1.0) * clamp((lat - 52.0) / 10.0, 0.0, 1.0)
    baltic = clamp((lon - 15.0) / 15.0, 0.0, 1.0) * clamp((lat - 53.0) / 8.0, 0.0, 1.0)
    inland_penalty = (1.0 - boundary_proximity) * clamp((48.0 - lat) / 12.0, 0.0, 1.0) * 0.035

    wind_annual = (
        0.185
        + 0.110 * boundary_proximity
        + 0.065 * northerliness
        + 0.050 * atlantic
        + 0.045 * north_sea
        + 0.025 * baltic
        + 0.025 * island_factor
        - inland_penalty
    )
    wind_annual = clamp(wind_annual, 0.160, 0.520)

    solar = np.clip(solar_annual * _seasonal_shape_solar(lat), 0.005, 0.32)
    wind = np.clip(wind_annual * _seasonal_shape_wind(lat, boundary_proximity, island_factor), 0.08, 0.70)
    return {
        "solar_cf_monthly": solar,
        "wind_cf_monthly": wind,
        "solar_cf_annual": float(solar.mean()),
        "wind_cf_annual": float(wind.mean()),
        "resource_source": "heuristic",
        "resource_error": "",
    }


def _cache_file(cache_dir: Path, lat: float, lon: float, prefix: str = "nasa_power") -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{prefix}_{lat:.3f}_{lon:.3f}.json"


def _call_nasa_power(lat: float, lon: float, cache_dir: Path, timeout_s: float = 15.0, sleep_s: float = 0.05) -> dict:
    import urllib.parse
    import urllib.request

    fp = _cache_file(cache_dir, lat, lon)
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))

    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,WS50M",
        "community": "RE",
        "longitude": f"{lon:.5f}",
        "latitude": f"{lat:.5f}",
        "format": "JSON",
    }
    url = "https://power.larc.nasa.gov/api/temporal/climatology/point?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "saf-eu-airport-siting/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        text = resp.read().decode("utf-8")
    data = json.loads(text)
    fp.write_text(json.dumps(data), encoding="utf-8")
    if sleep_s > 0:
        time.sleep(sleep_s)
    return data


def _call_pvgis_monthly(lat: float, lon: float, cache_dir: Path, timeout_s: float = 15.0) -> dict:
    """Optional high-accuracy PV source: PVGIS v5.2 PVcalc (JRC).

    Returns monthly energy per kWp for an optimally-tilted fixed system with
    14% system losses. Coverage: Europe, Africa, most of Asia - includes all
    model countries. Used when resource-source 'pvgis' is selected.
    """
    import urllib.parse
    import urllib.request

    fp = _cache_file(cache_dir, lat, lon, prefix="pvgis")
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    params = {
        "lat": f"{lat:.4f}", "lon": f"{lon:.4f}",
        "peakpower": "1", "loss": "14", "optimalangles": "1", "outputformat": "json",
    }
    url = "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "saf-eu-airport-siting/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    fp.write_text(json.dumps(data), encoding="utf-8")
    return data


def wind_cf_from_mean_speed(
    mean_speed_ms: float,
    k: float = WIND_WEIBULL_K,
    loss_factor: float = WIND_LOSS_FACTOR,
) -> float:
    """Capacity factor from a MEAN wind speed via Weibull integration.

    Integrates the normalised power curve over a Weibull distribution whose
    mean equals mean_speed_ms. This is the standard screening-level fix for
    the Jensen-inequality bias of evaluating a power curve at the mean speed.
    """
    if mean_speed_ms <= 0.1:
        return 0.0
    lam = mean_speed_ms / math.gamma(1.0 + 1.0 / k)
    pdf = (k / lam) * (_V_GRID / lam) ** (k - 1.0) * np.exp(-((_V_GRID / lam) ** k))
    _trapz = getattr(np, "trapezoid", None) or np.trapz
    cf = float(_trapz(_P_NORM * pdf, _V_GRID)) * loss_factor
    return clamp(cf, 0.0, 0.60)


def _shear_alpha(boundary_proximity: float, island_factor: float) -> float:
    """Power-law shear exponent: lower over open/coastal terrain, higher inland."""
    coastal = max(boundary_proximity, island_factor)
    return 0.20 - 0.06 * clamp(coastal, 0.0, 1.0)


def _wind_speed_to_capacity_factor_legacy(ws50_monthly: np.ndarray) -> np.ndarray:
    """Original conversion (kept for A/B comparison): power curve AT mean speed."""
    hub = ws50_monthly * (100.0 / 50.0) ** 0.14
    cf = []
    for v in hub:
        if v < 3.0:
            x = 0.02
        elif v < 12.0:
            x = 0.50 * ((v - 3.0) / 9.0) ** 2.15
        elif v < 25.0:
            x = 0.50
        else:
            x = 0.0
        cf.append(x)
    return np.clip(np.array(cf, dtype=float), 0.02, 0.58)


def _solar_tilt_gain_monthly(lat: float, ghi_shape: np.ndarray) -> np.ndarray:
    """Approximate monthly transposition gain for optimally tilted fixed PV.

    Tilting raises annual plane-of-array irradiation relative to horizontal
    (more at high latitude) and redistributes yield toward winter months.
    This is a screening approximation; use resource-source 'pvgis' for
    site-accurate monthly PV yields.
    """
    annual_gain = clamp(0.006 * (lat - 20.0), 0.0, 0.25)
    redistribution = 0.30 * (1.0 - ghi_shape / max(ghi_shape.mean(), 1e-9))
    gain = (1.0 + annual_gain) * (1.0 + redistribution)
    # renormalise so the annual energy gain equals annual_gain exactly
    gain *= (1.0 + annual_gain) / max((gain * ghi_shape).sum() / max(ghi_shape.sum(), 1e-9), 1e-9)
    return gain


def _nasa_power_profiles(
    lat: float,
    lon: float,
    cache_dir: Path,
    timeout_s: float,
    boundary_proximity: float = 0.0,
    island_factor: float = 0.0,
    calibration: str = "none",
    wind_method: str = "weibull",
) -> dict:
    """NASA POWER profiles.

    calibration="none" (recommended): direct physical conversion, no floors.
    calibration="legacy": original blend/floor/clip behaviour, reproduced exactly.
    """
    data = _call_nasa_power(lat, lon, cache_dir=cache_dir, timeout_s=timeout_s)
    p = data["properties"]["parameter"]
    ghi = np.array([float(p["ALLSKY_SFC_SW_DWN"][m]) for m in MONTH_KEYS], dtype=float)
    ws50 = np.array([float(p["WS50M"][m]) for m in MONTH_KEYS], dtype=float)

    solar_raw = np.clip(ghi * 0.82 / 24.0, 0.006, 0.32)
    wind_raw_legacy = _wind_speed_to_capacity_factor_legacy(ws50)

    if calibration == "legacy":
        heuristic = _heuristic_resource_profiles(lat, lon, boundary_proximity, island_factor)
        solar = 0.82 * solar_raw + 0.18 * heuristic["solar_cf_monthly"]
        wind_blended = 0.55 * wind_raw_legacy + 0.45 * heuristic["wind_cf_monthly"]
        wind = np.maximum(wind_blended, 0.82 * heuristic["wind_cf_monthly"])
        solar = np.clip(solar, 0.075, 0.285)
        wind = np.clip(wind, 0.135, 0.50)
        source = "nasa_power_calibrated"
    elif calibration == "none":
        # Solar: GHI -> horizontal CF at PR=0.82, then approximate optimal-tilt
        # transposition. Wind: Weibull-integrated CF at terrain-aware hub speed.
        ghi_shape = ghi / max(ghi.mean(), 1e-9)
        tilt_gain = _solar_tilt_gain_monthly(lat, ghi_shape)
        solar = np.clip(ghi * tilt_gain * 0.82 / 24.0, 0.0, 0.32)
        if wind_method == "weibull":
            alpha = _shear_alpha(boundary_proximity, island_factor)
            hub = ws50 * (WIND_HUB_HEIGHT_M / 50.0) ** alpha
            wind = np.array([wind_cf_from_mean_speed(v) for v in hub], dtype=float)
        else:
            wind = wind_raw_legacy
        source = "nasa_power_physical"
    else:
        raise ValueError(f"Unknown calibration: {calibration}")

    return {
        "solar_cf_monthly": solar,
        "wind_cf_monthly": wind,
        "solar_cf_annual": float(np.average(solar, weights=MONTH_HOURS)),
        "wind_cf_annual": float(np.average(wind, weights=MONTH_HOURS)),
        "resource_source": source,
        "resource_error": "",
        "raw_nasa_solar_cf_annual": float(np.average(solar_raw, weights=MONTH_HOURS)),
        "raw_nasa_wind_cf_annual": float(np.average(wind_raw_legacy, weights=MONTH_HOURS)),
        "nasa_ws50_annual_ms": float(np.average(ws50, weights=MONTH_HOURS)),
    }


def _pvgis_nasa_profiles(
    lat: float, lon: float, cache_dir: Path, timeout_s: float,
    boundary_proximity: float, island_factor: float, wind_method: str = "weibull",
) -> dict:
    """PVGIS monthly PV yield (accurate, optimal tilt) + NASA/Weibull wind."""
    base = _nasa_power_profiles(
        lat, lon, cache_dir, timeout_s, boundary_proximity, island_factor,
        calibration="none", wind_method=wind_method,
    )
    pv = _call_pvgis_monthly(lat, lon, cache_dir, timeout_s=timeout_s)
    monthly = pv["outputs"]["monthly"]["fixed"]
    e_month = np.array([float(m["E_m"]) for m in monthly], dtype=float)  # kWh/kWp per month
    solar = np.clip(e_month / MONTH_HOURS, 0.0, 0.35)
    base["solar_cf_monthly"] = solar
    base["solar_cf_annual"] = float(np.average(solar, weights=MONTH_HOURS))
    base["resource_source"] = "pvgis_nasa_physical"
    return base


def monthly_resource_profiles(
    lat: float,
    lon: float,
    boundary_proximity: float,
    island_factor: float,
    source: Literal["auto", "nasa", "heuristic", "pvgis"] = "auto",
    cache_dir: Path | None = None,
    timeout_s: float = 12.0,
    calibration: str = "none",
    wind_method: str = "weibull",
) -> dict:
    """Return monthly solar/wind CF profiles for a candidate location.

    source='nasa'   NASA POWER only, raises on failure.
    source='auto'   NASA POWER, falls back to heuristic.
    source='pvgis'  PVGIS monthly PV + NASA wind, falls back to 'auto' path.
    source='heuristic' offline reproducible fallback.
    calibration='legacy' restores the original blend/floor behaviour exactly.
    """
    if source == "heuristic":
        return _heuristic_resource_profiles(lat, lon, boundary_proximity, island_factor)

    cache_dir = cache_dir or Path("resource_cache")
    if source == "pvgis":
        try:
            return _pvgis_nasa_profiles(lat, lon, cache_dir, timeout_s, boundary_proximity, island_factor, wind_method)
        except Exception as exc:
            log(f"PVGIS unavailable ({str(exc)[:80]}); falling back to NASA path")
            source = "auto"
    try:
        return _nasa_power_profiles(
            lat, lon, cache_dir=cache_dir, timeout_s=timeout_s,
            boundary_proximity=boundary_proximity, island_factor=island_factor,
            calibration=calibration, wind_method=wind_method,
        )
    except Exception as exc:
        if source == "nasa":
            raise
        fallback = _heuristic_resource_profiles(lat, lon, boundary_proximity, island_factor)
        fallback["resource_source"] = "heuristic_fallback"
        fallback["resource_error"] = str(exc)[:220]
        return fallback


def daylight_fraction_monthly(lat: float) -> np.ndarray:
    """Fraction of each month's hours with the sun above the horizon.

    Standard sunset-hour-angle formula, evaluated at each month's mid-day.
    Used by the optimiser's day/night battery sub-model.
    """
    phi = math.radians(lat)
    fracs = []
    for n in MONTH_MID_DOY:
        decl = math.radians(23.45) * math.sin(2.0 * math.pi * (284.0 + n) / 365.0)
        x = -math.tan(phi) * math.tan(decl)
        x = clamp(x, -1.0, 1.0)
        omega_s = math.acos(x)
        fracs.append(omega_s / math.pi)
    return np.array(fracs, dtype=float)


def infer_boundary_proximity(geom, point) -> float:
    minx, miny, maxx, maxy = geom.bounds
    bbox_diag = math.hypot(maxx - minx, maxy - miny) + 1e-6
    dist_deg = point.distance(geom.boundary)
    near_edge = 1.0 - clamp(dist_deg / (0.18 * bbox_diag), 0.0, 1.0)
    return near_edge


def infer_island_factor(iso3: str) -> float:
    return 1.0 if iso3 in {"MLT", "CYP", "IRL", "GBR"} else 0.0
