# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Fork-Specific Fixes

## Session 2026-03-25: Chg%/Dchg% actual fixes (v7.9.15–v7.9.19)

### What was fixed and why

The Decision Details table Chg%/Dchg% actual column was broken in multiple ways, fixed across several versions:

**v7.9.15**: `fmtDual` showed `0` as `—`. Fixed by adding `showZero` parameter.
HistoricalDataStore made persistent to `/data/historical_store.json`.
fill(previous) added to `_parse_avg_batch_response` for sparse InfluxDB data.

**v7.9.17–v7.9.19**: fill(previous) with seed — the root cause chain:

1. `number.*` entities in InfluxDB only write on value change (sparse). Periods
   without a change event had no data → showed `–` even though inverter was
   still at same value.

2. fill(previous) was added but used `mean` as carry-forward value. If two data
   points existed in a period (e.g. 45% → 100% within seconds), the mean (73%)
   was carried forward instead of the last raw value (100%). Fixed: fill(previous)
   now uses `last raw value` of a period as seed for the next.

3. fill(previous) seed was empty at start of day — if no change event occurred
   today, `last_known` started empty. Fixed: `_fetch_seed_values()` queries
   `last()` before midnight to seed fill(previous) from the DB.

4. `all_sensor_names` was built from today's data only. Sensors with no data
   today were absent from the fill loop even with seed values. Fixed: seed sensor
   names merged into `all_sensor_names`.

5. Empty InfluxDB response (200 with empty body) caused `_parse_avg_batch_response`
   to return `{}` early before fill(previous) ran. Fixed: early return removed,
   fill(previous) always runs even with empty response.

### Key insight: config.yaml ≠ live HA config

`config.yaml` in repo root has example/default values. Live sensor IDs come from
HA add-on options. When sensor IDs are needed, always ask user to paste their config.
Do NOT reference config.yaml values as if they are live values.

### Pending to-do (next session)

1. Altijd plan tonen in Chg%/Dchg% ook als actual ontbreekt (consistent format)
2. HistoricalDataStore persistent maken naar `/data/` (v7.9.15 had this, check if still in)
3. Verwijder debug logging `=== SCHEDULE CREATION DEBUG START ===` in `battery_system_manager.py`
4. Verwijder ongebruikte componenten `EnergySankeyChart` en `TableBatteryDecisionExplorer`
5. Verwijder `_load_and_apply_settings()` in `app.py` (nooit aangeroepen)

### How actual Chg%/Dchg% works (per 2026-03-25)

1. `api.py` calls `get_control_sensor_data_batch(sensors, target_date)`
2. `_fetch_seed_values()` queries `last()` before midnight → seeds fill(previous)
3. Main Flux query fetches all data points for target_date
4. `_parse_avg_batch_response()`: groups by 15-min period, computes mean per period
5. fill(previous): carries `last raw value` forward to empty periods, seeded from DB
6. `api.py` looks up `sensor.{entity_id}` key per period in result

### Code review findings (2026-03-25, not yet fixed)

- `camel_to_snake()` duplicated in `api_conversion.py` and `settings.py`
- `_load_and_apply_settings()` in `app.py` never called
- `EnergySankeyChart.tsx` and `TableBatteryDecisionExplorer.tsx` unused
- Debug logging in `battery_system_manager.py` line ~1489
- 22+ `hasattr` checks violating deterministic design principle
- Hardcoded fallbacks in `SavingsPage.tsx` (totalCapacity: 10, etc.)

## Session 2026-03-24 (part 3): Battery Settings card Charge Power Rate fix (v7.9.16)

### v7.9.16: Fix Charge Power Rate showing hardcoded 40% instead of live sensor

**What**: The Battery Settings card showed "Charge Power Rate: 40%" — always, regardless
of what the inverter actually had set.

**Why**: `chargingPowerRate` in the card came from `batterySettings?.chargingPowerRate`
→ `BatterySettings.charging_power_rate` → hardcoded default `BATTERY_DEFAULT_CHARGING_POWER_RATE = 40`.
This is the power monitor's internal target value, not a live sensor read.
Meanwhile, `Discharge Power Rate` already used `controller.get_discharging_power_rate()`
(live HA sensor read) — inconsistent.

**How**: Added `charge_power_rate` to the `/api/battery-settings` response in `backend/api.py`
using `controller.get_charging_power_rate()` → reads `battery_charging_power_rate` sensor
(`number.growatt_min_3600tl_xh_battery_charge_power_limit`) live from HA.
Frontend now reads `inverterStatus?.chargePowerRate` instead of `batterySettings?.chargingPowerRate`.

**Note**: `get_charging_power_rate()` reads the current HA entity state — always returns
the last known value regardless of how long ago it changed. No sparse data issue here
(unlike InfluxDB which only stores change events).

## Session 2026-03-24 (part 2): Decision Details Chg%/Dchg% fixes (v7.9.15)

### v7.9.15: Persistent HistoricalDataStore, fill(previous) for Chg%/Dchg%, show 0 as 0

#### Problem analysis

The Decision Details table Chg%/Dchg% columns were broken in three ways:

1. **0% shown as `—`** — `fmtDual` in the frontend used `v === 0 ? '—' : v.toFixed(d)`, so
   IDLE plan (discharge=0%) always showed `—` instead of `0`.

2. **Actual values always `–`** — `_parse_avg_batch_response` computed mean values only for
   periods that had a data point. `number.*` entities in InfluxDB only write on value change
   (sparse). A period with no write got no entry → actual showed `–` even though the inverter
   was still at the previous value.

3. **Plan/actual data lost on restart** — `HistoricalDataStore` was purely in-memory.
   Every restart wiped all plan and actual data, leaving the table empty until periods
   completed in real-time.

#### How plan % is calculated (no sensor reads involved)

Plan Chg%/Dchg% come purely from `INTENT_TO_CONTROL` in `growatt_schedule.py`:

```python
INTENT_TO_CONTROL = {
    "GRID_CHARGING":     {"charge_rate": 100, "discharge_rate": 0},
    "SOLAR_STORAGE":     {"charge_rate": 100, "discharge_rate": 100},
    "LOAD_SUPPORT":      {"charge_rate": 100, "discharge_rate": 100},
    "EXPORT_ARBITRAGE":  {"charge_rate": 0,   "discharge_rate": 100},
    "IDLE":              {"charge_rate": 100, "discharge_rate": 0},
}
```

No sensor is read. The DP optimizer determines the intent → fixed mapping gives the %.

#### How actual % is calculated

1. `api.py` calls `controller.resolve_sensor_for_influxdb("battery_charging_power_rate")`
2. Returns entity ID from live HA config (e.g. `number.growatt_min_3600tl_xh_battery_charge_power_limit`)
3. `get_control_sensor_data_batch()` queries InfluxDB via Flux API for all data points today
4. `_parse_avg_batch_response()` groups by 15-min period and computes mean per period
5. **fill(previous)** (added in this version): last known value carried forward to periods
   with no data point
6. `api.py` looks up the value by `sensor.{entity_id}` key per period

#### Important: config.yaml in repo ≠ live HA config

`config.yaml` in the repo root contains **example/default values only**. The actual sensor
IDs used at runtime come from the HA add-on options. Never assume repo values are live values.
When sensor IDs are needed for analysis, ask the user to paste their config.

#### What changed in v7.9.15

**`frontend/src/components/InverterStatusDashboard.tsx`**

- `fmtDual` got a `showZero` parameter (default `false`). Chg%/Dchg% pass `showZero=true`
  so `0` renders as `"0"` not `"—"`.
- When `actual === null`, plan is now always shown as a grey `<span>` (consistent with
  other columns). Previously it rendered without styling.

**`core/bess/influxdb_helper.py` — `_parse_avg_batch_response`**

Added fill(previous) after computing per-period means:

```python
last_known: dict[str, float] = {}
for period in range(96):
    for sensor_name in all_sensor_names:
        if period in period_data and sensor_name in period_data[period]:
            last_known[sensor_name] = period_data[period][sensor_name]
        elif sensor_name in last_known:
            period_data.setdefault(period, {})[sensor_name] = last_known[sensor_name]
```

**`core/bess/historical_data_store.py` — persistence**

- `STORE_VERSION = 1` constant for forward compatibility
- `data_dir` parameter (default `/data`) — HA add-on persistent storage path
- `_save()`: serializes all four dicts to `/data/historical_store.json` after every write
- `_load()`: called in `__init__`, validates version, deserializes. On mismatch or any
  error: deletes the file and starts fresh (no crash)
- `_period_data_to_dict()` / `_period_data_from_dict()`: explicit field-by-field
  serialization to avoid `init=False` field issues in `EnergyData` and `EconomicData`
- `_save()` called after: `record_period`, `record_planned_period`,
  `record_period_for_date`, `roll_over_to_historical`

#### Limitation

Actual Chg%/Dchg% before `number.*` entities were added to InfluxDB will always show `–`.
fill(previous) only helps once there is a first data point to carry forward. Data before
that point is permanently missing.

## Session 2026-03-24: Cleanup & Table Fixes (v7.9.11–v7.9.14)

### v7.9.11: Remove net_grid_power sensor logic

**Why**: `net_grid_power` was a bidirectional power sensor (W, positive=import/negative=export)
intended for real-time grid monitoring. It was never actually used in any frontend component —
only defined in the type system and API. Grid import/export accuracy is now achieved by
configuring P1 meter cumulative energy sensors directly in `config.yaml`:

```yaml
lifetime_import_from_grid: "sensor.p1_meter_energy_import"
lifetime_export_to_grid:   "sensor.p1_meter_energy_export"
```

These are drop-in replacements for the Growatt lifetime sensors — both are cumulative kWh
sensors so the existing delta calculation works identically.

**Removed from**:

- `core/bess/ha_api_controller.py`: `get_net_grid_power()` method and METHOD_SENSOR_MAP entry
- `backend/api_dataclasses.py`: `netGridPower` field and `controller.get_net_grid_power()` call
- `frontend/src/api/scheduleApi.ts`: `netGridPowerW` and `netGridPowerFormatted` type fields
- `config.yaml`: `net_grid_power` sensor key and schema entry

### v7.9.12: Suppress false-positive DP energy mismatch warnings

**Why**: `dp_battery_algorithm.py` line 253 fired "Energy stored mismatch" warnings for
near-zero power states during optimization state exploration. `power > 0` triggered even for
floating-point artifacts (e.g. 0.001), giving `energy_stored ≈ 0.000` but `SOE delta = -0.050`,
causing dozens of harmless warnings on every startup.

**Fix**: Added `power > 0.1` guard so the sanity check only fires for meaningful charge power.

### v7.9.13: Sticky header for Decision Details table

**Why**: The table header scrolled out of view when the table had many rows.

**Fix**: Wrapped the table in `<div className="max-h-[600px] overflow-y-auto">` inside the
existing `overflow-x-auto` container. `sticky top-0` on `<thead>` now works correctly because
`sticky` is relative to the nearest scroll ancestor — the new inner div — not the outer
horizontal-scroll container (which was breaking it).

### v7.9.14: Remove InfluxDB startup backfill for Decision Details

**Why**: On startup, `_fetch_and_initialize_historical_data()` and `_fetch_historical_days()`
backfilled all past periods from InfluxDB into the Decision Details table. This data was
inaccurate (Growatt cumulative sensor resolution 0.1 kWh, SOC fallback to current live value,
prices recalculated at current moment). User wanted actual values to only appear as periods
complete in real-time.

**What changed**: Both backfill calls removed from `start()` in `battery_system_manager.py`.

**Side effect**: Caused "Incomplete Historical Data" warning on Dashboard because
`get_historical_data_status()` checks the same `HistoricalDataStore` for missing periods.
User chose to leave this as-is (v7.9.15 fix was pushed then reverted at user request).

**Note on 7-day savings**: The Savings page shows today only, not a 7-day history. The
"7-day" concept in the codebase is exclusively the consumption forecast for the DP optimizer
(`influxdb_7d_avg` strategy). InfluxDB data is persistent across restarts — a multi-day
savings view could be built in future using `collect_energy_data(period, date_offset=day_offset)`.

### What the historical_store is used for

- `get_historical_data_status()` → Dashboard "Incomplete Historical Data" warning
- `/api/period_details` → Decision Details table (15-min resolution on Inverter page)
- `/api/dashboard` → Today's energy flow charts (via `DailyView`)

It is **not** used for the 7-day savings view or any persistent multi-day reporting.

## Session 2026-03-23: Decision Details Table Improvements (v7.9.10)

### What was built

Major improvements to the "Decision Details (15-min resolution)" table in
[frontend/src/components/InverterStatusDashboard.tsx](frontend/src/components/InverterStatusDashboard.tsx).

### Changes per column

**SOE begin / SOE einde** — now displayed in % (not kWh). Calculated as `soeKwh / totalCapacity * 100`
in the backend before sending to the frontend. SOE einde shows `plan / actual` — the planned value
from the DP result in grey, the actual value from `historical_store` in bold.

**Verbruik** — now shows `plan / actual`. Actual comes from `actual.energy.home_consumption` in the
historical store. No separate InfluxDB query needed — already recorded at period end.

**Chg% / Dchg%** — now shows `plan / actual`. Planned values come from `INTENT_TO_CONTROL` (0 or 100
for most intents, or specific value from `adjust_charging_power`). Actual values are fetched from
InfluxDB using `get_control_sensor_data_batch()` — this averages `number.*` entity values
(`battery_charging_power_rate`, `battery_discharging_power_rate`) per 15-min period.
Note: these entities must be in InfluxDB (HA config updated to include `number` domain).

**Grid↓ / Grid↑** — already had plan/actual; no change needed.

**Kosten / Baseline / Besparing** — now show `plan / actual`. Actual values from
`actual.economic.hourly_cost`, `actual.economic.grid_only_cost`, `actual.economic.hourly_savings`.

**Currency** — all `SEK` labels replaced with `periodDetails?.currency ?? 'SEK'`. The backend
includes `currency: system.home_settings.currency` in the `/api/period_details` response.

### Multi-day history

**Why**: User wanted to see yesterday's actual data alongside today's plan, configurable via
`history_days` (default=1, meaning yesterday + today).

**How it works**:

1. **Midnight rollover** (`historical_store.roll_over_to_historical()`): At 23:55 (`prepare_next_day`),
   today's `_records` and `_planned_records` are copied into `_historical_records[today]` and
   `_historical_planned[today]` before clearing. Previously `historical_store.clear()` discarded
   everything. `evict_old_days()` removes data older than `history_days`.

2. **Startup backfill** (`_fetch_historical_days()`): Called during `start()` after today's backfill.
   Loops over `range(-history_days, 0)` (e.g., -1 for yesterday), calls
   `sensor_collector.collect_energy_data(period, date_offset=day_offset)` for each period, and
   stores results via `historical_store.record_period_for_date(target_date, period, period_data)`.

3. **`collect_energy_data(date_offset)`**: Added `date_offset: int = 0` parameter. When `date_offset < 0`,
   always uses historical backfill mode (skips live sensor path), passes the correct date to
   `_get_period_readings()`, and uses `prev_date_offset = date_offset - 1` for period 0 boundary.
   Cache (`_last_readings`) is only updated when `date_offset == 0`.

4. **API**: `get_period_details` prepends past dates from `historical_store.get_available_dates()`
   before today's periods. Frontend adds date separator rows when `p.date` changes.

### Bug fix: actual* fields always populated

Previously, `_make_actual_entry()` had `if planned else None` guards on all actual* fields, so
they only appeared when a planned snapshot existed. Fixed: actual* fields now always populated
from the historical store, using `if not is_missing` (where `is_missing = data_source == "missing"`).

### New infrastructure added

- `historical_data_store.py`: `roll_over_to_historical()`, `get_planned_period_for_date()`
- `sensor_collector.py`: `date_offset` parameter on `collect_energy_data()`
- `battery_system_manager.py`: `_fetch_historical_days()`, called in `start()`
- `influxdb_helper.py`: `get_control_sensor_data_batch()` + `_parse_avg_batch_response()` —
  averages raw state values (%) per period, no W→kWh conversion; strips domain prefix from
  entity IDs for InfluxDB 1.x compatibility (e.g. `number.entity` → checks `entity_id == "entity"`)
- `backend/api.py`: Import of `get_control_sensor_data_batch`; full replacement of
  `get_period_details()` endpoint

### InfluxDB: number.* entities

`number.rkm0d7n04x_battery_charge_power_limit` and `number.rkm0d7n04x_battery_discharge_power_limit`
are now written to InfluxDB. Required adding `number` to the `include_domains` list in the HA
InfluxDB integration config (homeassistant-config repo). Without this, actual charge/discharge
rates would not appear in the Decision Details table.

## Session 2026-03-23: Fix "Incomplete Historical Data" Warning (v7.9.9)

### Symptom

Production UI showed "Incomplete Historical Data — Missing data for 1 hour: 3" after each restart.
Hour 3 = period 15 (03:45). Periods 12–14 were present; period 15 was not.

### Root cause

During startup backfill (`_fetch_and_initialize_historical_data`), `collect_energy_data(period)`
raises `RuntimeError` if a period is absent from the InfluxDB batch cache. This happens when
cumulative energy sensors have a data gap at the period boundary (e.g. brief Growatt offline).
The exception was silently caught, leaving the period as `None` in `HistoricalDataStore`.

### Three fixes applied

**1. Power sensor fallback in `sensor_collector.py`** (`collect_energy_data` historical backfill path)

When `_get_period_readings()` returns `None` (no cumulative sensor data for the period or its
predecessor), the code now tries `_get_power_based_flows()` — power sensors (W) averaged over
the period and converted to kWh. If power data is available, `_energy_data_from_power_flows()`
constructs a real `EnergyData` directly from the flows. SOC start/end are set to the current
live HA value (same value for both) because historical SOC is unavailable in this path.

New helper method: `_energy_data_from_power_flows(period, power_flows) -> EnergyData`

**2. Last-resort placeholder in `battery_system_manager.py`** (`_fetch_and_initialize_historical_data`)

If both cumulative and power sensors fail (complete sensor outage), the exception handler now
stores a zero-energy `PeriodData` with `data_source="missing"` instead of doing nothing.
This ensures the period is never `None` in `HistoricalDataStore`, preventing the warning.

`EnergyData` was added to the `from .models import` list in this file.

**3. Logic bug fix in `api.py`** (`get_historical_data_status`)

`missing_hours` and `completed_hours` could contain the same hour when only one quarter was
missing (e.g. hour 3 appeared in both). Fixed so a hour is only in `completed_hours` if all
four quarters are present:

```python
missing_hours_set = {p // 4 for p in missing_periods}
missing_hours = sorted(missing_hours_set)
completed_hours = sorted({p // 4 for p in completed_periods} - missing_hours_set)
```

### Data flow for historical backfill (priority order)

1. Cumulative InfluxDB sensors → delta calculation → `data_source="actual"` ✅ best quality
2. Power sensors (W→kWh) → direct flows → `data_source="actual"` ✅ good quality, no SOC delta
3. Zero placeholder → `data_source="missing"` — only if all sensors offline simultaneously

## Session 2026-03-19: Period Details Table + Dynamic Adjustment Research

### What was built

Added `/api/period_details` endpoint ([backend/api.py](backend/api.py)) that returns all 96-192 `PeriodData`
entries with every parameter used by the DP algorithm: prices, solar, consumption, SOE start/end,
cost basis, strategic intent, battery action, inverter control settings (mode/gridCharge/chargeRate/
dischargeRate), energy flows, and economics per period.

Frontend: collapsible "Decision Details (15-min resolution)" table in
[frontend/src/components/InverterStatusDashboard.tsx](frontend/src/components/InverterStatusDashboard.tsx)
— 20 columns, color-coded by category, current period highlighted, actual vs. predicted rows distinguished.

### IDLE discharge_rate = 0 (not 100)

IDLE intent must have `discharge_rate = 0` in ALL four locations:

- `INTENT_TO_CONTROL["IDLE"]` in [core/bess/growatt_schedule.py](core/bess/growatt_schedule.py)
- `.get()` fallback in the same file
- `_calculate_hourly_settings_with_strategic_intents` IDLE branch
- `_apply_period_schedule` IDLE branch in [core/bess/battery_system_manager.py](core/bess/battery_system_manager.py)

This was reverted and released as v7.9.6 to force a fresh Docker build in HA Supervisor.

### Optimization correctness (verified 2026-03-19)

With 192 periods (2-day horizon) and only solar charging:

- Cost basis constant at 0.0450 SEK/kWh = correct (cycle_cost only, no grid charge cost)
- Single EXPORT_ARBITRAGE at 18:45 = correct (only peak sell window above cost basis)
- Alternating LOAD_SUPPORT/IDLE at night = discretization artifact, not a bug (0.1 kWh SOE grid)
- `min_action_profit_threshold` scales ×2.0 for 192-period horizon — monitor on low-spread days

### Dynamic adjustment: proposals (not yet implemented)

Current system: re-optimization every 15 min (full DP), charging power adjustment every 5 min
(power monitor, fuse-based). Discharge rate is set once per period, not adjusted mid-period.

Four proposals ranked by impact/complexity:

1. **Recency-weighted consumption forecast** — blend 7-day avg with recent 4-8 actual periods
   using `α × recent_avg + (1−α) × historical_avg[t]`. Infrastructure already exists in
   `historical_store.get_today_periods()`. Change is in `_gather_optimization_data`.

2. **PV correction factor on Solcast** — compute `actual_pv_now / solcast_forecast_now` (clip
   to [0.5, 2.0]) and apply to the next N periods' solar forecast. Only apply within ~4 hour
   horizon. Change is in `_gather_optimization_data`.

3. **SOC drift trigger** — light background task every 2-3 min checks
   `|actual_soc − planned_soe| > 5%` and triggers re-optimization outside the normal 15-min
   schedule. Event-driven, not polling the full DP.

4. **Export override at max SOC** — if SOC > 90% during SOLAR_STORAGE and sell price >
   threshold, temporarily switch to export without rerunning DP. Simple rule layer on top of
   existing schedule.

## InfluxDB & Consumption Strategy

### Setup

InfluxDB is configured and running:

- URL: `http://homeassistant.local:8086/api/v2/query`
- Bucket: `homeassistant/autogen` (InfluxDB 1.x with Flux compatibility)
- Username: `homeassistantinflux`

### Consumption strategies

Three strategies exist in `HomeSettings.consumption_strategy`:

- `sensor` — reads `48h_avg_grid_import` from HA (flat profile, grid import only)
- `fixed` — uses `default_hourly / 4.0` kWh for all 96 periods
- `influxdb_7d_avg` — queries past 7 days of `local_load_power` from InfluxDB, averages per 15-min slot → real hourly pattern

### How influxdb_7d_avg works

BESS never writes to InfluxDB. Data comes from the HA InfluxDB integration writing sensor states automatically. At each optimization run, BESS:

1. Queries `local_load_power` sensor for each of the past 7 days (one Flux query per day)
2. Averages all W readings within each 15-min window → converts to kWh (`W * 0.25 / 1000`)
3. Averages across all valid days (needs ≥ 48 periods per day)
4. Returns 96 kWh values as `home_consumption` input to the DP algorithm

Falls back to `fixed` if fewer than one valid day is available.

### Required sensor

The sensor used is configured in `config.yaml` under `sensors.local_load_power`:

```yaml
local_load_power: "sensor.growatt_min_3600tl_xh_local_load_power"
```

This same sensor is already used for realtime power monitoring — no extra configuration needed.

### Visibility

`consumptionStrategy` is defined in `types.ts` and returned by the settings API, but is **not displayed anywhere in the frontend UI**.

### Verification query (InfluxDB Flux)

To see the exact 96-value profile BESS would use:

```flux
import "date"

from(bucket: "homeassistant/autogen")
  |> range(start: -7d)
  |> filter(fn: (r) =>
      r["_measurement"] == "sensor.growatt_min_3600tl_xh_local_load_power" or
      r["entity_id"] == "growatt_min_3600tl_xh_local_load_power"
  )
  |> filter(fn: (r) => r["_field"] == "value")
  |> aggregateWindow(every: 15m, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({
      r with
      _value: r._value * 0.25 / 1000.0,
      hour:   date.hour(t: r._time),
      minute: date.minute(t: r._time)
  }))
  |> group(columns: ["hour", "minute"])
  |> mean(column: "_value")
  |> sort(columns: ["hour", "minute"])
```

## Fork Rules

1. This is a fork of johanzander/bess-manager. Never push to upstream.
2. Only ever push to origin HesselHam/bess-manager.
3. Never create pull requests to johanzander/bess-manager.
4. All changes stay within HesselHam/bess-manager.

## Project Overview

BESS Battery Manager is a Home Assistant add-on for optimizing battery energy storage systems. It provides price-based optimization, solar integration, and comprehensive web interface for managing battery schedules and monitoring energy flows.

## Development Commands

### Backend (Python)

```bash

# Install dependencies

pip install -r backend/requirements.txt

# Run development server

./dev-run.sh

# Run tests

pytest
pytest core/bess/tests/unit/
pytest core/bess/tests/integration/
pytest --cov=core.bess

# Code quality

black .
ruff check --fix .
mypy .
```text

### Frontend (React/TypeScript)

```bash
cd frontend

# Install dependencies

npm install

# Development server

npm run dev

# Build production

npm run build

# Generate API client from OpenAPI spec

npm run generate-api
```text

### Docker Development

```bash

# Start both backend and frontend

docker-compose up -d

# View logs

docker-compose logs -f
```text

### Build Add-on

```bash
chmod +x package-addon.sh
./package-addon.sh
```text

### Quality Checks

```bash

# Run comprehensive quality checks

./scripts/quality-check.sh

# Individual checks

black .                    # Format Python code
ruff check --fix .        # Fix Python linting issues
cd frontend && npm run lint:fix  # Fix TypeScript issues
```text

## Architecture Overview

### High-Level System Design

- **Backend**: FastAPI application (`backend/app.py`) with scheduled optimization jobs
- **Core**: Battery optimization engine (`core/bess/`) with modular components
- **Frontend**: React SPA with real-time dashboard and management interface
- **Integration**: Home Assistant add-on with sensor collection and device control

### Key Components

#### Core BESS System (`core/bess/`)

- **BatterySystemManager**: Main orchestrator managing optimization lifecycle
- **DP Battery Algorithm**: Dynamic programming optimization engine for cost minimization
- **HomeAssistantAPIController**: Centralized interface to HA with sensor abstraction
- **SensorCollector**: Aggregates real-time energy data from HA sensors
- **GrowattScheduleManager**: Converts optimization results to Growatt inverter commands
- **PriceManager**: Handles electricity pricing (Nordpool/Octopus Energy) with markup calculations
- **HealthCheck**: Comprehensive system and sensor validation

#### Data Flow

1. **Hourly Updates**: Scheduler triggers optimization every hour
2. **Sensor Collection**: Real-time data from HA sensors (battery, solar, grid, consumption)
3. **Price Integration**: Electricity spot prices (Nordpool/Octopus Energy) with VAT/markup calculations
4. **Optimization**: DP algorithm generates 24-hour battery schedule
5. **Schedule Deployment**: TOU intervals sent to Growatt inverter
6. **Monitoring**: Dashboard displays real-time status and historical analysis

#### API Structure (`backend/api.py`)

- **Settings Endpoints**: Battery and electricity price configuration
- **Dashboard API**: Unified data for energy flows, savings, and real-time monitoring
- **Decision Intelligence**: Detailed hourly strategy analysis and economic reasoning
- **Inverter Control**: Growatt-specific status and schedule management
- **System Health**: Component diagnostics and sensor validation

### Frontend Architecture (`frontend/src/`)

#### Pages

- **DashboardPage**: Live monitoring and daily overview
- **SavingsPage**: Financial analysis and historical reports
- **InverterPage**: Battery schedule management and inverter status
- **InsightsPage**: Decision intelligence and strategy analysis
- **SystemHealthPage**: Component health and diagnostics

#### Key Components

- **EnergyFlowChart**: Recharts-based visualization of hourly energy flows
- **SystemStatusCard**: Real-time power monitoring with live data
- **InverterStatusDashboard**: Battery status and schedule visualization
- **DecisionFramework**: Strategic decision analysis with economic reasoning

#### State Management

- **useSettings**: Global battery and price settings management
- **API Integration**: Axios-based API client with Home Assistant ingress support
- **Real-time Updates**: Polling-based data refresh for live monitoring

## Coding Guidelines

### Core Development Principles

#### Mandatory Codebase Review Before Refactoring

**CRITICAL**: Before starting any refactoring, architectural changes, or adding new functionality, you MUST perform a comprehensive codebase analysis to understand existing patterns and avoid duplication.

**Required Analysis Steps**:

1. **Search for Existing Implementations**:
   ```bash
   # Search for similar functionality
   grep -r "dataclass\|serialization\|formatting" --include="*.py"
   grep -r "HealthStatus\|SystemHealth" --include="*.ts" --include="*.tsx"
   find . -name "*api*" -name "*model*" -name "*conversion*"
   ```

1. **Examine Related Files**:
   - `backend/api_dataclasses.py` - existing API models
   - `backend/api_conversion.py` - serialization utilities
   - `frontend/src/types.ts` - TypeScript interfaces
   - `core/bess/` - domain models and services
   - Any files matching the functionality you plan to add

1. **Understand Existing Patterns**:
   - How does the codebase currently handle the problem you're solving?
   - What naming conventions and architectural patterns are used?
   - Are there existing utilities, services, or models you should extend?

1. **Document Existing Infrastructure**:
   - List what already exists and works
   - Identify what's actually missing vs what you assumed was missing
   - Plan minimal additions that integrate with existing code

**Red Flags That Indicate Insufficient Analysis**:

- Creating files with names similar to existing files (`api_models.py` when `api_dataclasses.py` exists)
- Recreating functionality that already exists (serialization, enum definitions)
- Writing code that doesn't follow existing patterns
- Adding new dependencies when existing ones could be used

**Example of Proper Analysis**:

```markdown
## Codebase Analysis for Sensor Formatting

### Existing Infrastructure Found:
- ✅ API Dataclasses: `backend/api_dataclasses.py`
- ✅ Serialization: `backend/api_conversion.py`
- ✅ Health Types: `frontend/src/types.ts`
- ✅ Health Endpoint: `/api/system-health` in `backend/api.py`

### What's Actually Missing:
- ❌ Centralized sensor unit formatting (only frontend string matching exists)
- ❌ Unit metadata in METHOD_SENSOR_MAP

### Minimal Required Changes:
1. Add unit metadata to existing METHOD_SENSOR_MAP
2. Create SensorFormattingService
3. Integrate with existing health check system
```

**Consequences of Skipping This Analysis**:

- Duplicate code that needs to be removed
- Inconsistent architecture
- Wasted development time
- Technical debt creation
- Loss of user trust

#### Code Preservation and Evolution

- **Never remove or modify existing functionality or comments** unless explicitly asked to
- **Produce code without reference to older versions** - don't write "UPDATED ALGORITHM" or reference previous implementations
- **Always check the code, don't make assumptions** - if you don't understand something, ask for clarification

#### Deterministic System Design

- **Never use hasattr, fallbacks or default values** - use error/assert instead
- **We are developing a deterministic system** - methods and functionality should not disappear or degrade gracefully
- **Explicit failures over silent failures** - better to crash with clear error than continue with undefined behavior

#### Architectural Consistency

- **Think about current software design** when adding new functionality
- **Extend existing components** instead of creating parallel implementations
- **You are not allowed to create new classes without approval** - work within existing design patterns
- **Never repeat code** - apply DRY principle rigorously

#### Modern Python Standards

- **Use union operator `|` instead of `Optional`** from typing module
- **Always ensure code passes Ruff, black, pylance** - code quality is non-negotiable
- **Follow existing type annotations** and maintain strict typing discipline

#### File Quality Standards

- **Never create files that generate IDE problems or linter errors**
- **All markdown files must pass markdownlint validation**
- **All Python files must pass Ruff, Black, and Pylance without warnings**
- **All TypeScript files must pass ESLint and TypeScript compiler checks**
- **Check Problems tab before committing - zero tolerance for preventable issues**

#### Markdown Formatting Rules

- **Blank lines around headers**: Always add blank line before and after headers
- **Proper list spacing**: Add blank line before lists, none between list items
- **No trailing spaces**: Remove all trailing whitespace
- **Single blank lines**: Never use multiple consecutive blank lines
- **Consistent heading levels**: Don't skip heading levels (no h1 → h3)

```markdown

# Good Example

## Header with proper spacing

This paragraph has proper spacing around it.

### Sub-header

- List item 1
- List item 2
- List item 3

Another paragraph after the list.

## Bad Example

###Missing blank lines

- List immediately after header
- No spacing


Too many blank lines above.
```text

#### Pre-Commit Quality Checklist

Before creating or modifying any files, ALWAYS:

1. **Check Problems Tab**: View → Problems in VS Code - must show zero errors/warnings for modified files
2. **Run Code Formatters**:
   - Python: `black .` and `ruff check --fix .`
   - TypeScript: `npm run lint:fix` in frontend directory
   - Markdown: Use markdownlint extension to fix formatting
3. **Validate File Extensions**: Ensure proper file extensions (.py, .ts, .tsx, .md, .json)
4. **Check File Encoding**: Use UTF-8 encoding for all text files
5. **Remove Temporary Files**: Never commit .tmp, .bak, or editor swap files

#### Automated Quality Check

Run the quality check script before committing:

```bash
./scripts/quality-check.sh
```text

This script automatically checks:

- Python formatting (Black) and linting (Ruff)
- TypeScript compilation and ESLint in frontend
- Markdown formatting issues (trailing spaces, blank lines)
- File encoding and common problems

#### Git Commit Policy

**CRITICAL**: Never commit files without explicit user approval.

**Rules**:

1. **Never commit automatically** - Always wait for the user to explicitly say "commit" or "please commit"
2. **Show changes first** - Always show what will be committed and get approval before running git commit
3. **Clean commit messages** - Write clear, professional commit messages that describe what changed and why

**Examples**:

Good commit message:

```text
Fix settings not updating from config.yaml due to camelCase/snake_case mismatch

The update() method was checking for camelCase keys but dataclass attributes
use snake_case. Added conversion to properly map keys before validation.
```

Bad commit messages:

```text
Fix issue 🤖 Generated with Claude Code
Update settings (AI-assisted)
Changes made by Claude
```

**When User Says "Don't Commit"**:

- Keep changes staged or unstaged as appropriate
- Do not create any git commits
- Changes remain in working directory for user review

#### Common Issues to Avoid

- **Markdown**: Missing blank lines around headers, trailing spaces, multiple consecutive blank lines
- **Python**: Type hints using Optional instead of `|`, missing docstrings, unused imports
- **TypeScript**: `any` types, missing interfaces, inconsistent naming conventions
- **JSON**: Trailing commas, incorrect indentation, missing quotes
- **General**: Mixed line endings (LF vs CRLF), BOM markers, trailing whitespace

### Existing Patterns to Follow

#### Component Integration

- **Search before implementing**: Use existing utilities and patterns before writing new code
- **Use existing controller methods** instead of creating wrappers (e.g., `controller.get_battery_soc()`)
- **Apply health check patterns**: `perform_health_check()` with standardized parameters
- **All sensor access** goes through `ha_api_controller` centralized mapping
- **Use `_get_sensor_key(method_name)`** for entity ID resolution instead of manual extraction

#### Architecture Patterns

- **Health Check System**: Use `perform_health_check()` for all validations
  - Define `required_methods` (critical) vs `optional_methods`
  - Return lists of health check dictionaries, not individual results
- **Sensor Management**: All access through centralized mapping, never hardcode device names
- **Error Handling**: Use existing validation, don't duplicate upstream error checking
- **Settings**: Use dataclass-based configuration with `update()` methods

#### Error Handling Standards

- **NEVER use string matching on exception messages** for flow control (e.g., `if "price data" in str(e)`)
- **Use specific exception types** instead of generic ValueError/Exception catching
- **Create proper exception classes** when needed rather than parsing error message strings
- **String-based error detection is brittle** and breaks when error messages change
- **Example of bad pattern**: `except ValueError as e: if "No price data" in str(e): ...`
- **Example of good pattern**: `except PriceDataUnavailableError: ...`

#### Anti-Patterns to Avoid

1. **Reinventing the wheel**: Creating new methods when existing ones work
2. **Inconsistent patterns**: Using different approaches for the same operation type
3. **Overengineering**: Adding unnecessary complexity to simple operations
4. **Hardcoding**: Using device-specific names instead of centralized mapping
5. **Code duplication**: Copy-pasting logic instead of using shared functions

#### Code Examples

```python

# Good: Use existing controller method

soc_value = self.ha_controller.get_battery_soc()

# Bad: Manual sensor key extraction

sensor_info = self.ha_controller.METHOD_SENSOR_MAP["get_battery_soc"]
soc_sensor_key = sensor_info.get("entity_id")

# Good: Use centralized health check

return perform_health_check(
    component_name="Battery Monitoring",
    description="Real-time battery state monitoring",
    is_required=True,
    controller=self.ha_controller,
    all_methods=battery_methods,
    required_methods=required_battery_methods
)

# Bad: Custom health check logic with hardcoded thresholds

working_count = sum(1 for method in methods if test_method(method))
if working_count >= 3:
    return "OK"
elif working_count >= 1:
    return "WARNING"
else:
    return "ERROR"
```text

#### API Conventions

- **CamelCase Conversion**: All API responses use `convert_keys_to_camel_case()`
- **Unified Data Models**: Use `APIBatterySettings`, `APIPriceSettings` for consistency
- **Error Responses**: Always include meaningful error messages and HTTP status codes
- **Real-time Data**: Use `APIRealTimePower.from_controller()` for live power monitoring

## Testing Strategy

### Unit Tests (`core/bess/tests/unit/`)

- **Scenario Testing**: JSON test data files for various conditions
- **Algorithm Validation**: DP optimization correctness and edge cases
- **Settings Management**: Configuration validation and updates
- **Data Models**: Energy balance validation and economic calculations

### Integration Tests (`core/bess/tests/integration/`)

- **System Workflow**: End-to-end optimization and schedule deployment
- **Cost Savings Flow**: Multi-scenario economic validation
- **Battery Management**: State tracking and capacity management
- **Schedule Management**: TOU interval generation and validation

### Test Data

- **Synthetic Scenarios**: EV charging, high solar export, extreme volatility
- **Historical Data**: Real price data from specific high-spread days
- **Seasonal Patterns**: Spring/summer/winter consumption profiles

## Home Assistant Integration

### Sensor Requirements

- **Battery**: SOC, charge/discharge power, mode status
- **Solar**: Production, home consumption, grid import/export
- **Pricing**: Electricity spot prices (Nordpool or Octopus Energy) with area configuration
- **Grid**: Import/export power and energy totals

### Add-on Configuration

- **Battery Settings**: Capacity, power limits, cycle costs, SOC bounds
- **Price Settings**: Area, VAT, markup, additional costs, tax reduction
- **Home Settings**: Consumption patterns, electrical limits, safety margins

### Device Control

- **Growatt Integration**: TOU schedules, battery modes, power rate control
- **Schedule Deployment**: Automatic hourly schedule updates
- **Real-time Monitoring**: Live power flow tracking and status updates

## Common Development Tasks

### Adding New Sensors

1. Update `METHOD_SENSOR_MAP` in `ha_api_controller.py`
2. Add validation in relevant health check functions
3. Update API response models if needed
4. Test with synthetic and real data

### Modifying Optimization Algorithm

1. Update core logic in `dp_battery_algorithm.py`
2. Add test scenarios in `unit/data/` directory
3. Validate with `test_optimization_algorithm.py`
4. Update decision intelligence reasoning if needed

### Frontend Component Development

1. Follow existing component patterns and API integration
2. Use TypeScript interfaces matching backend data models
3. Implement error boundaries and loading states
4. Test with real API data and edge cases

### Adding New API Endpoints

1. Define endpoint in `backend/api.py` with proper error handling
2. Use `convert_keys_to_camel_case()` for response formatting
3. Add corresponding frontend API integration
4. Update OpenAPI spec and regenerate client types

## Configuration Files

- **pyproject.toml**: Python tooling (black, ruff, mypy) with BESS-specific settings
- **frontend/package.json**: React/TypeScript dependencies and build scripts
- **docker-compose.yml**: Development environment with HA integration
- **config.yaml**: Add-on configuration schema and defaults (root directory)

## PR Merge Workflow

This is the standard process for taking in an external PR.

### Steps

1. **Review** — Read the diff, check for correctness, architecture fit, and any minor issues.
2. **Fix minor issues** — Apply small fixes directly (e.g. UX fallback strings, missing type assertions). For anything substantial, request changes from the author instead.
3. **Update CHANGELOG** — Add a concise entry under a new version heading. One line per change. Always credit the author: `(thanks [@username](https://github.com/username))`.
4. **Bump version** — Follow Semantic Versioning:
   - `PATCH` (x.y.**Z**): bug fixes, comment/doc cleanup, no behavior change
   - `MINOR` (x.**Y**.0): new features, backwards-compatible
   - `MAJOR` (**X**.0.0): breaking changes
   - Update the version in `config.yaml` (the `version:` field).
5. **Merge** — Use `gh pr merge <number> --squash --repo johanzander/bess-manager`. Wait for explicit user approval before merging.
6. **Local test** — User tests on real hardware before tagging.
7. **Tag and push** — After user confirms it works: `git tag vX.Y.Z && git push origin vX.Y.Z`.

### CHANGELOG Format

Follow the existing style — brief, no implementation details:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added

- Short description of what was added. (thanks [@author](https://github.com/author))

### Fixed

- Short description of what was fixed.
```

Never commit or tag without explicit user instruction.

## Unit Testing Guidelines

**CRITICAL**: Always write tests that verify **BEHAVIOR**, not **IMPLEMENTATION**.

### ❌ BAD: Testing Implementation Details

```python
# Don't test internal data structures
strategic_segments = [i for i in intervals if i.get('period_type') == 'strategic']
assert len(strategic_segments) == 1
assert strategic_segments[0]['start_time'] == '20:00'

# Don't test algorithm-specific details
assert len(intervals) == 9  # Specific to "9 fixed slots" algorithm
assert slot_start_times == ['02:40', '05:20']  # Specific slot boundaries
```

### ✅ GOOD: Testing Business Behavior

```python
# Test what the system should DO, not HOW it does it
def test_export_arbitrage_enables_battery_discharge():
    strategic_intents[20] = 'EXPORT_ARBITRAGE'
    scheduler.apply_schedule(strategic_intents)

    # Test BEHAVIOR: Battery should discharge during target hour
    assert scheduler.is_hour_configured_for_export(20)

    # Test CONSTRAINTS: Hardware requirements must be met
    assert scheduler.has_no_overlapping_intervals()
    assert scheduler.intervals_are_chronologically_ordered()
```

### The Test Rewrite Principle

**When algorithms change, behavior-based tests should NOT break.** If your tests break when you swap algorithms, they were testing implementation, not requirements.

#### Test Categories

1. **Business Logic Tests**: Does the system do what users need?
   - Strategic intents execute correctly (charge/discharge at right times)
   - Energy optimization produces cost savings
   - Schedule adapts to price changes

2. **Constraint Tests**: Does the system meet technical requirements?
   - No overlapping intervals (hardware constraint)
   - Chronological ordering (hardware constraint)
   - Minimal inverter writes (operational efficiency)

3. **Integration Tests**: Do components work together?
   - Price data feeds into optimization
   - Optimization results control hardware
   - Sensor data updates system state

#### Red Flags in Tests

- Testing specific field names (`period_type`, `segment_id`)
- Testing exact internal boundaries (`02:40-05:19`)
- Testing algorithm-specific counts (`len(intervals) == 9`)
- Comments mentioning implementation (`"Fixed slots approach"`)
- Tests that break when equivalent algorithms are swapped

#### Writing Good Tests

1. **Start with requirements**: What should this system do?
2. **Test the interface**: What would a user/integrator observe?
3. **Test constraints**: What rules must never be broken?
4. **Make tests algorithm-agnostic**: Could a different implementation pass?

**Remember**: Good tests survive refactoring. Bad tests require updates when internal implementation changes.
