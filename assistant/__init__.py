"""Work Life Agent package."""

from typing import Any

__all__ = ["root_agent"]


def __getattr__(name: str) -> Any:
    if name == "root_agent":
        from .agent import root_agent

        return root_agent
    raise AttributeError(name)
