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
import re
import time
from collections.abc import Generator
from typing import Any

import frappe
from frappe import _

from sales_ai.llm.types import ChatResponse, Notice, ToolCall, ToolCallBegin

# A provider that is busy is not a provider that is broken. Rate limits and dropped
# connections are the ordinary weather of calling someone else's API, and failing the whole
# run on the first one throws away everything the model has already been paid to read.
#
# Three attempts, because a fourth is rarely a different answer. Where the provider says
# how long to wait, that is obeyed — its own number is better than our guess — but never
# past `MAX_WAIT`, because a request handler that sleeps for five minutes is its own
# outage. Anything longer is honestly reported as a limit to wait out rather than sat on.
MAX_ATTEMPTS = 3
BACKOFF = 2.0
MAX_WAIT = 60.0


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

		for attempt in range(1, MAX_ATTEMPTS + 1):
			try:
				raw = litellm.completion(**self._request(messages, tools, stream=False))
				break
			except Exception as e:
				if attempt == MAX_ATTEMPTS or not _is_transient(e):
					_rethrow(e, self.model_id)
				time.sleep(_wait_for(e, attempt))

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
	) -> Generator[str | ToolCallBegin | Notice, None, ChatResponse]:
		"""One model turn, tried again while the failure is the provider's and nothing has
		been said yet.

		Once the model has started speaking a retry would repeat text the user has already
		read, so a failure mid-sentence is final. That costs little: the failures worth
		retrying — a rate limit above all — arrive before the first token, not during it.
		"""
		for attempt in range(1, MAX_ATTEMPTS + 1):
			try:
				return (yield from self._attempt_stream(messages, tools))
			except _ProviderFailure as failure:
				if failure.spoken or attempt == MAX_ATTEMPTS or not _is_transient(failure.cause):
					_rethrow(failure.cause, self.model_id)

				delay = _wait_for(failure.cause, attempt)
				yield Notice(
					_("The AI provider is busy. Trying again in {0} seconds.").format(round(delay))
				)
				time.sleep(delay)

	def _attempt_stream(
		self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
	) -> Generator[str | ToolCallBegin, None, ChatResponse]:
		"""A single try. Failures that came from the provider are wrapped, so the caller can
		tell them apart from a bug in the parsing below and retry only the former."""
		import litellm

		try:
			chunks = litellm.completion(**self._request(messages, tools, stream=True))
		except Exception as e:
			raise _ProviderFailure(e, spoken=False) from e

		content_parts: list[str] = []
		# Providers stream tool calls as fragments keyed by position, so accumulate by index.
		pending: dict[int, dict[str, Any]] = {}
		announced: set[int] = set()
		usage: dict[str, int] = {}
		finish_reason = None
		model = self.model_id
		# Whether the user has seen anything yet, which is what decides if a late failure
		# may be retried.
		spoken = False

		# `litellm.completion(stream=True)` returns before it has spoken to the provider, so a
		# refusal — a rate limit above all — arrives on the first pull rather than at the call
		# above. Only the pull is guarded; a bug in the parsing below is ours, not the
		# provider's, and must not be reported as theirs.
		stream = iter(chunks)
		while True:
			try:
				chunk = next(stream)
			except StopIteration:
				break
			except Exception as e:
				raise _ProviderFailure(e, spoken=spoken) from e

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
				spoken = True
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
					spoken = True
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


class _ProviderFailure(Exception):
	"""A failure that came from the provider, and whether the user had already seen output.

	Carried rather than re-raised so that a bug in this module's own parsing — which is not
	the provider's fault and will fail the same way three times — is never retried.
	"""

	def __init__(self, cause: Exception, *, spoken: bool):
		super().__init__(str(cause))
		self.cause = cause
		self.spoken = spoken


# Worth trying again: the provider is busy, overloaded, or was briefly unreachable. Not
# here on purpose: 400, 401, 403 and 404. A malformed request, a bad key or a model that
# does not exist will fail identically on every attempt, so retrying only delays the
# message that tells someone what to fix.
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_NAMES = frozenset(
	{
		"RateLimitError",
		"Timeout",
		"APITimeoutError",
		"APIConnectionError",
		"ServiceUnavailableError",
		"InternalServerError",
	}
)


def _is_transient(error: Exception) -> bool:
	# litellm re-wraps a mid-stream failure under its own class but copies the status
	# across, so the code is checked first and is the steadier signal of the two.
	if getattr(error, "status_code", None) in _TRANSIENT_STATUS:
		return True
	return type(error).__name__ in _TRANSIENT_NAMES


def _wait_for(error: Exception, attempt: int) -> float:
	"""How long to hold off, preferring the provider's own answer to our guess."""
	asked = _provider_delay(error)
	if asked is not None:
		return min(asked, MAX_WAIT)
	return min(BACKOFF * (2 ** (attempt - 1)), MAX_WAIT)


# Several providers put the wait in the message rather than in a header — Gemini's rate
# limit arrives as "Please retry in 45.484338593s" and nowhere else.
_RETRY_IN = re.compile(r"retry in ([0-9.]+)\s*s", re.IGNORECASE)


def _provider_delay(error: Exception) -> float | None:
	value: Any = getattr(error, "retry_after", None)
	if value is None:
		headers = getattr(getattr(error, "response", None), "headers", None) or {}
		value = headers.get("retry-after") or headers.get("Retry-After")
	if value is None and (found := _RETRY_IN.search(str(error))):
		value = found.group(1)

	try:
		return max(0.0, float(value)) if value is not None else None
	except (TypeError, ValueError):
		# A `Retry-After` may also be an HTTP date. Rare, and the backoff covers it.
		return None


def _rethrow(error: Exception, model_id: str) -> None:
	"""Surface provider failures as a Frappe error without leaking the API key."""
	frappe.log_error(
		title=f"Sales AI: model call failed ({model_id})", message=_redact(frappe.get_traceback())
	)

	# litellm re-wraps a mid-stream failure as MidStreamFallbackError but copies the original
	# status across, so the code is a steadier signal than the class name.
	if getattr(error, "status_code", None) == 429 or type(error).__name__ == "RateLimitError":
		# Not a fault, and not something logging harder will fix. Saying "the assistant could
		# not finish" sends someone hunting a bug that is really a quota to wait out or raise.
		frappe.throw(
			_(
				"{0} is being rate-limited, so the assistant cannot reply right now. This is the "
				"provider's quota, not a fault in ERPNext — wait and try again, or raise the "
				"plan's limit. The provider said: {1}"
			).format(model_id, _provider_reason(error)),
			title=_("Provider Rate Limit"),
		)

	frappe.throw(
		_("The AI provider could not be reached: {0}").format(_redact(str(error))[:300]),
		title=_("Model Error"),
	)


# The provider's explanation, inside the error body litellm passes through. Matched rather
# than JSON-parsed because the body often arrives as the repr of a bytes object, whose
# backslash escapes are no longer valid JSON.
_PROVIDER_MESSAGE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _provider_reason(error: Exception) -> str:
	"""The provider's own sentence, without the several hundred characters of quota
	bookkeeping it usually arrives wrapped in."""
	text = _redact(str(error))
	if found := _PROVIDER_MESSAGE.search(text):
		text = re.sub(r"\\+n", " ", found.group(1)).replace('\\"', '"')
	# Providers spend most of their reply pointing at documentation. Which limit was hit and how
	# long to wait are what the reader needs, so the signposting sentences give up their budget.
	sentences = [s for s in re.split(r"(?<=\.)\s+", text) if not re.search(r"https?://", s)]
	return " ".join(" ".join(sentences or [text]).split())[:400]


# litellm builds the request URL with the key in it, and that URL turns up in exception
# messages and tracebacks — which we write to the Error Log and show to the user.
_KEY_IN_URL = re.compile(r"([?&](?:key|api_key|access_token)=)[^&\s\"']+", re.IGNORECASE)


def _redact(text: str) -> str:
	return _KEY_IN_URL.sub(r"\1[redacted]", text)
