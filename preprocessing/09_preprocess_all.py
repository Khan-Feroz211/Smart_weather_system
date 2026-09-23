# Phase 2 - Complete Data Preprocessing Pipeline
# Updated: reads ERA5-Land runoff CSV for 2007-2023 discharge extension
# pip install pandas numpy xarray netCDF4 scipy scikit-learn

import os, sys, zipfile, glob
import numpy as np
import pandas as pd
import xarray as xr
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import PROJECT, RAW, PROC, TMPDIR, START_YEAR, END_YEAR, ensure_dirs

ensure_dirs()

print("=" * 60)
print("PHASE 2 - DATA PREPROCESSING")
print("=" * 60)

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

# -- Helper: read ERA5 NetCDF (unzipping if needed) --
def read_era5_file(fpath, var_options):
    with open(fpath, 'rb') as f:
        magic = f.read(4)
    if magic == b'PK\x03\x04':
        fpath = unzip_nc(fpath, TMPDIR)
        if fpath is None:
            return None, None
    try:
        ds  = xr.open_dataset(fpath, engine='netcdf4')
        var = next((v for v in var_options if v in ds.data_vars), None)
        if var is None:
            ds.close()
            return None, None
        lat_dim = next((d for d in ds.dims if 'lat' in d.lower()), None)
        lon_dim = next((d for d in ds.dims if 'lon' in d.lower()), None)
        if lat_dim and lon_dim:
            s = ds[var].mean(dim=[lat_dim, lon_dim]).to_series()
        else:
            s = ds[var].mean().to_series()
        s.index = pd.to_datetime(s.index).normalize()
        ds.close()
        return s, var
    except Exception as e:
        print(f"    Read error {os.path.basename(fpath)}: {e}")
        return None, None

# ==================================================================
# TASK 2.1 - Load all datasets onto common daily index
# ==================================================================
print("\n[2.1] Loading and aligning all datasets...")

idx = pd.date_range("1987-01-01", "2023-12-31", freq="D")
df  = pd.DataFrame(index=idx)
df.index.name = "date"

# GRDC discharge
grdc = pd.read_csv(
    os.path.join(RAW, "discharge", "nawuni_discharge_grdc.csv"),
    index_col=0, parse_dates=True)
df["discharge_grdc"] = grdc["discharge_m3s"].reindex(idx)
print(f"  GRDC:  {df['discharge_grdc'].notna().sum():,} days")

# CHIRPS
chirps = pd.read_csv(
    os.path.join(RAW, "rainfall", "chirps_combined_1985_2023.csv"),
    index_col=0, parse_dates=True)
df["rainfall_mm"] = chirps["rainfall_mm"].reindex(idx)
print(f"  CHIRPS: {df['rainfall_mm'].notna().sum():,} days")

# ERA5 temperature and ET
print("  Loading ERA5...")
temp_s, et_s = [], []
for year in range(1987, 2024):
    fpath = os.path.join(RAW, "era5", f"era5_{year}.nc")
    if not os.path.exists(fpath): continue
    t, _ = read_era5_file(fpath, ['t2m','2m_temperature','VAR_2T'])
    e, _ = read_era5_file(fpath, ['pev','potential_evaporation','VAR_228'])
    if t is not None: temp_s.append(t)
    if e is not None: et_s.append(e)
    if year % 5 == 0: print(f"    ERA5 {year} done")

if temp_s:
    ts = pd.concat(temp_s).sort_index()
    ts = ts[~ts.index.duplicated()]
    if ts.mean() > 200: ts = ts - 273.15
    df["temperature_c"] = ts.reindex(idx)
    print(f"  ERA5 temp: {df['temperature_c'].notna().sum():,} days | "
          f"mean={df['temperature_c'].mean():.1f}C")

if et_s:
    es = pd.concat(et_s).sort_index()
    es = es[~es.index.duplicated()].abs() * 1000
    df["et_mm"] = es.reindex(idx)
    print(f"  ERA5 ET:   {df['et_mm'].notna().sum():,} days | "
          f"mean={df['et_mm'].mean():.2f}mm")

# Soil moisture
print("  Loading soil moisture...")
sm_s = []
for year in range(1987, 2024):
    fpath = os.path.join(RAW, "soil_moisture", f"soil_moisture_{year}.nc")
    if not os.path.exists(fpath): continue
    s, _ = read_era5_file(fpath,
        ['swvl1','volumetric_soil_water_layer_1','VAR_39'])
    if s is not None: sm_s.append(s)
    if year % 5 == 0: print(f"    SM {year} done")

if sm_s:
    ss = pd.concat(sm_s).sort_index()
    ss = ss[~ss.index.duplicated()]
    df["soil_moisture"] = ss.reindex(idx)
    print(f"  Soil moisture: {df['soil_moisture'].notna().sum():,} days | "
          f"mean={df['soil_moisture'].mean():.3f}")

print(f"\n  After 2.1: {df.shape[0]:,} rows x {df.shape[1]} cols")

# ==================================================================
# TASK 2.2 - Load ERA5 runoff proxy and fill discharge gaps
# ==================================================================
print("\n[2.2] Loading ERA5-Land runoff proxy (2007-2023)...")

runoff_path = os.path.join(RAW, "glofas", "era5_runoff_nawuni_2007_2023.csv")
runoff      = pd.read_csv(runoff_path, parse_dates=['date'])
runoff      = runoff.set_index('date').sort_index()

df["discharge_proxy"] = runoff["discharge_m3s"].reindex(idx)
print(f"  ERA5 runoff proxy: {df['discharge_proxy'].notna().sum():,} days")
print(f"  Proxy range: {df['discharge_proxy'].min():.1f} to "
      f"{df['discharge_proxy'].max():.1f} m3/s")

# Bias-correct proxy against GRDC in overlap period (1987-2006)
overlap = df["1987":"2006"][["discharge_grdc","discharge_proxy"]].dropna()
print(f"  Overlap period for bias correction: {len(overlap):,} days")

if len(overlap) > 100:
    slope, intercept, r, p, _ = stats.linregress(
        overlap["discharge_proxy"], overlap["discharge_grdc"])
    df["discharge_proxy_bc"] = (
        df["discharge_proxy"] * slope + intercept).clip(lower=0)
    print(f"  Bias correction: slope={slope:.3f} | "
          f"intercept={intercept:.1f} | R={r:.3f} | p={p:.4f}")
    print(f"  Quality: {'Good' if r > 0.6 else 'Moderate' if r > 0.4 else 'Weak'}")
else:
    df["discharge_proxy_bc"] = df["discharge_proxy"]
    print("  Skipping bias correction - insufficient overlap")

df["discharge"] = df["discharge_grdc"].fillna(df["discharge_proxy_bc"])
grdc_n  = df["discharge_grdc"].notna().sum()
proxy_n = df["discharge"].notna().sum() - grdc_n
missing = df["discharge"].isna().sum()
print(f"\n  Combined discharge summary:")
print(f"    GRDC source:  {grdc_n:,} days (1987-2006)")
print(f"    Proxy source: {proxy_n:,} days (2007-2023)")
print(f"    Missing:      {missing:,} days")
print(f"    Total:        {df['discharge'].notna().sum():,} days")

# ==================================================================
# TASK 2.3 - CHIRPS confirmed
# ==================================================================
print(f"\n[2.3] CHIRPS catchment-averaged: confirmed")
print(f"  rainfall_mm: mean={df['rainfall_mm'].mean():.2f} mm/day | "
      f"max={df['rainfall_mm'].max():.1f} mm/day")

# ==================================================================
# TASK 2.4 - Bagre Dam storage proxy
# ==================================================================
print("\n[2.4] Building Bagre Dam storage proxy...")
jrc_files = glob.glob(os.path.join(RAW, "jrc_surface_water", "*.csv"))
bagre = pd.read_csv(jrc_files[0])
bagre["date"] = pd.to_datetime(bagre["date"], format="%Y-%m")
bagre = bagre.set_index("date").sort_index()
bagre_daily = bagre["area_km2"].resample("D").interpolate("linear")
b_min, b_max = bagre_daily.min(), bagre_daily.max()
bagre_norm = (bagre_daily - b_min) / (b_max - b_min)
df["bagre_storage"]     = bagre_norm.reindex(idx)
df["bagre_storage_30d"] = df["bagre_storage"].rolling(30, min_periods=1).mean()
print(f"  Bagre proxy: {df['bagre_storage'].notna().sum():,} days | "
      f"max area={b_max:.0f} km2")

# ==================================================================
# TASK 2.5 - Lag features
# ==================================================================
print("\n[2.5] Engineering lag features...")
for lag in range(1, 11):
    df[f"discharge_lag{lag}"] = df["discharge"].shift(lag)
for lag in range(1, 6):
    df[f"rainfall_lag{lag}"]  = df["rainfall_mm"].shift(lag)
df["discharge_3d"] = df["discharge"].rolling(3,  min_periods=1).mean()
df["discharge_7d"] = df["discharge"].rolling(7,  min_periods=1).mean()
df["rainfall_3d"]  = df["rainfall_mm"].rolling(3, min_periods=1).sum()
df["rainfall_7d"]  = df["rainfall_mm"].rolling(7, min_periods=1).sum()
lag_cols = [c for c in df.columns if 'lag' in c]
print(f"  {len(lag_cols)} lag features | {df.shape[1]} total columns")

# ==================================================================
# TASK 2.6 - Train / Val / Test splits
# ==================================================================
print("\n[2.6] Defining train/val/test splits...")
train = df["1987-01-01":"2000-12-31"]
val   = df["2001-01-01":"2006-12-31"]
test  = df["2007-01-01":"2023-12-31"]
print(f"  Train: 1987-2000 | {len(train):,} days | "
      f"GRDC discharge: {train['discharge'].notna().sum():,} days")
print(f"  Val:   2001-2006 | {len(val):,} days | "
      f"GRDC discharge: {val['discharge'].notna().sum():,} days")
print(f"  Test:  2007-2023 | {len(test):,} days | "
      f"Proxy discharge: {test['discharge'].notna().sum():,} days")

# ==================================================================
# TASK 2.7 - Normalise using train-set statistics only
# ==================================================================
print("\n[2.7] Normalising features...")
skip_cols = ["discharge","discharge_grdc","discharge_proxy",
             "discharge_proxy_bc","discharge_log","discharge_norm"]
feature_cols = [c for c in df.columns if c not in skip_cols]
df_norm = df.copy()
scaler_params = {}
for col in feature_cols:
    mu  = train[col].mean()
    std = train[col].std()
    std = std if std > 0 else 1.0
    df_norm[col] = (df[col] - mu) / std
    scaler_params[col] = {"mean": float(mu), "std": float(std)}
df["discharge_log"]      = np.log1p(df["discharge"])
log_mu  = np.log1p(train["discharge"]).mean()
log_std = np.log1p(train["discharge"]).std()
df_norm["discharge_norm"] = (df["discharge_log"] - log_mu) / log_std
scaler_params["discharge"] = {"log_mean": float(log_mu),
                               "log_std":  float(log_std)}
pd.DataFrame(scaler_params).T.to_csv(
    os.path.join(PROC, "features", "scaler_params.csv"))
print(f"  Scaler saved for {len(scaler_params)} features")

# ==================================================================
# TASK 2.8 - Exploratory data analysis
# ==================================================================
print("\n[2.8] Exploratory statistics...")
q_grdc = df["1987":"2006"]["discharge"].dropna()
print(f"  Discharge 1987-2006 (GRDC):")
print(f"    Mean={q_grdc.mean():.0f} | Median={q_grdc.median():.0f} | "
      f"P90={q_grdc.quantile(.9):.0f} | P99={q_grdc.quantile(.99):.0f} | "
      f"Max={q_grdc.max():.0f} m3/s")
print(f"\n  Monthly mean discharge (m3/s):")
months = ['Jan','Feb','Mar','Apr','May','Jun',
          'Jul','Aug','Sep','Oct','Nov','Dec']
for m, v in zip(months, q_grdc.groupby(q_grdc.index.month).mean()):
    print(f"    {m}: {v:6.0f}  {'|' * int(v/80)}")

corr_cols = ["rainfall_mm","rainfall_7d","temperature_c",
             "soil_moisture","bagre_storage","discharge_lag1","discharge_lag3"]
print(f"\n  Feature correlations with discharge (train set):")
for c in corr_cols:
    if c in train.columns:
        r = train[["discharge", c]].dropna().corr().iloc[0,1]
        print(f"    {c:25s}: r={r:+.3f}")

# ==================================================================
# TASK 2.9 - Bagre proxy vs flood events
# ==================================================================
print("\n[2.9] Bagre proxy vs known flood years:")
for yr in [2007, 2010, 2016, 2019, 2020]:
    s = f"{yr}-07-01"
    e = f"{yr}-10-31"
    peak_b = df.loc[s:e, "bagre_storage"].max()
    peak_q = df.loc[s:e, "discharge"].max()
    peak_r = df.loc[s:e, "rainfall_mm"].max()
    print(f"  {yr}: Bagre={peak_b:.2f} | discharge={peak_q:.0f} m3/s | "
          f"max daily rain={peak_r:.1f} mm")

# ==================================================================
# SAVE ALL OUTPUTS
# ==================================================================
print("\n[SAVING]...")
df.to_csv(     os.path.join(PROC, "features", "feature_matrix_full.csv"))
df_norm.to_csv(os.path.join(PROC, "features", "feature_matrix_norm.csv"))
train.to_csv(  os.path.join(PROC, "splits", "train_1987_2000.csv"))
val.to_csv(    os.path.join(PROC, "splits", "val_2001_2006.csv"))
test.to_csv(   os.path.join(PROC, "splits", "test_2007_2023.csv"))
print(f"  feature_matrix_full.csv saved: {df.shape}")
print(f"  feature_matrix_norm.csv saved: {df_norm.shape}")
print(f"  train/val/test split files saved")

print("\n" + "=" * 60)
print("PHASE 2 COMPLETE")
print(f"Feature matrix: {df.shape[0]:,} rows x {df.shape[1]} columns")
print(f"Discharge coverage: {df['discharge'].notna().sum():,} / "
      f"{len(df):,} days ({100*df['discharge'].notna().mean():.1f}%)")
print(f"Saved to: {PROC}")
print("=" * 60)
print("\nNext: Phase 3 - AI Model Development")
