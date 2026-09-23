"""
Process GRDC raw files into clean CSV
"""
import pandas as pd
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import RAW, ensure_dirs

RAW_DIR  = os.path.join(RAW, "discharge")
RAW_FILE = os.path.join(RAW_DIR, "1531450_Q_Day.Cmd.txt.txt")
OUT_FILE = os.path.join(RAW_DIR, "nawuni_discharge_grdc.csv")
ensure_dirs()

with open(RAW_FILE, encoding='latin-1') as f:
    lines = f.readlines()

records = []
for l in lines:
    l = l.strip()
    if not l or l.startswith('#') or l.startswith('YYYY'):
        continue
    parts = l.split(';')
    if len(parts) >= 3:
        try:
            date  = pd.to_datetime(parts[0].strip())
            value = float(parts[2].strip())
            if value == -999.0:
                value = float('nan')
            records.append({'date': date, 'discharge_m3s': value})
        except:
            pass

df = pd.DataFrame(records).set_index('date').sort_index()
df.to_csv(OUT_FILE)

print(f"Saved: {OUT_FILE}")
print(f"Records: {len(df):,} days")
print(f"Period:  {df.index.min().date()} to {df.index.max().date()}")
print(f"Missing: {df['discharge_m3s'].isna().sum()} days")
print(f"Max:     {df['discharge_m3s'].max():.1f} m3/s")
print("DONE")
