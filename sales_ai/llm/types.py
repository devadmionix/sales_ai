# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Shared value objects passed between the model, the agent loop and the API layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


@dataclass
class ToolCall:
	"""A tool invocation requested by the model.

	`error` is set when the model emitted arguments that are not valid JSON. The call is
	then fed back to the model as an error rather than executed, so it can correct itself.
	"""

	id: str
	name: str
	arguments: dict[str, Any] = field(default_factory=dict)
	error: str | None = None


@dataclass
class ChatResponse:
	"""One completed model turn."""

	content: str | None = None
	tool_calls: list[ToolCall] = field(default_factory=list)
	usage: dict[str, int] = field(default_factory=dict)
	finish_reason: str | None = None
	model: str | None = None


@dataclass
class ToolCallBegin:
	"""Emitted mid-stream the moment the model names a tool, before its arguments finish
	streaming. Lets the UI show what is happening without waiting for the full payload."""

	id: str
	name: str


@dataclass
class Notice:
	"""Something the user should know that is not part of the answer.

	Kept separate from the text deltas because it is not the assistant speaking: it never
	enters the transcript, so it cannot be mistaken later for something the model said.
	A waiting user who is told why is not a user watching a frozen panel.
	"""

	message: str


def empty_usage() -> dict[str, int]:
	return dict.fromkeys(USAGE_KEYS, 0)


def add_usage(total: dict[str, int], delta: dict[str, int] | None) -> dict[str, int]:
	"""Accumulate token counts across the turns of a single run."""
	for key in USAGE_KEYS:
		total[key] = total.get(key, 0) + int((delta or {}).get(key) or 0)
	return total
