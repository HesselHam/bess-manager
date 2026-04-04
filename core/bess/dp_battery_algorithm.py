"""
Dynamic Programming Algorithm for Battery Energy Storage System (BESS) Optimization.

This module implements a sophisticated dynamic programming approach to optimize battery
dispatch decisions over a 24-hour horizon, considering time-varying electricity prices,
solar production forecasts, and home consumption patterns.

UPDATED: Now captures strategic intent at decision time rather than analyzing flows afterward.

ALGORITHM OVERVIEW:
The optimization uses backward induction dynamic programming to find the globally optimal
battery charging and discharging schedule. At each hour, the algorithm evaluates all
possible battery actions (charge/discharge/hold) and selects the one that minimizes
total cost over the remaining time horizon.

KEY FEATURES:
- 24-hour optimization horizon with perfect foresight
- Cost basis tracking for stored energy (FIFO accounting)
- Profitability checks to prevent unprofitable discharging
- Minimum profit threshold system to prevent excessive cycling for low-profit actions
- Multi-objective optimization: cost minimization + battery longevity
- Simultaneous energy flow optimization across multiple sources/destinations
- Strategic intent capture at decision time for transparency and hardware control

MINIMUM PROFIT THRESHOLD SYSTEM:
The minimum profit threshold prevents unprofitable battery operations through a post-optimization profitability gate.
After optimization completes, the total savings are compared against an effective threshold derived from the configured
value scaled proportionally to the remaining horizon fraction:

    effective_threshold = min_action_profit_threshold * max(THRESHOLD_HORIZON_FLOOR, horizon / total_periods)

- If total_savings >= effective_threshold: Execute the optimized schedule
- If total_savings < effective_threshold: Reject optimization and use all-IDLE schedule (do nothing)

The scaling ensures the bar is proportional to how much of the day remains. A run at midnight faces the full threshold;
a run at 20:00 with only 4 hours left faces roughly 1/6 of it. Without scaling, late-day runs are held to an
unreachable standard and legitimate evening discharge opportunities get blocked.

THRESHOLD_HORIZON_FLOOR (0.15) prevents the effective threshold from collapsing to near-zero at end of day, which
would allow the battery to cycle for trivially small gains in the final hour or two.

Configurable via battery.min_action_profit_threshold in config.yaml (in your currency).
Example: a threshold of 8.0 at 16:00 (8/24 remaining) becomes an effective threshold of 8.0 * 0.33 = 2.67

STRATEGIC INTENT CAPTURE:
The algorithm now captures the strategic reasoning behind each decision:
- GRID_CHARGING: Storing cheap grid energy for arbitrage
- SOLAR_STORAGE: Storing excess solar for later use
- LOAD_SUPPORT: Discharging to meet home load
- EXPORT_ARBITRAGE: Discharging to grid for profit
- IDLE: No significant activity

ENERGY FLOW MODELING:
The algorithm models complex energy flows where multiple sources can serve multiple
destinations simultaneously:
- Solar → {Home, Battery, Grid Export}
- Battery → {Home, Grid Export}
- Grid → {Home, Battery Charging}

OPTIMIZATION OBJECTIVES:
1. Primary: Minimize total electricity costs over 24-hour period
2. Secondary: Minimize battery degradation through cycle cost modeling
3. Constraints: Physical battery limits, efficiency losses, minimum SOC

RETURN STRUCTURE:
The algorithm returns comprehensive results including:
- Optimal battery actions for each hour
- Strategic intent for each decision
- Detailed energy flow breakdowns showing where each kWh flows
- Economic analysis comparing different scenarios
- All data needed for hardware implementation and performance analysis
"""

__all__ = [
    "optimize_battery_schedule",
    "print_optimization_results",
]


import logging
from enum import Enum

import numpy as np

from core.bess.decision_intelligence import create_decision_data
from core.bess.models import (
    DecisionData,
    EconomicData,
    EconomicSummary,
    EnergyData,
    OptimizationResult,
    PeriodData,
)
from core.bess.settings import BatterySettings

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Algorithm parameters
SOE_STEP_KWH = 0.1

# Discrete modes for battery control — replaces continuous power levels
MODES = [
    "HOLD",             # load_first, charge=0%, discharge=0%: solar wasted, all load from grid
    "IDLE",             # load_first, charge=100%, discharge=100%: small fluctuations within deadband
    "LOAD_SUPPORT",     # load_first, charge=100%, discharge=100%: battery supports home load
    "SOLAR_STORAGE",    # battery_first, charge=100%, discharge=100%: store solar energy
    "GRID_CHARGING",    # battery_first, grid_charge=True, charge=100%, discharge=100%: charge from grid
    "EXPORT_ARBITRAGE", # grid_first, charge=100%, discharge=100%: sell stored energy
]


class StrategicIntent(Enum):
    """Strategic intents for battery actions, determined at decision time."""

    # Primary intents (mutually exclusive)
    HOLD = "HOLD"  # No battery action, solar wasted
    GRID_CHARGING = "GRID_CHARGING"  # Storing cheap grid energy for arbitrage
    SOLAR_STORAGE = "SOLAR_STORAGE"  # Storing excess solar for later use
    LOAD_SUPPORT = "LOAD_SUPPORT"  # Discharging to meet home load
    EXPORT_ARBITRAGE = "EXPORT_ARBITRAGE"  # Discharging to grid for profit
    IDLE = "IDLE"  # Small fluctuations within deadband


def _discretize_state_space(battery_settings: BatterySettings) -> np.ndarray:
    """Return discretized SOE levels for DP state space.

    Uses linspace instead of arange to guarantee exact min/max endpoints,
    avoiding floating-point overshoot (e.g. 7.55 kWh on a 7.5 kWh battery)
    that caused SOE > max_soe states and incorrect battery actions.
    """
    n = round((battery_settings.max_soe_kwh - battery_settings.min_soe_kwh) / SOE_STEP_KWH)
    return np.round(
        np.linspace(battery_settings.min_soe_kwh, battery_settings.max_soe_kwh, n + 1),
        decimals=6,
    )


def _calculate_mode_energy_flows(
    mode: str,
    soe: float,
    solar: float,
    consumption: float,
    battery_settings: BatterySettings,
    dt: float,
    max_charge_power_override: float | None = None,
) -> tuple[float, float, float, float, float]:
    """Calculate energy flows for a given mode.

    Args:
        mode: One of MODES
        soe: Current state of energy (kWh)
        solar: Solar production this period (kWh)
        consumption: Home consumption this period (kWh)
        battery_settings: Battery configuration
        dt: Period duration in hours
        max_charge_power_override: Temperature derating limit (kW), or None

    Returns:
        (battery_charge, battery_discharge, grid_imported, grid_exported, next_soe)
        battery_charge: energy throughput into battery (kWh)
        battery_discharge: energy throughput out of battery (kWh)
        grid_imported: energy drawn from grid (kWh)
        grid_exported: energy sent to grid (kWh)
        next_soe: battery state of energy after period (kWh)
    """
    eff_c = battery_settings.efficiency_charge
    eff_d = battery_settings.efficiency_discharge
    max_c_kw = min(
        battery_settings.max_charge_power_kw,
        max_charge_power_override if max_charge_power_override is not None else battery_settings.max_charge_power_kw,
    )
    max_d_kw = battery_settings.max_discharge_power_kw

    # Max energy that can flow through battery this period
    max_charge = min(max_c_kw * dt, (battery_settings.max_soe_kwh - soe) / eff_c)
    max_discharge = min(max_d_kw * dt, (soe - battery_settings.min_soe_kwh) * eff_d)

    if mode == "HOLD":
        # AC output silent (discharge=0%), charge=0%: solar wasted, all load from grid
        battery_charge = 0.0
        battery_discharge = 0.0
        grid_imported = consumption
        grid_exported = 0.0

    elif mode == "IDLE":
        # Battery passive: solar → load → grid, no battery action
        battery_charge = 0.0
        battery_discharge = 0.0
        grid_imported = max(0.0, consumption - solar)
        grid_exported = max(0.0, solar - consumption)

    elif mode == "LOAD_SUPPORT":
        # Battery discharges to support load; solar surplus charges battery
        solar_to_load = min(solar, consumption)
        remaining_load = max(0.0, consumption - solar_to_load)

        battery_discharge = min(remaining_load, max_discharge)
        grid_imported = max(0.0, remaining_load - battery_discharge)

        solar_surplus = max(0.0, solar - consumption)
        battery_charge = min(solar_surplus, max_charge)
        grid_exported = max(0.0, solar_surplus - battery_charge)

    elif mode == "SOLAR_STORAGE":
        # battery_first: ALL solar → battery (DC path), ALL load ← grid (AC path)
        battery_charge = min(solar, max_charge)
        battery_discharge = 0.0

        grid_imported = consumption
        grid_exported = max(0.0, solar - battery_charge)

    elif mode == "GRID_CHARGING":
        # Charge from grid + solar at max rate (grid_charge=True)
        battery_charge = max_charge  # Full rate, limited by capacity and derating
        solar_surplus = max(0.0, solar - consumption)
        solar_to_battery = min(solar_surplus, battery_charge)
        grid_to_battery = battery_charge - solar_to_battery

        solar_to_load = min(solar, consumption)
        grid_imported = max(0.0, consumption - solar_to_load) + grid_to_battery
        grid_exported = max(0.0, solar_surplus - solar_to_battery)
        battery_discharge = 0.0

    elif mode == "EXPORT_ARBITRAGE":
        # Hybrid inverter: AC output = solar + battery, capped at max_discharge.
        # Solar fills part of the inverter's capacity; battery provides the remainder.
        battery_discharge = max(0.0, max_discharge - solar)

        # Solar beyond inverter capacity could charge battery; in practice always 0
        # because DP chooses SOLAR_STORAGE when solar > max_discharge.
        solar_to_battery = max(0.0, solar - max_discharge)
        battery_charge = min(solar_to_battery, max_charge)

        inverter_output = battery_discharge + solar - solar_to_battery  # = max_discharge
        grid_imported = max(0.0, consumption - inverter_output)
        grid_exported = max(0.0, inverter_output - consumption)

    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Compute next SOE from actual flows
    next_soe = soe + battery_charge * eff_c - battery_discharge / eff_d
    next_soe = min(battery_settings.max_soe_kwh, max(battery_settings.min_soe_kwh, next_soe))

    return battery_charge, battery_discharge, grid_imported, grid_exported, next_soe


def _calculate_reward(
    mode: str,
    battery_charge: float,
    battery_discharge: float,
    grid_imported: float,
    grid_exported: float,
    soe: float,
    next_soe: float,
    period: int,
    home_consumption: float,
    battery_settings: BatterySettings,
    buy_price: list[float],
    sell_price: list[float],
    solar_production: float,
    cost_basis: float,
    currency: str,
) -> tuple[float, float, PeriodData]:
    """
    Calculate reward for a given mode with cycle cost accounting and profitability checks.

    CYCLE COST POLICY:
    - Applied only to charging operations (not discharging)
    - Applied to energy actually stored (after efficiency losses)
    - Cost basis includes BOTH grid costs AND cycle costs for profitability analysis

    PROFITABILITY CHECK:
    - Applied only to LOAD_SUPPORT and EXPORT_ARBITRAGE (discharge modes)
    - Value = max(avoiding grid purchases, grid export revenue) per kWh stored
    - Discharge blocked (returns -inf) if value <= cost_basis
    """

    current_buy_price = buy_price[period]
    current_sell_price = sell_price[period]

    # Snap next_soe to the DP grid so battery_soe_end matches battery_soe_start of the
    # next period exactly, eliminating the display gap caused by quantization rounding.
    _max_i = round((battery_settings.max_soe_kwh - battery_settings.min_soe_kwh) / SOE_STEP_KWH)
    _snapped_i = min(max(0, round((next_soe - battery_settings.min_soe_kwh) / SOE_STEP_KWH)), _max_i)
    snapped_next_soe = battery_settings.min_soe_kwh + _snapped_i * SOE_STEP_KWH

    energy_data = EnergyData(
        solar_production=solar_production,
        home_consumption=home_consumption,
        battery_charged=battery_charge,
        battery_discharged=battery_discharge,
        grid_imported=grid_imported,
        grid_exported=grid_exported,
        battery_soe_start=soe,
        battery_soe_end=snapped_next_soe,
    )

    # ============================================================================
    # BATTERY CYCLE COST CALCULATION
    # ============================================================================
    energy_stored = 0.0
    if battery_charge > 0.0:
        energy_stored = battery_charge * battery_settings.efficiency_charge
        battery_wear_cost = energy_stored * battery_settings.cycle_cost_per_kwh

        expected_stored = next_soe - soe
        if battery_charge > 0.01 and abs(energy_stored - expected_stored) > 0.01:
            logger.warning(
                f"Energy stored mismatch: calculated={energy_stored:.3f}, "
                f"SOE delta={expected_stored:.3f}"
            )
    else:
        battery_wear_cost = 0.0

    # ============================================================================
    # PROFITABILITY CHECK (discharge modes only)
    # ============================================================================
    if mode in ("LOAD_SUPPORT", "EXPORT_ARBITRAGE") and battery_discharge > 0.01:
        avoid_purchase_value = current_buy_price * battery_settings.efficiency_discharge
        export_value = current_sell_price * battery_settings.efficiency_discharge
        effective_value_per_kwh_stored = max(avoid_purchase_value, export_value)

        if effective_value_per_kwh_stored <= cost_basis:
            logger.debug(
                f"Period {period}: Unprofitable {mode} blocked. "
                f"Buy: {current_buy_price:.3f}, Sell: {current_sell_price:.3f}, "
                f"Best value: {effective_value_per_kwh_stored:.3f} <= "
                f"Cost basis: {cost_basis:.3f} {currency}/kWh stored"
            )
            economic_data = EconomicData(
                buy_price=current_buy_price,
                sell_price=current_sell_price,
                battery_cycle_cost=0.0,
                hourly_cost=float("inf"),
                grid_only_cost=home_consumption * current_buy_price,
                solar_only_cost=max(0, home_consumption - solar_production)
                * current_buy_price
                - max(0, solar_production - home_consumption) * current_sell_price,
            )
            period_data = PeriodData(
                period=period,
                energy=energy_data,
                timestamp=None,
                data_source="predicted",
                economic=economic_data,
                decision=DecisionData(
                    strategic_intent="IDLE",
                    battery_action=0.0,
                    cost_basis=cost_basis,
                ),
            )
            return float("-inf"), cost_basis, period_data

    # ============================================================================
    # COST BASIS CALCULATION
    # ============================================================================
    new_cost_basis = cost_basis

    if battery_charge > 0.0:
        if mode == "SOLAR_STORAGE":
            # battery_first: ALL solar goes directly to battery (DC path), no grid to battery
            solar_to_battery = battery_charge
            grid_to_battery = 0.0
        else:
            solar_available = max(0.0, solar_production - home_consumption)
            solar_to_battery = min(solar_available, battery_charge)
            grid_to_battery = max(0.0, battery_charge - solar_to_battery)

        grid_energy_cost = grid_to_battery * current_buy_price
        total_new_cost = grid_energy_cost + battery_wear_cost

        if next_soe > battery_settings.min_soe_kwh:
            new_cost_basis = (soe * cost_basis + total_new_cost) / next_soe
        else:
            new_cost_basis = (
                (total_new_cost / energy_stored) if energy_stored > 0 else cost_basis
            )

    # ============================================================================
    # REWARD CALCULATION
    # ============================================================================
    import_cost = grid_imported * current_buy_price
    export_revenue = grid_exported * current_sell_price
    total_cost = import_cost - export_revenue + battery_wear_cost
    reward = -total_cost

    # ============================================================================
    # DECISION AND ECONOMIC DATA
    # ============================================================================
    battery_action = battery_charge - battery_discharge

    decision_data = create_decision_data(
        mode=mode,
        battery_action=battery_action,
        energy_data=energy_data,
        hour=period,
        cost_basis=new_cost_basis,
        reward=reward,
        import_cost=import_cost,
        export_revenue=export_revenue,
        battery_wear_cost=battery_wear_cost,
        buy_price=current_buy_price,
        sell_price=current_sell_price,
        currency=currency,
    )

    economic_data = EconomicData.from_energy_data(
        energy_data=energy_data,
        buy_price=current_buy_price,
        sell_price=current_sell_price,
        battery_cycle_cost=battery_wear_cost,
    )

    new_period_data = PeriodData(
        period=period,
        energy=energy_data,
        timestamp=None,
        data_source="predicted",
        economic=economic_data,
        decision=decision_data,
    )

    return reward, new_cost_basis, new_period_data


def print_optimization_results(results, buy_prices, sell_prices):
    """Log a detailed results table with strategic intents - new format version.

    Args:
        results: OptimizationResult object with period_data and economic_summary
        buy_prices: List of buy prices
        sell_prices: List of sell prices
    """
    period_data_list = results.period_data
    economic_results = results.economic_summary

    # Initialize totals
    total_consumption = 0
    total_base_cost = 0
    total_solar = 0
    total_solar_to_bat = 0
    total_grid_to_bat = 0
    total_grid_cost = 0
    total_battery_cost = 0
    total_combined_cost = 0
    total_savings = 0
    total_charging = 0
    total_discharging = 0

    # Initialize output string
    output = []

    output.append("\nBattery Schedule:")
    output.append(
        "╔════╦═══════════╦══════╦═══════╦╦═════╦══════╦══════╦═════╦═══════╦═══════════════╦═══════╦══════╦══════╗"
    )
    output.append(
        "║ Hr ║  Buy/Sell ║Cons. ║ Cost  ║║Sol. ║Sol→B ║Gr→B  ║ SoE ║Action ║    Intent     ║  Grid ║ Batt ║ Save ║"
    )
    output.append(
        "║    ║   (SEK)   ║(kWh) ║ (SEK) ║║(kWh)║(kWh) ║(kWh) ║(kWh)║(kWh)  ║               ║ (SEK) ║(SEK) ║(SEK) ║"
    )
    output.append(
        "╠════╬═══════════╬══════╬═══════╬╬═════╬══════╬══════╬═════╬═══════╬═══════════════╬═══════╬══════╬══════╣"
    )

    # Process each hour - replicating original logic exactly
    for i, period_data in enumerate(period_data_list):
        period = period_data.period
        consumption = period_data.energy.home_consumption
        solar = period_data.energy.solar_production
        action = period_data.decision.battery_action or 0.0
        soe_kwh = period_data.energy.battery_soe_end
        intent = period_data.decision.strategic_intent

        # Calculate values exactly like original function
        base_cost = (
            consumption * buy_prices[i]
            if i < len(buy_prices)
            else consumption * period_data.economic.buy_price
        )

        # Extract solar flows from detailed flow data (always available from EnergyData)
        solar_to_battery = period_data.energy.solar_to_battery
        grid_to_battery = period_data.energy.grid_to_battery

        # Calculate costs using original logic - FIXED: use property accessor for battery_cycle_cost
        grid_cost = (
            period_data.energy.grid_imported * period_data.economic.buy_price
            - period_data.energy.grid_exported * period_data.economic.sell_price
        )
        battery_cost = (
            period_data.economic.battery_cycle_cost
        )  # FIXED: access via economic component
        combined_cost = grid_cost + battery_cost
        period_savings = base_cost - combined_cost

        # Update totals
        total_consumption += consumption
        total_base_cost += base_cost
        total_solar += solar
        total_solar_to_bat += solar_to_battery
        total_grid_to_bat += grid_to_battery
        total_grid_cost += grid_cost
        total_battery_cost += battery_cost
        total_combined_cost += combined_cost
        total_savings += period_savings
        total_charging += period_data.energy.battery_charged
        total_discharging += period_data.energy.battery_discharged

        # Format intent to fit column width
        intent_display = intent[:15] if len(intent) > 15 else intent

        # Format period row - preserving original formatting exactly
        buy_sell_str = f"{buy_prices[i] if i < len(buy_prices) else period_data.economic.buy_price:.2f}/{sell_prices[i] if i < len(sell_prices) else period_data.economic.sell_price:.2f}"

        output.append(
            f"║{period:3d} ║ {buy_sell_str:9s} ║{consumption:5.1f} ║{base_cost:6.2f} ║║{solar:4.1f} ║{solar_to_battery:5.1f} ║{grid_to_battery:5.1f} ║{soe_kwh:4.0f} ║{action:6.1f} ║ {intent_display:13s} ║{grid_cost:6.2f} ║{battery_cost:5.2f} ║{period_savings:5.2f} ║"
        )

    # Add separator and total row
    output.append(
        "╠════╬═══════════╬══════╬═══════╬╬═════╬══════╬══════╬═════╬═══════╬═══════════════╬═══════╬══════╬══════╣"
    )
    output.append(
        f"║Tot ║           ║{total_consumption:5.1f} ║{total_base_cost:6.2f} ║║{total_solar:4.1f} ║{total_solar_to_bat:5.1f} ║{total_grid_to_bat:5.1f} ║     ║C:{total_charging:4.1f} ║               ║{total_grid_cost:6.2f} ║{total_battery_cost:5.2f} ║{total_savings:5.2f} ║"
    )
    output.append(
        f"║    ║           ║      ║       ║║     ║      ║      ║     ║D:{total_discharging:4.1f} ║               ║       ║      ║      ║"
    )
    output.append(
        "╚════╩═══════════╩══════╩═══════╩╩═════╩══════╩══════╩═════╩═══════╩═══════════════╩═══════╩══════╩══════╝"
    )

    # Append summary stats to output
    output.append("\n      Summary:")
    output.append(
        f"      Grid-only cost:           {economic_results.grid_only_cost:.2f} SEK"
    )
    output.append(
        f"      Optimized cost:           {economic_results.battery_solar_cost:.2f} SEK"
    )
    output.append(
        f"      Total savings:            {economic_results.grid_to_battery_solar_savings:.2f} SEK"
    )
    savings_percentage = economic_results.grid_to_battery_solar_savings_pct
    output.append(f"      Savings percentage:         {savings_percentage:.1f} %")

    # Log all output in a single call
    logger.info("\n".join(output))


def _run_dynamic_programming(
    horizon: int,
    buy_price: list[float],
    sell_price: list[float],
    home_consumption: list[float],
    battery_settings: BatterySettings,
    dt: float,
    solar_production: list[float] | None = None,
    initial_soe: float | None = None,
    initial_cost_basis: float = 0.0,
    terminal_value_per_kwh: float = 0.0,
    currency: str = "SEK",
    max_charge_power_per_period: list[float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """
    Enhanced DP that stores the PeriodData objects calculated during optimization.
    This eliminates the need for reward recalculation in simulation.
    """

    logger.debug("Starting DP optimization with PeriodData storage")

    if solar_production is None:
        solar_production = [0.0] * horizon
    if initial_soe is None:
        initial_soe = battery_settings.min_soe_kwh

    soe_levels = _discretize_state_space(battery_settings)

    V = np.zeros((horizon + 1, len(soe_levels)))

    # Terminal value: assign value to usable energy remaining at end of horizon
    if terminal_value_per_kwh > 0.0:
        for i, soe in enumerate(soe_levels):
            usable_energy = soe - battery_settings.min_soe_kwh
            V[horizon, i] = max(0.0, usable_energy) * terminal_value_per_kwh

    policy = np.zeros((horizon, len(soe_levels)), dtype=int)  # stores mode index
    C = np.full((horizon + 1, len(soe_levels)), initial_cost_basis)

    stored_period_data: dict = {}  # Key: (t, i), Value: PeriodData

    # Backward induction over discrete modes
    for t in reversed(range(horizon)):
        period_max_charge = (
            max_charge_power_per_period[t]
            if max_charge_power_per_period is not None
            else None
        )

        for i, soe in enumerate(soe_levels):
            best_value = float("-inf")
            best_reward = float("-inf")
            best_mode_idx = 0  # default: HOLD
            best_cost_basis = C[t, i]
            best_next_soe = soe
            best_period_data = None

            for mode_idx, mode in enumerate(MODES):
                # HOLD wastes all solar production (solar → grid at 0 revenue in HOLD).
                # Block HOLD when solar is present so the DP can use it productively.
                if mode == "HOLD" and solar_production[t] > 0.01:
                    continue

                # IDLE can be disabled entirely via config (idle_enabled: false).
                if mode == "IDLE" and not battery_settings.idle_enabled:
                    continue

                # IDLE requires solar >= consumption: blocked when there is no solar,
                # or when solar cannot cover home load.
                if mode == "IDLE" and (
                    solar_production[t] <= 0.01
                    or solar_production[t] < home_consumption[t]
                ):
                    continue

                # GRID_CHARGING is blocked when solar production exceeds the threshold
                # (solar alone can charge the battery) or when there is insufficient
                # headroom for a meaningful charge action.
                if mode == "GRID_CHARGING":
                    if solar_production[t] > battery_settings.grid_charge_max_solar_threshold_kwh:
                        continue
                    available_space = (battery_settings.max_soe_kwh - soe) / battery_settings.efficiency_charge
                    if available_space < battery_settings.grid_charge_min_headroom_kwh:
                        continue

                # EXPORT_ARBITRAGE requires enough SOE for at least one full period
                # at max discharge power. Without this, the DP prefers emptying the
                # last scraps of battery over decently supporting load.
                # Also blocked when export is blocked (sell_price < 0): battery would
                # discharge with nowhere to send the energy.
                if mode == "EXPORT_ARBITRAGE":
                    available_soe = soe - battery_settings.min_soe_kwh
                    min_export_soe = battery_settings.max_discharge_power_kw * dt
                    if available_soe < min_export_soe:
                        continue
                    if sell_price[t] < 0.0:
                        continue

                battery_charge, battery_discharge, grid_imported, grid_exported, next_soe = (
                    _calculate_mode_energy_flows(
                        mode=mode,
                        soe=soe,
                        solar=solar_production[t],
                        consumption=home_consumption[t],
                        battery_settings=battery_settings,
                        dt=dt,
                        max_charge_power_override=period_max_charge,
                    )
                )

                # When export is blocked (sell_price < 0), solar surplus that cannot
                # be stored is curtailed by the inverter. Zero grid_exported so the
                # reward correctly reflects no export revenue.
                if sell_price[t] < 0.0:
                    grid_exported = 0.0

                reward, new_cost_basis, period_data = _calculate_reward(
                    mode=mode,
                    battery_charge=battery_charge,
                    battery_discharge=battery_discharge,
                    grid_imported=grid_imported,
                    grid_exported=grid_exported,
                    soe=soe,
                    next_soe=next_soe,
                    period=t,
                    home_consumption=home_consumption[t],
                    battery_settings=battery_settings,
                    buy_price=buy_price,
                    sell_price=sell_price,
                    solar_production=solar_production[t],
                    cost_basis=C[t, i],
                    currency=currency,
                )

                if reward == float("-inf"):
                    continue

                next_i = round((next_soe - battery_settings.min_soe_kwh) / SOE_STEP_KWH)
                next_i = min(max(0, next_i), len(soe_levels) - 1)

                value = reward + V[t + 1, next_i]

                if value > best_value:
                    best_value = value
                    best_reward = reward
                    best_mode_idx = mode_idx
                    best_cost_basis = new_cost_basis
                    best_next_soe = next_soe
                    best_period_data = period_data

            V[t, i] = best_value
            policy[t, i] = best_mode_idx
            stored_period_data[(t, i)] = best_period_data
            if best_period_data is not None:
                best_period_data.decision.dp_reward = best_reward
                best_period_data.decision.dp_value = best_value

            # Propagate cost basis to next period
            if t + 1 < horizon:
                next_i = round((best_next_soe - battery_settings.min_soe_kwh) / SOE_STEP_KWH)
                next_i = min(max(0, next_i), len(soe_levels) - 1)
                C[t + 1, next_i] = best_cost_basis

    return V, policy, C, stored_period_data


def _create_idle_schedule(
    horizon: int,
    buy_price: list[float],
    sell_price: list[float],
    home_consumption: list[float],
    solar_production: list[float],
    initial_soe: float,
    battery_settings: BatterySettings,
) -> OptimizationResult:
    """
    Create an all-IDLE schedule where battery does nothing.

    Used as fallback when optimization doesn't meet minimum profit threshold.
    """
    period_data_list = []
    current_soe = initial_soe

    for t in range(horizon):
        # No battery action - pure grid consumption
        energy_data = EnergyData(
            solar_production=solar_production[t],
            home_consumption=home_consumption[t],
            battery_charged=0.0,
            battery_discharged=0.0,
            grid_imported=max(0, home_consumption[t] - solar_production[t]),
            grid_exported=max(0, solar_production[t] - home_consumption[t]),
            battery_soe_start=current_soe,
            battery_soe_end=current_soe,
        )

        economic_data = EconomicData.from_energy_data(
            energy_data=energy_data,
            buy_price=buy_price[t],
            sell_price=sell_price[t],
            battery_cycle_cost=0.0,
        )

        decision_data = DecisionData(
            strategic_intent="IDLE",
            battery_action=0.0,
            cost_basis=battery_settings.cycle_cost_per_kwh,
        )

        period_data = PeriodData(
            period=t,
            energy=energy_data,
            timestamp=None,
            data_source="predicted",
            economic=economic_data,
            decision=decision_data,
        )

        period_data_list.append(period_data)

    # Calculate economic summary for idle schedule
    total_base_cost = sum(home_consumption[i] * buy_price[i] for i in range(horizon))
    total_optimized_cost = sum(h.economic.hourly_cost for h in period_data_list)

    economic_summary = EconomicSummary(
        grid_only_cost=total_base_cost,
        solar_only_cost=total_base_cost,
        battery_solar_cost=total_optimized_cost,
        grid_to_solar_savings=0.0,
        grid_to_battery_solar_savings=0.0,  # No savings - doing nothing
        solar_to_battery_solar_savings=0.0,
        grid_to_battery_solar_savings_pct=0.0,
        total_charged=0.0,
        total_discharged=0.0,
    )

    return OptimizationResult(
        period_data=period_data_list,
        economic_summary=economic_summary,
        input_data={
            "buy_price": buy_price,
            "sell_price": sell_price,
            "home_consumption": home_consumption,
            "solar_production": solar_production,
            "initial_soe": initial_soe,
            "initial_cost_basis": battery_settings.cycle_cost_per_kwh,
            "horizon": horizon,
        },
    )


def optimize_battery_schedule(
    buy_price: list[float],
    sell_price: list[float],
    home_consumption: list[float],
    battery_settings: BatterySettings,
    solar_production: list[float] | None = None,
    initial_soe: float | None = None,
    initial_cost_basis: float | None = None,
    period_duration_hours: float = 0.25,
    terminal_value_per_kwh: float = 0.0,
    currency: str = "SEK",
    max_charge_power_per_period: list[float] | None = None,
) -> OptimizationResult:
    """
    Battery optimization that eliminates dual cost calculation by using
    DP-calculated PeriodData directly in simulation.

    Args:
        buy_price: List of electricity buy prices for each period
        sell_price: List of electricity buy prices for each period
        home_consumption: List of home consumption for each period (kWh)
        battery_settings: Battery configuration and limits
        solar_production: List of solar production for each period (kWh), defaults to 0
        initial_soe: Initial battery state of energy (kWh), defaults to min_soe
        initial_cost_basis: Initial cost basis for battery cycling, defaults to cycle_cost
        period_duration_hours: Duration of each period in hours (always 0.25 for quarterly resolution)
        terminal_value_per_kwh: Value assigned to each kWh of usable energy remaining at
            end of horizon. Used to prevent end-of-day battery dumping when tomorrow's
            prices aren't available yet. Defaults to 0.0 (no terminal value).
        max_charge_power_per_period: Per-period max charge power limits (kW), typically
            from temperature derating. When provided, charging actions exceeding the
            limit for each period are excluded from the optimization. Defaults to None
            (no per-period limits, uses battery_settings.max_charge_power_kw).

    Returns:
        OptimizationResult with optimal battery schedule
    """

    horizon = len(buy_price)
    dt = period_duration_hours

    logger.info(f"Optimization using dt={dt} hours for horizon={horizon} periods")

    # Handle defaults
    if solar_production is None:
        solar_production = [0.0] * horizon
    if initial_soe is None:
        initial_soe = battery_settings.min_soe_kwh
    if initial_cost_basis is None:
        initial_cost_basis = battery_settings.cycle_cost_per_kwh

    # Validate inputs to prevent impossible scenarios
    if initial_soe > battery_settings.max_soe_kwh:
        raise ValueError(
            f"Invalid initial_soe={initial_soe:.1f}kWh exceeds battery capacity={battery_settings.max_soe_kwh:.1f}kWh"
        )

    # Allow optimization to start from below minimum SOC (can happen after restart or deep discharge)
    # The optimizer will naturally work to bring SOE back above minimum through charging
    if initial_soe < battery_settings.min_soe_kwh:
        logger.warning(
            f"Starting optimization with initial_soe={initial_soe:.1f}kWh below minimum SOE={battery_settings.min_soe_kwh:.1f}kWh. "
            f"Optimizer will work to restore battery charge."
        )

    logger.info(
        f"Starting direct optimization: horizon={horizon}, initial_soe={initial_soe:.1f}, initial_cost_basis={initial_cost_basis:.3f}"
    )

    # Step 1: Run DP with PeriodData storage
    _, _, _, stored_period_data = _run_dynamic_programming(
        horizon=horizon,
        buy_price=buy_price,
        sell_price=sell_price,
        home_consumption=home_consumption,
        solar_production=solar_production,
        initial_soe=initial_soe,
        battery_settings=battery_settings,
        initial_cost_basis=initial_cost_basis,
        dt=dt,
        terminal_value_per_kwh=terminal_value_per_kwh,
        currency=currency,
        max_charge_power_per_period=max_charge_power_per_period,
    )

    # Step 2: Extract optimal path results directly from stored DP data
    hourly_results = []
    current_soe = initial_soe
    soe_levels = _discretize_state_space(battery_settings)

    for t in range(horizon):
        # Find current state index (same logic as simulation)
        i = round((current_soe - battery_settings.min_soe_kwh) / SOE_STEP_KWH)
        i = min(max(0, i), len(soe_levels) - 1)

        # Get the PeriodData from DP results - should always exist with valid inputs
        if (t, i) not in stored_period_data:
            raise RuntimeError(
                f"Missing DP result for hour {t}, state {i} (SOE={current_soe:.1f}). "
                f"This indicates a bug in the DP algorithm or invalid inputs."
            )

        period_data = stored_period_data[(t, i)]
        hourly_results.append(period_data)
        current_soe = period_data.energy.battery_soe_end

    # Step 3: Calculate economic summary directly from PeriodData
    total_base_cost = sum(
        home_consumption[i] * buy_price[i] for i in range(len(buy_price))
    )

    total_optimized_cost = sum(h.economic.hourly_cost for h in hourly_results)
    total_charged = sum(h.energy.battery_charged for h in hourly_results)
    total_discharged = sum(h.energy.battery_discharged for h in hourly_results)

    # Calculate savings directly - renamed variables for clarity
    grid_to_battery_solar_savings = total_base_cost - total_optimized_cost

    economic_summary = EconomicSummary(
        grid_only_cost=total_base_cost,
        solar_only_cost=total_base_cost,  # Simplified - no solar in this scenario
        battery_solar_cost=total_optimized_cost,
        grid_to_solar_savings=0.0,  # No solar
        grid_to_battery_solar_savings=grid_to_battery_solar_savings,
        solar_to_battery_solar_savings=grid_to_battery_solar_savings,
        grid_to_battery_solar_savings_pct=(
            (grid_to_battery_solar_savings / total_base_cost) * 100
            if total_base_cost > 0
            else 0
        ),
        total_charged=total_charged,
        total_discharged=total_discharged,
    )

    logger.info(
        f"Direct Results: Grid-only cost: {total_base_cost:.2f}, "
        f"Optimized cost: {total_optimized_cost:.2f}, "
        f"Savings: {grid_to_battery_solar_savings:.2f} {currency} ({economic_summary.grid_to_battery_solar_savings_pct:.1f}%)"
    )

    # ============================================================================
    # PROFITABILITY GATE: Reject optimization if savings below effective threshold
    # ============================================================================
    # Scale the threshold proportionally to the remaining horizon so that mid-day
    # and late-day runs are not held to a full-day savings bar.
    # A floor of 15% prevents the threshold from collapsing to near-zero at end of day.
    THRESHOLD_HORIZON_FLOOR = 0.15
    total_periods = round(24.0 / dt)
    horizon_fraction = max(THRESHOLD_HORIZON_FLOOR, horizon / total_periods)
    effective_threshold = (
        battery_settings.min_action_profit_threshold * horizon_fraction
    )
    if grid_to_battery_solar_savings < effective_threshold:
        logger.warning(
            f"Optimization savings ({grid_to_battery_solar_savings:.2f} {currency}) below "
            f"effective threshold ({effective_threshold:.2f} {currency}) "
            f"(configured: {battery_settings.min_action_profit_threshold:.2f}, "
            f"horizon: {horizon}/{total_periods} periods, scale: {horizon_fraction:.2f}). "
            f"Using all-IDLE schedule instead."
        )
        return _create_idle_schedule(
            horizon=horizon,
            buy_price=buy_price,
            sell_price=sell_price,
            home_consumption=home_consumption,
            solar_production=solar_production,
            initial_soe=initial_soe,
            battery_settings=battery_settings,
        )

    return OptimizationResult(
        period_data=hourly_results,
        economic_summary=economic_summary,
        input_data={
            "buy_price": buy_price,
            "sell_price": sell_price,
            "home_consumption": home_consumption,
            "solar_production": solar_production,
            "initial_soe": initial_soe,
            "initial_cost_basis": initial_cost_basis,
            "horizon": horizon,
        },
    )
