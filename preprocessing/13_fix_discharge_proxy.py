"""
Task 2.2 - Fix / extend discharge data using ERA5-Land runoff proxy
for 2007-2023.  Bias-correct the proxy against GRDC overlap (1987-2006).
"""
import os, sys, glob
import numpy as np
import pandas as pd
import xarray as xr
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import PROJECT, RAW, OUTPUTS, TMPDIR, ensure_dirs

# -- Paths --
DISCHARGE_DIR   = os.path.join(RAW, "discharge")
GRDC_PROC       = os.path.join(DISCHARGE_DIR, "nawuni_discharge_grdc.csv")
RUNOFF_DIR      = os.path.join(OUTPUTS, "era5_runoff")
os.makedirs(RUNOFF_DIR, exist_ok=True)
ensure_dirs()

# -- Helper: unzip CDS zip-wrapped NetCDF --
def unzip_nc(src_path, out_dir):
    basename = os.path.splitext(os.path.basename(src_path))[0]
    out_path = os.path.join(out_dir, basename + ".nc")
    if os.path.exists(out_path):
        return out_path
    try:
        with zipfile.ZipFile(src_path, 'r') as z:
            nc_files = [n for n in z.namelist() if n.endswith('.nc')]
            if nc_files:
                z.extract(nc_files[0], out_dir)
                extracted = os.path.join(out_dir, nc_files[0])
                if extracted != out_path:
                    os.rename(extracted, out_path)
                return out_path
    except Exception as e:
        print(f"    Unzip error {os.path.basename(src_path)}: {e}")
    return None

def read_era5_runoff(fpath):
    """Read a daily runoff NetCDF (possibly zip-wrapped) -> daily pandas Series."""
    with open(fpath, 'rb') as f:
        magic = f.read(4)
    if magic == b'PK\x03\x04':
        fpath = unzip_nc(fpath, TMPDIR)
        if fpath is None:
            return None
    try:
        ds = xr.open_dataset(fpath, engine='netcdf4')
        var = next((v for v in ds.data_vars
                     if 'runoff' in v.lower() or v.startswith('ro')), None)
        if var is None:
            ds.close()
            return None
        lat_dim = next((d for d in ds.dims if 'lat' in d.lower()), None)
        lon_dim = next((d for d in ds.dims if 'lon' in d.lower()), None)
        if lat_dim and lon_dim:
            s = ds[var].mean(dim=[lat_dim, lon_dim]).to_series()
        else:
            s = ds[var].mean().to_series()
        s.index = pd.to_datetime(s.index).normalize()
        s.name = 'runoff'
        ds.close()
        return s
    except Exception as e:
        print(f"    Read error {os.path.basename(fpath)}: {e}")
        return None

print("=" * 60)
print("DISCHARGE PROXY FIX - 13_fix_discharge_proxy")
print("=" * 60)

# -- Load GRDC (1987-2006) --
print("\n[1] Loading processed GRDC discharge (1987-2006)...")
grdc = pd.read_csv(GRDC_PROC, index_col=0, parse_dates=True)
grdc = grdc[["discharge_m3s"]].rename(columns={"discharge_m3s": "grdc"})
print(f"    GRDC: {len(grdc):,} days | "
      f"{grdc.index.min().date()} to {grdc.index.max().date()}")
print(f"    Mean={grdc['grdc'].mean():.1f} | "
      f"Median={grdc['grdc'].median():.1f} m3/s")

# -- Find ERA5 runoff files --
print("\n[2] Locating ERA5-Land runoff files...")
era5_dirs = [
    os.path.join(RAW, "era5"),
    os.path.join(RAW, "era5_runoff"),
    RUNOFF_DIR,
]
nc_files = []
for d in era5_dirs:
    nc_files += sorted(glob.glob(os.path.join(d, "*.nc")))
    nc_files += sorted(glob.glob(os.path.join(d, "*.zip")))
print(f"    Found {len(nc_files)} candidate files")

# -- Read and combine runoff series --
print("\n[3] Reading runoff data (2007-2023)...")
runoff_series = []
for i, f in enumerate(nc_files):
    s = read_era5_runoff(f)
    if s is not None:
        runoff_series.append(s)
        if (i + 1) % 5 == 0:
            print(f"      Processed {i+1}/{len(nc_files)} files")

if not runoff_series:
    print("    WARNING: No runoff files could be read.")
    print("    Please download ERA5-Land runoff data or check file paths.")
    sys.exit(1)

runoff = pd.concat(runoff_series).sort_index()
runoff = runoff[~runoff.index.duplicated(keep='last')]
runoff = runoff["2007-01-01":"2023-12-31"]
print(f"    Combined runoff: {len(runoff):,} days | "
      f"{runoff.index.min().date()} to {runoff.index.max().date()}")
print(f"    Mean={runoff['runoff'].mean():.4f} | "
      f"Max={runoff['runoff'].max():.4f}")

# -- Bias correction: align runoff units to GRDC discharge --
print("\n[4] Bias-correcting runoff against GRDC...")
overlap = grdc["1987":"2006"]
overlap_dates = overlap.index.intersection(runoff.index)
if len(overlap_dates) > 30:
    rov = runoff.loc[overlap_dates]
    gov = overlap.loc[overlap_dates]
    from scipy import stats
    slope, intercept, r_val, p_val, _ = stats.linregress(
        rov["runoff"].values, gov["grdc"].values)
    print(f"    Overlap days: {len(overlap_dates)}")
    print(f"    Regression: discharge = {slope:.4f} * runoff + {intercept:.1f}")
    print(f"    R = {r_val:.4f} (p = {p_val:.6f})")
    print(f"    Quality: {'GOOD' if r_val > 0.7 else 'MODERATE' if r_val > 0.5 else 'WEAK'}")
    if r_val > 0.5:
        discharge_proxy = runoff["runoff"] * slope + intercept
    else:
        # Fallback: use ratio of means
        ratio = gov["grdc"].mean() / rov["runoff"].mean()
        discharge_proxy = runoff["runoff"] * ratio
        print(f"    Using ratio-of-means fallback: {ratio:.4f}")
else:
    # No overlap -- use ratio of means on annual medians
    print(f"    Insufficient overlap ({len(overlap_dates)} days). Using annual medians.")
    grdc_annual = grdc["2004":"2006"]["grdc"].resample("YE").median()
    rov_annual  = runoff["2004":"2006"]["runoff"].resample("YE").median()
    if len(grdc_annual) > 0 and len(rov_annual) > 0:
        ratio = grdc_annual.mean() / rov_annual.mean()
        discharge_proxy = runoff["runoff"] * ratio
        print(f"    Ratio (annual median means): {ratio:.4f}")
    else:
        discharge_proxy = runoff["runoff"] * 1000  # rough fallback
        print("    Fallback: runoff * 1000")

discharge_proxy = discharge_proxy.clip(lower=0)
print(f"\n    Proxy discharge: mean={discharge_proxy.mean():.1f} | "
      f"median={discharge_proxy.median():.1f} | "
      f"max={discharge_proxy.max():.0f} m3/s")

# -- Build combined discharge --
print("\n[5] Building combined discharge (GRDC 1987-2006 + proxy 2007-2023)...")
combined = pd.concat([grdc["grdc"], discharge_proxy.rename("proxy")], axis=1)
combined.columns = ["grdc", "proxy"]
combined = combined.sort_index()
combined.index.name = "date"

# Mark source
combined["source"] = "proxy"
combined.loc[combined.index <= "2006-12-31", "source"] = "grdc"
combined.loc[(combined.index <= "2006-12-31") & (combined["grdc"].isna()), "source"] = "missing"
combined["discharge"] = combined["grdc"].fillna(combined["proxy"])

grdc_n  = combined["source"].eq("grdc").sum()
proxy_n = combined["source"].eq("proxy").sum()
miss_n  = combined["source"].eq("missing").sum()
print(f"    GRDC source:  {grdc_n:,} days (1987-2006)")
print(f"    Proxy source: {proxy_n:,} days (2007-2023)")
print(f"    Missing:      {miss_n:,} days")
print(f"    Combined:     {combined['discharge'].notna().sum():,} days "
      f"({100*combined['discharge'].notna().mean():.1f}%)")

# -- Save outputs --
print("\n[6] Saving processed discharge data...")
OUT_DIR  = os.path.join(RAW, "discharge")
os.makedirs(OUT_DIR, exist_ok=True)
COMB_OUT = os.path.join(OUT_DIR, "nawuni_discharge_1987_2023.csv")
PROXY_OUT = os.path.join(OUT_DIR, "era5_discharge_proxy_2007_2023.csv")
combined.to_csv(COMB_OUT)
discharge_proxy.to_csv(PROXY_OUT, header=["discharge_m3s_proxy"])
print(f"    {COMB_OUT}")
print(f"    {PROXY_OUT}")

# -- Summary statistics by period --
print("\n[7] Summary by period:")
for label, sl, su in [("GRDC (1987-2006)", "1987", "2006"),
                       ("Proxy (2007-2023)", "2007", "2023")]:
    d = combined.loc[sl:su, "discharge"].dropna()
    if len(d) > 0:
        print(f"    {label:25s}: mean={d.mean():7.1f} median={d.median():6.1f} "
              f"max={d.max():8.0f} p90={d.quantile(0.9):7.0f} m3/s")

print("\n" + "=" * 60)
print("DISCHARGE PROXY FIX COMPLETE")
print("=" * 60)
