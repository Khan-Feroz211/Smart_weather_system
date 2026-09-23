"""
White Volta Basin FEWS -- Data Preprocessing Pipeline.
"""

__all__ = ["phase1_downloads", "phase2_processing"]

phase1_downloads = [
    "01_download_chirps.py",
    "02_download_era5.py",
    "03_download_glofas.py",
    "04_download_soil_moisture.py",
    "06_process_grdc.py",
    "07_test_cds_connection.py",
]

phase2_processing = [
    "05_verify_downloads.py",
    "09_preprocess_all.py",
    "10_check_file_formats.py",
    "13_fix_discharge_proxy.py",
]
