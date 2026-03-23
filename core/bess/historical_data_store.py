"""HistoricalDataStore - Stores actual sensor data at quarterly resolution.

Stores today's data plus up to `history_days` past days for plan-vs-actual
comparison in the Decision Details table.
"""

import logging
from datetime import date, datetime, timedelta

from core.bess import time_utils
from core.bess.models import PeriodData
from core.bess.settings import BatterySettings
from core.bess.time_utils import get_period_count

logger = logging.getLogger(__name__)


class HistoricalDataStore:
    """Stores actual sensor data at quarterly resolution.

    Today's data uses simple integer indices (0 = today 00:00, 95 = today 23:45).
    Past days are stored keyed by date and period index.
    """

    def __init__(self, battery_settings: BatterySettings, history_days: int = 1):
        """Initialize the historical data store.

        Args:
            battery_settings: Battery settings reference (shared, always up-to-date)
            history_days: Number of past days to retain (1 = yesterday + today)
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

        logger.debug("Initialized HistoricalDataStore (history_days=%d)", history_days)

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

    def record_planned_period(self, period_index: int, planned_data: PeriodData) -> None:
        """Snapshot the DP-planned values for a period at the moment it completes.

        Args:
            period_index: Continuous index from today 00:00 (0-95)
            planned_data: PeriodData with data_source="predicted" from optimization result
        """
        self._planned_records[period_index] = planned_data

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
