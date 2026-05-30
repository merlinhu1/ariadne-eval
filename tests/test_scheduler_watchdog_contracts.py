import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from agent_health.cli import build_parser
from agent_health.scheduler_bootstrap import (
    DEFAULT_SCHEDULER_POLL_SECONDS,
    DEFAULT_WATCHDOG_SCHEDULE,
    WATCHDOG_JOB_NAME,
    WATCHDOG_SCRIPT_NAME,
    install_scheduler_watchdog,
    register_scheduler_watchdog_cron,
)


class SchedulerWatchdogContractTest(unittest.TestCase):
    def test_dashboard_install_parser_uses_watchdog_defaults(self):
        args = build_parser().parse_args(["dashboard", "install"])

        self.assertFalse(args.no_scheduler_watchdog)
        self.assertEqual(args.watchdog_schedule, DEFAULT_WATCHDOG_SCHEDULE)
        self.assertEqual(args.scheduler_poll_seconds, DEFAULT_SCHEDULER_POLL_SECONDS)

    def test_install_scheduler_watchdog_writes_script_and_registers_cron(self):
        calls = []

        def fake_register(hermes_home, *, schedule, script_name):
            calls.append((Path(hermes_home), schedule, script_name))
            return "job-1", True

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "agent_health.scheduler_bootstrap.register_scheduler_watchdog_cron",
                side_effect=fake_register,
            ):
                result = install_scheduler_watchdog(
                    tmp,
                    schedule="every 5m",
                    python_executable="/usr/bin/python3",
                    poll_seconds=42.0,
                )

            self.assertEqual(result.script_path, Path(tmp) / "scripts" / WATCHDOG_SCRIPT_NAME)
            self.assertEqual(calls, [(Path(tmp), "every 5m", WATCHDOG_SCRIPT_NAME)])
            self.assertEqual(result.job_id, "job-1")
            self.assertTrue(result.job_created)
            self.assertTrue(result.job_registered)
            self.assertIsNone(result.error)

            script = result.script_path.read_text(encoding="utf-8")
            compile(script, str(result.script_path), "exec")
            self.assertIn("POLL_SECONDS = 42.0", script)
            self.assertIn('"scheduler"', script)
            self.assertIn('"run"', script)
            self.assertTrue(result.script_path.stat().st_mode & 0o100)

    def test_register_scheduler_watchdog_cron_updates_existing_job_without_live_cron(self):
        cron_package = types.ModuleType("cron")
        cron_package.__path__ = []
        cron_jobs = types.ModuleType("cron.jobs")
        updates = []

        cron_jobs.list_jobs = mock.Mock(
            return_value=[
                {
                    "id": "existing-job",
                    "name": WATCHDOG_JOB_NAME,
                    "enabled": False,
                    "state": "paused",
                }
            ]
        )
        cron_jobs.update_job = mock.Mock(
            side_effect=lambda job_id, payload: updates.append((job_id, payload)) or {"id": job_id, **payload}
        )
        cron_jobs.create_job = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp:
            previous_home = os.environ.get("HERMES_HOME")
            os.environ["HERMES_HOME"] = "/original/hermes"
            try:
                with mock.patch.dict(sys.modules, {"cron": cron_package, "cron.jobs": cron_jobs}):
                    job_id, created = register_scheduler_watchdog_cron(
                        tmp,
                        schedule="every 7m",
                        script_name="custom_watchdog.py",
                    )
            finally:
                if previous_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = previous_home

            self.assertEqual(job_id, "existing-job")
            self.assertFalse(created)
            cron_jobs.list_jobs.assert_called_once_with(include_disabled=True)
            cron_jobs.create_job.assert_not_called()
            self.assertEqual(cron_jobs.HERMES_DIR, Path(tmp).resolve())
            self.assertEqual(os.environ.get("HERMES_HOME"), previous_home)

        updated_id, payload = updates[0]
        self.assertEqual(updated_id, "existing-job")
        self.assertEqual(payload["name"], WATCHDOG_JOB_NAME)
        self.assertEqual(payload["schedule"], "every 7m")
        self.assertEqual(payload["script"], "custom_watchdog.py")
        self.assertTrue(payload["no_agent"])
        self.assertEqual(payload["deliver"], "local")
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["state"], "scheduled")
        self.assertIsNone(payload["paused_at"])
        self.assertIsNone(payload["paused_reason"])

    def test_register_scheduler_watchdog_cron_creates_missing_job_without_live_cron(self):
        cron_package = types.ModuleType("cron")
        cron_package.__path__ = []
        cron_jobs = types.ModuleType("cron.jobs")

        cron_jobs.list_jobs = mock.Mock(return_value=[])
        cron_jobs.update_job = mock.Mock()
        cron_jobs.create_job = mock.Mock(return_value={"id": "new-job"})

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(sys.modules, {"cron": cron_package, "cron.jobs": cron_jobs}):
                job_id, created = register_scheduler_watchdog_cron(tmp)

        self.assertEqual(job_id, "new-job")
        self.assertTrue(created)
        cron_jobs.update_job.assert_not_called()
        cron_jobs.create_job.assert_called_once_with(
            prompt="",
            schedule=DEFAULT_WATCHDOG_SCHEDULE,
            name=WATCHDOG_JOB_NAME,
            deliver="local",
            script=WATCHDOG_SCRIPT_NAME,
            no_agent=True,
        )


if __name__ == "__main__":
    unittest.main()
