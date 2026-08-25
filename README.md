# eSAF Supply-Security Model

Spatially explicit techno-economic model behind the paper *"Domestic production versus
imports under ReFuelEU Aviation: delivered costs and the supply-security premium of
synthetic aviation fuel at 29 European hub airports"* (under review). The model computes
the delivered cost of mandate-sized synthetic aviation fuel (eSAF) at the principal hub
airport of every EU-27 Member State plus the United Kingdom and Switzerland, computes the
delivered cost of imported supply from Saudi Arabia, the United Arab Emirates and Morocco
over routed sea lanes, and reads the difference as a supply-security premium per country
and compliance year.

## Repository structure

```
run_full_eu27_uk_ch.py        entry point for the domestic model (all 29 markets + exporters)
src/saf_eu_model/             the model package
  economics.py                capex, opex, annuities, scale multipliers, WACC treatment
  process.py                  FT process train, electrolysis, storage configurations
  optimizer.py                site-level renewable mix and operating-strategy optimisation
  resources.py                solar and wind resource layers (NASA POWER / PVGIS / offline)
  carbon.py                   biogenic CO2 supply and DAC fallback
  geography.py                country geometries and candidate grids
  runner.py                   batch orchestration and result assembly
  data/                       input datasets with full source strings per parameter
import_case/                  export-side production, sea shipping and import delivery
  run_import_case.py          entry point for the import model
  shipping_kerosene.py        routed sea-lane graph and clean-tanker voyage costing
presentation/                 figure, workbook and graphical-abstract scripts
results_v8_2030/              model outputs used by the paper (2030 mandate)
results_v8_2050t30/           2050 volumes at 2030 technology (scale-only scenario)
results_v8_2050t50/           2050 mandate at 2050 technology
results_v8_fixed/             common-scale geography experiment
```

Every parameter in `src/saf_eu_model/data/literature_inputs.json` carries its source
string, so the input set is auditable line by line. All monetary values are real USD-2024.

## Installation

Python 3.10 or newer.

```
pip install -r requirements.txt
```

`geopandas` and `pyogrio` install as binary wheels on all major platforms.

## Running in Spyder

Every script resolves its own paths from its file location, so it can be opened and run
directly (F5) without setting a working directory:

1. `run_full_eu27_uk_ch.py` runs the domestic model. Useful variables to adjust are
   exposed as command-line arguments with defaults, so a plain run reproduces the core
   case. From a terminal, `python run_full_eu27_uk_ch.py --help` lists the options
   (country subset, candidate-grid spacing, resource source, scenario year).
2. `import_case/run_import_case.py` runs the export-side production and shipping model
   and writes the import delivered-cost tables.
3. `presentation/make_figures.py` regenerates every figure of the paper from the result
   CSVs, `presentation/make_workbook.py` builds the provenance workbook, and
   `presentation/make_graphical_abstract.py` builds the graphical abstract from
   `results_v8_2030` and `import_case`.

The result CSVs shipped in `results_v8_*` are the model outputs used in the paper, so the
presentation scripts run without re-running the model. Re-running the model reproduces
them, and the offline resource heuristic (`--resource-source heuristic`) reproduces every
number in the paper without any external data service.

## Notes on data

The sea-lane geometries in `presentation/sea_routes_geometry.json` are extracted from the
shipping graph in `import_case/shipping_kerosene.py`. The map background in
`presentation/map_countries_110m.json` is Natural Earth 1:110m country boundaries (public
domain). Airport fuel uplifts, mandate shares and all techno-economic inputs are
documented with their sources in `src/saf_eu_model/data/`.

## Citation

If you use this code or its results, please cite the paper above (a `CITATION.cff` file
is included) and this repository.

## License

MIT, see `LICENSE`.
