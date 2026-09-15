# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The tool-calling loop, with pause/resume for human approval.

The loop is: ask the model → run the tools it asked for → feed the results back → repeat,
until the model answers without asking for a tool or the iteration budget runs out.

The whole run state is the OpenAI-format message transcript, which is plain JSON. That is
deliberate: a run can be paused for a human, persisted, and resumed in a different
process hours later, because there is no in-memory state to lose.

This module knows nothing about sales or permissions. Whether a call may proceed, needs a
human, or is refused outright is decided by the `policy` callback supplied by the caller
(`sales_ai.guard.policy`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from sales_ai.llm.model import Model
from sales_ai.llm.tool import Tool, ToolArgumentError
from sales_ai.llm.types import ChatResponse, ToolCall, ToolCallBegin, add_usage, empty_usage

APPROVE = "Approve"
DENY = "Deny"

RunStatus = Literal["completed", "paused", "stopped"]


@dataclass
class Question:
	"""A pause. The run stops here until a human answers.

	`Approve` runs the tool, `Deny` halts the run, and any other text is handed back to
	the model as feedback so it can take a different approach.
	"""

	tool_call_id: str
	tool_name: str
	arguments: dict[str, Any]
	prompt: str
	options: list[str] = field(default_factory=lambda: [APPROVE, DENY])
	allow_other: bool = True
	# What the call would actually come to, worked out before anyone approves it. The
	# arguments alone can hide the thing a person most needs to see — "200 bolts" does not
	# tell you the money. Filled in by the policy layer; the loop only carries it.
	preview: dict[str, Any] | None = None


@dataclass
class Refusal:
	"""A call the policy will not allow at all. No human is asked; the model is told."""

	message: str


@dataclass
class Step:
	"""One executed tool call, for the audit trail."""

	tool_call_id: str
	name: str
	arguments: dict[str, Any]
	result: str
	duration_ms: int
	approved_by_human: bool = False
	error: str | None = None


@dataclass
class RunResult:
	status: RunStatus
	messages: list[dict[str, Any]]
	usage: dict[str, int] = field(default_factory=empty_usage)
	steps: list[Step] = field(default_factory=list)
	content: str | None = None
	question: Question | None = None
	iterations: int = 0


@dataclass
class ToolFinished:
	"""Streamed once a tool has run, so the UI can close out the pending indicator."""

	id: str
	name: str
	error: str | None = None


@dataclass
class Checkpoint:
	"""The transcript at the end of an iteration, when every tool call has a result.

	A long run can be killed by a worker timeout or a crash. Emitting the transcript at
	each safe point lets the caller persist it, so the run can be resumed instead of lost.
	Callers that do not care simply ignore the event.
	"""

	messages: list[dict[str, Any]]


Event = str | ToolCallBegin | ToolFinished | Checkpoint


class Agent:
	def __init__(
		self,
		model: Model,
		tools: Iterable[Tool] = (),
		*,
		system_prompt: str | None = None,
		max_iterations: int = 12,
		policy: Callable[[Tool, ToolCall], Question | Refusal | None] | None = None,
	):
		self.model = model
		self.tools = {t.name: t for t in tools}
		self.system_prompt = system_prompt
		self.max_iterations = max_iterations
		self.policy = policy

	# -- entry points ----------------------------------------------------------------

	def run(self, prompt: str, *, history: list[dict[str, Any]] | None = None) -> RunResult:
		return drain(self.stream(prompt, history=history))

	def stream(
		self, prompt: str, *, history: list[dict[str, Any]] | None = None
	) -> Generator[Event, None, RunResult]:
		messages = list(history or [])
		if self.system_prompt and not (messages and messages[0].get("role") == "system"):
			messages.insert(0, {"role": "system", "content": self.system_prompt})
		messages.append({"role": "user", "content": prompt})
		return self._loop(messages, [])

	def resume(self, messages: list[dict[str, Any]], answers: dict[str, str]) -> RunResult:
		return drain(self.resume_stream(messages, answers))

	def resume_stream(
		self, messages: list[dict[str, Any]], answers: dict[str, str]
	) -> Generator[Event, None, RunResult]:
		"""Continue a paused run. `answers` maps tool call id to the human's reply."""
		messages = list(messages)
		steps: list[Step] = []

		outcome = yield from self._settle_pending(messages, answers, steps)
		if outcome is not None:
			return outcome

		return (yield from self._loop(messages, steps))

	# -- the loop --------------------------------------------------------------------

	def _loop(
		self, messages: list[dict[str, Any]], steps: list[Step]
	) -> Generator[Event, None, RunResult]:
		usage = empty_usage()
		schemas = [t.schema() for t in self.tools.values()] or None

		for iteration in range(1, self.max_iterations + 1):
			response: ChatResponse = yield from self.model.chat(messages, tools=schemas, stream=True)
			add_usage(usage, response.usage)
			messages.append(_assistant_message(response))

			if not response.tool_calls:
				return RunResult(
					status="completed",
					messages=messages,
					usage=usage,
					steps=steps,
					content=response.content,
					iterations=iteration,
				)

			for call in response.tool_calls:
				decision = self._gate(call)

				if isinstance(decision, Question):
					# Later calls in this batch stay unanswered on purpose; resume picks
					# them up in order once a human has replied to this one.
					return RunResult(
						status="paused",
						messages=messages,
						usage=usage,
						steps=steps,
						question=decision,
						iterations=iteration,
					)

				if isinstance(decision, Refusal):
					# The model reads the refusal and gets to try something else.
					steps.append(_refused(call, decision.message))
					messages.append(_tool_message(call, json.dumps({"error": decision.message})))
					yield ToolFinished(id=call.id, name=call.name, error=decision.message)
					continue

				step = self._execute(call)
				steps.append(step)
				messages.append(_tool_message(call, step.result))
				yield ToolFinished(id=call.id, name=call.name, error=step.error)

			# Every call in the batch now has a result, so the transcript is valid to save.
			yield Checkpoint(messages=list(messages))

		# The budget exists so a model stuck in a tool loop cannot burn tokens forever.
		messages.append({"role": "assistant", "content": _BUDGET_MESSAGE})
		return RunResult(
			status="stopped",
			messages=messages,
			usage=usage,
			steps=steps,
			content=_BUDGET_MESSAGE,
			iterations=self.max_iterations,
		)

	# -- pause / resume --------------------------------------------------------------

	def _gate(self, call: ToolCall) -> Question | Refusal | None:
		"""Ask the caller's policy what to do with this call: run it, ask, or refuse."""
		if self.policy is None:
			return None
		tool = self.tools.get(call.name)
		if tool is None or call.error:
			# Nothing to decide: `_execute` turns both into an error the model reads.
			return None
		return self.policy(tool, call)

	def _settle_pending(
		self, messages: list[dict[str, Any]], answers: dict[str, str], steps: list[Step]
	) -> Generator[Event, None, RunResult | None]:
		"""Answer the tool calls that were left hanging when the run paused.

		Returns a `RunResult` if the run cannot continue (denied, or still waiting on a
		human), otherwise `None` to let the main loop carry on.
		"""
		pending_calls = _unanswered_calls(messages)
		for call in pending_calls:
			answer = (answers.get(call.id) or "").strip()

			if not answer:
				pending = self._gate(call)
				return RunResult(
					status="paused",
					messages=messages,
					steps=steps,
					question=pending if isinstance(pending, Question) else _default_question(call),
				)

			if answer == DENY:
				messages.append(_tool_message(call, json.dumps({"error": "Denied by the user."})))
				_cancel_remaining(messages)
				return RunResult(
					status="stopped",
					messages=messages,
					steps=steps,
					content="Stopped: the user denied this action.",
				)

			if answer != APPROVE:
				# Free text is redirection, not rejection: the model gets to rethink.
				messages.append(
					_tool_message(
						call,
						json.dumps({"status": "redirect", "user_feedback": answer}),
					)
				)
				continue

			# The approval may have been granted before the policy changed, or before a
			# permission was withdrawn. A human saying yes does not skip the check.
			decision = self._gate(call)
			if isinstance(decision, Refusal):
				steps.append(_refused(call, decision.message))
				messages.append(_tool_message(call, json.dumps({"error": decision.message})))
				yield ToolFinished(id=call.id, name=call.name, error=decision.message)
				continue

			step = self._execute(call)
			step.approved_by_human = True
			steps.append(step)
			messages.append(_tool_message(call, step.result))
			yield ToolFinished(id=call.id, name=call.name, error=step.error)

		if pending_calls:
			# The approved calls have run. Save that before going back to the model.
			yield Checkpoint(messages=list(messages))

		return None

	# -- execution -------------------------------------------------------------------

	def _execute(self, call: ToolCall) -> Step:
		started = time.monotonic()

		def done(result: str, error: str | None = None) -> Step:
			return Step(
				tool_call_id=call.id,
				name=call.name,
				arguments=call.arguments,
				result=result,
				duration_ms=int((time.monotonic() - started) * 1000),
				error=error,
			)

		if call.error:
			return done(json.dumps({"error": call.error}), call.error)

		tool = self.tools.get(call.name)
		if tool is None:
			# Closed world: a hallucinated tool name is an error, never a lookup fallback.
			message = f"Unknown tool {call.name!r}."
			return done(json.dumps({"error": message}), message)

		try:
			value = tool(**call.arguments)
		except ToolArgumentError as e:
			return done(json.dumps({"error": str(e)}), str(e))
		except Exception as e:
			# A failing tool must not fail the run; the model is told and can adapt.
			message = f"{type(e).__name__}: {e}"[:500]
			return done(json.dumps({"error": message}), message)

		return done(_serialise(value))


_BUDGET_MESSAGE = (
	"I stopped because this task needed more steps than allowed. "
	"Please narrow the request or continue with a more specific instruction."
)


def drain[T](stream: Generator[Any, None, T]) -> T:
	"""Run a streaming generator to completion and hand back its return value."""
	while True:
		try:
			next(stream)
		except StopIteration as stop:
			return stop.value


def _assistant_message(response: ChatResponse) -> dict[str, Any]:
	message: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
	if response.tool_calls:
		message["tool_calls"] = [
			{
				"id": call.id,
				"type": "function",
				"function": {"name": call.name, "arguments": json.dumps(call.arguments)},
			}
			for call in response.tool_calls
		]
	return message


def _tool_message(call: ToolCall, content: str) -> dict[str, Any]:
	return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}


def _unanswered_calls(messages: list[dict[str, Any]]) -> list[ToolCall]:
	"""Tool calls from the last assistant turn that have no result yet, in order."""
	for message in reversed(messages):
		if message.get("role") != "assistant":
			continue
		raw_calls = message.get("tool_calls") or []
		if not raw_calls:
			return []
		answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
		return [_to_tool_call(raw) for raw in raw_calls if raw.get("id") not in answered]
	return []


def _to_tool_call(raw: dict[str, Any]) -> ToolCall:
	function = raw.get("function") or {}
	raw_arguments = function.get("arguments")
	try:
		arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else (raw_arguments or {})
	except ValueError:
		arguments = {}
	if not isinstance(arguments, dict):
		arguments = {}
	return ToolCall(id=raw.get("id", ""), name=function.get("name", ""), arguments=arguments)


def _cancel_remaining(messages: list[dict[str, Any]]) -> None:
	"""Close out the transcript after a denial, so it stays valid for the provider."""
	for call in _unanswered_calls(messages):
		messages.append(_tool_message(call, json.dumps({"error": "Cancelled."})))


def _refused(call: ToolCall, message: str) -> Step:
	"""A refusal is still a step: the audit trail should show what was asked for."""
	return Step(
		tool_call_id=call.id,
		name=call.name,
		arguments=call.arguments,
		result=json.dumps({"error": message}),
		duration_ms=0,
		error=message,
	)


def _default_question(call: ToolCall) -> Question:
	return Question(
		tool_call_id=call.id,
		tool_name=call.name,
		arguments=call.arguments,
		prompt=f"Run {call.name}?",
	)


def _serialise(value: Any) -> str:
	if isinstance(value, str):
		return value
	try:
		return json.dumps(value, default=str)
	except (TypeError, ValueError):
		return str(value)
