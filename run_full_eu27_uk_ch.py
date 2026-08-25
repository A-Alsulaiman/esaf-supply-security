from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from saf_eu_model import run_batch


def main():
    parser = argparse.ArgumentParser(
        description="Run literature-based PtL-SAF siting model for EU-27 + UK + Switzerland, with optional renewable-rich reference cases."
    )
    parser.add_argument("--countries", nargs="*", default=None, help="ISO3 list to run, e.g. CHE FRA GBR SAU")
    parser.add_argument("--include-renewable-rich-cases", action="store_true", help="Also run Saudi Arabia (SAU), UAE (ARE), and Morocco (MAR) reference cases.")
    parser.add_argument("--case-set", choices=["core", "renewable_rich", "all"], default="core", help="Default country set when --countries is not supplied.")
    parser.add_argument("--cell-step-deg", type=float, default=0.75, help="Backward-compatible grid spacing; converted to km if --cell-size-km is not set")
    parser.add_argument("--cell-size-km", type=float, default=None, help="Projected candidate-grid spacing in km. Recommended: 50-100 km")
    parser.add_argument("--max-candidates-per-country", type=int, default=None, help="Optional deterministic cap for faster national runs")
    parser.add_argument("--resource-source", choices=["auto", "nasa", "heuristic", "pvgis"], default="auto", help="auto=NASA POWER with fallback; nasa=NASA only; heuristic=offline fallback; pvgis=PVGIS PV + NASA wind")
    parser.add_argument("--resource-timeout-s", type=float, default=12.0, help="Timeout per resource API request")
    parser.add_argument("--results-dir", default="results", help="Output folder name")
    parser.add_argument("--max-delivery-distance-km", type=float, default=650.0, help="Screen out candidate sites farther than this from the target airport for normal-sized countries")
    parser.add_argument("--scenario", choices=["low", "base", "high"], default="base", help="Deterministic literature scenario")
    parser.add_argument("--annual-saf-tonnes", type=float, default=740000.0, help="Constant annual SAF demand/production target used for every country in the scenario")
    parser.add_argument("--plant-availability", type=float, default=None, help="PtL plant availability. Default: the value in the inputs file (0.90 base)")
    parser.add_argument("--currency", choices=["CHF", "EUR", "USD"], default="EUR", help="Reporting currency columns and map labels; USD is the internal model unit")
    parser.add_argument("--usd-to-eur", type=float, default=0.9239, help="Reporting conversion USD->EUR (2024 ECB annual average USD/EUR 1.0824). The model computes in real USD-2024.")
    parser.add_argument("--uncertainty-samples", type=int, default=0, help="Number of literature-range uncertainty samples")
    parser.add_argument("--seed", type=int, default=42, help="Seed for uncertainty sampling")
    parser.add_argument("--model-version", choices=["v3", "v2", "legacy"], default="v3", help="v3 = seasonally flexible plant with fuel storage + carbon sourcing + scale (v8, recommended); v2 = constant-output corrected model; legacy = original package")
    parser.add_argument("--sizing", choices=["fixed", "mandate"], default="fixed", help="fixed = --annual-saf-tonnes for every country; mandate = hub-airport e-SAF demand (fuel uplift x ReFuelEU/UK/CH synthetic share)")
    parser.add_argument("--scenario-year", type=int, choices=[2030, 2035, 2040, 2045, 2050], default=2030, help="Mandate year used when --sizing mandate")
    parser.add_argument("--carbon-sourcing", choices=["auto", "dac", "market"], default="auto", help="auto = cheaper of on-site DAC vs purchased biogenic CO2 (+DAC top-up); dac = DAC only; market = force biogenic where available")
    parser.add_argument("--wind-method", choices=["weibull", "legacy"], default="weibull", help="Wind-speed-to-CF conversion for NASA data")
    parser.add_argument("--wacc-mode", choices=["country", "uniform"], default="country", help="country = per-country real pre-tax WACC from country_finance.json (v7 default); uniform = single wacc from the inputs file")
    parser.add_argument("--inputs", choices=["v8", "2050", "v6"], default="v8", help="Input set: v8 = 2030-EU values in USD-2024 (literature_inputs.json); 2050 = 2050 technology-cost overlay; v6 = original values")
    args = parser.parse_args()

    case_set = args.case_set
    if args.include_renewable_rich_cases and args.case_set == "core":
        case_set = "all"

    run_batch(
        ROOT,
        countries=args.countries,
        step_deg=args.cell_step_deg,
        output_root=args.results_dir,
        scenario=args.scenario,
        cell_size_km=args.cell_size_km,
        max_candidates=args.max_candidates_per_country,
        resource_source=args.resource_source,
        resource_timeout_s=args.resource_timeout_s,
        max_delivery_distance_km=args.max_delivery_distance_km,
        uncertainty_samples=args.uncertainty_samples,
        seed=args.seed,
        case_set=case_set,
        annual_saf_tonnes=args.annual_saf_tonnes,
        plant_availability=args.plant_availability,
        report_currency=args.currency,
        usd_to_eur=args.usd_to_eur,
        model_version=args.model_version,
        wind_method=args.wind_method,
        wacc_mode=args.wacc_mode,
        inputs_filename={"v8": "literature_inputs.json", "2050": "literature_inputs_2050.json", "v6": "literature_inputs_v6.json"}[args.inputs],
        sizing=args.sizing,
        scenario_year=args.scenario_year,
        carbon_sourcing=args.carbon_sourcing,
    )


if __name__ == "__main__":
    # BUG FIX (B1): the previous version overwrote sys.argv with a hard-coded
    # list here, so every command-line option a user passed was silently
    # ignored (e.g. `--countries CHE --resource-source heuristic` still ran
    # the full default batch with auto resources). The CLI now works as
    # documented in the README.
    main()
