#!/usr/bin/env python3
"""
BESS DP Analysis Script
Fetches live data from HA/InfluxDB and runs the DP algorithm,
exactly matching the live BESS software behavior.

Usage:
    python scripts/dp_analysis.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import requests
import logging

logging.disable(logging.CRITICAL)  # Suppress all BESS debug/info logging

# Add project root to path so we can import BESS modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

from core.bess.dp_battery_algorithm import optimize_battery_schedule
from core.bess.settings import BatterySettings

# ── Config ────────────────────────────────────────────────────────────────────
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
OPTIONS_FILE = os.path.join(PROJECT_ROOT, "backend", "dev-options.json")

# Load .env
env_file = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(env_file):
    for line in open(env_file):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
INFLUX_URL = "http://homeassistant.local:8086/query"
INFLUX_DB = "homeassistant"
INFLUX_USER = os.environ.get("HA_DB_USER_NAME", "homeassistantinflux")
INFLUX_PASS = os.environ.get("HA_DB_PASSWORD", "homeassistantinflux123")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_options() -> dict:
    with open(OPTIONS_FILE) as f:
        return json.load(f)


def ha_get(entity_id: str) -> dict:
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    r = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def influx_query(q: str) -> list:
    r = requests.get(
        INFLUX_URL,
        params={"db": INFLUX_DB, "q": q},
        auth=(INFLUX_USER, INFLUX_PASS),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [{}])
    series = results[0].get("series", [])
    if not series:
        return []
    return series[0].get("values", [])


def now_local() -> datetime:
    return datetime.now(AMSTERDAM)


# ── Battery SOC ───────────────────────────────────────────────────────────────

def get_battery_soc(options: dict) -> float:
    entity = options["sensors"]["battery_soc"]
    state = ha_get(entity)
    return float(state["state"])


# ── Prices ────────────────────────────────────────────────────────────────────

def _parse_raw_entries(raw: list, ep: dict) -> tuple[list[float], list[float]]:
    """Convert raw Nordpool entries (VAT-inclusive, quarterly) to buy/sell arrays.

    Matches HomeAssistantSource logic in price_manager.py:
    - Divides spot by vat_multiplier to get VAT-exclusive base
    - buy  = (base + markup) * vat + additional
    - sell = base + tax_reduction  (no VAT on sell)
    """
    markup = ep["markup_rate"]
    vat = ep["vat_multiplier"]
    additional = ep["additional_costs"]
    tax_red = ep["tax_reduction"]

    buy, sell = [], []
    for entry in raw:
        base = entry["value"] / vat  # remove VAT → exclusive base price
        buy.append((base + markup) * vat + additional)
        sell.append(base + tax_red)
    return buy, sell


def parse_nordpool_prices(attrs: dict, ep: dict) -> tuple[list[float], list[float]]:
    """Parse raw Nordpool today prices into buy+sell lists (96 periods)."""
    raw = attrs.get("raw_today", [])
    if not raw:
        raise RuntimeError("No raw_today in Nordpool sensor")
    buy, sell = _parse_raw_entries(raw, ep)
    return buy[:96], sell[:96]


def parse_nordpool_tomorrow(attrs: dict, ep: dict) -> tuple[list[float], list[float]]:
    """Parse tomorrow's prices. Returns ([], []) if not available."""
    raw = attrs.get("raw_tomorrow", [])
    if not raw:
        return [], []
    buy, sell = _parse_raw_entries(raw, ep)
    return buy[:96], sell[:96]


def get_prices(options: dict) -> tuple[list[float], list[float], bool]:
    """Returns (buy_prices, sell_prices, has_tomorrow) for 96 or 192 periods."""
    entity = options["energy_provider"]["nordpool"]["today_entity"]
    state = ha_get(entity)
    attrs = state.get("attributes", {})
    ep = options["electricity_price"]

    buy_today, sell_today = parse_nordpool_prices(attrs, ep)
    buy_tmrw, sell_tmrw = parse_nordpool_tomorrow(attrs, ep)

    if buy_tmrw:
        return buy_today + buy_tmrw, sell_today + sell_tmrw, True
    return buy_today, sell_today, False


# ── Solar forecast ────────────────────────────────────────────────────────────

def get_solar_forecast(options: dict, include_tomorrow: bool) -> list[float]:
    """
    Fetch solar forecast exactly as BESS does:
    - Uses detailedHourly attribute
    - Each hourly value divided by 4 and repeated 4x for quarterly periods
    """
    def parse_entity(entity_id: str) -> list[float]:
        state = ha_get(entity_id)
        attrs = state.get("attributes", {})
        hourly_data = attrs.get("detailedHourly", [])
        if not hourly_data:
            return [0.0] * 96

        hourly_values = [0.0] * 24
        for entry in hourly_data:
            ps = entry.get("period_start", "")
            try:
                dt = datetime.fromisoformat(ps).astimezone(AMSTERDAM)
                hour = dt.hour
                val = float(entry.get("pv_estimate", 0.0))
                hourly_values[hour] = val
            except Exception:
                continue

        quarterly = []
        for val in hourly_values:
            quarterly.extend([val / 4.0] * 4)
        return quarterly

    today_entity = options["sensors"]["solar_forecast_today"]
    tomorrow_entity = options["sensors"]["solar_forecast_tomorrow"]

    solar_today = parse_entity(today_entity)
    if include_tomorrow:
        solar_tomorrow = parse_entity(tomorrow_entity)
        return solar_today + solar_tomorrow
    return solar_today


# ── Consumption (7d avg, exact BESS method) ───────────────────────────────────

def get_consumption_7d_avg(options: dict, num_periods: int) -> list[float]:
    """
    Fetch 7-day average consumption exactly as BESS does:
    - One query per day for past 7 days
    - get_power_sensor_data_batch equivalent: mean(value) per 15-min period
    - Average across days with >= 48 valid periods
    - Returns kWh per period
    """
    sensor_entity = options["sensors"]["local_load_power"]
    # Strip 'sensor.' prefix for InfluxDB entity_id
    entity_id = sensor_entity.replace("sensor.", "", 1)

    today = now_local().date()
    day_profiles = []

    for days_back in range(1, 8):
        target_date = today - timedelta(days=days_back)
        # Use local-timezone boundaries (matches live app's get_power_sensor_data_batch)
        local_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=AMSTERDAM)
        local_end = local_start + timedelta(days=1)
        start = local_start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = local_end.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

        q = (
            f"SELECT mean(value) FROM \"W\" "
            f"WHERE entity_id='{entity_id}' "
            f"AND time >= '{start}' AND time < '{end}' "
            f"GROUP BY time(15m)"
        )
        rows = influx_query(q)
        if not rows:
            continue

        profile = [None] * 96
        for row in rows:
            ts_str, val = row[0], row[1]
            if val is None:
                continue
            try:
                local_ts = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                ).astimezone(AMSTERDAM)
                slot = local_ts.hour * 4 + local_ts.minute // 15
                if 0 <= slot < 96:
                    profile[slot] = float(val) * 0.25 / 1000.0  # W -> kWh
            except Exception:
                continue

        valid = sum(1 for v in profile if v is not None)
        if valid >= 48:
            day_profiles.append([v if v is not None else 0.0 for v in profile])

    if not day_profiles:
        print("  [WARN] No consumption data, using fixed 0.4 kWh/period")
        base = [0.4] * 96
    else:
        base = [
            sum(d[i] for d in day_profiles) / len(day_profiles)
            for i in range(96)
        ]

    # Extend to 192 if needed (repeat same profile for tomorrow)
    if num_periods > 96:
        return base + base
    return base


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("BESS DP Analysis -- live data from HA/InfluxDB")
    print("=" * 70)

    options = load_options()
    bat_cfg = options["battery"]

    now = now_local()
    current_period = now.hour * 4 + now.minute // 15
    print(f"Time: {now.strftime('%H:%M')} -- current period: {current_period} "
          f"({now.hour:02d}:{(now.minute // 15) * 15:02d})")

    battery = BatterySettings(
        total_capacity=bat_cfg["total_capacity"],
        min_soc=bat_cfg["min_soc"],
        max_soc=bat_cfg["max_soc"],
        max_charge_power_kw=bat_cfg["max_charge_discharge_power"],
        max_discharge_power_kw=bat_cfg["max_charge_discharge_power"],
        cycle_cost_per_kwh=bat_cfg["cycle_cost"],
        min_action_profit_threshold=bat_cfg["min_action_profit_threshold"],
    )

    print(f"\nInstellingen: capacity={battery.total_capacity}kWh, "
          f"max={bat_cfg['max_charge_discharge_power']}kW, "
          f"SOC={bat_cfg['min_soc']}-{bat_cfg['max_soc']}%")

    print("\nFetching live data...")

    print("  Battery SOC... ", end="", flush=True)
    soc = get_battery_soc(options)
    soe_kwh = battery.total_capacity * soc / 100.0
    print(f"{soc:.0f}% = {soe_kwh:.2f} kWh")

    print("  Electricity prices... ", end="", flush=True)
    buy_prices, sell_prices, has_tomorrow = get_prices(options)
    horizon_periods = len(buy_prices)
    print(f"buy nu: {buy_prices[current_period]:.4f}, sell nu: {sell_prices[current_period]:.4f} EUR/kWh "
          f"({'192' if has_tomorrow else '96'} periodes)")

    print("  Solar forecast... ", end="", flush=True)
    solar = get_solar_forecast(options, has_tomorrow)
    if len(solar) < horizon_periods:
        solar = solar + [0.0] * (horizon_periods - len(solar))
    total_solar = sum(solar[current_period:])
    print(f"{total_solar:.2f} kWh resterend vandaag+morgen")

    print("  Consumption 7d avg... ", end="", flush=True)
    consumption = get_consumption_7d_avg(options, horizon_periods)
    total_cons = sum(consumption[current_period:current_period + (96 - current_period)])
    print(f"{total_cons:.2f} kWh resterend vandaag")

    remaining = horizon_periods - current_period
    print(f"\nRunning DP for {remaining} remaining periods ({current_period} -> {horizon_periods})...")

    result = optimize_battery_schedule(
        buy_price=buy_prices[current_period:],
        sell_price=sell_prices[current_period:],
        home_consumption=consumption[current_period:],
        battery_settings=battery,
        solar_production=solar[current_period:],
        initial_soe=soe_kwh,
        initial_cost_basis=battery.cycle_cost_per_kwh,
        currency=options["home"].get("currency", "EUR"),
    )

    # Output table
    print()
    hdr = f"{'Tijd':<6} {'Intent':<16} {'Action':>8} {'SOE':>6} {'Grid_in':>6} {'Buy':>7} {'Sell':>7} {'Cost':>8} {'Base':>8} {'Save':>8}"
    print(hdr)
    print("-" * len(hdr))

    today_cost = today_base = today_save = 0.0
    total_cost = total_base = total_save = 0.0
    prev_date_label = ""

    for i, pd_item in enumerate(result.period_data):
        period_abs = current_period + i
        # Determine day
        if period_abs < 96:
            day_offset = 0
        else:
            day_offset = (period_abs) // 96

        hour = (period_abs % 96) // 4
        minute = (period_abs % 4) * 15
        tijd = f"{hour:02d}:{minute:02d}"

        if day_offset > 0 and prev_date_label != "morgen":
            print(f"{'--- morgen ---'}")
            prev_date_label = "morgen"

        intent = pd_item.decision.strategic_intent or "IDLE"
        action = pd_item.decision.battery_action or 0.0
        soe_end = pd_item.energy.battery_soe_end
        grid_import = pd_item.energy.grid_imported
        buy = buy_prices[current_period + i]
        sell = sell_prices[current_period + i]
        cost = pd_item.economic.hourly_cost
        base = pd_item.economic.grid_only_cost
        save = pd_item.economic.hourly_savings

        total_cost += cost
        total_base += base
        total_save += save
        if day_offset == 0:
            today_cost += cost
            today_base += base
            today_save += save

        print(f"{tijd:<6} {intent:<16} {action:>+8.3f} {soe_end:>5.2f}k "
              f"{grid_import:>6.3f} {buy:>7.4f} {sell:>7.4f} {cost:>8.4f} {base:>8.4f} {save:>8.4f}")

    print("-" * len(hdr))
    print(f"{'VANDAAG':<6} {'':<16} {'':<8} {'':<6} {'':<6} {'':<7} {'':<7} "
          f"{today_cost:>8.4f} {today_base:>8.4f} {today_save:>8.4f}")
    if has_tomorrow:
        print(f"{'TOTAAL':<6} {'':<16} {'':<8} {'':<6} {'':<6} {'':<7} {'':<7} "
              f"{total_cost:>8.4f} {total_base:>8.4f} {total_save:>8.4f}")
    print()
    print(f"Verwachte besparing vandaag:  {today_save:.4f} EUR")
    if has_tomorrow:
        print(f"Verwachte besparing totaal:   {total_save:.4f} EUR")


if __name__ == "__main__":
    main()
