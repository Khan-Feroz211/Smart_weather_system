"""
Task 1.3 -- Download CHIRPS daily rainfall for White Volta Basin
Saves to PIPELINE_DATA_DIR (set in .env)
"""

import os
import sys
import time
import gzip
import shutil
import requests
from tqdm import tqdm
from datetime import date, timedelta

# Project root must be on the path so pipeline_config is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_config import (
    PROJECT, RAW, START_YEAR, END_YEAR,
    CHIRPS_BASE, MAX_RETRIES, RETRY_WAIT, ensure_dirs,
)

# -- Save location
PROJECT_DIR = PROJECT
SAVE_DIR    = os.path.join(RAW, "rainfall")
ensure_dirs()

os.makedirs(SAVE_DIR, exist_ok=True)
log_file = os.path.join(SAVE_DIR, "download_log.txt")

def log(msg):
    print(msg)
    try:
        with open(log_file, "a") as f:
            f.write(msg + "\n")
    except:
        pass

def download_file(url, gz_path, tif_path):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=60, stream=True)
            if r.status_code == 200:
                with open(gz_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                with gzip.open(gz_path, "rb") as f_in:
                    with open(tif_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(gz_path)
                return True
            elif r.status_code == 404:
                return False
            else:
                log(f"    HTTP {r.status_code} on attempt {attempt}")
        except OSError as e:
            log(f"    DISK ERROR: {e}")
            raise  # Stop immediately if disk is full
        except Exception as e:
            if attempt < MAX_RETRIES:
                log(f"    Attempt {attempt} failed: {e} -- retrying in {RETRY_WAIT}s")
                time.sleep(RETRY_WAIT)
            else:
                log(f"    FAILED after {MAX_RETRIES} attempts: {e}")
    return False

def download_year(year):
    year_dir = os.path.join(SAVE_DIR, str(year))
    os.makedirs(year_dir, exist_ok=True)
    current    = date(year, 1, 1)
    end        = date(year, 12, 31)
    downloaded = skipped = failed = 0
    while current <= end:
        filename = f"chirps-v2.0.{current.strftime('%Y.%m.%d')}.tif.gz"
        gz_path  = os.path.join(year_dir, filename)
        tif_path = os.path.join(year_dir, filename.replace(".gz", ""))
        url      = f"{CHIRPS_BASE}/{year}/{filename}"
        if os.path.exists(tif_path) and os.path.getsize(tif_path) > 1000:
            skipped += 1
        else:
            success = download_file(url, gz_path, tif_path)
            if success:
                downloaded += 1
            else:
                failed += 1
        current += timedelta(days=1)
    return downloaded, skipped, failed

# -- Main
log(f"CHIRPS Download -- saving to {SAVE_DIR}")
log(f"Period: {START_YEAR}-{END_YEAR}\n")

total_dl = total_sk = total_fail = 0
for year in tqdm(range(START_YEAR, END_YEAR + 1), desc="Downloading years"):
    dl, sk, fail = download_year(year)
    total_dl   += dl
    total_sk   += sk
    total_fail += fail
    log(f"  {year}: {dl} downloaded, {sk} existed, {fail} failed")

log(f"\nDONE: {total_dl} downloaded, {total_sk} skipped, {total_fail} failed")
