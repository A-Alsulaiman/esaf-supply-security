
from __future__ import annotations
from pathlib import Path
import pandas as pd

def load_airports(base_dir: Path) -> pd.DataFrame:
    return pd.read_csv(base_dir / "src" / "saf_eu_model" / "data" / "major_airports.csv")
