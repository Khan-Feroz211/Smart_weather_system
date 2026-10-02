# Task 1.4 - Download ERA5 Temperature and Evapotranspiration
# Saves to PIPELINE_DATA_DIR (set in .env)
# Setup: create ~/.cdsapirc with your CDS API key
# pip install cdsapi

import cdsapi
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import RAW, ensure_dirs

SAVE_DIR = os.path.join(RAW, "era5")
os.makedirs(SAVE_DIR, exist_ok=True)
ensure_dirs()

c = cdsapi.Client()
months = [f"{m:02d}" for m in range(1, 13)]

for year in range(1985, 2024):
    outfile = os.path.join(SAVE_DIR, f"era5_{year}.nc")
    if os.path.exists(outfile) and os.path.getsize(outfile) > 10000:
        print(f"  {year}: already exists, skipping")
        continue
    print(f"  Downloading ERA5 {year}...")
    c.retrieve(
        "reanalysis-era5-land",
        {
            "variable": ["2m_temperature", "potential_evaporation"],
            "year":   str(year),
            "month":  months,
            "day":    [f"{d:02d}" for d in range(1, 32)],
            "time":   "12:00",
            "area":   [14.5, -4.0, 8.0, 1.0],
            "format": "netcdf",
        },
        outfile,
    )
    print(f"  {year}: saved")

print("ERA5 download complete.")
