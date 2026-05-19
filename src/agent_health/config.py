from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG = """# Ariadne Eval / agent-health configuration
evaluation:
  cooldown_minutes_after_session_end: 30
  cooldown_minutes_after_inactive_turn: 120
  evaluate_last_turn_without_reaction: true
  reevaluate_previous_turn_when_next_user_reaction_arrives: true
thresholds:
  prolonged_tool_calls: 8
  prolonged_api_calls: 4
  prolonged_turn_minutes: 10
  repeated_same_tool_same_args: 3
  long_tool_result_chars: 8000
capture:
  max_args_chars: 4000
  max_result_preview_chars: 4000
  hash_full_args: true
  hash_full_result: true
judge:
  provider: main
  model: main
  temperature: 0
  max_retries: 2
  prompt_version: instruction_health_v1
"""


def instruction_health_dir(hermes_home: str | Path) -> Path:
    return Path(hermes_home).expanduser() / "instruction-health"


def init_home(hermes_home: str | Path) -> Path:
    base = instruction_health_dir(hermes_home)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    (base / "events.jsonl").touch(exist_ok=True)
    config = base / "config.yaml"
    if not config.exists():
        config.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return base
