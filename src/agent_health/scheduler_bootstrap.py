from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


WATCHDOG_SCRIPT_NAME = "ariadne_eval_scheduler_watchdog.py"
WATCHDOG_JOB_NAME = "Ariadne Eval scheduler watchdog"
DEFAULT_WATCHDOG_SCHEDULE = "every 10m"
DEFAULT_SCHEDULER_POLL_SECONDS = 600.0


@dataclass(frozen=True)
class SchedulerWatchdogInstallResult:
    script_path: Path
    job_id: str | None
    job_created: bool
    job_registered: bool
    error: str | None = None


def _quote_script_literal(value: str) -> str:
    return repr(value)


def build_scheduler_watchdog_script(
    *,
    hermes_home: str | Path,
    python_executable: str | Path | None = None,
    poll_seconds: float = DEFAULT_SCHEDULER_POLL_SECONDS,
) -> str:
    """Return a quiet Hermes cron watchdog script for the Ariadne scheduler daemon."""
    home = str(Path(hermes_home).expanduser())
    python = str(python_executable or sys.executable)
    poll = float(poll_seconds)
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

HERMES_HOME = Path({_quote_script_literal(home)})
PYTHON = {_quote_script_literal(python)}
POLL_SECONDS = {poll!r}
PID_FILE = HERMES_HOME / "instruction-health" / "scheduler.pid"
LOG_FILE = HERMES_HOME / "logs" / "ariadne-eval-scheduler.log"


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _current_pid() -> int | None:
    try:
        payload = json.loads(PID_FILE.read_text(encoding="utf-8"))
        return int(payload.get("pid") or 0)
    except Exception:
        return None


def _scheduler_process_running() -> bool:
    pid = _current_pid()
    if pid and _pid_running(pid):
        return True
    if os.name == "nt":
        return False
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return False
    needle_home = str(HERMES_HOME)
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or str(os.getpid()) in text.split(maxsplit=1)[:1]:
            continue
        if "scheduler" in text and "run" in text and needle_home in text and ("agent-health" in text or "agent_health.cli" in text):
            return True
    return False


def main() -> int:
    if _scheduler_process_running():
        return 0

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(HERMES_HOME)
    env.setdefault("PYTHONUNBUFFERED", "1")
    cmd = [
        PYTHON,
        "-m",
        "agent_health.cli",
        "--hermes-home",
        str(HERMES_HOME),
        "scheduler",
        "run",
        "--poll-seconds",
        str(POLL_SECONDS),
    ]
    log = LOG_FILE.open("ab")
    popen_kwargs = dict(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)
    PID_FILE.write_text(
        json.dumps({{"pid": proc.pid, "cmd": cmd}}, indent=2) + "\\n",
        encoding="utf-8",
    )
    print(f"Started Ariadne Eval scheduler daemon pid={{proc.pid}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def install_scheduler_watchdog_script(
    hermes_home: str | Path,
    *,
    python_executable: str | Path | None = None,
    poll_seconds: float = DEFAULT_SCHEDULER_POLL_SECONDS,
) -> Path:
    home = Path(hermes_home).expanduser()
    script_path = home / "scripts" / WATCHDOG_SCRIPT_NAME
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        build_scheduler_watchdog_script(
            hermes_home=home,
            python_executable=python_executable,
            poll_seconds=poll_seconds,
        ),
        encoding="utf-8",
    )
    try:
        mode = script_path.stat().st_mode
        script_path.chmod(mode | stat.S_IXUSR)
    except OSError:
        pass
    return script_path


def _set_hermes_home_for_cron_import(hermes_home: Path):
    previous = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = str(hermes_home)
    return previous


def _restore_hermes_home(previous: str | None) -> None:
    if previous is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = previous


def register_scheduler_watchdog_cron(
    hermes_home: str | Path,
    *,
    schedule: str = DEFAULT_WATCHDOG_SCHEDULE,
    script_name: str = WATCHDOG_SCRIPT_NAME,
) -> tuple[str | None, bool]:
    """Create or update the Hermes cron watchdog job.

    Returns ``(job_id, created)``.  The import is intentionally local so
    installations outside Hermes can still copy the dashboard plugin and script;
    callers surface a concise warning when Hermes cron modules are unavailable.
    """
    home = Path(hermes_home).expanduser()
    previous_home = _set_hermes_home_for_cron_import(home)
    try:
        import cron.jobs as cron_jobs  # type: ignore
    finally:
        _restore_hermes_home(previous_home)

    # Hermes cron stores profile paths in module globals computed at import time.
    # If another module imported cron.jobs before this installer set HERMES_HOME,
    # retarget those globals so the install writes to the requested Hermes home.
    cron_jobs.HERMES_DIR = home.resolve()
    cron_jobs.CRON_DIR = cron_jobs.HERMES_DIR / "cron"
    cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
    cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"

    for job in cron_jobs.list_jobs(include_disabled=True):
        if job.get("name") != WATCHDOG_JOB_NAME:
            continue
        updated = cron_jobs.update_job(
            job["id"],
            {
                "prompt": "",
                "name": WATCHDOG_JOB_NAME,
                "schedule": schedule,
                "script": script_name,
                "no_agent": True,
                "deliver": "local",
                "enabled": True,
                "state": "scheduled",
                "paused_at": None,
                "paused_reason": None,
            },
        )
        return (updated or job).get("id"), False

    created = cron_jobs.create_job(
        prompt="",
        schedule=schedule,
        name=WATCHDOG_JOB_NAME,
        deliver="local",
        script=script_name,
        no_agent=True,
    )
    return created.get("id"), True


def install_scheduler_watchdog(
    hermes_home: str | Path,
    *,
    schedule: str = DEFAULT_WATCHDOG_SCHEDULE,
    python_executable: str | Path | None = None,
    poll_seconds: float = DEFAULT_SCHEDULER_POLL_SECONDS,
) -> SchedulerWatchdogInstallResult:
    script_path = install_scheduler_watchdog_script(
        hermes_home,
        python_executable=python_executable,
        poll_seconds=poll_seconds,
    )
    try:
        job_id, created = register_scheduler_watchdog_cron(
            hermes_home,
            schedule=schedule,
            script_name=script_path.name,
        )
    except Exception as exc:  # pragma: no cover - exact Hermes import/runtime varies by install
        return SchedulerWatchdogInstallResult(
            script_path=script_path,
            job_id=None,
            job_created=False,
            job_registered=False,
            error=str(exc),
        )
    return SchedulerWatchdogInstallResult(
        script_path=script_path,
        job_id=job_id,
        job_created=created,
        job_registered=True,
        error=None,
    )
