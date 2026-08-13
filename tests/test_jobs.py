"""Tests for the periodic background job system.

Covers registration, execution, error handling, status reporting,
and lifecycle (start/stop).
"""

from __future__ import annotations

import asyncio

import pytest

from app.jobs import JobScheduler, job_scheduler, register_job

# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

class TestRegister:
    """Job registration."""

    def test_register_adds_job(self):
        sched = JobScheduler()
        sched.register("test-job", 60, lambda: None)
        assert "test-job" in sched.job_names

    def test_register_replaces_existing(self):
        sched = JobScheduler()
        sched.register("job", 60, lambda: None)
        new_func = lambda: None
        sched.register("job", 30, new_func)
        assert len(sched.job_names) == 1
        status = sched.get_status("job")
        assert status.interval == 30

    def test_register_rejects_zero_interval(self):
        sched = JobScheduler()
        with pytest.raises(ValueError, match="positive"):
            sched.register("bad", 0, lambda: None)

    def test_register_rejects_negative_interval(self):
        sched = JobScheduler()
        with pytest.raises(ValueError, match="positive"):
            sched.register("bad", -5, lambda: None)

    def test_register_job_module_level(self):
        """The module-level convenience function works."""
        try:
            register_job("temp-test", 999, lambda: None)
            assert "temp-test" in job_scheduler.job_names
        finally:
            job_scheduler.unregister("temp-test")
        assert "temp-test" not in job_scheduler.job_names


# --------------------------------------------------------------------------- #
# Unregistration
# --------------------------------------------------------------------------- #

class TestUnregister:
    """Job unregistration."""

    def test_unregister_existing(self):
        sched = JobScheduler()
        sched.register("job", 60, lambda: None)
        assert sched.unregister("job") is True
        assert "job" not in sched.job_names

    def test_unregister_nonexistent(self):
        sched = JobScheduler()
        assert sched.unregister("nope") is False


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

class TestStatus:
    """Status reporting."""

    def test_status_returns_list(self):
        sched = JobScheduler()
        sched.register("a", 60, lambda: None)
        sched.register("b", 120, lambda: None)
        statuses = sched.status()
        assert len(statuses) == 2
        names = {s.name for s in statuses}
        assert names == {"a", "b"}

    def test_status_fields(self):
        sched = JobScheduler()
        sched.register("job", 60, lambda: None)
        st = sched.get_status("job")
        assert st is not None
        assert st.name == "job"
        assert st.interval == 60
        assert st.running is False
        assert st.success_count == 0
        assert st.error_count == 0
        assert st.last_run is None
        assert st.last_error is None

    def test_get_status_nonexistent(self):
        sched = JobScheduler()
        assert sched.get_status("nope") is None

    def test_to_dict(self):
        sched = JobScheduler()
        sched.register("job", 60, lambda: None)
        st = sched.get_status("job")
        d = st.to_dict()
        assert d["name"] == "job"
        assert d["interval"] == 60
        assert d["running"] is False
        assert d["success_count"] == 0
        assert d["last_run"] is None


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

class TestExecution:
    """Job execution behaviour."""

    @pytest.mark.asyncio
    async def test_async_job_runs_immediately(self):
        sched = JobScheduler()
        call_count = 0

        async def my_job():
            nonlocal call_count
            call_count += 1

        sched.register("test", 999, my_job)
        await sched.start_all()
        # Give the immediate run a moment to complete
        await asyncio.sleep(0.1)
        assert call_count >= 1
        assert sched.get_status("test").success_count >= 1
        await sched.stop_all()

    @pytest.mark.asyncio
    async def test_sync_job_runs(self):
        sched = JobScheduler()
        call_count = 0

        def my_job():
            nonlocal call_count
            call_count += 1

        sched.register("test", 999, my_job)
        await sched.start_all()
        await asyncio.sleep(0.1)
        assert call_count >= 1
        await sched.stop_all()

    @pytest.mark.asyncio
    async def test_job_error_does_not_crash_loop(self):
        sched = JobScheduler()
        call_count = 0

        async def always_failing():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("always fails")

        sched.register("test", 0.05, always_failing)
        await sched.start_all()
        await asyncio.sleep(0.2)
        await sched.stop_all()

        st = sched.get_status("test")
        assert st.error_count >= 2
        assert st.last_error is not None
        assert "always fails" in st.last_error
        assert st.success_count == 0
        assert call_count >= 2  # ran again after the error

    @pytest.mark.asyncio
    async def test_stop_all_cancels_jobs(self):
        sched = JobScheduler()
        call_count = 0

        async def my_job():
            nonlocal call_count
            call_count += 1

        sched.register("test", 0.01, my_job)
        await sched.start_all()
        await asyncio.sleep(0.05)
        await sched.stop_all()
        count_after_stop = call_count
        await asyncio.sleep(0.1)
        assert call_count == count_after_stop  # no more calls after stop

    @pytest.mark.asyncio
    async def test_start_all_idempotent(self):
        sched = JobScheduler()
        call_count = 0

        async def my_job():
            nonlocal call_count
            call_count += 1

        sched.register("test", 999, my_job)
        await sched.start_all()
        await sched.start_all()  # should not create duplicate tasks
        await asyncio.sleep(0.15)
        assert call_count == 1  # only one immediate run
        await sched.stop_all()


# --------------------------------------------------------------------------- #
# Lifecycle with multiple jobs
# --------------------------------------------------------------------------- #

class TestMultipleJobs:
    """Multiple jobs running concurrently."""

    @pytest.mark.asyncio
    async def test_two_jobs_run_independently(self):
        sched = JobScheduler()
        results = {"a": 0, "b": 0}

        async def job_a():
            results["a"] += 1

        async def job_b():
            results["b"] += 1

        sched.register("a", 0.02, job_a)
        sched.register("b", 0.02, job_b)
        await sched.start_all()
        await asyncio.sleep(0.1)
        await sched.stop_all()

        assert results["a"] >= 1
        assert results["b"] >= 1
