"""Live monitoring: periodic refresh of the last 30 days of multi-source data.

Scheduler decision: APScheduler's BackgroundScheduler is used when installed (already in
requirements.txt). If it is missing, a simple threading.Timer loop is used instead, so live
mode still works without extra dependencies. Nothing starts on import and nothing is wired
into the Flask app.

CLI: ``python -m data_loaders.live --lat 31.5 --lon 74.3 --once``  (never prints secrets).
"""
from __future__ import annotations

import argparse
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from . import himawari, openmeteo_radiation
from .openmeteo_radiation import radiation_sanity_check
from .utils import get_logger

log = get_logger("data_loaders.live")
WINDOW_DAYS = 30


class LiveMonitor:
    def __init__(self, lat: float, lon: float, interval_hours: float = 6.0,
                 callback: Optional[Callable[[Dict[str, Any]], None]] = None,
                 window_days: int = WINDOW_DAYS):
        if interval_hours <= 0:
            raise ValueError("interval_hours must be > 0")
        self.lat, self.lon = lat, lon
        self.interval_hours = interval_hours
        self.callback = callback
        self.window_days = window_days
        self._scheduler = None
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._lock = threading.Lock()

    # ---- one refresh -------------------------------------------------------------
    def window(self, now: Optional[datetime] = None):
        now = now or datetime.now(timezone.utc)
        return (now - timedelta(days=self.window_days)).date().isoformat(), now.date().isoformat()

    def run_once(self) -> Dict[str, Any]:
        start, end = self.window()
        result: Dict[str, Any] = {"start": start, "end": end, "feature_stack": None,
                                  "himawari": None, "openmeteo": None, "sanity": None, "errors": []}
        log.info(f"live run_once lat={self.lat} lon={self.lon} range={start}..{end}")

        try:
            from . import feature_stack  # Track A module; may be absent
            import ee
            aoi = ee.Geometry.Point([self.lon, self.lat])
            builder = getattr(feature_stack, "build_feature_frame", None) or feature_stack.build_feature_stack
            result["feature_stack"] = builder(aoi, start, end, mode="live")
        except ImportError as exc:
            result["errors"].append(f"feature_stack unavailable: {exc}")
        except Exception as exc:
            result["errors"].append(f"feature_stack failed: {exc}")

        try:
            result["himawari"] = himawari.fetch(self.lat, self.lon, start, end, "PAR")
        except Exception as exc:
            result["errors"].append(f"himawari failed: {exc}")
        try:
            result["openmeteo"] = openmeteo_radiation.fetch(self.lat, self.lon, start, end)
        except Exception as exc:
            result["errors"].append(f"openmeteo failed: {exc}")

        h, o = result["himawari"], result["openmeteo"]
        if h is not None and o is not None and len(h) and len(o):
            result["sanity"] = radiation_sanity_check(h, o, col_a="PAR", col_b="PAR_proxy")
        for e in result["errors"]:
            log.warning(f"live: {e}")
        return result

    def _tick(self):
        try:
            res = self.run_once()
        except Exception as exc:
            log.error(f"live run failed: {exc}")
            return
        if self.callback:
            try:
                self.callback(res)
            except Exception as exc:
                log.error(f"live callback raised: {exc}")

    # ---- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
                self._scheduler = BackgroundScheduler()
                self._scheduler.add_job(self._tick, "interval", hours=self.interval_hours,
                                        next_run_time=datetime.now(timezone.utc))
                self._scheduler.start()
            except ImportError:
                log.warning("apscheduler not installed; using threading.Timer fallback")
                self._schedule_timer(0.0)

    def _schedule_timer(self, delay: float) -> None:
        self._timer = threading.Timer(delay, self._timer_loop)
        self._timer.daemon = True
        self._timer.start()

    def _timer_loop(self) -> None:
        if not self._running:
            return
        self._tick()
        with self._lock:
            if self._running:
                self._schedule_timer(self.interval_hours * 3600)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._scheduler is not None:
                try:
                    self._scheduler.shutdown(wait=False)
                except Exception as exc:
                    log.warning(f"scheduler shutdown: {exc}")
                self._scheduler = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    @property
    def running(self) -> bool:
        return self._running


def _summary(res: Dict[str, Any]) -> str:
    def n(x):
        return "n/a" if x is None else len(x)
    return (f"window={res['start']}..{res['end']} feature_stack={n(res['feature_stack'])} "
            f"himawari={n(res['himawari'])} openmeteo={n(res['openmeteo'])} "
            f"sanity={res['sanity']} errors={len(res['errors'])}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m data_loaders.live", description=__doc__)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--interval-hours", type=float, default=6.0)
    p.add_argument("--once", action="store_true", help="run a single refresh and exit")
    a = p.parse_args(argv)
    mon = LiveMonitor(a.lat, a.lon, a.interval_hours, callback=lambda r: print(_summary(r)))
    if a.once:
        print(_summary(mon.run_once()))
        return 0
    mon.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        mon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
