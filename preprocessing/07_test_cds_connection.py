"""
Quick test to confirm CDS API is working correctly.
Set PIPELINE_DATA_DIR env var before running.
"""
import cdsapi
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import RAW, ensure_dirs

ensure_dirs()
print("Testing CDS API connection...")

try:
    c = cdsapi.Client()
    print("Connected successfully.")

    # Try a tiny ERA5 request -- just 1 day, 1 variable, small area
    print("Sending test request (small ERA5 download)...")
    c.retrieve(
        "reanalysis-era5-land",
        {
            "variable": ["2m_temperature"],
            "year":   "2020",
            "month":  "01",
            "day":    "01",
            "time":   "12:00",
            "area":   [11.0, -1.5, 10.0, -0.5],
            "format": "netcdf",
        },
        os.path.join(RAW, "era5", "test_connection.nc")
    )
    print("")
    print("SUCCESS -- CDS API is working correctly.")
    print(f"Test file saved to: {os.path.join(RAW, 'era5', 'test_connection.nc')}")
    print("You can delete this file -- it was just a connection test.")
    print("")
    print("Ready to run full ERA5, soil moisture and GloFAS downloads.")

except Exception as e:
    print(f"")
    print(f"ERROR: {e}")
    print("")
    print("Common fixes:")
    print("  1. Check your .cdsapirc file exists and has correct format")
    print("  2. Format must be exactly:")
    print("       url: https://cds.climate.copernicus.eu/api/v2")
    print("       key: YOUR_UID:YOUR_API_KEY")
    print("  3. Make sure you confirmed your email after registering")
    print("  4. Try logging into https://cds.climate.copernicus.eu to confirm account is active")
