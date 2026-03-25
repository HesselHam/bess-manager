"""Monitors home power usage and adapts battery charging power to prevent overloading of fuses.

It does this by:

1. Power Monitoring:
   - Continuously monitors current draw on electrical phases (single or three-phase)
   - Calculates total power consumption per phase
   - Considers house fuse limits (e.g., 25A per phase)
   - Maintains a safety margin to prevent tripping fuses

2. Battery Charge Management:
   - Adjusts battery charging power based on available power
   - Ensures total power draw (including battery) stays within fuse limits
   - Respects maximum charging rate configuration
   - Only activates when grid charging is enabled

This module is designed to work with the Home Assistant controller and to be run periodically

"""

import logging

from .ha_api_controller import HomeAssistantAPIController
from .health_check import perform_health_check
from .settings import BatterySettings, HomeSettings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class HomePowerMonitor:
    """Monitors home power consumption and manages battery charging."""

    def __init__(
        self,
        ha_controller: HomeAssistantAPIController,
        home_settings: HomeSettings | None = None,
        battery_settings: BatterySettings | None = None,
    ) -> None:
        """Initialize power monitor.

        Args:
            ha_controller: Home Assistant controller instance
            home_settings: Home electrical settings (optional)
            battery_settings: Battery settings (optional)
            step_size: Size of power adjustments in percent (default: 5%)

        """
        self.controller = ha_controller
        self.home_settings = home_settings or HomeSettings()
        self.battery_settings = battery_settings or BatterySettings()

        # Calculate max power per phase with safety margin
        self.max_power_per_phase = (
            self.home_settings.voltage
            * self.home_settings.max_fuse_current
            * self.home_settings.safety_margin
        )

        # Max charging power in watts (convert from kW)
        self.max_charge_power_w = self.battery_settings.max_charge_power_kw * 1000

        # Target charging power percentage - initialized from battery settings
        # This can be modified by external components like growatt_schedule
        # to reflect the actual charging power needed for strategic intents
        self.target_charging_power_pct = self.battery_settings.charging_power_rate

        log_message = (
            "Initialized HomePowerMonitor with:\n"
            "  Max power per phase: {}W\n"
            "  Max charging power: {}W\n"
            "  Target charging rate: {}%"
        )
        logger.info(
            log_message.format(
                self.max_power_per_phase,
                self.max_charge_power_w,
                self.target_charging_power_pct,
            )
        )

    def check_health(self) -> list:
        """Check the health of the Power Monitor component."""
        inverter_phase = self.home_settings.inverter_phase
        if inverter_phase:
            current_methods = [f"get_{inverter_phase.lower()}_current"]
        elif self.home_settings.phase_count == 1:
            current_methods = ["get_l1_current"]
        else:
            current_methods = ["get_l1_current", "get_l2_current", "get_l3_current"]

        power_methods = current_methods + ["get_charging_power_rate"]

        # For power monitoring, since the component itself is optional, all methods are optional
        # System can operate without power monitoring - it's an enhancement feature
        required_power_methods = []

        health_check = perform_health_check(
            component_name="Power Monitoring",
            description="Monitors home power consumption and adapts battery charging",
            is_required=False,
            controller=self.controller,
            all_methods=power_methods,
            required_methods=required_power_methods,
        )

        return [health_check]

    def _get_phase_voltage(self, phase: str) -> float:
        """Get live voltage for a phase, falling back to config voltage if sensor unavailable."""
        fallback = float(self.home_settings.voltage)
        try:
            if phase == "L1":
                return self.controller.get_l1_voltage() or fallback
            if phase == "L2":
                return self.controller.get_l2_voltage() or fallback
            if phase == "L3":
                return self.controller.get_l3_voltage() or fallback
        except Exception:
            pass
        return fallback

    def _get_phase_load_w(self, phase: str) -> float:
        """Get current load in watts for a single phase."""
        phase_map = {
            "L1": self.controller.get_l1_current,
            "L2": self.controller.get_l2_current,
            "L3": self.controller.get_l3_current,
        }
        current = phase_map[phase]()
        voltage = self._get_phase_voltage(phase)
        return current * voltage

    def get_current_phase_loads_w(self) -> tuple[float, ...]:
        """Get current load on each phase in watts.

        Returns a tuple with one element per phase (1 for single-phase, 3 for three-phase).
        """
        if self.home_settings.phase_count == 1:
            return (self._get_phase_load_w("L1"),)

        return (
            self._get_phase_load_w("L1"),
            self._get_phase_load_w("L2"),
            self._get_phase_load_w("L3"),
        )

    def calculate_available_charging_power(self) -> float:
        """Calculate safe battery charging power based on inverter phase load and target power.

        If inverter_phase is configured, only that phase is evaluated and the full battery
        max power applies to it (single-phase inverter on multi-phase grid).
        If inverter_phase is empty, the most loaded phase is used and battery power is
        distributed across all phases (three-phase inverter).
        """
        inverter_phase = self.home_settings.inverter_phase

        if inverter_phase:
            # Single-phase inverter: only look at the inverter phase
            load_w = self._get_phase_load_w(inverter_phase)
            available_power_w = self.max_power_per_phase - load_w
            max_battery_power_w = self.max_charge_power_w
            phase_log = f"Inverter phase {inverter_phase}: {load_w:.0f}W ({(load_w / self.max_power_per_phase) * 100:.1f}%)"
        else:
            # Three-phase inverter: use most loaded phase, distribute battery power
            phase_loads = self.get_current_phase_loads_w()
            load_w = max(phase_loads)
            available_power_w = self.max_power_per_phase - load_w
            max_battery_power_w = self.max_charge_power_w / self.home_settings.phase_count
            phase_parts = []
            for i, pl in enumerate(phase_loads):
                pct = (pl / self.max_power_per_phase) * 100
                phase_parts.append(f"L{i + 1}: {pl:.0f}W ({pct:.1f}%)")
            phase_log = "Phase loads: " + ", ".join(phase_parts)

        if max_battery_power_w > 0:
            available_pct = (available_power_w / max_battery_power_w) * 100
        else:
            available_pct = 0

        charging_power_pct = min(available_pct, self.target_charging_power_pct)

        logger.info(
            "%s\nAvailable power: %.0fW (%.1f%% of battery max)\nTarget charging: %.1f%%\nRecommended charging: %.1f%%",
            phase_log,
            available_power_w,
            available_pct,
            self.target_charging_power_pct,
            charging_power_pct,
        )

        return max(0, charging_power_pct)

    def adjust_battery_charging(self) -> None:
        if not self.controller.grid_charge_enabled():
            # Grid charge is off: set the inverter to the intended rate so it is
            # ready when grid charging activates on the next schedule update.
            target_power = self.target_charging_power_pct
        else:
            target_power = self.calculate_available_charging_power()
        current_power = self.controller.get_charging_power_rate()

        # Skip if no change needed (within 1% tolerance)
        if abs(target_power - current_power) < 1:
            return

        logger.info(
            f"Adjusting charging power from {current_power:.0f}% to {target_power:.0f}%"
        )
        self.controller.set_charging_power_rate(int(target_power))

    def update_target_charging_power(self, percentage: float) -> None:
        """Update the target charging power percentage.

        This method allows external components (like GrowattScheduleManager)
        to update the target charging power percentage based on strategic intents
        and optimization results.

        Args:
            percentage: Target charging power percentage (0-100)
        """
        if not 0 <= percentage <= 100:
            logger.warning(
                f"Invalid charging power percentage: {percentage}. Must be between 0-100."
            )
            percentage = min(100, max(0, percentage))

        # Only log when there's an actual change
        if (
            abs(self.target_charging_power_pct - percentage) > 0.01
        ):  # Use small tolerance for float comparison
            logger.info(
                f"Updating target charging power from {self.target_charging_power_pct:.1f}% to {percentage:.1f}%"
            )

        self.target_charging_power_pct = percentage
