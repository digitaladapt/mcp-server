"""Periodic background job system.

A lightweight scheduler that runs registered callables on a fixed interval
in an asyncio background task.  Jobs are registered with a name, an interval
(in seconds), and an async (or sync) callable.  The scheduler manages a
single asyncio task per job and handles graceful shutdown via
:func:`JobScheduler.stop_all`.

Design goals:

* **Extensible** – any callable can be registered as a job; the system is
  not tied to any particular domain (ICS, CalDAV, etc.).
* **Self-healing** – if a job raises an exception, the error is logged but
  the job continues to run on its next scheduled interval.
* **Observable** – :meth:`JobScheduler.status` returns a list of
  :class:`JobStatus` objects describing each job's last run, next run,
  success/failure count, and whether it is currently active.
* **Lifecycle-aware** – started/stopped via FastAPI lifespan events so
  jobs are cleanly cancelled on shutdown.

Usage::

    from app.jobs import job_scheduler, register_job

    async def refresh_cache() -> None:
        ...

    register_job("refresh-ics-cache", interval=300, func=refresh_cache)
    await job_scheduler.start_all()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# A job callable can be sync or async.  Sync callables are run directly
# (they should be non-blocking / very quick).  Async callables are awaited.
JobFunc = Callable[[], None] | Callable[[], Awaitable[None]]


@dataclass
class JobStatus:
    """Runtime status of a single scheduled job."""

    name: str
    interval: float
    running: bool
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_error: str | None = None
    success_count: int = 0
    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON responses."""
        return {
            "name": self.name,
            "interval": self.interval,
            "running": self.running,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "last_error": self.last_error,
            "success_count": self.success_count,
            "error_count": self.error_count,
        }


@dataclass
class _JobEntry:
    """Internal bookkeeping for a registered job."""

    name: str
    interval: float
    func: JobFunc
    task: asyncio.Task[None] | None = None
    status: JobStatus = field(default_factory=lambda: JobStatus(
        name="", interval=0, running=False,
    ))

    def __post_init__(self) -> None:
        self.status.name = self.name
        self.status.interval = self.interval


class JobScheduler:
    """Manages periodic background jobs.

    A single instance (``job_scheduler``) is shared app-wide.  Jobs are
    registered before startup and started via :meth:`start_all` (typically
    from a FastAPI lifespan handler).  :meth:`stop_all` cancels all running
    jobs and awaits their cleanup.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _JobEntry] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register(
        self,
        name: str,
        interval: float,
        func: JobFunc,
    ) -> None:
        """Register a job for periodic execution.

        If a job with *name* already exists, it is replaced.

        Parameters
        ----------
        name:
            Unique identifier for the job.
        interval:
            Seconds between runs.  Must be positive.
        func:
            A sync or async callable taking no arguments.
        """
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        entry = _JobEntry(name=name, interval=interval, func=func)
        self._jobs[name] = entry
        logger.info("Registered job '%s' (interval=%ss)", name, interval)

    def unregister(self, name: str) -> bool:
        """Unregister a job.  Returns True if the job existed."""
        entry = self._jobs.pop(name, None)
        if entry is None:
            return False
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()
        return True

    @property
    def job_names(self) -> list[str]:
        """Names of all registered jobs."""
        return list(self._jobs)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start_all(self) -> None:
        """Start all registered jobs that are not already running."""
        for entry in self._jobs.values():
            if entry.task is not None and not entry.task.done():
                continue
            entry.task = asyncio.create_task(
                self._run_loop(entry),
                name=f"job:{entry.name}",
            )
            entry.status.running = True
            logger.info("Started job '%s'", entry.name)

    async def stop_all(self) -> None:
        """Cancel all running jobs and wait for them to finish."""
        tasks = [
            entry.task for entry in self._jobs.values()
            if entry.task is not None and not entry.task.done()
        ]
        for entry in self._jobs.values():
            if entry.task is not None and not entry.task.done():
                entry.task.cancel()
            entry.status.running = False
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Stopped all jobs")

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def status(self) -> list[JobStatus]:
        """Return status of all registered jobs."""
        return [entry.status for entry in self._jobs.values()]

    def get_status(self, name: str) -> JobStatus | None:
        """Return status of a single job, or None if not registered."""
        entry = self._jobs.get(name)
        return entry.status if entry else None

    # ------------------------------------------------------------------ #
    # Internal runner
    # ------------------------------------------------------------------ #

    async def _run_loop(self, entry: _JobEntry) -> None:
        """Run a job on a fixed interval until cancelled."""
        # Run once immediately on startup, then on interval.
        await self._run_once(entry)
        while True:
            await asyncio.sleep(entry.interval)
            await self._run_once(entry)

    async def _run_once(self, entry: _JobEntry) -> None:
        """Execute a single job invocation, handling errors."""
        now = datetime.now(UTC)
        entry.status.last_run = now
        entry.status.next_run = datetime.fromtimestamp(
            now.timestamp() + entry.interval, tz=UTC,
        )
        try:
            result = entry.func()
            if asyncio.iscoroutine(result):
                await result
            entry.status.success_count += 1
            entry.status.last_error = None
        except Exception as exc:
            entry.status.error_count += 1
            entry.status.last_error = str(exc)
            logger.exception("Job '%s' failed", entry.name)


# --------------------------------------------------------------------------- #
# Module-level singleton
# --------------------------------------------------------------------------- #

job_scheduler = JobScheduler()


def register_job(name: str, interval: float, func: JobFunc) -> None:
    """Convenience wrapper around :meth:`JobScheduler.register`."""
    job_scheduler.register(name, interval, func)
