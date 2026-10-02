"""
Verify all downloaded data files are complete and correctly formatted.
Set PIPELINE_DATA_DIR env var before running.
    python preprocessing/05_verify_downloads.py
"""

import os
import sys
import glob
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import PROJECT, ensure_dirs

PROJECT_DIR = PROJECT
ensure_dirs()

passed = []
failed = []

def check(name, condition, detail=""):
    if condition:
        print(f"  OK  {name}")
        passed.append(name)
    else:
        print(f"  XX  {name}  {detail}")
        failed.append(name)

print("=" * 60)
print("DATA VERIFICATION REPORT")
print("=" * 60)

# -- CHIRPS Rainfall --
print("\n1. CHIRPS RAINFALL")
rainfall_dir  = os.path.join(PROJECT_DIR, "data", "raw", "rainfall")
chirps_files  = sorted(glob.glob(os.path.join(rainfall_dir, "chirps_white_volta_*.csv")))

check("CHIRPS folder exists",        os.path.isdir(rainfall_dir))
check("8 CHIRPS batch files present", len(chirps_files) == 8,
      f"found {len(chirps_files)}")

if chirps_files:
    dfs = []
    for f in chirps_files:
        try:
            dfs.append(pd.read_csv(f, parse_dates=['date']))
        except Exception as e:
            print(f"    ERROR reading {os.path.basename(f)}: {e}")

    if dfs:
        chirps = pd.concat(dfs).drop_duplicates('date').sort_values('date')
        chirps = chirps.set_index('date')

        check("CHIRPS starts by 1985",   chirps.index.min().year <= 1985)
        check("CHIRPS ends at 2023+",    chirps.index.max().year >= 2023)
        check("Total days >= 13,000",    len(chirps) >= 13000,
              f"got {len(chirps):,}")
        check("Missing values < 5%",
              chirps['rainfall_mm'].isna().sum() / len(chirps) < 0.05)

        print(f"\n     {len(chirps):,} days | "
              f"{chirps.index.min().date()} to {chirps.index.max().date()} | "
              f"{chirps['rainfall_mm'].isna().sum()} missing")

        out = os.path.join(rainfall_dir, "chirps_combined_1985_2023.csv")
        chirps.to_csv(out)
        print(f"     Combined file saved: chirps_combined_1985_2023.csv")

# -- Bagre Reservoir --
print("\n2. BAGRE RESERVOIR (JRC Surface Water)")
jrc_dir   = os.path.join(PROJECT_DIR, "data", "raw", "jrc_surface_water")
jrc_files = glob.glob(os.path.join(jrc_dir, "*.csv"))

check("JRC folder exists",       os.path.isdir(jrc_dir))
check("Bagre CSV file present",  len(jrc_files) >= 1)

if jrc_files:
    bagre = pd.read_csv(jrc_files[0])
    check("Has date/year column",
          any(c in bagre.columns for c in ['date','year','system:time_start']))
    check("Has area column",
          any('area' in c.lower() or 'km' in c.lower() for c in bagre.columns))
    check("Has 100+ records", len(bagre) >= 100, f"got {len(bagre)}")
    print(f"\n     {len(bagre)} monthly records | columns: {list(bagre.columns)}")

# -- GRDC Discharge --
print("\n3. GRDC DISCHARGE")
discharge_dir = os.path.join(PROJECT_DIR, "data", "raw", "discharge")
grdc_csv      = os.path.join(discharge_dir, "nawuni_discharge_grdc.csv")
grdc_raw      = os.path.join(discharge_dir, "1531450_Q_Day.Cmd.txt.txt")

check("Discharge folder exists", os.path.isdir(discharge_dir))
check("Raw GRDC file present",   os.path.exists(grdc_raw),
      f"looking for: {grdc_raw}")

if not os.path.exists(grdc_csv) and os.path.exists(grdc_raw):
    print("     Processing raw GRDC file...")
    with open(grdc_raw, encoding='latin-1') as f:
        lines = f.readlines()
    records = []
    for l in lines:
        l = l.strip()
        if not l or l.startswith('#') or l.startswith('YYYY'):
            continue
        parts = l.split(';')
        if len(parts) >= 3:
            try:
                dt  = pd.to_datetime(parts[0].strip())
                val = float(parts[2].strip())
                if val == -999.0:
                    val = float('nan')
                records.append({'date': dt, 'discharge_m3s': val})
            except:
                pass
    df = pd.DataFrame(records).set_index('date').sort_index()
    df.to_csv(grdc_csv)
    print(f"     Processed and saved: nawuni_discharge_grdc.csv")

check("Processed GRDC CSV present", os.path.exists(grdc_csv))

if os.path.exists(grdc_csv):
    grdc = pd.read_csv(grdc_csv, index_col=0, parse_dates=True)
    check("GRDC has discharge column",  'discharge_m3s' in grdc.columns)
    check("GRDC covers 1975-2007",
          grdc.index.min().year <= 1975 and grdc.index.max().year >= 2006)
    print(f"\n     {len(grdc):,} days | "
          f"{grdc.index.min().date()} to {grdc.index.max().date()} | "
          f"{grdc['discharge_m3s'].isna().sum()} missing")

# -- Summary --
print("\n" + "=" * 60)
print(f"RESULT: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:")
    for f in failed:
        print(f"  - {f}")
else:
    print("ALL CHECKS PASSED -- ready for preprocessing")
print("=" * 60)
