import tempfile
import unittest
from pathlib import Path

from agent_health.scheduler_bootstrap import (
    DEFAULT_SCHEDULER_POLL_SECONDS,
    DEFAULT_WATCHDOG_SCHEDULE,
    WATCHDOG_SCRIPT_NAME,
    build_scheduler_watchdog_script,
    install_scheduler_watchdog_script,
)


class SchedulerWatchdogScriptTest(unittest.TestCase):
    def test_generated_watchdog_script_compiles_and_uses_ten_minute_poll(self):
        script = build_scheduler_watchdog_script(
            hermes_home="/tmp/hermes-home",
            python_executable="/usr/bin/python3",
        )

        compile(script, "<ariadne-eval-scheduler-watchdog>", "exec")
        self.assertIn("POLL_SECONDS = 600.0", script)
        self.assertIn('json.dumps({"pid": proc.pid, "cmd": cmd}, indent=2) + "\\n"', script)

    def test_install_scheduler_watchdog_script_writes_executable_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = install_scheduler_watchdog_script(
                tmp,
                python_executable="/usr/bin/python3",
                poll_seconds=DEFAULT_SCHEDULER_POLL_SECONDS,
            )

            self.assertEqual(path, Path(tmp) / "scripts" / WATCHDOG_SCRIPT_NAME)
            script = path.read_text(encoding="utf-8")
            compile(script, str(path), "exec")
            self.assertTrue(path.stat().st_mode & 0o100)

    def test_default_watchdog_schedule_is_ten_minutes(self):
        self.assertEqual(DEFAULT_WATCHDOG_SCHEDULE, "every 10m")
        self.assertEqual(DEFAULT_SCHEDULER_POLL_SECONDS, 600.0)


if __name__ == "__main__":
    unittest.main()
