# Changelog

All notable changes to BESS Battery Manager will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [7.9.66] - 2026-04-07

### Added

- Solar forecast bias correction: applies a forecast-level-dependent correction to Solcast
  predictions based on linear regression over historical daily totals. A day forecast at
  10 kWh gets a different correction factor than one at 25 kWh, matching the observed
  Solcast bias pattern for the installation. Correction factor visible in Decision Details
  table (☀ corr. column). Configurable via new "Solar Forecast Correction" settings card
  on the Inverter page. Feature disabled by default.

## [7.9.65] - 2026-04-07

### Fixed

- `export_postprocess_reorder` sorted export slots by `grid_exported × sell_price`
  using window-start SOE as approximation for all periods. This gave wrong results
  when solar differs per period — low-solar periods got higher grid_exported and
  could outscore higher-priced periods. Simplified to sort by `sell_price` only:
  highest price always gets the export slot.

## [7.9.64] - 2026-04-07

### Fixed

- Crash `name 'V' is not defined` when `export_postprocess_reorder` is enabled.
  `_run_dynamic_programming` return value for V was discarded with `_`; fixed by
  capturing it so it can be passed to `_postprocess_export_reorder`.

## [7.9.63] - 2026-04-07

### Fixed

- `export_postprocess_reorder` created more EXPORT slots than originally planned.
  Root cause: demoted EXPORT periods kept their original EXPORT_ARBITRAGE mode
  instead of being replaced. Fixed with a swap map: demoted periods get the mode
  of the period that replaced them (e.g. LOAD_SUPPORT↔EXPORT_ARBITRAGE swap).
- `dp_reward` and `dp_value` after postprocessing now use the correct new reward
  (computed with the actual new mode and SOE) plus V[t+1, next_i] from the
  original V-matrix with the updated SOE trajectory. No extra backward pass needed.

## [7.9.62] - 2026-04-07

### Fixed

- Reward and V[t,i] columns showing 0.0000 when `export_postprocess_reorder` is
  enabled. The post-processing step creates new `PeriodData` objects via
  `_calculate_reward`; these default to `dp_reward=0.0` and `dp_value=0.0`.
  Fixed by copying the original DP diagnostics onto the replacement object.

## [7.9.59] - 2026-04-06

### Fixed

- Charge/discharge rate briefly flickering to wrong value at period boundaries when
  the optimized intent differs from the previous schedule. Root cause: the initial
  pre-optimization write used the stale (previous) schedule. Fixed by setting HOLD
  `charge_rate` and `discharge_rate` to 100% — BDC hardware-isolates the battery
  during HOLD so the rate value is irrelevant, and this eliminates the 0%→100% flip
  visible in HA graphs at HOLD↔LOAD_SUPPORT transitions.
- BDC spurious EEPROM writes at period boundaries. The initial pre-optimization call
  could write BDC=off (stale HOLD intent) immediately followed by BDC=on (optimized
  LOAD_SUPPORT intent) seconds later, causing two unnecessary Modbus EEPROM writes.
  Fixed by deferring BDC writes to the post-optimization call only.

## [7.9.58] - 2026-04-06

### Changed

- IDLE mode now blocked when `sell_price < 0`. Solar surplus during IDLE flows to
  grid, so a negative sell price would cost money — same reasoning as the existing
  EXPORT_ARBITRAGE guard. DP will prefer SOLAR_STORAGE or LOAD_SUPPORT instead.
- IDLE state machine now inverted when SOC equals `min_soc` at period start.
  Normal IDLE holds SOC at current level (charge when SOC drops below goal minus
  deadband). Inverted IDLE charges immediately and stops when SOC reaches
  goal plus deadband, resuming charge when SOC drops back to goal. Prevents
  battery from sitting at minimum SOC during IDLE periods.

## [7.9.57] - 2026-04-05

### Fixed

- SOE display showing values above 100% (e.g. 100.7%) in the Decision Details table.
  Root cause: the snapping formula used `min_soe + i × SOE_STEP_KWH` while
  `_discretize_state_space` uses `linspace(min_soe, max_soe, n+1)`. When the usable
  range is not an exact multiple of 0.1 kWh the two grids diverge, producing snapped
  SOE values that exceed `max_soe_kwh`. Fixed by deriving the actual linspace step
  (`range / n`) and adding a hard clamp as a floating-point safety net.

## [7.9.56] - 2026-04-05

### Added

- `export_look_ahead_guard` (config, default `false`): Blocks `EXPORT_ARBITRAGE` in the DP
  backward pass when the immediately following period has a strictly higher sell price.
  Simple heuristic; most effective when the better price is exactly one period away.
- `export_postprocess_reorder` (config, default `false`): After the DP forward pass,
  re-assigns `EXPORT_ARBITRAGE` actions within each contiguous sell window to the
  highest-priced periods. Addresses the greedy early-export bias caused by SOE-state
  discretization flatness (`V[t+1, i_high] ≈ V[t+1, i_low]` when both states have
  sufficient charge for full discharge). Falls back to original mode if SOE is
  insufficient after reordering. Both options are disabled by default; enable
  independently via `config.yaml`.

## [7.9.55] - 2026-04-04

### Fixed

- Health check crash on startup: `_power_monitor` reference not updated after rename to `power_monitor`.

## [7.9.54] - 2026-04-04

### Changed

- Power monitor refactored into two independent scheduled functions:
  `adjust_fuse_protection` (every 15 s, only when grid charging active) and
  `enforce_idle_deadband` (every 30 s, only when IDLE context is set). Previously
  a single 1-minute job ran both unconditionally.
- `check_preemptive_bdc` is now a public method scheduled via
  `CronTrigger(minute="14,29,44,59")` — one minute before each period boundary.
- Power monitor jobs are only registered when the power monitor is available
  (skipped in controller-less environments to prevent startup crash).
- IDLE mode guard tightened: blocked when solar is zero, or when the solar deficit
  exceeds 0.1 kWh (100 Wh) per period. Previously blocked only when solar was zero.

## [7.9.53] - 2026-04-04

### Added

- Export limit control via `export_limit_entity` sensor (optional, Modbus only). On each
  period transition, blocks all grid export when sell price is negative by writing
  `export_limit_enable_option` (default: "Meter 1") to the inverter's select entity;
  restores "Disabled" otherwise. On startup, always writes "Disabled" as a safe initial state.
- `export_limit_simulation: true` (default): DP and UI reflect export blocking without
  writing to hardware. Set to `false` to enable actual inverter writes.
- Decision Details table: new "BlkExp" column shows a red ✓ for periods where export is
  planned to be blocked (sell price negative).

### Fixed

- `inverter_phase` from `config.yaml` was never propagated to `HomeSettings` — the power
  monitor always used the most-loaded phase instead of the configured inverter phase.
- BDC is now explicitly enabled on startup for a guaranteed safe initial state.

### Changed

- DP optimization: EXPORT_ARBITRAGE is skipped when sell price is negative. For all other
  modes, solar surplus that cannot be exported is modelled as curtailed (grid_exported=0),
  so reward and SOE calculations correctly reflect no export revenue.

## [7.9.52] - 2026-04-04

### Fixed

- Decision Details table: SOE end of period t now equals SOE start of period t+1.
  Previously a gap of up to 0.67% (0.05 kWh on a 7.5 kWh battery) appeared due to the
  DP discretizing the state space in 0.1 kWh steps — `battery_soe_end` stored the exact
  computed float while the next period's `battery_soe_start` used the nearest grid point.
  Fix: snap `battery_soe_end` to the same grid at storage time. DP optimization is unaffected.

## [7.9.51] - 2026-04-04

### Fixed

- HA API no longer retries on 4xx client errors. Previously all errors (including
  400 Bad Request) triggered up to 3 retries with 2+4+8s backoff. A 400 response
  (e.g. Modbus entity not ready on startup) now fails immediately and falls back to
  cloud API, eliminating ~14 seconds of wasted delay per failed segment.

## [7.9.50] - 2026-04-04

### Fixed

- EXPORT_ARBITRAGE energy flow now correctly models the hybrid inverter's AC output cap.
  Solar and battery share the inverter's 3.6 kW output (0.9 kWh/period); solar fills
  part of that capacity and battery provides the rest. Previously the DP used
  `battery_discharge = max_discharge` regardless of solar, so its internal model assumed
  more battery discharge than actually occurs and overestimated grid_exported.
  Fix: `battery_discharge = max(0, max_discharge - solar)`. The hardware was unaffected;
  this corrects the DP's accounting of SOE and export revenue.

## [7.9.49] - 2026-04-04

### Fixed

- Charge rate no longer transitions mid-period in InfluxDB. `_apply_period_schedule` is
  now called at the very start of `update_battery_schedule` (before optimization) so the
  inverter is updated immediately at the period boundary. Previously the call happened only
  after optimization + cloud API completed (up to several minutes late), causing InfluxDB
  to record a within-period 100%→0% transition that averaged to ~50% or ~33%.
- Power monitor `target_charging_power_pct` is now set exclusively by `_apply_period_schedule`
  and no longer overwritten every minute by `adjust_charging_power`. The stale-schedule
  overwrite was the second cause of mid-period charge rate flips.

## [7.9.48] - 2026-04-04

### Changed

- Power monitor now reads inverter control settings at 15-minute period resolution
  instead of hourly aggregation. Removed `hourly_settings` dict and
  `_calculate_hourly_settings_with_strategic_intents()` from `GrowattScheduleManager`.
  Replaced `get_hourly_settings()` with `get_period_control(period)` which directly
  returns `INTENT_TO_CONTROL` for the exact current period.

## [7.9.47] - 2026-04-04

### Fixed

- Power monitor no longer applies fuse-based charge rate limiting during non-charging
  modes (LOAD_SUPPORT, HOLD, EXPORT_ARBITRAGE, SOLAR_STORAGE, IDLE). Fuse protection
  now only active during GRID_CHARGING. Previously the power monitor could incorrectly
  reduce charge rate to ~50% during discharge modes when phase load happened to be high.

## [7.9.46] - 2026-04-03

### Added

- Decision Details table: two new DP diagnostic columns **Reward** and **V[t,i]**.
  Reward = per-period DP reward for the chosen action (`-(grid_import×inkoop − grid_export×verkoop + wear)`).
  V[t,i] = value function = reward + V[t+1, next_i], the total expected value from this state onward.
  Both values are taken from the planned DP optimization result. Shown in indigo; reward is
  red when negative.

## [7.9.45] - 2026-04-03

### Added

- Optional Modbus TOU control via SolaX Modbus integration. Set `modbus_tou_control: true`
  and `modbus_tou_entity_prefix` in config to write TOU segments via local Modbus instead
  of the Growatt cloud API. Eliminates ~1.5 minute cloud roundtrip delay. Cloud API remains
  as automatic fallback if Modbus write fails. Write order and diff logic unchanged.

## [7.9.44] - 2026-04-02

### Added

- HOLD is now blocked by the DP when solar is present (`> 0.01 kWh`). HOLD wastes all
  solar production, so the DP will now prefer IDLE, LOAD_SUPPORT, or SOLAR_STORAGE instead.

## [7.9.43] - 2026-04-02

### Fixed

- `grid_charge_max_solar_threshold_kwh` and `grid_charge_min_headroom_kwh` were not
  forwarded to `BatterySettings` via `_apply_settings`, so config changes had no effect
  and DP guards always used hardcoded defaults (0.1 and 0.9).

## [7.9.42] - 2026-04-02

### Fixed

- `idle_enabled` config option was not passed through `_apply_settings` to `BatterySettings`,
  so toggling it in the add-on UI had no effect. Now correctly forwarded as `idleEnabled`.

## [7.9.41] - 2026-04-02

### Added

- `idle_enabled` config option under `battery`: set to `false` to exclude IDLE from DP
  mode selection entirely. Default `true` preserves existing behaviour.

## [7.9.40] - 2026-04-01

### Fixed

- Buffer day trim now strips exactly 96 periods from the end of the optimization result
  instead of capping at 192. Mid-day optimization runs produce fewer than 3×96 periods
  (e.g. 205 at period 83), so the old `min(192, ...)` cap left buffer periods in the
  result and caused `Period index 192 beyond tomorrow` errors.

## [7.9.39] - 2026-04-01

### Changed

- DP horizon extended to 3 days (288 periods): a third buffer day (proxy = tomorrow's
  prices, consumption, and solar) is added so the DP has a real future beyond day 2.
  This prevents end-of-horizon battery drain to minimum SOC. The buffer day is never
  shown in the UI or applied to the schedule — result is trimmed to 192 periods after
  optimization. Terminal value reverted to 0.0 (buffer makes it redundant).

## [7.9.38] - 2026-04-01

### Fixed

- Terminal value now always calculated from median buy price, regardless of horizon length.
  Previously returned 0.0 when horizon extended past today, causing the DP to drain the
  battery to minimum SOC by end of horizon. Now correctly values remaining energy at
  end of 192-period horizon.

## [7.9.37] - 2026-04-01

### Fixed

- GRID_CHARGING headroom guard used undefined `eff_c` variable in `_run_dynamic_programming`,
  causing `NameError` and optimization failure. Replaced with `battery_settings.efficiency_charge`.

## [7.9.36] - 2026-04-01

### Removed

- IDLE solar-surplus guard (`solar_surplus > 0.05`) removed from DP backward induction.
  This guard was added without authorisation in v7.9.34 and incorrectly blocked IDLE
  from being considered during periods with solar surplus.

## [7.9.35] - 2026-04-01

### Added

- Two new battery config settings to control GRID_CHARGING behaviour:
  `grid_charge_max_solar_threshold_kwh` (default 0.1 kWh) blocks grid charge when solar
  production exceeds the threshold — solar alone can charge the battery.
  `grid_charge_min_headroom_kwh` (default 0.9 kWh) blocks grid charge when available
  battery space is less than this value — no room for a meaningful charge action.

## [7.9.34] - 2026-04-01

### Fixed

- DP state space discretisation now uses `linspace` instead of `arange`, guaranteeing exact
  `min_soe` and `max_soe` endpoints. Previously, floating-point overshoot in `arange` produced
  an extra SOE level above `max_soe` (e.g. 7.55 kWh on a 7.5 kWh battery = 100.7%), causing
  spurious small battery actions in periods immediately after a full charge.

## [7.9.33] - 2026-03-31

### Fixed

- DP no longer selects IDLE during solar surplus. When solar exceeds consumption by more than
  0.05 kWh per period, IDLE is excluded from the mode set and LOAD_SUPPORT is forced instead.
  Root cause: backward induction caused a convergence cascade — the optimal policy from the
  higher SOE state would also export future surplus (IDLE), collapsing the value difference
  between states to the opportunity-swap value (~0.04) rather than the full future discharge
  value (~0.07). Result was solar being exported at ~0.13 EUR/kWh instead of stored for
  discharge at 0.26+ EUR/kWh. Also removes the temporary DP-IDLE-WIN debug logging added in v7.9.32.

## [7.9.32] - 2026-03-31

### Added

- Temporary DP-IDLE-WIN debug logging to investigate IDLE mode selection during solar surplus
  (removed in v7.9.33).

## [7.9.31] - 2026-03-31

### Changed

- IDLE mode redesigned: battery is now fully passive (solar → load → grid, no battery action in DP).
  Hardware runs load_first with charge_rate=0%, discharge_rate=100%. The power monitor only
  intervenes when SOC drops more than `idle_deadband_pct` below the goal SOC, at which point it
  sets charge_rate=100% until SOC recovers. Discharge rate is never touched by the power monitor
  during IDLE.

## [7.9.30] - 2026-03-31

### Fixed

- DP now always plans a 192-period (48-hour) horizon. When tomorrow's prices are not yet
  published (typically before ~14:30 for NL Nordpool), today's prices are used as a proxy
  for tomorrow so the schedule always extends into the next day.

## [7.9.29] - 2026-03-31

### Fixed

- Tomorrow's planned periods (96–191 in the 192-period horizon) now appear under a correct
  date header in the Decision Details table. Previously all periods were assigned today's date,
  so the frontend date separator never triggered for tomorrow's entries.

## [7.9.28] - 2026-03-31

### Fixed

- `idle_deadband_pct` from config.yaml was never applied to the algorithm — `_apply_settings()`
  built the battery settings dict without it, so the DP always used the hardcoded default of 2%.
  Added `idleDeadbandPct` to the settings dict passed to `update_settings()`.
- `idleDeadbandPct` added to `/api/battery-settings` response so the live value is verifiable
  without restarting or reading logs.

## [7.9.27] - 2026-03-30

### Fixed

- SOLAR_STORAGE energy flow corrected to match battery_first hardware behavior: all solar goes
  directly to the battery (DC path) and all home load is supplied from the grid (AC path).
  Previously only solar surplus (solar − consumption) was stored, making SOLAR_STORAGE behave
  identically to IDLE without a deadband — causing the DP to undervalue it compared to IDLE.
- SOLAR_STORAGE cost basis now correctly attributes all battery charging to solar (zero grid
  component), so discharge profitability checks reflect the true cycle-cost-only cost basis.

## [7.9.26] - 2026-03-30

### Changed

- EXPORT_ARBITRAGE mode: only valid when available SOE >= one full period at max discharge power (`max_discharge_power_kw * 0.25h`). Prevents the DP from choosing export when the battery is nearly empty, preferring instead to support load with the remaining energy.

## [7.9.25] - 2026-03-30

### Changed

- IDLE mode: only valid when solar forecast > 0.01 kWh for that period. Without solar, IDLE degrades to slow battery drain with no meaningful benefit over HOLD or LOAD_SUPPORT. This eliminates nightly SOC drift caused by repeated small IDLE discharges.

## [7.9.24] - 2026-03-30

### Added

- BDC (Battery DC Converter) control during HOLD mode: when `bdc_switch` sensor is configured, `BDC Off` is sent on transition into HOLD to eliminate ~80W battery standby draw. `BDC On` is sent when leaving HOLD. Feature disabled by default (empty sensor key). Writes occur only on transitions to protect Modbus EEPROM from excessive wear.
- Preemptive BDC On: one minute before a HOLD→non-HOLD transition (detected via `minute % 15 == 14`), `BDC On` is sent early so the battery is ready at the period boundary (~45s startup time).

## [7.9.23] - 2026-03-29

### Changed

- DP optimizer: replaced continuous power steps (37 levels × 0.2 kW) with 6 discrete inverter modes: HOLD, IDLE, LOAD_SUPPORT, SOLAR_STORAGE, GRID_CHARGING, EXPORT_ARBITRAGE. Each mode maps directly to Growatt inverter settings.
- Battery control (dom instellen): every 15-min period, charge rate, discharge rate, and grid charge are unconditionally set from plan. No comparison with previous state.
- IDLE mode: SOE deadband enforcement added to power monitor. Blocks charge when SOE drifts above `soe_start + deadband`, blocks discharge when below `soe_start - deadband`. Restores full rates when SOE returns to baseline. Configurable via `idle_deadband_pct` (default 2%).
- Power monitor cadence: changed from every 5 minutes to every minute to support IDLE state machine.
- HOLD mode added: battery fully preserved (charge=0%, discharge=0%), solar and grid supply load directly.
- `INTENT_TO_CONTROL` revised to "dom instellen" principle: all modes use 100%/100% rates except HOLD (0%/0%). Actual behavior determined by Growatt battery mode.

## [7.9.21] - 2026-03-25

### Fixed

- Power monitor: when grid charge is off, charge rate is now set to the intended percentage (e.g. 100%) instead of being skipped. This ensures the inverter is correctly configured when grid charging activates on the next schedule update.

## [7.9.20] - 2026-03-25

### Added

- Power monitor: `inverter_phase` config option (L1/L2/L3) to specify which phase a single-phase inverter is connected to. When set, only that phase is monitored and battery max power is not divided across phases. When empty, existing 3-phase logic applies.
- Power monitor: optional `voltage_l1/l2/l3` sensor config for live per-phase voltage readings. When configured, wattage is calculated as live V × A instead of fixed 230V × A. Falls back to configured `voltage` value when sensor is unavailable.

### Fixed

- Power monitor: `max_charge_discharge_power` config key now correctly maps to `max_charge_power_kw` and `max_discharge_power_kw`. Previously the key mismatch caused the default 15kW to be used instead of the configured value.

## [7.9.19] - 2026-03-25

### Fixed

- Decision Details Chg%/Dchg% actual: fill(previous) with seed was not applied when InfluxDB returned empty response for today (no data yet). Early return on missing header now skipped so seed values always propagate to all periods.

## [7.9.18] - 2026-03-25

### Fixed

- Decision Details Chg%/Dchg% actual: sensors absent from today's data were skipped in fill(previous) loop even when seed values were available.

## [7.9.17] - 2026-03-25

### Fixed

- Decision Details Chg%/Dchg% actual: seed fill(previous) with last known value before midnight so all periods from 00:00 show correct actual rates even when no change event occurred today.
- Decision Details Chg%/Dchg% actual: fill(previous) now carries the last raw value of a period forward (not the mean), so subsequent empty periods correctly reflect the final inverter setting.

## [7.9.16] - 2026-03-24

### Fixed

- Battery Settings card: Charge Power Rate now shows live HA sensor value instead of hardcoded 40% default.

## [7.9.15] - 2026-03-24

### Fixed

- Decision Details table: Chg%/Dchg% actual values now use fill(previous) so periods without a new InfluxDB write carry the last known value forward.
- Decision Details table: 0% now displays as `0` instead of `—` in Chg%/Dchg% columns.
- Decision Details table: plan value always shown in grey even when actual is absent.
- HistoricalDataStore now persists to `/data/historical_store.json` — plan/actual data survives restarts. Version mismatch wipes the file and starts fresh.

## [7.9.14] - 2026-03-24

### Changed

- Decision Details table: removed InfluxDB backfill on startup. Actual values now only appear as periods complete in real-time. Data accumulated during the day is preserved across midnight via the history rollover, retained for `history_days` days.

## [7.9.13] - 2026-03-24

### Fixed

- Decision Details table: header row now stays visible while scrolling (sticky header with vertical scroll container, max height 600px).

## [7.9.12] - 2026-03-24

### Fixed

- Suppress noisy "Energy stored mismatch" warnings in DP algorithm for near-zero power states (false positives during optimization state exploration).

## [7.9.11] - 2026-03-24

### Removed

- `net_grid_power` sensor logic removed: `get_net_grid_power()` method, METHOD_SENSOR_MAP entry, `netGridPower` in API response, and TypeScript interface fields. Grid import/export accuracy is now achieved by configuring P1 meter cumulative energy sensors in `lifetime_import_from_grid` / `lifetime_export_to_grid`.

## [7.9.10] - 2026-03-23

### Added

- Decision Details table: plan vs actual comparison for SOE end %, consumption, costs, baseline, and savings columns
- Decision Details table: actual charge/discharge rate % from InfluxDB (when entities are configured)
- Decision Details table: multi-day history view (configurable via `history_days` setting)
- Decision Details table: currency column headers now use configured currency instead of hardcoded SEK
- SOE columns now display in % instead of kWh for easier reading
- Date separator rows in Decision Details table for multi-day view

### Fixed

- Actual values (actualGridImported etc.) now always shown for past periods, not only when planned snapshot exists
- `missing` data source correctly shown in Decision Details table

## [7.9.9] - 2026-03-23

### Fixed

- Startup backfill no longer leaves gaps in historical data when a cumulative energy sensor has a brief data gap in InfluxDB; power sensors (W→kWh) are now used as fallback.
- If both cumulative and power sensors are unavailable for a period, a zero-energy placeholder (`data_source="missing"`) is stored so the period is never reported as missing.
- "Incomplete Historical Data" warning no longer appears when only a single quarter within an hour is missing (was a false alarm in most cases).
- `historical-data-status` API: an hour is now only reported as completed when all four quarters are present; previously an hour could appear in both `missingHours` and `completedHours` simultaneously.

## [7.9.8] - 2026-03-21

### Added

- Decision Details table shows plan vs. actual comparison for past periods: planned intent/action alongside observed values so deviations are immediately visible.

## [7.9.7] - 2026-03-20

### Added

- Plan/actual comparison columns added to the Decision Details table.

## [7.9.6] - 2026-03-19

### Fixed

- IDLE strategic intent: `discharge_rate` reverted to 0 in all four code locations where it was incorrectly set to 100. Version bump forces fresh Docker build in HA Supervisor.

## [7.9.5] - 2026-03-14

### Added

- Configurable consumption forecast strategy via `home.consumption_strategy`: `sensor` (default, HA 48h average), `fixed` (flat rate from config), or `influxdb_7d_avg` (7-day rolling average from InfluxDB power sensor data at 15-minute resolution). (thanks [@pookey](https://github.com/pookey))

## [7.9.4] - 2026-03-14

### Changed

- HA API retries now use exponential backoff (2s, 4s, 8s) instead of a fixed 4-second delay. (thanks [@pookey](https://github.com/pookey))
- TOU segment write failures now include a descriptive operation string and the HTTP response body for actionable diagnostics. (thanks [@pookey](https://github.com/pookey))

### Fixed

- Unavailable or unknown HA sensors now return `None` instead of 0.0, preventing zero values from corrupting optimization. (thanks [@pookey](https://github.com/pookey))
- Inverter page no longer blanks when a single API endpoint fails on startup. (thanks [@pookey](https://github.com/pookey))

## [7.9.3] - 2026-03-13

### Added

- Expired TOU intervals shown with reduced opacity, strikethrough times, and an "Expired" badge in the inverter schedule view. (thanks [@pookey](https://github.com/pookey))
- "Pending Write" amber badge on the inverter page for TOU segments queued but not yet written to hardware. (thanks [@pookey](https://github.com/pookey))

### Changed

- TOU schedule now uses a rolling window: only future periods generate segments, freeing hardware slots during mid-day re-optimizations. (thanks [@pookey](https://github.com/pookey))
- TOU segment IDs are stable across re-optimizations, preventing hardware slot divergence and overlap warnings. (thanks [@pookey](https://github.com/pookey))
- When >9 TOU segments are generated, all are kept in memory and the next 9 non-expired are written to hardware; pending segments cascade into freed slots on the next cycle. (thanks [@pookey](https://github.com/pookey))

### Fixed

- Schedule creation crash when optimization produces more than 9 TOU segments. (thanks [@pookey](https://github.com/pookey))
- KeyError when building stable segment IDs from intervals that had not yet been written to hardware. (thanks [@pookey](https://github.com/pookey))

## [7.8.1] - 2026-03-12

### Fixed

- Battery Mode Schedule tooltip showing incorrect times for sub-hour slot boundaries (e.g. 22:30 displayed as 22:00). (thanks [@pookey](https://github.com/pookey))
- Current-time marker on Battery Mode Schedule positioned at start of hour regardless of minutes elapsed. (thanks [@pookey](https://github.com/pookey))

## [7.8.0] - 2026-03-10

### Added

- Configurable single/three-phase electricity support via `home.phase_count` (1 or 3, default 3); fixes fuse protection for single-phase systems (common in the UK). (thanks [@pookey](https://github.com/pookey))

### Fixed

- `max_fuse_current`, `voltage`, and `safety_margin_factor` from config.yaml were not being applied — power monitor always ran on hardcoded defaults. (thanks [@pookey](https://github.com/pookey))

## [7.7.1] - 2026-03-10

### Fixed

- Add-on no longer discoverable from GitHub due to invalid `list?` schema type in `config.yaml`. Removed `derating_curve` from schema validation (HA Supervisor does not support nested list types).

## [7.7.0] - 2026-03-09

### Added

- Temperature-based charge power derating for outdoor batteries, using HA weather forecast to apply per-period charge limits via a configurable LFP derating curve. Opt-in via `battery.temperature_derating.enabled` in config.yaml. (thanks [@pookey](https://github.com/pookey))

## [7.6.2] - 2026-03-07

### Changed

- Profitability gate threshold now scales with remaining horizon (`max(15%, remaining/total)`) so mid-day optimizer runs are not held to a full-day savings bar.

## [7.6.1] - 2026-03-07

### Fixed

- Chart dark mode detection now tracks the `dark` CSS class on `<html>` via MutationObserver instead of OS `prefers-color-scheme`, correctly following Tailwind's `class` strategy.
- Axis tick label colors, grid lines, and price line now render correctly in dark mode.

### Changed

- Vite dev proxy target can be overridden via `VITE_API_TARGET` environment variable.

## [7.6.0] - 2026-03-07

### Added

- Battery Mode Schedule timeline on the Dashboard page, showing a color-coded horizontal bar of strategic intents (Grid Charging, Solar Storage, Load Support, Export Arbitrage, Idle) with hover tooltips, current-hour marker, and tomorrow's plan faded when available. (thanks [@pookey](https://github.com/pookey))

## [7.5.0] - 2026-03-07

### Added

- Timezone is now read automatically from Home Assistant's `/api/config` at startup instead of being hardcoded to `Europe/Stockholm`. Falls back to `Europe/Stockholm` with a warning if HA is unreachable. (thanks [@pookey](https://github.com/pookey))

## [7.4.5] - 2026-03-07

### Fixed

- Startup data collection for the last completed period used live sensors instead of InfluxDB, causing inflated values (e.g. ~2x) and leaving the next period nearly empty on the chart. (thanks [@pookey](https://github.com/pookey))
- Chart price line now shows visual gaps instead of dropping to zero when price data is unavailable.
- BatteryLevelChart SOC line no longer shows a flat 0% line for predicted hours with no data.

## [7.4.4] - 2026-03-07

### Fixed

- Chart grid lines now use `prefers-color-scheme` media query for dark mode detection, matching Tailwind's `media` strategy. Previously, charts used a DOM class check that detected Home Assistant's dark mode theme even when BESS UI was rendering in light mode, causing dark grid lines on a white background.

## [7.4.3] - 2026-03-07

### Fixed

- Visual improvements and alignment across EnergyFlowChart and BatteryLevelChart: predicted hours grey overlay added to BatteryLevelChart to match EnergyFlowChart, both charts now show a subtle grey background for tomorrow's data with a solid divider line at midnight.
- BatteryLevelChart tooltip now handles N/A values correctly and suppresses hover on the zero-anchor phantom point.
- Fixed `-0` display in battery action tooltip (now shows `0`).

## [7.4.2] - 2026-03-07

### Fixed

- EnergyFlowChart and BatteryLevelChart data now aligned to period start, eliminating one-period misalignment caused by a fake zero-point offset. (thanks [@pookey](https://github.com/pookey))
- Electricity price line now renders as a step function instead of smooth interpolation.
- Predicted hours shading now uses Recharts ReferenceArea instead of a raw SVG rect that rendered at incorrect coordinates.
- Tomorrow period numbers normalised correctly when API returns them as 96-191 continuation.
- X-axis tick labels use modulo 24 for clean hour display across the day boundary.

## [7.4.1] - 2026-03-07

### Fixed

- Terminal value calculation now uses the median of remaining buy prices instead of the average, preventing peak prices from inflating the estimate and causing the optimizer to hold charge instead of discharging during high-price periods. (thanks [@pookey](https://github.com/pookey))

## [7.4.0] - 2026-03-06

### Changed

- Currency is now configurable throughout the optimization pipeline and UI; removed hardcoded SEK/Swedish locale references. (thanks [@pookey](https://github.com/pookey))

## [7.3.0] - 2026-03-04

### Added

- Extended optimization horizon to 2 days when tomorrow's prices are available, enabling true cross-day arbitrage decisions. Only today's schedule is deployed to the inverter. (thanks [@pookey](https://github.com/pookey))
- Terminal value fallback when tomorrow's prices aren't yet published, preventing the optimizer from treating stored battery energy as worthless at end of day.
- Tomorrow's solar forecast support via Solcast `solar_forecast_tomorrow` sensor.
- Dashboard, Inverter, and Savings pages show tomorrow's planned schedule when available.
- DST-safe period-to-timestamp conversion throughout.

### Fixed

- Economic summary and profitability gate now scoped to today-only periods, preventing inflated savings figures when the horizon extends into tomorrow.

## [7.2.0] - 2026-03-02

### Changed

- DP optimizer assigns terminal value to stored battery energy at end of horizon, preventing premature end-of-day export.

## [7.1.1] - 2026-03-02

### Fixed

- Battery SOC no longer shows impossible values (e.g. 168%) when battery capacity differs from the 30 kWh default. `SensorCollector`, `EnergyFlowCalculator`, and `HistoricalDataStore` were initialised with the default capacity and only received the configured value via manual propagation in `update_settings()`. They now hold a shared `BatterySettings` reference so the configured capacity is always used for SOC-to-SOE conversion.

## [7.1.0] - 2026-03-01

Thanks to [@pookey](https://github.com/pookey) for contributing this fix (PR #20).

### Fixed

- InfluxDB CSV parsing now uses header-aware column detection instead of hardcoded indices, supporting both InfluxDB 1.x and 2.x where columns appear at different positions depending on version and tag configuration. Queries also match on both `_measurement` and `entity_id` tag to handle both data models.
- Historical data no longer lost after restart. A sensor name prefix mismatch in the batch query parser caused initial-value lookups to create duplicate entries that overwrote correct per-period values during normalization, producing flat SOC and zero energy deltas across the entire day.

## [7.0.0] - 2026-03-01

Thanks to [@pookey](https://github.com/pookey) for contributing Octopus Energy support (PR #19).

### Added

- Octopus Energy Agile tariff support as a new price source alongside Nordpool. Fetches import and export rates from Home Assistant event entities at 30-minute resolution with VAT-inclusive GBP/kWh prices.
- Separate import and export rate entities for Octopus Energy, allowing direct sell price data instead of calculated fallback.
- `get_sell_prices_for_date()` on `PriceSource` for sources that provide direct export/sell rates.
- `PriceManager.clear_cache()` to propagate settings changes at runtime without restart.
- Documentation for Octopus Energy setup in README, Installation Guide, and User Guide.
- UPGRADE.md with step-by-step migration instructions for the breaking config change.

### Changed

- **Breaking:** Unified energy provider configuration into a single `energy_provider:` section. The previous `nordpool:` top-level section and `nordpool_kwh_today`/`nordpool_kwh_tomorrow` sensor entries have been replaced. See [UPGRADE.md](UPGRADE.md) for migration instructions.
- Price logging now uses currency-neutral column headers instead of hardcoded "SEK".
- `HomeAssistantSource` now takes entity IDs directly via constructor instead of looking them up from the sensor map.
- Pricing parameters (markup, VAT, additional costs) now propagate immediately when updated via settings without requiring a restart.

### Removed

- `use_official_integration` boolean from config (replaced by `energy_provider.provider` field).
- `nordpool_kwh_today`/`nordpool_kwh_tomorrow` from `sensors:` section (moved to `energy_provider.nordpool`).
- Dead code: `LegacyNordpoolSource` class and unused Nordpool price methods from `ha_api_controller.py`.

### Fixed

- Grid charging now always charges at full power (100%) instead of being throttled to the DP algorithm's planned kW. The DP power level is an energy model artifact, not a hardware rate limit — the power monitor already handles fuse protection correctly. Previously, `hourly_settings` stored a proportional rate (e.g. 25% when the DP planned 1.5 kW out of 6 kW max), causing the inverter to charge far slower than it should during cheap price periods.
- Removed dead `charge_rate` local variable from `_apply_period_schedule` which was computed but never applied to hardware, eliminating the misleading split-brain between two code paths.

## [6.0.7] - 2026-03-01

### Fixed

- Grid charging now always charges at full power (100%) instead of being throttled to the DP algorithm's planned kW. The DP power level is an energy model artifact, not a hardware rate limit — the power monitor already handles fuse protection correctly. Previously, `hourly_settings` stored a proportional rate (e.g. 25% when the DP planned 1.5 kW out of 6 kW max), causing the inverter to charge far slower than it should during cheap price periods.
- Removed dead `charge_rate` local variable from `_apply_period_schedule` which was computed but never applied to hardware, eliminating the misleading split-brain between two code paths.

## [6.0.6] - 2026-02-26

### Fixed

- Historical data no longer shows as missing all day when InfluxDB is configured with InfluxDB 1.x (accessed via v2 compatibility API). The Flux query previously included a `domain == "sensor"` tag filter that is absent in 1.x setups, causing the batch query to silently return zero rows. The `_measurement` filter already uniquely identifies sensors, making the domain filter redundant.
- Batch sensor data that loads successfully but returns no periods is no longer cached, allowing the system to retry on the next 15-minute period rather than remaining stuck with an empty cache for the entire day.

## [6.0.5] - 2026-02-18

### Fixed

- System no longer crashes at startup if the inverter is temporarily unreachable when syncing SOC limits. A warning is logged and startup continues normally; the inverter retains its previous limits.

## [6.0.4] - 2026-02-08

### Added

- Compact mode for debug data export - reduces export size by including only latest schedule/snapshot and last 2000 log lines
- `compact` query parameter on `/api/export-debug-data` endpoint (defaults to `true`)

### Changed

- MCP server `fetch_live_debug` now uses `compact` parameter instead of `save_locally`
- Increased MCP server fetch timeout from 60s to 90s for large exports
- Raised `min_action_profit_threshold` default from 5.0 to 8.0 SEK

### Fixed

- Corrected `lifetime_load_consumption` sensor name in config.yaml (was pointing to daily sensor instead of lifetime)

## [6.0.0] - 2026-02-01

### Changed

- TOU scheduling now uses 15-minute resolution instead of hourly aggregation
- Eliminates "charging gaps" where minority intents were lost due to hourly majority voting
- Each 15-minute strategic intent period now directly maps to TOU segments
- Schedule comparison uses minute-level precision for accurate differential updates

### Added

- `_group_periods_by_mode()` groups consecutive 15-min periods by battery mode
- `_groups_to_tou_intervals()` converts period groups to Growatt TOU intervals
- `_enforce_segment_limit()` handles 9-segment hardware limit using duration-based priority
- DST handling for fall-back scenarios (100 periods) with proper time capping

### Fixed

- Single strategic period (e.g., 15-min GRID_CHARGING) now creates TOU segment instead of being outvoted
- Overlap detection uses minute-level precision instead of hour-level

## [5.7.0] - 2026-01-31

### Added

- MCP server for BESS debug log analysis - enables Claude Code to fetch and analyze debug logs directly
- Token-based authentication for debug export API endpoint (for external/programmatic access)
- `.bess-logs/` directory for cached debug logs (gitignored)

### Changed

- SSL certificate verification enabled by default for MCP server connections (security improvement)
- Optional `BESS_SKIP_SSL_VERIFY=true` environment variable for local self-signed certificates

## [5.6.0] - 2026-01-27

General release consolidating recent fixes.

## [5.5.0] - 2026-01-27

### Fixed

- Cost basis calculation now correctly accounts for pre-existing battery energy

## [5.4.0] - 2026-01-26

### Added

- InfluxDB bucket now configurable by end user in config.yaml

## [5.3.1] - 2026-01-23

### Fixed

- Improved sensor value handling in EnergyFlowCalculator

## [5.3.0] - 2026-01-22

### Changed

- Updated safety margin to 100%
- Removed "60 öringen" threshold
- Removed step-wise power adjustments

## [5.2.0] - 2026-01-22

General release consolidating v5.1.x fixes.

## [5.1.7] - 2026-01-18

### Fixed

- Missing period handling when HA sensors unavailable
- DailyViewBuilder now creates placeholder periods instead of skipping them when sensor data is unavailable (e.g., HA restart)
- Snapshot comparison API no longer crashes with IndexError

### Added

- `_create_missing_period()` to create placeholders with `data_source="missing"`
- Recovery of planned intent from persisted storage when available
- `missing_count` field in DailyView for transparency

## [5.1.6] - 2026-01-18

### Changed

- Refactored strategic intent to use economics-based decisions
- Strategic intent now derived from economic analysis rather than inferred from energy flows
- Prevents feedback loop where observed exports were incorrectly classified as EXPORT_ARBITRAGE

## [5.1.5] - 2026-01-17

### Fixed

- Fixed floating-point precision issue in DP algorithm where near-zero power levels (e.g., 2.2e-16) were incorrectly classified as charging/discharging instead of IDLE
- Fixed edge case in optimization where no valid action at boundary states (e.g., max SOE with unprofitable discharge) would leave period data undefined, now creates proper IDLE state
- Fixed `grid_to_battery` energy flow calculation to be correctly constrained by actual battery charging amount, preventing impossible energy flows

## [2.5.7] - 2025-11-10

### Fixed

- Fixed critical bug where invalid estimatedConsumption field in battery settings prevented all settings from being applied
- Fixed settings failures silently continuing with defaults instead of failing explicitly
- Currency and other user configuration now properly applied on startup

### Changed

- Settings application now fails fast with clear error message when configuration is invalid
- Removed estimatedConsumption from internal battery settings (now computed on-demand for API responses only)

## [2.5.5] - 2025-11-07

### Fixed

- Fixed initial_cost_basis returning 0.0 when battery at reserved capacity, causing irrational grid charging at high prices
- Fixed settings not updating from config.yaml due to camelCase/snake_case mismatch in update() methods
- Fixed dict-ordering bug where max_discharge_power_kw would be overwritten by max_charge_power_kw depending on key order
- Added explicit AttributeError for invalid setting keys instead of silent failures

### Changed

- Settings classes now convert camelCase API keys to snake_case attributes automatically
- Removed silent hasattr() checks in favor of explicit error handling
- Added Git Commit Policy to CLAUDE.md documentation

## [2.5.4] - 2025-11-07

### Fixed

- Fixed test mode to properly block all hardware write operations using "deny by default" pattern
- Fixed duplicate config.yaml files - now single source of truth in repository root
- Removed unused ac_power sensor configuration

### Changed

- Test mode now controlled via HA_TEST_MODE environment variable instead of hardcoded
- Updated docker-compose.yml to mount root config.yaml for development
- Updated deploy.sh and package-addon.sh to use root config.yaml

## [2.5.3] - 2025-11-06

### Fixed

- Fixed HACS/GitHub repository installation by restructuring to single add-on layout
- Moved add-on configuration files (config.yaml, Dockerfile, build.json, DOCS.md) to repository root
- Removed unnecessary bess_manager/ subdirectory (proper for single add-on repositories)
- Dockerfile now correctly references backend/, core/, and frontend/ from repository root
- Build context is now repository root, allowing direct access to all source directories

## [2.5.2] - 2024-11-06

### Added

- Home Assistant add-on repository support for direct GitHub installation
- Multi-architecture build configuration (aarch64, amd64, armhf, armv7, i386)
- repository.json for Home Assistant repository validation

### Fixed

- Removed duplicate config.yaml and run.sh files (now using symlinks)
- Removed duplicate CHANGELOG.md from bess_manager directory
- Fixed deploy.sh to work with symlinked configuration files

### Changed

- Restructured repository to comply with Home Assistant add-on store requirements

## [2.5.0] - 2024-10

- Quarterly resolution support for Nordpool integration
- Improved price data handling and metadata architecture

## [2.4.0] - 2024-10

- Added warning banner for missing historical data
- Added optimization start from below minimum SOC with warning
- Fixed savings and grid import columns in savings view

## [2.3.0] and Earlier

For earlier version history, see the [commit history](https://github.com/johanzander/bess-manager/commits/main/).
