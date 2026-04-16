"""Load profile queries for trend visualization.

Fetches per-period average load power (W) for the past N days.
Prefers sensor.total_home_load_power; falls back to summing
kwh_meter_production_power + p1_meter_power when primary has no data.
"""

import logging
from datetime import date, timedelta

from core.bess.influxdb_helper import get_power_sensor_data_batch

_LOGGER = logging.getLogger(__name__)

_PRIMARY = "total_home_load_power"
_FALLBACK = ["kwh_meter_production_power", "p1_meter_power"]
_MIN_PRIMARY_PERIODS = 10  # minimum periods to consider primary sensor usable


def get_load_profile(days: int = 7) -> list[dict]:
    """Return load profile for the past N days.

    Args:
        days: Number of days to return (today counts as day 1).

    Returns:
        List of dicts ordered oldest-first:
        [{"date": "2026-04-11", "periods": [400.0, None, 380.0, ...]}, ...]
        Each "periods" list has 96 entries (15-min slots), values in W or None.
    """
    today = date.today()
    result = []
    for offset in range(days - 1, -1, -1):
        target = today - timedelta(days=offset)
        result.append({"date": target.isoformat(), "periods": _day_load_w(target)})
    return result


def _day_load_w(target: date) -> list[float | None]:
    """Return 96-slot average W values for one day."""
    primary = get_power_sensor_data_batch([_PRIMARY], target, mode="power")
    if primary["status"] == "success" and len(primary["data"]) >= _MIN_PRIMARY_PERIODS:
        return _to_watts(primary["data"], [_PRIMARY])

    fallback = get_power_sensor_data_batch(_FALLBACK, target, mode="power")
    if fallback["status"] != "success":
        return [None] * 96
    return _to_watts(fallback["data"], _FALLBACK)


def _to_watts(data: dict, sensors: list[str]) -> list[float | None]:
    """Convert per-period kWh sums back to average W.

    get_power_sensor_data_batch returns mean_W * 0.25 / 1000 (kWh).
    Reverse: kWh * 4000 = mean_W.
    Sums multiple sensors per period.
    Keys in data are prefixed with "sensor." by influxdb_helper.
    """
    keys = [f"sensor.{s}" for s in sensors]
    result: list[float | None] = []
    for period in range(96):
        period_data = data.get(period, {})
        values = [period_data[k] for k in keys if k in period_data]
        if values:
            result.append(round(sum(values) * 4000))
        else:
            result.append(None)
    return result
