"""Lightweight import and local-tool smoke test."""

from __future__ import annotations

from assistant import root_agent
from assistant.tools import add_todo, get_daily_summary, log_meal, log_sleep


def main() -> int:
    print(f"agent={root_agent.name}")
    todo = add_todo("smoke test todo", priority="low", notes="created by smoke_test")
    meal = log_meal("snack", notes="created by smoke_test")
    sleep = log_sleep(quality="test", notes="created by smoke_test")
    summary = get_daily_summary()
    print({"todo_ok": todo["ok"], "meal_ok": meal["ok"], "sleep_ok": sleep["ok"], "summary_ok": summary["ok"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
