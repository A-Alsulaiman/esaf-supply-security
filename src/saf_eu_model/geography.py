from __future__ import annotations
from pathlib import Path
import math
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


def load_country_geometry(base_dir: Path, iso3: str):
    gdf = gpd.read_file(base_dir / "src" / "saf_eu_model" / "data" / "countries.geojson")
    row = gdf[gdf["country_iso3"] == iso3]
    if row.empty:
        raise ValueError(f"Country geometry not found for {iso3}")
    geom = row.iloc[0].geometry
    # Keep the largest mainland component for continental countries, but keep full
    # geometry for island states where small islands matter less than not losing the country.
    if geom.geom_type == "MultiPolygon" and iso3 not in {"MLT", "CYP", "IRL", "GBR", "DNK", "SWE", "FIN", "GRC", "ITA", "ESP", "PRT"}:
        geom = max(list(geom.geoms), key=lambda x: x.area)
    return geom


def country_area_km2(geom) -> float:
    gs = gpd.GeoSeries([geom], crs=4326).to_crs(3035)
    return float(gs.area.iloc[0] / 1e6)


def make_candidate_points_within_country(
    geom,
    cell_size_km: float = 80.0,
    max_candidates: int | None = None,
    airport_lat: float | None = None,
    airport_lon: float | None = None,
):
    """Create a projected equal-distance candidate grid inside the country.

    The previous degree-based grid could over-represent the middle of countries and
    under-sample edges/coasts. This uses EPSG:3035 metres, adds edge-aware points,
    and includes a near-airport point so transport trade-offs are visible.
    """
    if cell_size_km <= 0:
        raise ValueError("cell_size_km must be positive")

    cell_m = cell_size_km * 1000.0
    geom_proj = gpd.GeoSeries([geom], crs=4326).to_crs(3035).iloc[0]
    minx, miny, maxx, maxy = geom_proj.bounds
    rows = []

    # Offset grid by half cell; for small countries guarantee at least one loop.
    xs = list(_frange(minx + cell_m / 2.0, maxx, cell_m))
    ys = list(_frange(miny + cell_m / 2.0, maxy, cell_m))
    if not xs:
        xs = [(minx + maxx) / 2.0]
    if not ys:
        ys = [(miny + maxy) / 2.0]

    for x in xs:
        for y in ys:
            p_proj = Point(x, y)
            if geom_proj.contains(p_proj) or geom_proj.touches(p_proj):
                rows.append({"x": x, "y": y, "geometry_projected": p_proj, "kind": "grid"})

    # Add representative point if the grid misses a small/narrow country.
    if not rows:
        rp = geom_proj.representative_point()
        rows.append({"x": rp.x, "y": rp.y, "geometry_projected": rp, "kind": "fallback_representative_point"})

    # Add near-airport candidate if the airport lies inside/near the country polygon.
    if airport_lat is not None and airport_lon is not None:
        airport_proj = gpd.GeoSeries([Point(float(airport_lon), float(airport_lat))], crs=4326).to_crs(3035).iloc[0]
        nearest = airport_proj if geom_proj.buffer(cell_m * 0.6).contains(airport_proj) else geom_proj.boundary.interpolate(geom_proj.boundary.project(airport_proj))
        rows.append({"x": nearest.x, "y": nearest.y, "geometry_projected": nearest, "kind": "airport_or_nearest_boundary"})

    # Add four quartile interior points to avoid a single centroid-like fallback.
    for fracx, fracy in [(0.25, 0.25), (0.25, 0.75), (0.75, 0.25), (0.75, 0.75)]:
        p = Point(minx + fracx * (maxx - minx), miny + fracy * (maxy - miny))
        if geom_proj.contains(p):
            rows.append({"x": p.x, "y": p.y, "geometry_projected": p, "kind": "quartile"})

    # Remove near-duplicate projected points.
    dedup = []
    seen = set()
    for r in rows:
        key = (round(r["x"] / max(cell_m * 0.20, 1.0)), round(r["y"] / max(cell_m * 0.20, 1.0)))
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    rows = dedup

    # If the grid is extremely dense, thin it deterministically but preserve spatial spread.
    if max_candidates and len(rows) > max_candidates:
        # Sort along a Hilbert-like simple diagonal key and take evenly spaced records.
        rows_sorted = sorted(rows, key=lambda r: (r["x"] - minx) / (maxx - minx + 1e-9) + (r["y"] - miny) / (maxy - miny + 1e-9))
        idx = [round(i * (len(rows_sorted) - 1) / (max_candidates - 1)) for i in range(max_candidates)]
        rows = [rows_sorted[i] for i in sorted(set(idx))]

    gdf_proj = gpd.GeoDataFrame(rows, geometry="geometry_projected", crs=3035)
    gdf_ll = gdf_proj.to_crs(4326)
    out = pd.DataFrame({
        "lon": gdf_ll.geometry.x,
        "lat": gdf_ll.geometry.y,
        "kind": gdf_ll["kind"].values,
    })
    out_gdf = gpd.GeoDataFrame(out, geometry=[Point(xy) for xy in zip(out["lon"], out["lat"])], crs=4326)
    return out_gdf.reset_index(drop=True)


def _frange(start: float, stop: float, step: float):
    x = start
    # guard against accidental infinite loops
    n = 0
    while x <= stop and n < 100000:
        yield x
        x += step
        n += 1
