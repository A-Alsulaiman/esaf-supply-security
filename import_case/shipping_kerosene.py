"""Kerosene (e-SAF) sea-shipping module for the import case.

Ported from the user's ammonia trade model (AP_IRA_Model/Shipping_route_model.py),
keeping IDENTICAL: the lane graph build (Shipping_Lanes_v1.geojson), the cached
0.5-degree ocean-grid graph, lane-to-grid connectors, Suez/Panama canal boxes and
scenario routing, the WPI (UpdatedPub150) port matching, the emission anchors
(g CO2 per tonne-nm) and the 50% EU / 50% origin carbon-price treatment of
shipping emissions.

REPLACED: the ammonia cost curve (Schuler EUR/MWh x NH3 LHV) with a clean
product-tanker voyage model for jet fuel (charter + bunkers + port costs + canal
tolls over a round trip, divided over the cargo parcel), with the same year
multipliers (BAU 1.00 in 2030 -> 0.80 in 2050) and the same +/-30% band.

All money is real USD-2024 (EUR inputs converted at 1.0824 USD/EUR, the v8 rate).
Validation: reproduces the ammonia model's Jiddah->Rotterdam all_canals distance
of 3,855.746 nm from the same caches (see __main__).
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

import networkx as nx
import pandas as pd

HERE = Path(__file__).resolve().parent
AP = HERE / "ap_ira_staged"

KM_PER_NM = 1.852
EARTH_RADIUS_NM = 3440.065
EUR_TO_USD = 1.0824

# --- identical to the ammonia model -----------------------------------------
COST_YEAR_MULTIPLIERS_BAU = {2020: 1.00, 2030: 1.00, 2040: 0.90, 2050: 0.80}
EMISSION_ANCHORS_BAU = {2020: 10.0, 2030: 9.0, 2040: 7.0, 2050: 5.0}  # g CO2 / t-nm
SUEZ_TOLL_USD = 300_000.0 * EUR_TO_USD          # their default, per transit
PANAMA_TOLL_USD = 250_000.0 * EUR_TO_USD


def is_in_suez(lon, lat):
    return (31.0 <= lon <= 33.5) and (29.0 <= lat <= 32.0)


def is_in_panama(lon, lat):
    return (-80.5 <= lon <= -79.0) and (8.0 <= lat <= 10.0)


def haversine_nm(lon1, lat1, lon2, lat2):
    lon1r, lat1r, lon2r, lat2r = map(math.radians, (lon1, lat1, lon2, lat2))
    a = math.sin((lat2r - lat1r) / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin((lon2r - lon1r) / 2) ** 2
    return EARTH_RADIUS_NM * 2 * math.asin(math.sqrt(a))


def interpolate_by_year(anchor, year):
    ys = sorted(anchor)
    if year <= ys[0]:
        return anchor[ys[0]]
    if year >= ys[-1]:
        return anchor[ys[-1]]
    for y0, y1 in zip(ys[:-1], ys[1:]):
        if y0 <= year <= y1:
            r = (year - y0) / (y1 - y0)
            return anchor[y0] + r * (anchor[y1] - anchor[y0])
    return anchor[ys[-1]]


# --- graph build (lane geojson parsed with plain json; no geopandas) --------

def _lane_lines(geojson_path):
    gj = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    for feat in gj["features"]:
        geom = feat.get("geometry") or {}
        typ = (feat.get("properties") or {}).get("Type")
        if geom.get("type") == "LineString":
            yield typ, geom["coordinates"]
        elif geom.get("type") == "MultiLineString":
            for line in geom["coordinates"]:
                yield typ, line


def build_graph_from_lanes(geojson_path):
    G = nx.Graph()
    node_coords, coord_to_node = {}, {}
    counter = 0
    for lane_type, coords in _lane_lines(geojson_path):
        if len(coords) < 2:
            continue
        for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
            k1, k2 = (round(lon1, 5), round(lat1, 5)), (round(lon2, 5), round(lat2, 5))
            u = coord_to_node.get(k1)
            if u is None:
                u = counter; counter += 1
                coord_to_node[k1] = u; node_coords[u] = (lon1, lat1)
                G.add_node(u, lon=lon1, lat=lat1, kind="lane")
            v = coord_to_node.get(k2)
            if v is None:
                v = counter; counter += 1
                coord_to_node[k2] = v; node_coords[v] = (lon2, lat2)
                G.add_node(v, lon=lon2, lat=lat2, kind="lane")
            mid_lon, mid_lat = (lon1 + lon2) / 2, (lat1 + lat2) / 2
            canal = "suez" if is_in_suez(mid_lon, mid_lat) else ("panama" if is_in_panama(mid_lon, mid_lat) else None)
            G.add_edge(u, v, distance_nm=haversine_nm(lon1, lat1, lon2, lat2),
                       lane_type=lane_type, canal=canal, kind="lane")
    return G, node_coords


def load_ocean_grid(gpickle_path):
    with open(gpickle_path, "rb") as f:
        G = pickle.load(f)
    coords = {n: (d["lon"], d["lat"]) for n, d in G.nodes(data=True)}
    g = G.graph
    meta = {
        "lat_min": g.get("lat_min", g.get("grid_lat_min")),
        "lon_min": g.get("lon_min", g.get("grid_lon_min")),
        "step": g.get("step_deg", g.get("grid_resolution_deg")),
    }
    return G, coords, meta


def connect_lanes_to_ocean_grid(G_lanes, lane_coords, G_ocean, ocean_coords, meta, max_radius_cells=3):
    G = nx.compose(G_lanes, G_ocean)
    node_coords = dict(lane_coords); node_coords.update(ocean_coords)
    lat_min, lon_min, step = meta["lat_min"], meta["lon_min"], meta["step"]
    index = {}
    for nid, (lon, lat) in ocean_coords.items():
        index.setdefault((int(round((lat - lat_min) / step)), int(round((lon - lon_min) / step))), []).append(nid)
    added = 0
    for lid, (lon, lat) in lane_coords.items():
        i0, j0 = int(round((lat - lat_min) / step)), int(round((lon - lon_min) / step))
        best, bestd = None, float("inf")
        for radius in range(max_radius_cells + 1):
            found = False
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    for oid in index.get((i0 + di, j0 + dj), ()):
                        lon2, lat2 = ocean_coords[oid]
                        d = haversine_nm(lon, lat, lon2, lat2)
                        if d < bestd:
                            bestd, best, found = d, oid, True
            if found:
                break
        if best is not None:
            G.add_edge(lid, best, distance_nm=bestd, lane_type="LaneToGrid", canal=None, kind="connector")
            added += 1
    return G, node_coords


# --- ports (same WPI matching as the ammonia model) --------------------------

def load_wpi(path=None):
    return pd.read_csv(path or AP / ".ports_cache" / "UpdatedPub150.csv", low_memory=False)


def find_port(wpi, name, country=None):
    df = wpi
    if country is not None:
        df = df[df["Country Code"].astype(str).str.upper() == str(country).upper()]
    names = df["Main Port Name"].astype(str)
    t = name.strip().upper()
    m = names.str.upper() == t
    if not m.any():
        m = names.str.upper().str.contains(t, regex=False)
    if not m.any():
        raise ValueError(f"port '{name}' not found")
    return df.loc[m].iloc[0]


# --- routing -----------------------------------------------------------------

class SeaRouter:
    """Build once, route many. Snaps ports to the nearest LANE node (as the
    ammonia model does) and runs the four canal scenarios."""

    def __init__(self):
        self.G_lanes, self.lane_coords = build_graph_from_lanes(AP / "Trade_version" / "Shipping_Lanes_v1.geojson")
        G_ocean, ocean_coords, meta = load_ocean_grid(AP / ".ocean_cache" / "ocean_grid_0p5_lat-75_75.gpickle")
        self.G, self.node_coords = connect_lanes_to_ocean_grid(
            self.G_lanes, self.lane_coords, G_ocean, ocean_coords, meta)
        self.ocean_coords, self.meta = ocean_coords, meta
        self._ocean_index = {}
        for nid, (lon, lat) in ocean_coords.items():
            key = (int(round((lat - meta["lat_min"]) / meta["step"])),
                   int(round((lon - meta["lon_min"]) / meta["step"])))
            self._ocean_index.setdefault(key, []).append(nid)

    def _nearest_ocean_nodes(self, lon, lat, k=4, max_radius_cells=6):
        i0 = int(round((lat - self.meta["lat_min"]) / self.meta["step"]))
        j0 = int(round((lon - self.meta["lon_min"]) / self.meta["step"]))
        found = []
        for radius in range(max_radius_cells + 1):
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    if max(abs(di), abs(dj)) != radius:
                        continue
                    for oid in self._ocean_index.get((i0 + di, j0 + dj), ()):
                        olon, olat = self.ocean_coords[oid]
                        found.append((haversine_nm(lon, lat, olon, olat), oid))
            if len(found) >= k:
                break
        return sorted(found)[:k]

    def _nearest_lane_node(self, lon, lat):
        best, bestd = None, float("inf")
        for nid, (nlon, nlat) in self.lane_coords.items():
            d = haversine_nm(lon, lat, nlon, nlat)
            if d < bestd:
                bestd, best = d, nid
        return best, bestd

    def route(self, o_lon, o_lat, d_lon, d_lat):
        """Return list of dicts (one per feasible canal scenario).

        Deviation from the ammonia model (documented): each port is inserted
        as a temporary node connected to its nearest lane node AND its k=4
        nearest ocean-grid cells, and the sailed distance includes those
        connector legs. The ammonia runs snapped ports to the nearest lane
        node and excluded the offset (fine for Rotterdam/Jeddah, which sit on
        lanes); Baltic and Adriatic import ports here sit hundreds of nm off
        the lane network, where a straight offset would cut across land —
        routing them through the water-only ocean grid instead."""
        temp = []
        for tag, lon, lat in [("P_ORIG", o_lon, o_lat), ("P_DEST", d_lon, d_lat)]:
            self.G.add_node(tag, lon=lon, lat=lat, kind="port")
            temp.append(tag)
            ln, loff = self._nearest_lane_node(lon, lat)
            self.G.add_edge(tag, ln, distance_nm=loff, canal=None, kind="port_connector")
            for d, oid in self._nearest_ocean_nodes(lon, lat):
                self.G.add_edge(tag, oid, distance_nm=d, canal=None, kind="port_connector")
        try:
            out = []
            for name, no_suez, no_pan in [("all_canals", False, False), ("avoid_suez", True, False),
                                          ("avoid_panama", False, True), ("avoid_suez_and_panama", True, True)]:
                if no_suez or no_pan:
                    def ok(u, v, _ns=no_suez, _np=no_pan):
                        c = self.G.edges[u, v].get("canal")
                        return not ((_ns and c == "suez") or (_np and c == "panama"))
                    Gw = nx.subgraph_view(self.G, filter_edge=ok)
                else:
                    Gw = self.G
                try:
                    path = nx.shortest_path(Gw, "P_ORIG", "P_DEST", weight="distance_nm")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                dist = sum(self.G.edges[u, v]["distance_nm"] for u, v in zip(path[:-1], path[1:]))
                uses_suez = any(self.G.edges[u, v].get("canal") == "suez" for u, v in zip(path[:-1], path[1:]))
                uses_pan = any(self.G.edges[u, v].get("canal") == "panama" for u, v in zip(path[:-1], path[1:]))
                out.append({"scenario": name, "distance_nm": dist,
                            "uses_suez": uses_suez, "uses_panama": uses_pan})
            return out
        finally:
            for tag in temp:
                self.G.remove_node(tag)


# --- kerosene tanker economics ----------------------------------------------
# Clean product tankers (jet fuel is a standard clean petroleum cargo — no
# cryogenics or pressure vessels, unlike ammonia). Parameters are editable and
# sourced in the workbook sheet: 1-yr time-charter mid-cycle rates and bunker
# consumption from Clarksons/BRS-style ranges; VLSFO 2024 ~550 USD/t.

VESSELS = {
    # cargo_t, charter_usd_day, sea_burn_t_day, port_burn_t_day, speed_kn, port_cost_usd_call
    "MR":  dict(cargo_t=40_000.0, charter_usd_day=25_000.0, sea_burn_t_day=26.0,
                port_burn_t_day=5.0, speed_kn=13.5, port_cost_usd_call=60_000.0),
    "LR2": dict(cargo_t=90_000.0, charter_usd_day=32_000.0, sea_burn_t_day=38.0,
                port_burn_t_day=7.0, speed_kn=13.5, port_cost_usd_call=80_000.0),
}
BUNKER_USD_T = 550.0          # VLSFO, real 2024
PORT_DAYS_ROUND_TRIP = 3.0    # load + discharge + waiting
CANAL_DAYS_PER_TRANSIT = 1.0
TOLL_TRANSITS_PER_VOYAGE = 2  # laden + ballast return (dedicated shuttle service)


def kerosene_cost_usd_per_t(distance_nm, year, vessel="MR", uses_suez=False, uses_panama=False):
    """Deterministic (low, mid, high) USD-2024 per tonne of jet cargo, one-way
    delivery, round-trip vessel economics. Same year multiplier and +/-30% band
    as the ammonia model."""
    v = VESSELS[vessel]
    transits = (1 if uses_suez else 0) + (1 if uses_panama else 0)
    sea_days = 2.0 * distance_nm / (v["speed_kn"] * 24.0)
    days = sea_days + PORT_DAYS_ROUND_TRIP + CANAL_DAYS_PER_TRANSIT * transits * TOLL_TRANSITS_PER_VOYAGE
    charter = v["charter_usd_day"] * days
    bunkers = (v["sea_burn_t_day"] * sea_days + v["port_burn_t_day"] * PORT_DAYS_ROUND_TRIP) * BUNKER_USD_T
    ports = 2 * v["port_cost_usd_call"]
    tolls = TOLL_TRANSITS_PER_VOYAGE * ((SUEZ_TOLL_USD if uses_suez else 0.0) + (PANAMA_TOLL_USD if uses_panama else 0.0))
    mid_2030 = (charter + bunkers + ports + tolls) / v["cargo_t"]
    mid = mid_2030 * interpolate_by_year(COST_YEAR_MULTIPLIERS_BAU, year)
    return 0.7 * mid, mid, 1.3 * mid


def shipping_co2_t_per_t_cargo(distance_nm, year):
    """Same emission anchors as the ammonia model (BAU), g CO2 per tonne-nm."""
    ef = interpolate_by_year(EMISSION_ANCHORS_BAU, year)
    return ef / 1e6 * distance_nm


def shipping_ets_usd_per_t(co2_t_per_t, eu_ets_usd_per_t_co2, origin_carbon_usd_per_t_co2=0.0,
                           eu_share=0.5, origin_share=0.5):
    """Identical carbon treatment to the ammonia DCF model: 50% x EU price +
    50% x origin-country price on voyage emissions (EU ETS maritime covers 50%
    of extra-EEA voyage emissions; SAU/ARE/MAR carbon prices default to 0)."""
    return co2_t_per_t * (eu_share * eu_ets_usd_per_t_co2 + origin_share * origin_carbon_usd_per_t_co2)


def best_route_economics(routes, year, vessel, eu_ets_usd, origin_carbon_usd=0.0):
    """Pick the cheapest canal scenario by mid total cost incl. ETS (as the
    ammonia wrapper keeps the best route) and return its full economics."""
    best = None
    for r in routes:
        lo, mid, hi = kerosene_cost_usd_per_t(r["distance_nm"], year, vessel, r["uses_suez"], r["uses_panama"])
        co2 = shipping_co2_t_per_t_cargo(r["distance_nm"], year)
        ets = shipping_ets_usd_per_t(co2, eu_ets_usd, origin_carbon_usd)
        tot = mid + ets
        if best is None or tot < best["total_usd_per_t"]:
            best = dict(r, cost_usd_per_t_low=lo, cost_usd_per_t=mid, cost_usd_per_t_high=hi,
                        co2_t_per_t=co2, ets_usd_per_t=ets, total_usd_per_t=tot, vessel=vessel, year=year)
    return best


if __name__ == "__main__":
    # Validation against the ammonia model's own cached result:
    # Jiddah -> Rotterdam, all_canals = 3855.746 nm (their summary CSV).
    wpi = load_wpi()
    r = SeaRouter()
    o = find_port(wpi, "Jiddah")
    d = find_port(wpi, "Rotterdam")
    routes = r.route(float(o["Longitude"]), float(o["Latitude"]), float(d["Longitude"]), float(d["Latitude"]))
    for rr in sorted(routes, key=lambda x: x["distance_nm"]):
        print(f"{rr['scenario']:24s} {rr['distance_nm']:9.3f} nm  suez={rr['uses_suez']} panama={rr['uses_panama']}")
    ac = [rr for rr in routes if rr["scenario"] == "all_canals"][0]
    ref = 3855.7460865150533
    print(f"\nall_canals vs ammonia model reference: {ac['distance_nm']:.3f} vs {ref:.3f} "
          f"(diff {abs(ac['distance_nm']-ref):.4f} nm)")
    lo, mid, hi = kerosene_cost_usd_per_t(ac["distance_nm"], 2030, "MR", ac["uses_suez"], ac["uses_panama"])
    co2 = shipping_co2_t_per_t_cargo(ac["distance_nm"], 2030)
    print(f"kerosene MR 2030: {mid:.1f} USD/t (band {lo:.1f}-{hi:.1f}); CO2 {co2*1000:.1f} kg/t; "
          f"ETS@141USD 50%: {shipping_ets_usd_per_t(co2, 141.0):.2f} USD/t")
