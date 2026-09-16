# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The tool registry: the complete list of things the agent is able to do.

This is a closed world. The model can only ever call a tool that some module here has
registered by hand, with a typed signature. There is no generic "read any DocType",
"run any whitelisted method" or "execute this Python" tool, because such a tool would
hand the model the whole database and make every other control decorative.

Tools are registered as a side effect of importing their module, so `MODULES` below is
the single place that decides what exists.
"""

from __future__ import annotations

import importlib

from sales_ai.llm.tool import Tool

# Modules that register tools. Adding a tool means adding it to one of these.
MODULES: tuple[str, ...] = (
	"sales_ai.tools.read",
	"sales_ai.tools.analyse",
	"sales_ai.tools.insight",
	"sales_ai.tools.sell",
	"sales_ai.tools.write",
	# For the customer-facing agent only. Registering a tool makes it *nameable* by an agent
	# profile, not available to every agent — each profile lists the tools it may call.
	"sales_ai.tools.portal",
)

_registry: dict[str, Tool] = {}
_loaded = False


def register(tool: Tool) -> Tool:
	if tool.name in _registry and _registry[tool.name] is not tool:
		raise ValueError(f"A different tool named {tool.name!r} is already registered.")
	_registry[tool.name] = tool
	return tool


def get(name: str) -> Tool | None:
	_load()
	return _registry.get(name)


def names() -> list[str]:
	_load()
	return sorted(_registry)


def select(wanted: list[str]) -> list[Tool]:
	"""Resolve names to tools, silently dropping any that no longer exist.

	A tool removed from the code should disable itself everywhere rather than break
	every agent profile that still lists it.
	"""
	_load()
	return [_registry[name] for name in wanted if name in _registry]


def _load() -> None:
	global _loaded
	if _loaded:
		return
	for module in MODULES:
		importlib.import_module(module)
	_loaded = True
