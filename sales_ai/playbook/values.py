# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Turning what a playbook author wrote into the values a step actually runs with.

Two different things are written by hand in a playbook, and they are handled differently
on purpose:

- **Expressions** (a condition, the list a loop walks) are Python, evaluated through
  Frappe's `safe_eval`. They are written by a System Manager, but they are still run in
  the sandbox rather than with `eval`, because the data they read came from records that
  outsiders can write into.
- **Arguments** are JSON. Only the *string leaves* are treated as templates, and each one
  is rendered on its own. Rendering the whole JSON blob through Jinja would mean a
  customer with a quote mark in their company name could rewrite the argument structure.

Nothing here decides whether a step may run. That is the policy's job.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _


class StepError(Exception):
	"""The step cannot run as written. Fails the step, not the whole worker."""


def evaluate(expression: str, context: dict[str, Any]) -> Any:
	from frappe.utils.safe_exec import safe_eval

	try:
		return safe_eval(expression.strip(), eval_locals=dict(context))
	except Exception as e:
		raise StepError(_("The expression failed: {0}").format(str(e)[:200])) from e


def render(value: Any, context: dict[str, Any]) -> Any:
	"""Fill in `{{ }}` on every string in a JSON structure, leaving the shape alone."""
	if isinstance(value, str):
		if "{{" not in value and "{%" not in value:
			return value
		try:
			return frappe.render_template(value, context)
		except Exception as e:
			raise StepError(_("A value could not be filled in: {0}").format(str(e)[:200])) from e
	if isinstance(value, dict):
		return {key: render(item, context) for key, item in value.items()}
	if isinstance(value, list):
		return [render(item, context) for item in value]
	return value


def arguments(raw: str | None, context: dict[str, Any]) -> dict[str, Any]:
	if not raw:
		return {}
	parsed = frappe.parse_json(raw)
	if not isinstance(parsed, dict):
		raise StepError(_("Arguments must be a JSON object."))
	return render(parsed, context)
