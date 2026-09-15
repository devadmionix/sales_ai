# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The tool contract.

A `Tool` is a plain Python callable plus a JSON Schema derived from its type hints.
Two rules make this the security boundary of the whole agent:

1. The schema is generated from the signature, so the schema the model sees and the
   code that runs can never drift apart.
2. Arguments are validated against that signature before the function body runs, so a
   handler never has to defend itself against model output.

This module is deliberately ignorant of sales, permissions and approvals. `meta` carries
whatever the guard layer needs (e.g. ``writes=True``); nothing here interprets it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from pydantic import ConfigDict, Field, ValidationError, create_model, validate_call


class ToolArgumentError(ValueError):
	"""Raised when the model's arguments do not match the handler signature.

	The agent loop turns this into a tool result the model can read and retry, rather
	than failing the run.
	"""


@dataclass
class Tool:
	name: str
	description: str
	parameters: dict[str, Any]
	func: Callable[..., Any]
	meta: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		self._validated = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(self.func)

	def schema(self) -> dict[str, Any]:
		"""OpenAI-style function declaration, which litellm translates per provider."""
		return {
			"type": "function",
			"function": {
				"name": self.name,
				"description": self.description,
				"parameters": self.parameters,
			},
		}

	def __call__(self, **kwargs: Any) -> Any:
		try:
			return self._validated(**kwargs)
		except ValidationError as e:
			raise ToolArgumentError(_format_validation_error(e)) from e
		except TypeError as e:
			# Unexpected or missing keyword that pydantic reports as a plain TypeError.
			raise ToolArgumentError(str(e)) from e


def tool(
	func: Callable[..., Any] | None = None,
	*,
	name: str | None = None,
	description: str | None = None,
	**meta: Any,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
	"""Turn a function into a `Tool`.

	    @tool(writes=True)
	    def create_lead(lead_name: Annotated[str, "Full name of the person"]) -> dict:
	        '''Create a Lead.'''

	The description defaults to the docstring, so the model is documented by the same
	text a developer reads.
	"""

	def wrap(fn: Callable[..., Any]) -> Tool:
		text = description or inspect.getdoc(fn)
		if not text:
			raise ValueError(f"Tool {fn.__name__!r} needs a docstring or an explicit description.")
		return Tool(
			name=name or fn.__name__,
			description=inspect.cleandoc(text),
			parameters=build_schema(fn),
			func=fn,
			meta=meta,
		)

	return wrap(func) if func else wrap


def build_schema(func: Callable[..., Any]) -> dict[str, Any]:
	"""Derive a JSON Schema for `func`'s parameters from its type hints.

	Use `Annotated[str, "what this is for"]` to document a parameter; that string
	becomes the schema description the model reads.
	"""
	signature = inspect.signature(func)
	hints = get_type_hints(func, include_extras=True)

	fields: dict[str, Any] = {}
	for param_name, param in signature.parameters.items():
		if param_name in ("self", "cls"):
			continue
		if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
			raise TypeError(
				f"Tool {func.__name__!r} may not use *args/**kwargs: "
				"every argument must be typed so it can be validated."
			)
		if param_name not in hints:
			raise TypeError(f"Tool {func.__name__!r} parameter {param_name!r} needs a type hint.")

		annotation, param_description = _split_description(hints[param_name])
		default = ... if param.default is param.empty else param.default
		fields[param_name] = (annotation, Field(default, description=param_description))

	model = create_model(
		f"{func.__name__}_arguments",
		__config__=ConfigDict(arbitrary_types_allowed=True),
		**fields,
	)
	schema = _simplify(model.model_json_schema())
	schema.setdefault("properties", {})
	# Closed world: a model that invents an argument gets a validation error it can read,
	# rather than having the argument silently ignored.
	schema["additionalProperties"] = False
	return schema


def _split_description(annotation: Any) -> tuple[Any, str | None]:
	"""Pull a bare string out of `Annotated[...]` and treat it as the description."""
	if get_origin(annotation) is not Annotated:
		return annotation, None

	base, *extras = get_args(annotation)
	descriptions = [e for e in extras if isinstance(e, str)]
	rest = [e for e in extras if not isinstance(e, str)]
	if not descriptions:
		return annotation, None
	return (Annotated[tuple([base, *rest])] if rest else base), descriptions[0]


def _simplify(schema: dict[str, Any]) -> dict[str, Any]:
	"""Inline `$defs` and drop generated titles.

	Nested models are legal JSON Schema but `$ref` support varies across providers, and
	pydantic's auto-generated titles are noise that costs tokens on every single call.
	"""
	defs = schema.pop("$defs", {})
	return _resolve(schema, defs, set())


def _resolve(node: Any, defs: dict[str, Any], seen: frozenset[str] | set[str]) -> Any:
	if isinstance(node, list):
		return [_resolve(item, defs, seen) for item in node]
	if not isinstance(node, dict):
		return node

	if ref := node.get("$ref"):
		key = ref.rsplit("/", 1)[-1]
		if key in seen:
			# Self-referential model: leave an open object rather than recursing forever.
			return {"type": "object"}
		target = defs.get(key)
		if target is None:
			return {"type": "object"}
		merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
		return _resolve(merged, defs, {*seen, key})

	return {k: _resolve(v, defs, seen) for k, v in node.items() if k != "title"}


def _format_validation_error(error: ValidationError) -> str:
	"""A compact, model-readable summary. The model has to fix this from the text alone."""
	problems = []
	for item in error.errors():
		location = ".".join(str(part) for part in item["loc"]) or "arguments"
		problems.append(f"{location}: {item['msg']}")
	return "Invalid arguments. " + "; ".join(problems)
