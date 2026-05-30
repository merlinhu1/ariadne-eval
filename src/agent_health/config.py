from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG = """# Ariadne Eval / agent-health configuration
thresholds:
  prolonged_tool_calls: 8
  prolonged_api_calls: 4
  prolonged_turn_minutes: 10
  repeated_same_tool_same_args: 3
  long_tool_result_chars: 8000
judge:
  provider: main
  model: main
  temperature: 0
  max_retries: 2
  prompt_version: turn_case_review_v1
  judgement_threshold: strict
"""


def instruction_health_dir(hermes_home: str | Path) -> Path:
    return Path(hermes_home).expanduser() / "instruction-health"


def init_home(hermes_home: str | Path) -> Path:
    base = instruction_health_dir(hermes_home)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    config = base / "config.yaml"
    if not config.exists():
        config.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return base
