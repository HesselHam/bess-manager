"""HistoricalDataStore - Stores actual sensor data at quarterly resolution.

Stores today's data plus up to `history_days` past days for plan-vs-actual
comparison in the Decision Details table.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta

from core.bess import time_utils
from core.bess.models import DecisionData, EconomicData, EnergyData, PeriodData
from core.bess.settings import BatterySettings
from core.bess.time_utils import get_period_count

logger = logging.getLogger(__name__)

STORE_VERSION = 1
_STORE_FILENAME = "historical_store.json"


def _period_data_to_dict(pd: PeriodData) -> dict:
    e = pd.energy
    return {
        "period": pd.period,
        "data_source": pd.data_source,
        "timestamp": pd.timestamp.isoformat() if pd.timestamp else None,
        "energy": {
            "solar_production": e.solar_production,
            "home_consumption": e.home_consumption,
            "battery_charged": e.battery_charged,
            "battery_discharged": e.battery_discharged,
            "grid_imported": e.grid_imported,
            "grid_exported": e.grid_exported,
            "battery_soe_start": e.battery_soe_start,
            "battery_soe_end": e.battery_soe_end,
        },
        "economic": {
            "buy_price": pd.economic.buy_price,
            "sell_price": pd.economic.sell_price,
            "grid_cost": pd.economic.grid_cost,
            "battery_cycle_cost": pd.economic.battery_cycle_cost,
            "hourly_cost": pd.economic.hourly_cost,
            "grid_only_cost": pd.economic.grid_only_cost,
            "solar_only_cost": pd.economic.solar_only_cost,
            "hourly_savings": pd.economic.hourly_savings,
        },
        "decision": {
            "strategic_intent": pd.decision.strategic_intent,
            "observed_intent": pd.decision.observed_intent,
            "battery_action": pd.decision.battery_action,
            "cost_basis": pd.decision.cost_basis,
            "pattern_name": pd.decision.pattern_name,
            "description": pd.decision.description,
            "economic_chain": pd.decision.economic_chain,
            "immediate_value": pd.decision.immediate_value,
            "future_value": pd.decision.future_value,
            "net_strategy_value": pd.decision.net_strategy_value,
            "advanced_flow_pattern": pd.decision.advanced_flow_pattern,
            "detailed_flow_values": pd.decision.detailed_flow_values,
            "future_target_hours": pd.decision.future_target_hours,
        },
    }


def _period_data_from_dict(d: dict) -> PeriodData:
    e = d["energy"]
    ec = d["economic"]
    dec = d["decision"]
    ts_str = d.get("timestamp")
    return PeriodData(
        period=d["period"],
        data_source=d["data_source"],
        timestamp=datetime.fromisoformat(ts_str) if ts_str else None,
        energy=EnergyData(
            solar_production=e["solar_production"],
            home_consumption=e["home_consumption"],
            battery_charged=e["battery_charged"],
            battery_discharged=e["battery_discharged"],
            grid_imported=e["grid_imported"],
            grid_exported=e["grid_exported"],
            battery_soe_start=e["battery_soe_start"],
            battery_soe_end=e["battery_soe_end"],
        ),
        economic=EconomicData(
            buy_price=ec["buy_price"],
            sell_price=ec["sell_price"],
            grid_cost=ec["grid_cost"],
            battery_cycle_cost=ec["battery_cycle_cost"],
            hourly_cost=ec["hourly_cost"],
            grid_only_cost=ec["grid_only_cost"],
            solar_only_cost=ec["solar_only_cost"],
            hourly_savings=ec["hourly_savings"],
        ),
        decision=DecisionData(
            strategic_intent=dec["strategic_intent"],
            observed_intent=dec.get("observed_intent"),
            battery_action=dec.get("battery_action"),
            cost_basis=dec["cost_basis"],
            pattern_name=dec.get("pattern_name", ""),
            description=dec.get("description", ""),
            economic_chain=dec.get("economic_chain", ""),
            immediate_value=dec.get("immediate_value", 0.0),
            future_value=dec.get("future_value", 0.0),
            net_strategy_value=dec.get("net_strategy_value", 0.0),
            advanced_flow_pattern=dec.get("advanced_flow_pattern", ""),
            detailed_flow_values=dec.get("detailed_flow_values", {}),
            future_target_hours=dec.get("future_target_hours", []),
        ),
    )


class HistoricalDataStore:
    """Stores actual sensor data at quarterly resolution.

    Today's data uses simple integer indices (0 = today 00:00, 95 = today 23:45).
    Past days are stored keyed by date and period index.
    """

    def __init__(
        self,
        battery_settings: BatterySettings,
        history_days: int = 1,
        data_dir: str = "/data",
    ):
        """Initialize the historical data store.

        Args:
            battery_settings: Battery settings reference (shared, always up-to-date)
            history_days: Number of past days to retain (1 = yesterday + today)
            data_dir: Directory for persistent storage (default: /data for HA add-on)
        """
        # Today's data: period_index → PeriodData
        self._records: dict[int, PeriodData] = {}
        # Today's planned snapshots: period_index → PeriodData
        self._planned_records: dict[int, PeriodData] = {}
        # Past days: date → {period_index → PeriodData}
        self._historical_records: dict[date, dict[int, PeriodData]] = {}
        self._historical_planned: dict[date, dict[int, PeriodData]] = {}

        self.battery_settings = battery_settings
        self.history_days = history_days
        self._store_path = os.path.join(data_dir, _STORE_FILENAME)

        self._load()
        logger.debug("Initialized HistoricalDataStore (history_days=%d)", history_days)

    def _save(self) -> None:
        """Persist the store to disk."""
        try:
            payload = {
                "version": STORE_VERSION,
                "records": {str(k): _period_data_to_dict(v) for k, v in self._records.items()},
                "planned_records": {str(k): _period_data_to_dict(v) for k, v in self._planned_records.items()},
                "historical_records": {
                    d.isoformat(): {str(k): _period_data_to_dict(v) for k, v in periods.items()}
                    for d, periods in self._historical_records.items()
                },
                "historical_planned": {
                    d.isoformat(): {str(k): _period_data_to_dict(v) for k, v in periods.items()}
                    for d, periods in self._historical_planned.items()
                },
            }
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
            with open(self._store_path, "w") as f:
                json.dump(payload, f)
        except Exception as e:
            logger.warning("Failed to save historical store: %s", e)

    def _load(self) -> None:
        """Load the store from disk. Wipes and starts fresh on version mismatch or error."""
        if not os.path.exists(self._store_path):
            return
        try:
            with open(self._store_path) as f:
                payload = json.load(f)
            if payload.get("version") != STORE_VERSION:
                logger.warning(
                    "Historical store version mismatch (got %s, expected %s) — starting fresh",
                    payload.get("version"),
                    STORE_VERSION,
                )
                os.remove(self._store_path)
                return
            self._records = {int(k): _period_data_from_dict(v) for k, v in payload.get("records", {}).items()}
            self._planned_records = {int(k): _period_data_from_dict(v) for k, v in payload.get("planned_records", {}).items()}
            self._historical_records = {
                date.fromisoformat(d): {int(k): _period_data_from_dict(v) for k, v in periods.items()}
                for d, periods in payload.get("historical_records", {}).items()
            }
            self._historical_planned = {
                date.fromisoformat(d): {int(k): _period_data_from_dict(v) for k, v in periods.items()}
                for d, periods in payload.get("historical_planned", {}).items()
            }
            logger.info(
                "Loaded historical store: %d today periods, %d past days",
                len(self._records),
                len(self._historical_records),
            )
        except Exception as e:
            logger.warning("Failed to load historical store (%s) — starting fresh", e)
            self._records = {}
            self._planned_records = {}
            self._historical_records = {}
            self._historical_planned = {}
            try:
                os.remove(self._store_path)
            except OSError:
                pass

    def record_period(self, period_index: int, period_data: PeriodData) -> None:
        """Record actual sensor data for a period (today).

        Args:
            period_index: Continuous index from today 00:00 (0-95)
            period_data: Sensor data with data_source="actual"

        Raises:
            ValueError: If period_index is out of range for today
        """
        today = datetime.now(tz=time_utils.TIMEZONE).date()
        today_periods = get_period_count(today)

        if not 0 <= period_index < today_periods:
            raise ValueError(
                f"Period index {period_index} out of range for today "
                f"(0-{today_periods-1})"
            )

        self._records[period_index] = period_data
        self._save()

        logger.debug(
            "Recorded period %d: SOC %.1f → %.1f kWh",
            period_index,
            period_data.energy.battery_soe_start,
            period_data.energy.battery_soe_end,
        )

    def record_period_for_date(
        self, d: date, period_index: int, period_data: PeriodData
    ) -> None:
        """Record actual sensor data for a period on a specific past date.

        Args:
            d: The date the period belongs to
            period_index: Period index (0-95)
            period_data: Sensor data
        """
        if d not in self._historical_records:
            self._historical_records[d] = {}
        self._historical_records[d][period_index] = period_data
        self._save()

    def record_planned_period(self, period_index: int, planned_data: PeriodData) -> None:
        """Snapshot the DP-planned values for a period at the moment it completes.

        Args:
            period_index: Continuous index from today 00:00 (0-95)
            planned_data: PeriodData with data_source="predicted" from optimization result
        """
        self._planned_records[period_index] = planned_data
        self._save()

    def get_period(self, period_index: int) -> PeriodData | None:
        """Get actual data for a specific period (today).

        Args:
            period_index: Continuous index from today 00:00

        Returns:
            PeriodData if available, None if missing
        """
        return self._records.get(period_index)

    def get_period_for_date(self, d: date, period_index: int) -> PeriodData | None:
        """Get actual data for a specific period on a past date.

        Args:
            d: The date to look up
            period_index: Period index (0-95)

        Returns:
            PeriodData if available, None if missing
        """
        return self._historical_records.get(d, {}).get(period_index)

    def get_planned_period(self, period_index: int) -> PeriodData | None:
        """Get the DP-planned snapshot for a specific period (today).

        Args:
            period_index: Continuous index from today 00:00

        Returns:
            Planned PeriodData if snapshotted, None if missing
        """
        return self._planned_records.get(period_index)

    def get_today_periods(self) -> list[PeriodData | None]:
        """Get all periods for today (accounting for DST).

        Returns:
            List of 92-100 PeriodData (or None for missing periods)
            Length depends on DST (92 = spring, 96 = normal, 100 = fall)
        """
        today = datetime.now(tz=time_utils.TIMEZONE).date()
        num_periods = get_period_count(today)
        return [self._records.get(i) for i in range(num_periods)]

    def get_available_dates(self) -> list[date]:
        """Return all dates with stored historical data, sorted ascending.

        Includes only past dates (not today).
        """
        return sorted(self._historical_records.keys())

    def get_periods_for_date(self, d: date) -> list[PeriodData | None]:
        """Get all periods for a specific past date.

        Args:
            d: The date to retrieve

        Returns:
            List of PeriodData (or None for missing periods)
        """
        num_periods = get_period_count(d)
        day_records = self._historical_records.get(d, {})
        return [day_records.get(i) for i in range(num_periods)]

    def clear(self) -> None:
        """Clear all stored data."""
        self._records.clear()
        self._historical_records.clear()
        self._historical_planned.clear()
        logger.info("Cleared all historical data")

    def get_stored_count(self) -> int:
        """Get count of stored periods for today."""
        return len(self._records)

    def evict_old_days(self) -> None:
        """Remove historical data older than history_days."""
        today = datetime.now(tz=time_utils.TIMEZONE).date()
        cutoff = today - timedelta(days=self.history_days)
        stale = [d for d in self._historical_records if d < cutoff]
        for d in stale:
            del self._historical_records[d]
            self._historical_planned.pop(d, None)
            logger.debug("Evicted historical data for %s", d)

    def roll_over_to_historical(self) -> None:
        """Move today's completed data to the historical store, then clear today's records.

        Called at prepare_next_day (23:55). Saves today's actual and planned data keyed
        by today's date in the historical stores, then evicts data older than history_days.
        """
        today = datetime.now(tz=time_utils.TIMEZONE).date()

        if self._records:
            self._historical_records[today] = dict(self._records)
            logger.debug(
                "Rolled over %d actual periods to historical for %s",
                len(self._records),
                today,
            )
        if self._planned_records:
            self._historical_planned[today] = dict(self._planned_records)
            logger.debug(
                "Rolled over %d planned periods to historical for %s",
                len(self._planned_records),
                today,
            )

        self._records.clear()
        self._planned_records.clear()
        self.evict_old_days()
        self._save()
        logger.info("Historical roll-over complete for %s", today)

    def get_planned_period_for_date(self, d: date, period_index: int) -> PeriodData | None:
        """Get planned data for a specific period on a past date.

        Args:
            d: The date to look up
            period_index: Period index (0-95)

        Returns:
            Planned PeriodData if available, None if missing
        """
        return self._historical_planned.get(d, {}).get(period_index)
