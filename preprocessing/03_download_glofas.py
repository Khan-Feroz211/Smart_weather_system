# Task 1.6 - Download GloFAS-ERA5 Reanalysis Discharge
# Downloads one year at a time to stay within CDS size limits

import cdsapi
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import RAW, ensure_dirs

SAVE_DIR = os.path.join(RAW, "glofas")
os.makedirs(SAVE_DIR, exist_ok=True)
ensure_dirs()

c = cdsapi.Client(url="https://ewds.climate.copernicus.eu/api", quiet=False)

for year in range(1979, 2024):
    outfile = os.path.join(SAVE_DIR, f"glofas_era5_{year}.grib2")
    if os.path.exists(outfile) and os.path.getsize(outfile) > 1000:
        print(f"  {year}: already exists, skipping")
        continue
    print(f"  Downloading GloFAS-ERA5 {year}...")
    try:
        c.retrieve(
            "cems-glofas-historical",
            {
                "system_version":     ["version_4_0"],
                "hydrological_model": ["lisflood"],
                "product_type":       ["consolidated"],
                "variable":           ["river_discharge_in_the_last_24_hours"],
                "hyear":              [str(year)],
                "hmonth":             [f"{m:02d}" for m in range(1, 13)],
                "hday":               [f"{d:02d}" for d in range(1, 32)],
                "data_format":        "grib2",
                "download_format":    "unarchived",
                "area":               [11.0, -2.0, 9.0, 0.0],
            },
            outfile,
        )
        print(f"  {year}: saved")
    except Exception as e:
        print(f"  {year}: FAILED -- {e}")

print("GloFAS-ERA5 download complete.")
