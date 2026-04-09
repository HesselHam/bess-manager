import json
import os
from contextlib import asynccontextmanager
from datetime import datetime

import log_config  # noqa: F401

# Import endpoints router
from api import router as endpoints_router
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

# Import BESS system modules
from core.bess.battery_system_manager import BatterySystemManager
from core.bess.ha_api_controller import HomeAssistantAPIController

# from core.bess.health_check import run_system_health_checks # TODO ADD health check

# Get ingress prefix from environment variable
INGRESS_PREFIX = os.environ.get("INGRESS_PREFIX", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager for FastAPI app."""
    # Startup
    routes = []
    for route in app.routes:
        path = getattr(route, "path", getattr(route, "mount_path", "Unknown path"))
        methods = getattr(route, "methods", None)
        if methods is not None:
            routes.append(f"{path} - {methods}")
        else:
            routes.append(f"{path} - Mounted route or no methods")
    logger.info(f"Registered routes: {routes}")

    yield

    # Shutdown (if needed in the future)
    pass


# Create FastAPI app with correct root_path
app = FastAPI(root_path=INGRESS_PREFIX, lifespan=lifespan)


# Add global exception handler to prevent server restarts
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback

    from fastapi.responses import JSONResponse

    # Get the full stack trace
    tb_str = traceback.format_exception(type(exc), exc, exc.__traceback__)
    error_msg = "".join(tb_str)

    # Log the full error details
    logger.error(f"Unhandled exception: {exc!s}")
    logger.error(f"Request path: {request.url.path}")
    logger.error(f"Stack trace:\n{error_msg}")

    # Return a 500 response but keep the server running
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "type": str(type(exc).__name__),
            "message": "The server encountered an internal error but is still running.",
        },
    )


# Now that logger patching is complete, log the ingress prefix
logger.info(f"Ingress prefix: {INGRESS_PREFIX}")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for the various paths
static_directory = "/app/frontend"
if os.path.exists(static_directory):
    # Root path assets
    app.mount(
        "/assets", StaticFiles(directory=f"{static_directory}/assets"), name="assets"
    )

# Include the router from endpoints.py
app.include_router(endpoints_router)


class BESSController:
    def __init__(self):
        """Initialize the BESS Controller."""
        # Environment variables are injected by HA Supervisor (production)
        # or docker-compose (development).

        # Load all settings as early as possible
        options = self._load_options()
        if not options:
            logger.warning("No configuration options found, using defaults")
            options = {}

        # Initialize Home Assistant API Controller with sensor config from options
        sensor_config = options.get("sensors", {})
        growatt_config = options.get("growatt", {})
        growatt_device_id = growatt_config.get("device_id")
        self.ha_controller = self._init_ha_controller(sensor_config, growatt_device_id)

        # Set timezone from HA config before any BESS modules use it
        try:
            ha_config = self.ha_controller.get_ha_config()
            ha_timezone = ha_config["time_zone"]
            from core.bess.time_utils import set_timezone

            set_timezone(ha_timezone)
            logger.info(f"Timezone set from HA: {ha_timezone}")
        except Exception as e:
            logger.warning(f"Could not read timezone from HA, using default: {e}")

        # Enable test mode based on environment variable (defaults to False for production)
        test_mode = os.environ.get("HA_TEST_MODE", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        if test_mode:
            logger.info("Enabling test mode - hardware writes will be simulated")
        self.ha_controller.set_test_mode(test_mode)

        # Extract energy provider configuration
        energy_provider_config = options.get("energy_provider", {})

        # Create Battery System Manager with price provider configuration
        # Let the system manager choose the appropriate price source
        self.system = BatterySystemManager(
            self.ha_controller,
            price_source=None,  # Let system manager auto-select based on config
            energy_provider_config=energy_provider_config,
            addon_options=options,
        )

        # Create scheduler with increased misfire grace time to avoid unnecessary warnings
        self.scheduler = BackgroundScheduler(
            {
                "apscheduler.executors.default": {
                    "class": "apscheduler.executors.pool:ThreadPoolExecutor",
                    "max_workers": "20",
                },
                "apscheduler.job_defaults": {
                    "misfire_grace_time": 30  # Allow 30 seconds of misfire before warning
                },
            }
        )

        # Apply all settings to the system immediately
        self._apply_settings(options)

        logger.info("BESS Controller initialized with early settings loading")

    def _init_ha_controller(self, sensor_config, growatt_device_id=None):
        """Initialize Home Assistant API controller based on environment.

        Args:
            sensor_config: Sensor configuration dictionary to use for the controller.
            growatt_device_id: Growatt device ID for TOU segment operations.
        """
        ha_token = os.getenv("HASSIO_TOKEN")
        if ha_token:
            ha_url = "http://supervisor/core"
        else:
            ha_token = os.environ.get("HA_TOKEN", "")
            ha_url = os.environ.get("HA_URL", "http://supervisor/core")

        logger.info(
            f"Initializing HA controller with {len(sensor_config)} sensor configurations"
        )

        return HomeAssistantAPIController(
            ha_url=ha_url,
            token=ha_token,
            sensor_config=sensor_config,
            growatt_device_id=growatt_device_id,
        )

    def _load_and_apply_settings(self):
        """Load options and apply settings.

        This method is kept for backwards compatibility, but all settings should now be
        applied early during initialization using _apply_settings().
        """
        try:
            options = self._load_options()
            if options:
                logger.debug(
                    "Reapplying settings from _load_and_apply_settings (redundant)"
                )
                self._apply_settings(options)
            else:
                logger.warning("No options found when reapplying settings")
        except Exception as e:
            logger.error(f"Error reloading settings: {e}", exc_info=True)

    def _load_options(self):
        """Load options from Home Assistant add-on standard location.

        In production: /data/options.json provided by Home Assistant add-on system
        In development: /data/options.json mounted from backend/dev-options.json (extracted by dev-run.sh)
        """
        options_json = "/data/options.json"

        if os.path.exists(options_json):
            try:
                with open(options_json) as f:
                    options = json.load(f)
                    logger.info(f"Loaded options from {options_json}")
                    return options
            except Exception as e:
                logger.error(f"Error loading options from {options_json}: {e!s}")
                raise RuntimeError(
                    f"Failed to load configuration from {options_json}. " f"Error: {e}"
                ) from e
        else:
            raise RuntimeError(
                f"Configuration file not found at {options_json}. "
                f"In development, ensure dev-run.sh has extracted options from config.yaml."
            )

    def _init_scheduler_jobs(self):
        """Configure scheduler jobs."""

        # Quarterly schedule update (every 15 minutes: 0, 15, 30, 45)
        def update_schedule_quarterly():
            now = datetime.now()
            current_period = now.hour * 4 + now.minute // 15
            self.system.update_battery_schedule(current_period=current_period)

        self.scheduler.add_job(
            update_schedule_quarterly,
            CronTrigger(minute="0,15,30,45"),
            misfire_grace_time=30,  # Allow 30 seconds of misfire before warning
        )

        # Next day preparation (daily at 23:55)
        def prepare_next_day():
            now = datetime.now()
            current_period = now.hour * 4 + now.minute // 15
            self.system.update_battery_schedule(
                current_period=current_period, prepare_next_day=True
            )

        self.scheduler.add_job(
            prepare_next_day,
            CronTrigger(hour=23, minute=55),
            misfire_grace_time=30,  # Allow 30 seconds of misfire before warning
        )

        # Fuse protection and IDLE deadband (only when power monitor is available)
        if self.system.power_monitor is not None:
            self.scheduler.add_job(
                self.system.power_monitor.adjust_fuse_protection,
                IntervalTrigger(seconds=15),
                misfire_grace_time=5,
            )
            self.scheduler.add_job(
                self.system.power_monitor.enforce_idle_deadband,
                IntervalTrigger(seconds=30),
                misfire_grace_time=5,
            )

        # BDC pre-emptive: enable BDC one minute before HOLD→non-HOLD transition
        self.scheduler.add_job(
            self.system.check_preemptive_bdc,
            CronTrigger(minute="14,29,44,59"),
            misfire_grace_time=30,
        )

        self.scheduler.start()

    def _apply_settings(self, options):
        """Apply all settings from the provided options dictionary.

        This consolidates settings application in one place, ensuring settings
        are applied as early as possible in the initialization process.

        All user-facing settings must be explicitly configured in config.yaml.
        No fallback defaults are provided to ensure deterministic behavior.

        Args:
            options: Dictionary containing all configuration options
        """
        try:
            if not options:
                raise ValueError("Configuration options are required but not provided")

            logger.debug(f"Applying settings: {json.dumps(options, indent=2)}")

            # Validate required sections exist
            required_sections = ["battery", "electricity_price", "home"]
            for section in required_sections:
                if section not in options:
                    raise ValueError(
                        f"Required configuration section '{section}' is missing from config.yaml"
                    )

            # Validate required values without defaults
            battery_config = options["battery"]
            electricity_price_config = options["electricity_price"]
            home_config = options["home"]
            # dp section is optional; fall back to battery section for migration
            dp_config = options.get("dp", battery_config)

            # Required battery settings
            required_battery_keys = [
                "total_capacity",
                "min_soc",
                "max_soc",
                "cycle_cost",
                "max_charge_discharge_power",
            ]
            for key in required_battery_keys:
                if key not in battery_config:
                    raise ValueError(
                        f"Required battery setting '{key}' is missing from config.yaml"
                    )

            # Required dp settings (checked in dp section, fall back to battery for migration)
            required_dp_keys = ["min_action_profit_threshold"]
            for key in required_dp_keys:
                if key not in dp_config:
                    raise ValueError(
                        f"Required dp setting '{key}' is missing from config.yaml"
                    )

            # Required electricity price settings
            required_price_keys = [
                "area",
                "markup_rate",
                "vat_multiplier",
                "additional_costs",
                "tax_reduction",
            ]
            for key in required_price_keys:
                if key not in electricity_price_config:
                    raise ValueError(
                        f"Required electricity_price setting '{key}' is missing from config.yaml"
                    )

            # Required home settings
            required_home_keys = [
                "consumption",
                "currency",
                "max_fuse_current",
                "voltage",
                "safety_margin_factor",
                "phase_count",
                "consumption_strategy",
            ]
            for key in required_home_keys:
                if key not in home_config:
                    raise ValueError(
                        f"Required home setting '{key}' is missing from config.yaml"
                    )

            settings = {
                "battery": {
                    # Hardware settings (from battery section)
                    "totalCapacity": battery_config["total_capacity"],
                    "minSoc": battery_config["min_soc"],
                    "maxSoc": battery_config["max_soc"],
                    "cycleCostPerKwh": battery_config["cycle_cost"],
                    "maxChargePowerKw": battery_config["max_charge_discharge_power"],
                    "maxDischargePowerKw": battery_config["max_charge_discharge_power"],
                    "modbusTouControl": battery_config.get("modbus_tou_control", False),
                    "modbusTouEntityPrefix": battery_config.get("modbus_tou_entity_prefix", ""),
                    "exportLimitEnableOption": battery_config.get("export_limit_enable_option", "Meter 1"),
                    "exportLimitSimulation": battery_config.get("export_limit_simulation", True),
                    # DP/algorithm settings (from dp section, fallback to battery for migration)
                    "minActionProfitThreshold": dp_config["min_action_profit_threshold"],
                    "idleDeadbandPct": dp_config.get("idle_deadband_pct", 2.0),
                    "gridChargeMaxSolarThresholdKwh": dp_config.get("grid_charge_max_solar_threshold_kwh", 0.1),
                    "gridChargeMinHeadroomKwh": dp_config.get("grid_charge_min_headroom_kwh", 0.9),
                    "idleEnabled": dp_config.get("idle_enabled", True),
                    "exportLookAheadGuard": dp_config.get("export_look_ahead_guard", False),
                    "exportPostprocessReorder": dp_config.get("export_postprocess_reorder", False),
                    "dpSoeStates": dp_config.get("soe_states", dp_config.get("dp_soe_states", 100)),
                    "dischargeTiebreakerEnabled": dp_config.get("discharge_tiebreaker_enabled", False),
                    "dischargeTiebreakerEpsilon": dp_config.get("discharge_tiebreaker_epsilon", 0.003),
                    "consumptionRoundingEnabled": dp_config.get("consumption_rounding_enabled", False),
                    "consumptionRoundingStep": dp_config.get("consumption_rounding_step", 0.025),
                    "loadSegmentsEnabled": dp_config.get("load_segments", {}).get("enabled", False),
                    "loadSegmentsEveningStart": dp_config.get("load_segments", {}).get("evening_start", "19:30"),
                    "loadSegmentsEveningEnd": dp_config.get("load_segments", {}).get("evening_end", "01:00"),
                    "loadSegmentsNightStart": dp_config.get("load_segments", {}).get("night_start", "01:00"),
                    "loadSegmentsNightEnd": dp_config.get("load_segments", {}).get("night_end", "07:00"),
                },
                "home": {
                    "defaultHourly": home_config["consumption"],
                    "currency": home_config["currency"],
                    "maxFuseCurrent": home_config["max_fuse_current"],
                    "voltage": home_config["voltage"],
                    "safetyMargin": home_config["safety_margin_factor"],
                    "phaseCount": home_config["phase_count"],
                    "consumptionStrategy": home_config["consumption_strategy"],
                    "historyDays": home_config.get("history_days", 1),
                    "inverterPhase": home_config.get("inverter_phase", ""),
                },
                "price": {
                    "area": electricity_price_config["area"],
                    "markupRate": electricity_price_config["markup_rate"],
                    "vatMultiplier": electricity_price_config["vat_multiplier"],
                    "additionalCosts": electricity_price_config["additional_costs"],
                    "taxReduction": electricity_price_config["tax_reduction"],
                },
            }

            logger.debug(f"Formatted settings: {json.dumps(settings, indent=2)}")
            self.system.update_settings(settings)
            logger.info("All settings applied successfully")

        except Exception as e:
            logger.error(
                f"CRITICAL: Failed to apply settings from config.yaml: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Settings application failed - system cannot start safely. "
                f"Check config.yaml for invalid or missing settings. Error: {e}"
            ) from e

    def start(self):
        """Start the scheduler."""
        self.system.start()
        now = datetime.now()
        current_period = now.hour * 4 + now.minute // 15
        self.system.update_battery_schedule(current_period=current_period)
        self._init_scheduler_jobs()
        logger.info("Scheduler started successfully")


# Global BESS controller instance
bess_controller = BESSController()
bess_controller.start()

# Get ingress base path, important for Home Assistant ingress
ingress_base_path = os.environ.get("INGRESS_BASE_PATH", "/local_bess_manager/ingress")


# Handle root and ingress paths
@app.get("/")
async def root_index():
    logger.info("Root path requested")
    return FileResponse("/app/frontend/index.html")


# All API endpoints are found in api.py and are imported via the router
# The endpoints router is included in the app instance at the top of this file


# SPA catch-all: serve index.html for any path not matched by API or asset routes
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str, request: Request):
    return FileResponse("/app/frontend/index.html")
