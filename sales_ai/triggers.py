# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What makes the agent start working without being asked.

`dispatch` is wired to every document event on the site, so the first thing it does on
an ordinary save must be cheap: one cache read that returns nothing. Only a site that
has actually configured a trigger pays for anything more.

Three things stop a trigger from running away:

- **Recursion.** A trigger never fires from inside an agent run, so the records the agent
  writes cannot hand it more work.
- **Dedup.** The queued job is keyed on the trigger and the document, so ten saves in the
  time it takes a worker to pick one up produce one run, not ten.
- **Debounce.** A configurable window per trigger and document, because `on_update` fires
  on every save and a busy Quotation is saved a lot.

Anything that goes wrong while deciding — a condition that raises, a template that will
not render — means the trigger does not fire. Failing closed is the only safe default for
work that nobody asked for.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from sales_ai import background, playbook

# The events worth watching. `on_update` covers ordinary saves.
EVENTS = ("after_insert", "on_update", "on_submit", "on_cancel")

# A prompt built from record text is only as trustworthy as the record. It cannot be an
# instruction to the agent that outlives the policy gate, but it can be long, and length
# costs money on every fire.
MAX_PROMPT = 4000

# Bulk machinery, where documents are written by the hundred and nobody is asking for an
# opinion on any of them. A patch that touches every Quotation must not start a run per
# Quotation — and during the migration that creates them, our own tables may not exist yet.
_BULK_FLAGS = ("in_migrate", "in_install", "in_patch", "in_import", "in_setup_wizard", "in_test")


def dispatch(doc: Any, method: str | None = None) -> None:
	"""Document event hook, called for every doctype on the site."""
	if any(frappe.flags.get(flag) for flag in _BULK_FLAGS):
		return

	names = _watched().get(f"{doc.doctype}::{method}")
	if not names:
		return
	if frappe.flags.get("sales_ai_run") or frappe.flags.get("sales_ai_playbook_run"):
		# A write the agent or a playbook just made must not start another run.
		return
	if not armed():
		return

	for name in names:
		_consider(name, doc)


def run_scheduled() -> None:
	"""Scheduler tick. Fires the scheduled triggers whose cron slot has passed."""
	if not armed():
		return

	from croniter import croniter

	now = now_datetime()
	for name in frappe.get_all(
		"Sales AI Trigger", filters={"enabled": 1, "trigger_type": "Scheduled"}, pluck="name"
	):
		trigger = frappe.get_cached_doc("Sales AI Trigger", name)
		if croniter(trigger.cron, trigger.last_fired_at or now).get_next(type(now)) > now:
			continue

		# Stamped before queueing, so a slow worker cannot cause a second fire.
		frappe.db.set_value("Sales AI Trigger", name, "last_fired_at", now)
		_fire(trigger, None)


def armed() -> bool:
	"""Both switches must be on. Enabling Sales AI does not enable unattended work."""
	return bool(
		frappe.db.get_single_value("Sales AI Settings", "enabled")
		and frappe.db.get_single_value("Sales AI Settings", "triggers_enabled")
	)


# -- internals -------------------------------------------------------------------------


def _watched() -> dict[str, list[str]]:
	"""`doctype::event` -> trigger names, cached until a trigger is saved or deleted."""
	cached = frappe.cache.get_value("sales_ai:watched")
	if cached is not None:
		return cached

	watched: dict[str, list[str]] = {}
	for row in frappe.get_all(
		"Sales AI Trigger",
		filters={"enabled": 1, "trigger_type": "Document Event"},
		fields=["name", "reference_doctype", "event"],
	):
		watched.setdefault(f"{row.reference_doctype}::{row.event}", []).append(row.name)

	frappe.cache.set_value("sales_ai:watched", watched)
	return watched


def _consider(name: str, doc: Any) -> None:
	trigger = frappe.get_cached_doc("Sales AI Trigger", name)
	if not trigger.enabled:
		return
	if not _passes(trigger, doc):
		return
	if _debounced(trigger, doc):
		return
	_fire(trigger, doc)


def _passes(trigger: Any, doc: Any) -> bool:
	if not (trigger.condition or "").strip():
		return True
	try:
		from frappe.utils.safe_exec import safe_eval

		return bool(safe_eval(trigger.condition, eval_locals={"doc": doc.as_dict()}))
	except Exception:
		frappe.log_error(title=f"Sales AI: trigger condition failed ({trigger.name})")
		return False


def _debounced(trigger: Any, doc: Any) -> bool:
	"""True if this trigger already fired for this document inside its window."""
	window = int(trigger.debounce_minutes or 0)
	if window <= 0:
		return False

	key = f"sales_ai:debounce:{trigger.name}:{doc.doctype}:{doc.name}"
	if frappe.cache.get_value(key):
		return True
	frappe.cache.set_value(key, 1, expires_in_sec=window * 60)
	return False


def _fire(trigger: Any, doc: Any) -> None:
	reference_doctype = doc.doctype if doc else None
	reference_name = doc.name if doc else None

	if trigger.runs == "Playbook":
		playbook.start(
			trigger.playbook,
			run_as=trigger.run_as,
			trigger=trigger.name,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			job_id=_job_id(trigger, doc),
		)
		return

	prompt = _render(trigger, doc)
	if not prompt:
		return

	background.enqueue_run(
		prompt,
		run_as=trigger.run_as,
		agent_profile=trigger.agent_profile,
		trigger=trigger.name,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		job_id=_job_id(trigger, doc),
	)


def _job_id(trigger: Any, doc: Any) -> str:
	subject = f"{doc.doctype}::{doc.name}" if doc else "scheduled"
	return f"sales-ai-trigger::{trigger.name}::{subject}"


def _render(trigger: Any, doc: Any) -> str:
	"""Build the instruction. The template is written by a System Manager, the data is not."""
	try:
		text = frappe.render_template(
			trigger.prompt_template,
			{"doc": doc.as_dict() if doc else None, "now": now_datetime()},
		)
	except Exception:
		frappe.log_error(title=f"Sales AI: trigger prompt failed to render ({trigger.name})")
		return ""
	return (text or "").strip()[:MAX_PROMPT]
