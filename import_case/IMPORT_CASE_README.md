# Import case — SAU / ARE / MAR exporting e-SAF to every EU-27 hub

## What this computes

Delivered cost (real USD-2024 per tonne of e-SAF) of importing from Saudi
Arabia, the UAE and Morocco to each EU-27 hub airport, versus producing
domestically (v8 mandate runs), for 2030 (2030 technology) and 2050 (2050
technology).

Chain: production at the best renewables site near the chosen export terminal
(v8 siting model, DAC carbon, country WACC) + inland to terminal (v8 transport
model: road/rail/pipeline, cheapest) + terminal handling (5 USD/t) + sea
shipping (kerosene product tanker on the routed lane network) + EU ETS on
voyage emissions + import-port handling (5 USD/t) + inland import port -> hub
airport (v8 road tariff 0.092 USD/t-km x 1.35 routing factor) + airport
receiving fee (5.412 USD/t, as domestic).

## Export strategy (as specified)

Exporters size ONE production system to 50% of the EU-27 synthetic mandate —
economies of scale without the burden of small national mandates, but with
the import share hard-capped at half the market, mirroring the REPowerEU
renewable-hydrogen balance (10 Mt domestic + 10 Mt imported by 2030):
EU fuel supplied at Union airports 2024 = 32.2 Mt (EASA ReFuelEU ATR 2025:
193 kt SAF = 0.6% of uplift) x growth (1.02 / 1.00) x synthetic share
(1.2% / 35%) x 50% = 197 kt/y (2030) and 5.64 Mt/y (2050). The production
cost to every EU country is then identical; countries differ only in the sea
lane, its ETS cost, and the inland legs. (`EXPORT_SHARE_OF_EU_DEMAND` in
`run_import_case.py`.)

Export terminals are chosen by trying candidates and keeping the cheapest
production + inland chain (terminal follows the resource):
SAU = King Fahd Port (Yanbu, Red Sea); ARE = Mina Jabal Ali (vs Al Fujayrah,
~7 USD/t apart); MAR = Ad Dakhla (beats Mohammedia/Agadir by 55-80 USD/t;
greenfield — Morocco's planned southern green-fuel corridor, Morocco-
administered Western Sahara).

## Shipping model provenance (AP_IRA_Model)

Ported UNCHANGED from the ammonia trade model
(`AP_IRA_Model/Shipping_route_model.py` + trade wrapper):

- lane graph from `Shipping_Lanes_v1.geojson` (same node/edge construction),
- cached 0.5-degree ocean-grid graph (`.ocean_cache/*.gpickle`, 152,360 nodes),
- lane-to-grid connectors, Suez/Panama canal boxes, four canal scenarios,
  cheapest kept,
- WPI `UpdatedPub150.csv` port matching,
- emission anchors g CO2/t-nm (BAU): 10 (2020), 9 (2030), 7 (2040), 5 (2050),
- carbon treatment of voyage emissions: 50% x EU carbon price + 50% x origin
  price (origin = 0 for SAU/ARE/MAR) — mirrors the ammonia DCF model and the
  EU ETS maritime 50% scope for extra-EEA voyages. EU ETS: 141 (2030) / 271
  (2050) USD/t CO2, as the Ticket impact sheet,
- cost-year multipliers (BAU): 1.00 (2030), 0.90 (2040), 0.80 (2050); +/-30% band,
- Suez/Panama tolls 300k/250k EUR per transit (x 1.0824 to USD; 2 transits
  per round-trip shuttle voyage).

REPLACED (ammonia -> kerosene), as instructed: the Schuler EUR/MWh ammonia
cost curve is swapped for a clean product-tanker voyage model (jet fuel is a
standard clean petroleum cargo — no cryogenics): round-trip charter + bunkers
+ port costs + tolls over the cargo parcel. MR (40 kt, 25k USD/day, 26 t/d at
13.5 kn) for 2030 volumes; LR2 (90 kt, 32k USD/day, 38 t/d) for 2050; VLSFO
550 USD/t; 3 port days; 1 day per canal transit. All editable in
`shipping_kerosene.py` (VESSELS / BUNKER_USD_T / ...).

Two documented deviations from the ammonia runs:

1. Ports are inserted as temporary graph nodes connected to the nearest lane
   node AND their 4 nearest ocean-grid cells, and connector legs count in the
   distance. The ammonia runs snapped to the nearest lane node and dropped
   the offset — fine for Rotterdam/Jeddah (on lanes), wrong for Baltic and
   Atlantic-Africa ports that sit 100-600 nm off the lane network (a straight
   offset would cross land; the water-only grid routes around it).
2. Kerosene cost parameters as above (the point of the exercise).

Validation: rebuilt from the same caches, Jiddah->Rotterdam lane-to-lane =
3,938 nm vs the ammonia model's cached 3,856 nm (2.1%; their cache was built
by an earlier grid-connector variant). With port connectors, five real-route
anchors agree within 1-2.5%: Yanbu->Rotterdam 4,074 (real ~4,150), Jebel
Ali->Rotterdam ~6,440, Mohammedia->Rotterdam 1,519 (~1,560), Mohammedia->
Sines 307 (~300), Yanbu->Stockholm 5,043 (~5,150).

## Import ports

Per EU country: the energy-capable WPI port (oil-terminal depth >= 9 m, or
oil-terminal flag, or Large/Medium liquid-bulk) nearest the hub airport.
Coastal countries always use a national port (relaxed to any Small+ national
harbor where WPI carries no oil attributes: SVN -> Koper, MLT -> Valletta).
Landlocked countries use the nearest foreign energy port: AUT -> Trieste,
CZE -> Szczecin, HUN/SVK -> Bakar, LUX -> Vlissingen. `import_ports.csv`.

## Files

- `shipping_kerosene.py` — router + tanker economics (validation in __main__)
- `run_import_case.py` — steps: ports | routes | production | assemble
- `import_ports.csv`, `sea_routes.csv` (300 routes), `export_production_raw.csv`
- `import_delivered_costs.csv` (162 rows: 26 x 3 x 2 + components)
- `import_best_by_country.csv` — cheapest exporter per country/year
- `results_export_{2030,2050}/<terminal>/<ISO3>/` — full siting outputs
- `ap_ira_staged/` — read-only copies of the ammonia-model inputs used

## Headline (base case, 50% import cap)

ARE is the cheapest exporter everywhere (WACC 8.0% vs SAU 8.7%, MAR 10.0% —
financing, not resource, decides). 2030: imports (7,037-7,108 USD/t
delivered) beat domestic production in 20 of 27 countries (exceptions: FRA,
DEU, ESP, NLD, PRT, DNK, ITA — the big low-cost hubs); micro-mandate
countries save 20-85%; on a demand-weighted average imports (7,076) and
domestic (7,056) are at parity. 2050: imports (3,010-3,070) win in 23 of 27
(exceptions ESP, FRA, DEU, PRT); weighted average 3,041 vs 3,207 domestic
(-5%). The whole sea leg incl. ETS is 10-72 USD/t — under 1% of delivered
cost in 2030 and ~1-2.5% in 2050; the import decision is set by production
scale and financing, not by shipping.

Downstream use: the Ticket impact sheet prices airlines' e-SAF at a live
50/50 blend of domestic and imported supply — 7,066 USD/t (2030) and 3,124
(2050), per-hub blends demand-weighted.

Caveats: no producer margin or certificate value on either side; RFNBO
import eligibility assumed (RED-compliant production); exporter volumes of
5.64 Mt/y (2050) represent a national export industry, not one plant — scale
exponents number-up accordingly; heuristic resource layer (same as domestic
runs).
