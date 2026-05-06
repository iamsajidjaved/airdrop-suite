"""Background scheduler for the airdrop monitor.

Runs `AirdropMonitorService.run_monitor()` on a fixed interval inside the
FastAPI event loop. The loop:

* Never dies on errors — each iteration's exception is logged and the loop
  continues after the configured interval.
* Is naturally drift-free in the "don't miss records" sense because the monitor
  resumes from each token's `last_scanned_block` on every run.
* Shuts down cleanly when the FastAPI lifespan exits (cancels and awaits the
  task).

State is exposed via `get_state()` so the admin UI / status endpoint can show
the last run timestamp, last error, and whether the loop is currently running.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.config import settings
from backend.services.airdrop_monitor import AirdropMonitorService

logger = logging.getLogger(__name__)


@dataclass
class SchedulerState:
    enabled: bool = False
    running: bool = False
    interval_seconds: int = 0
    started_at: Optional[str] = None
    last_run_started_at: Optional[str] = None
    last_run_finished_at: Optional[str] = None
    last_run_duration_seconds: Optional[float] = None
    last_run_inserted: int = 0
    last_run_errors: list[str] = field(default_factory=list)
    last_error: Optional[str] = None
    next_run_eta: Optional[str] = None
    total_runs: int = 0
    total_inserted: int = 0


class AirdropScheduler:
    def __init__(self, monitor: Optional[AirdropMonitorService] = None):
        self._monitor = monitor or AirdropMonitorService()
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._state = SchedulerState(
            enabled=settings.airdrop_scheduler_enabled,
            interval_seconds=settings.airdrop_scheduler_interval_seconds,
        )

    @property
    def state(self) -> SchedulerState:
        return self._state

    def start(self) -> None:
        if not settings.airdrop_scheduler_enabled:
            logger.info("Airdrop scheduler disabled via settings; not starting.")
            self._state.enabled = False
            return
        if self._task is not None and not self._task.done():
            logger.info("Airdrop scheduler already running.")
            return

        self._stop_event = asyncio.Event()
        self._state.enabled = True
        self._state.running = True
        self._state.started_at = _now_iso()
        self._state.interval_seconds = settings.airdrop_scheduler_interval_seconds
        self._task = asyncio.create_task(self._run_loop(), name="airdrop-scheduler")
        logger.info(
            "Airdrop scheduler started (interval=%ss, initial_delay=%ss).",
            settings.airdrop_scheduler_interval_seconds,
            settings.airdrop_scheduler_initial_delay_seconds,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception) as e:  # noqa: BLE001
            logger.debug("Scheduler task ended: %s", e)
        finally:
            self._task = None
            self._stop_event = None
            self._state.running = False
            self._state.next_run_eta = None
            logger.info("Airdrop scheduler stopped.")

    async def trigger_now(self) -> None:
        """Run a single iteration immediately, off the scheduler cadence."""
        await self._run_once()

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        try:
            # Initial delay so app startup isn't blocked by a long first scan.
            initial_delay = max(0, settings.airdrop_scheduler_initial_delay_seconds)
            if initial_delay:
                self._state.next_run_eta = _eta_iso(initial_delay)
                if await _wait_or_stop(self._stop_event, initial_delay):
                    return

            while True:
                await self._run_once()
                interval = max(5, settings.airdrop_scheduler_interval_seconds)
                self._state.next_run_eta = _eta_iso(interval)
                if await _wait_or_stop(self._stop_event, interval):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — guard against catastrophic loop death
            logger.exception("Airdrop scheduler loop crashed: %s", e)
            self._state.last_error = f"loop crashed: {e}"
            self._state.running = False

    async def _run_once(self) -> None:
        started = datetime.now(tz=timezone.utc)
        self._state.last_run_started_at = started.isoformat()
        try:
            result = await self._monitor.run_monitor()
            finished = datetime.now(tz=timezone.utc)
            self._state.last_run_finished_at = finished.isoformat()
            self._state.last_run_duration_seconds = (finished - started).total_seconds()
            self._state.last_run_inserted = result.new_transfers_inserted
            self._state.last_run_errors = list(result.errors or [])
            self._state.total_runs += 1
            self._state.total_inserted += result.new_transfers_inserted
            if result.errors:
                # Keep loop alive but surface the first error in state.
                self._state.last_error = result.errors[0]
            else:
                self._state.last_error = None
            logger.info(
                "Scheduler run done: inserted=%s tokens=%s errors=%s",
                result.new_transfers_inserted,
                result.tokens_scanned,
                len(result.errors or []),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            finished = datetime.now(tz=timezone.utc)
            self._state.last_run_finished_at = finished.isoformat()
            self._state.last_run_duration_seconds = (finished - started).total_seconds()
            self._state.last_error = f"{type(e).__name__}: {e}"
            logger.exception("Scheduler iteration failed: %s", e)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _eta_iso(seconds: float) -> str:
    from datetime import timedelta
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)).isoformat()


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    """Sleep up to `seconds`, returning True if stop was requested."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


# Module-level singleton, mirroring monitor_service.
scheduler = AirdropScheduler()
