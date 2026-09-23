"""
Inspect file formats and metadata of downloaded data products.
"""
import os, sys, glob, json
import netCDF4
import xarray as xr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import PROJECT, RAW, ensure_dirs

ensure_dirs()

print("=" * 60)
print("FILE FORMAT INSPECTION")
print("=" * 60)

# -- NetCDF / GRIB2 --
print("\n[NetCDF / GRIB2 Files]")
nc_files = (
    sorted(glob.glob(os.path.join(RAW, "**", "*.nc"), recursive=True)) +
    sorted(glob.glob(os.path.join(RAW, "**", "*.grib2"), recursive=True))
)
for f in nc_files[:10]:
    size_mb = os.path.getsize(f) / 1e6
    try:
        ds = xr.open_dataset(f, engine='netcdf4')
        var_list = list(ds.data_vars)
        time_var = None
        for tv in ['time','t','datetime']:
            if tv in ds.coords or tv in ds.dims:
                time_var = tv; break
        if time_var:
            t = ds[time_var]
            try:
                tvals = t.values
                if hasattr(tvals, '__len__') and len(tvals) > 1:
                    tr = f"{tvals[0]} to {tvals[-1]} ({len(tvals)} steps)"
                else:
                    tr = str(tvals)
            except:
                tr = "unknown"
        else:
            tr = "no time dim"
        dims = dict(ds.dims)
        print(f"  {os.path.basename(f):40s} | {size_mb:7.1f}MB | vars={var_list[:5]} | time={tr} | dims={dims}")
        ds.close()
    except Exception as e:
        print(f"  {os.path.basename(f):40s} | {size_mb:7.1f}MB | ERROR: {e}")
if not nc_files:
    print("  No NetCDF/GRIB2 files found")

# -- CSV files --
print("\n[CSV Files]")
csv_files = sorted(glob.glob(os.path.join(RAW, "**", "*.csv"), recursive=True))
for f in csv_files[:15]:
    size_kb = os.path.getsize(f) / 1e3
    try:
        import pandas as pd
        df = pd.read_csv(f, nrows=2)
        cols = list(df.columns)
        print(f"  {os.path.basename(f):45s} | {size_kb:7.1f}KB | cols={cols[:6]}")
    except Exception as e:
        print(f"  {os.path.basename(f):45s} | {size_kb:7.1f}KB | ERROR: {e}")

# -- TXT files (GRDC) --
print("\n[TXT Files]")
txt_files = sorted(glob.glob(os.path.join(RAW, "**", "*.txt"), recursive=True))
for f in txt_files[:10]:
    size_kb = os.path.getsize(f) / 1e3
    with open(f, encoding='latin-1') as fh:
        head = fh.readline().strip()[:80]
    print(f"  {os.path.basename(f):45s} | {size_kb:7.1f}KB | preview: {head}")

# -- GeoTIFF --
print("\n[GeoTIFF Files]")
tif_files = sorted(glob.glob(os.path.join(RAW, "**", "*.tif"), recursive=True))
for f in tif_files[:10]:
    size_mb = os.path.getsize(f) / 1e6
    try:
        import rasterio
        with rasterio.open(f) as src:
            print(f"  {os.path.basename(f):40s} | {size_mb:7.1f}MB | "
                  f"crs={src.crs} | res={src.res} | bands={src.count}")
    except ImportError:
        print(f"  {os.path.basename(f):40s} | {size_mb:7.1f}MB | rasterio not installed")
    except Exception as e:
        print(f"  {os.path.basename(f):40s} | {size_mb:7.1f}MB | ERROR: {e}")
if not tif_files:
    print("  No GeoTIFF files found")

print("\n" + "=" * 60)
print("FORMAT INSPECTION COMPLETE")
print("=" * 60)
