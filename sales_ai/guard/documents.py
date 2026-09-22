# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Submitting and cancelling sales documents.

These are the two actions in the app that a user cannot simply undo by editing a field.
Submitting a Sales Order reserves stock and commits a delivery date; submitting a Sales
Invoice posts to the general ledger. Cancelling reverses those entries but leaves the
cancelled document behind forever, and ERPNext will not let it be un-cancelled.

Two rules follow from that, and they are the reason this module exists rather than the
work being folded into `writes.py`:

- **Nothing here deletes.** "Delete the Sharma order" becomes a cancellation, which is
  reversible in the sense that matters: the numbers come back out of the ledger and the
  record of what happened stays. A destroyed document cannot be audited, and an agent that
  misreads which Sharma was meant has destroyed the wrong one.
- **Every call returns a priced preview first.** `preview` is wired into the tools so the
  approval card a human sees carries the customer and the amount, not just an ID. Approving
  "submit SAL-ORD-2026-00031" is not consent; approving "submit a ₹4,20,000 order for ABC
  Medical Store" is.

Permissions are ERPNext's. `may_change` decides what the user is *told*; `submit()` and
`cancel()` re-check and run every `on_submit` / `on_cancel` hook in every installed app.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt

from sales_ai.guard import GuardError, may_change
from sales_ai.guard.permissions import check_ai_permission
from sales_ai.guard.quotations import summarise
from sales_ai.guard.specs import SPECS
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

# The sales side only. This is a sales assistant, and a Purchase Order or a Journal Entry
# being submittable by the same code would make the allowlist the only thing standing
# between the model and the ledger.
SUBMITTABLE = ("Quotation", "Sales Order", "Sales Invoice", "Delivery Note")

# Cancelling a document the agent cannot read is the wrong way round. The approval card
# would have nothing to put on it, and the model would be asked to confirm a document it
# has never seen — so the dangerous power would be the one granted without the safe one.
# Here rather than in `specs.py` because the dependency only runs this way.
assert set(SUBMITTABLE) <= set(SPECS), "SUBMITTABLE has a DocType the agent cannot read"

MAX_REASON = 500


def submit_document(doctype: str, name: str, *, tool: str) -> dict[str, Any]:
	"""Submit a draft, turning it from a working paper into a commitment."""
	_submittable(doctype)

	# RBAC pre-check: does the chatbot's role matrix allow submit on this DocType?
	result = check_ai_permission(doctype=doctype, document_name=name, action="submit")
	if not result.allowed:
		raise GuardError(result.reason)

	doc = may_change(doctype, name, "submit", "Submit")

	if doc.docstatus != 0:
		raise GuardError(
			f"{doctype} {name} is {_state(doc)} and cannot be submitted again."
		)

	doc.submit()
	doc.add_comment("Info", _("Submitted by Sales AI for {0}.").format(frappe.session.user))

	record_action(
		action="Submit",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes={"status": doc.get("status"), "grand_total": flt(doc.get("grand_total"))},
	)

	return {"submitted": doctype, **summarise(doc)}


def cancel_document(doctype: str, name: str, reason: str, *, tool: str) -> dict[str, Any]:
	"""Cancel a submitted document. Reverses its effects; does not delete it.

	A reason is required and not optional politeness. A cancelled Sales Invoice is a
	question somebody will ask about months later, and "the assistant cancelled it" is not
	an answer. It goes on the timeline, where a human looking at the document will find it,
	and into the action log, where an auditor will.
	"""
	_submittable(doctype)

	text = (reason or "").strip()
	if not text:
		raise GuardError(f"Cancelling a {doctype} needs a reason. Why is it being cancelled?")

	# RBAC pre-check: does the chatbot's role matrix allow cancel on this DocType?
	result = check_ai_permission(doctype=doctype, document_name=name, action="cancel")
	if not result.allowed:
		raise GuardError(result.reason)

	doc = may_change(doctype, name, "cancel", "Cancel")

	if doc.docstatus == 0:
		raise GuardError(
			f"{doctype} {name} is still a draft, so there is nothing to cancel. "
			"A draft commits nothing; leave it or have somebody delete it in ERPNext."
		)
	if doc.docstatus != 1:
		raise GuardError(f"{doctype} {name} is already {_state(doc)}.")

	try:
		doc.cancel()
	except frappe.LinkExistsError as exc:
		# ERPNext refuses while a later document depends on this one — an invoice against
		# an order, a delivery note against an invoice. That refusal is the useful answer,
		# and without this it would reach the user as the loop's generic failure phrase.
		raise GuardError(
			f"{doctype} {name} cannot be cancelled while other documents depend on it. "
			f"ERPNext says: {exc}"
		)

	doc.add_comment(
		"Info",
		_("Cancelled by Sales AI for {0}. Reason: {1}").format(frappe.session.user, text[:MAX_REASON]),
	)

	record_action(
		action="Cancel",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes={"reason": text[:MAX_REASON], "grand_total": flt(doc.get("grand_total"))},
	)

	return {
		"cancelled": doctype,
		"name": doc.name,
		"note": "Cancelled, not deleted. The record stays, and its effects have been reversed.",
	}


def preview(doctype: str, name: str) -> dict[str, Any]:
	"""What the human is actually approving: the customer, the lines and the amount.

	The permission check stays here rather than in the tool, for the same reason it does in
	`quotations.preview` — an approval card must not show somebody figures from a document
	they are not allowed to see.
	"""
	_submittable(doctype)
	doc = may_change(doctype, name, "read", "Read")
	return {"doctype": doctype, "state": _state(doc), **summarise(doc)}


# -- internals -----------------------------------------------------------------------


def _submittable(doctype: str) -> None:
	if doctype not in SUBMITTABLE:
		raise GuardError(
			f"{doctype!r} is not a document the assistant can submit or cancel. "
			f"Available: {', '.join(SUBMITTABLE)}."
		)


def _state(doc: Any) -> str:
	return {0: "still a draft", 1: "submitted", 2: "cancelled"}.get(doc.docstatus, "in an odd state")
