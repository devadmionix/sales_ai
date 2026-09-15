# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Provider-agnostic LLM layer for the Sales AI Agent.

Nothing in this package knows anything about sales. It provides three things:

- `Model`   — a thin wrapper over litellm, so any provider is interchangeable.
- `Tool`    — a callable with a JSON Schema and validated arguments.
- `Agent`   — the tool-calling loop, with pause/resume for human approval.

Everything sales-specific lives in `sales_ai.tools`, `sales_ai.guard` and
`sales_ai.orchestrator`.
"""

from sales_ai.llm.agent import Agent, Question, RunResult
from sales_ai.llm.model import Model
from sales_ai.llm.tool import Tool, build_schema, tool
from sales_ai.llm.types import ChatResponse, ToolCall

__all__ = [
	"Agent",
	"ChatResponse",
	"Model",
	"Question",
	"RunResult",
	"Tool",
	"ToolCall",
	"build_schema",
	"tool",
]
