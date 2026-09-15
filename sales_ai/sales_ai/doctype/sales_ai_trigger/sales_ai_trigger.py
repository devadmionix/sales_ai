# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class SalesAITrigger(Document):
	def validate(self) -> None:
		self._check_run_as()
		self._check_target()
		if self.trigger_type == "Document Event":
			self._check_document_event()
		else:
			self._check_cron()

	def before_insert(self) -> None:
		# Start the clock now, so a new schedule fires at its next slot rather than
		# immediately on the tick after it was saved.
		self.last_fired_at = now_datetime()

	def on_update(self) -> None:
		clear_cache()

	def on_trash(self) -> None:
		clear_cache()

	# -- checks ----------------------------------------------------------------------

	def _check_run_as(self) -> None:
		if self.run_as == "Administrator":
			# Administrator bypasses every permission check, so a run as Administrator has
			# no limits at all. Unattended work must be answerable to a real person.
			frappe.throw(
				_("A trigger cannot run as Administrator. Name the person it acts for."),
				title=_("Pick a Real User"),
			)
		if not frappe.db.get_value("User", self.run_as, "enabled"):
			frappe.throw(_("{0} is a disabled user.").format(self.run_as))

	def _check_target(self) -> None:
		if self.runs != "Playbook":
			return
		if not frappe.db.get_value("Sales AI Playbook", self.playbook, "enabled"):
			frappe.throw(
				_("Playbook {0} is not enabled, so nothing would happen when this fires.").format(
					self.playbook
				),
				title=_("Playbook Disabled"),
			)

	def _check_document_event(self) -> None:
		if frappe.db.get_value("DocType", self.reference_doctype, "module") == "Sales AI":
			# Otherwise the agent's own runs and logs would start more runs.
			frappe.throw(
				_("A trigger cannot watch a Sales AI document."), title=_("That Would Loop")
			)
		if self.event in ("on_submit", "on_cancel") and not frappe.db.get_value(
			"DocType", self.reference_doctype, "is_submittable"
		):
			frappe.throw(
				_("{0} is not submittable, so {1} never happens.").format(
					self.reference_doctype, self.event
				)
			)

	def _check_cron(self) -> None:
		from croniter import croniter

		if not croniter.is_valid(self.cron or ""):
			frappe.throw(_("{0} is not a valid cron expression.").format(self.cron))


def clear_cache() -> None:
	frappe.cache.delete_value("sales_ai:watched")
