import os
import sys

# Make repo root importable (existing tests rely on cwd being the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    config.addinivalue_line("markers", "gee: test requires a live Google Earth Engine session")
