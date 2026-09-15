# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Provider-agnostic chat completion.

Every provider goes through litellm, so `model_id` is a litellm identifier:

    anthropic/claude-opus-4-6
    openai/gpt-4o
    ollama/llama3.1
    azure/my-deployment

Credentials come from the `Sales AI Provider` / `Sales AI Model` DocTypes, never from
the calling code, so swapping providers is a configuration change and not a code change.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import frappe
from frappe import _

from sales_ai.llm.types import ChatResponse, ToolCall, ToolCallBegin


class Model:
	def __init__(
		self,
		model_id: str,
		*,
		api_key: str | None = None,
		base_url: str | None = None,
		params: dict[str, Any] | None = None,
	):
		if not model_id:
			raise ValueError("model_id is required")
		self.model_id = model_id
		self.api_key = api_key
		self.base_url = base_url
		self.params = params or {}

	@classmethod
	def from_name(cls, name: str) -> Model:
		"""Build a Model from a `Sales AI Model` record, inheriting provider credentials."""
		doc = frappe.get_cached_doc("Sales AI Model", name)
		if not doc.enabled:
			frappe.throw(_("Model {0} is disabled.").format(name), title=_("Model Unavailable"))

		api_key = doc.get_password("api_key", raise_exception=False)
		base_url = doc.base_url
		params = _load_json(doc.params, "Sales AI Model.params")

		if doc.provider:
			provider = frappe.get_cached_doc("Sales AI Provider", doc.provider)
			api_key = api_key or provider.get_password("api_key", raise_exception=False)
			base_url = base_url or provider.base_url
			# Model-level params win over provider-level defaults.
			params = {**_load_json(provider.extra_params, "Sales AI Provider.extra_params"), **params}

		return cls(doc.model_id, api_key=api_key, base_url=base_url, params=params)

	def chat(
		self,
		messages: list[dict[str, Any]],
		*,
		tools: list[dict[str, Any]] | None = None,
		stream: bool = False,
	) -> ChatResponse | Generator[str | ToolCallBegin, None, ChatResponse]:
		"""One model turn.

		With `stream=False` returns a `ChatResponse`. With `stream=True` returns a generator
		that yields text deltas and `ToolCallBegin` markers; the finished `ChatResponse` is
		the generator's return value (available as `StopIteration.value`).
		"""
		if stream:
			return self._chat_stream(messages, tools)
		return self._chat_once(messages, tools)

	def _request(
		self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, stream: bool
	) -> dict[str, Any]:
		kwargs: dict[str, Any] = {
			"model": self.model_id,
			"messages": messages,
			"stream": stream,
			**self.params,
		}
		if tools:
			kwargs["tools"] = tools
		if self.api_key:
			kwargs["api_key"] = self.api_key
		if self.base_url:
			kwargs["base_url"] = self.base_url
		if stream:
			kwargs["stream_options"] = {"include_usage": True}
		return kwargs

	def _chat_once(
		self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
	) -> ChatResponse:
		import litellm

		try:
			raw = litellm.completion(**self._request(messages, tools, stream=False))
		except Exception as e:
			_rethrow(e, self.model_id)

		choice = raw.choices[0]
		return ChatResponse(
			content=choice.message.content,
			tool_calls=_parse_tool_calls(getattr(choice.message, "tool_calls", None)),
			usage=_parse_usage(getattr(raw, "usage", None)),
			finish_reason=choice.finish_reason,
			model=getattr(raw, "model", self.model_id),
		)

	def _chat_stream(
		self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
	) -> Generator[str | ToolCallBegin, None, ChatResponse]:
		import litellm

		try:
			chunks = litellm.completion(**self._request(messages, tools, stream=True))
		except Exception as e:
			_rethrow(e, self.model_id)

		content_parts: list[str] = []
		# Providers stream tool calls as fragments keyed by position, so accumulate by index.
		pending: dict[int, dict[str, Any]] = {}
		announced: set[int] = set()
		usage: dict[str, int] = {}
		finish_reason = None
		model = self.model_id

		for chunk in chunks:
			if chunk_usage := _parse_usage(getattr(chunk, "usage", None)):
				usage = chunk_usage
			model = getattr(chunk, "model", None) or model
			if not chunk.choices:
				continue

			choice = chunk.choices[0]
			finish_reason = choice.finish_reason or finish_reason
			delta = choice.delta
			if delta is None:
				continue

			if text := getattr(delta, "content", None):
				content_parts.append(text)
				yield text

			for fragment in getattr(delta, "tool_calls", None) or []:
				index = fragment.index if fragment.index is not None else 0
				slot = pending.setdefault(index, {"id": None, "name": None, "arguments": ""})
				if fragment.id:
					slot["id"] = fragment.id
				function = getattr(fragment, "function", None)
				if function is None:
					continue
				if function.name:
					slot["name"] = function.name
				if function.arguments:
					slot["arguments"] += function.arguments

				# Announce as soon as the name is known, so the UI is not blank while
				# a long argument payload streams in.
				if index not in announced and slot["id"] and slot["name"]:
					announced.add(index)
					yield ToolCallBegin(id=slot["id"], name=slot["name"])

		return ChatResponse(
			content="".join(content_parts) or None,
			tool_calls=_assemble_tool_calls(pending),
			usage=usage,
			finish_reason=finish_reason,
			model=model,
		)


def validate_params_field(value: Any, label: str) -> None:
	"""Reject malformed parameter JSON at save time, not halfway through a run."""
	_load_json(value, label)


def _load_json(value: Any, label: str) -> dict[str, Any]:
	if not value:
		return {}
	if isinstance(value, dict):
		return value
	try:
		parsed = json.loads(value)
	except (TypeError, ValueError):
		frappe.throw(_("{0} is not valid JSON.").format(label), title=_("Invalid Configuration"))
	if not isinstance(parsed, dict):
		frappe.throw(_("{0} must be a JSON object.").format(label), title=_("Invalid Configuration"))
	return parsed


def _parse_usage(usage: Any) -> dict[str, int]:
	if not usage:
		return {}
	return {
		"prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
		"completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
		"total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
	}


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
	calls: list[ToolCall] = []
	for raw in raw_calls or []:
		arguments, error = _decode_arguments(raw.function.arguments)
		calls.append(ToolCall(id=raw.id, name=raw.function.name, arguments=arguments, error=error))
	return calls


def _assemble_tool_calls(pending: dict[int, dict[str, Any]]) -> list[ToolCall]:
	calls: list[ToolCall] = []
	for index in sorted(pending):
		slot = pending[index]
		if not slot["name"]:
			continue
		arguments, error = _decode_arguments(slot["arguments"])
		calls.append(
			ToolCall(
				id=slot["id"] or f"call_{index}",
				name=slot["name"],
				arguments=arguments,
				error=error,
			)
		)
	return calls


def _decode_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
	"""Never raise on model output. A bad payload becomes an error the model can read and retry."""
	if not raw:
		return {}, None
	try:
		decoded = json.loads(raw)
	except (TypeError, ValueError) as e:
		return {}, f"Arguments were not valid JSON: {e}"
	if not isinstance(decoded, dict):
		return {}, f"Arguments must be a JSON object, got {type(decoded).__name__}."
	return decoded, None


def _rethrow(error: Exception, model_id: str) -> None:
	"""Surface provider failures as a Frappe error without leaking the API key."""
	message = str(error)
	frappe.log_error(title=f"Sales AI: model call failed ({model_id})", message=frappe.get_traceback())
	frappe.throw(
		_("The AI provider could not be reached: {0}").format(message[:300]),
		title=_("Model Error"),
	)
