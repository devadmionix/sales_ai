# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Changing what is on a draft quotation or order.

Until now the agent could create a document and cancel one, but not amend one — so
"make it twenty" meant starting again. This is that gap, and it is deliberately narrow.

**Only drafts.** A submitted document is the company's word; changing it is cancel and
amend, which is a person's job in the desk with the whole history in front of them.

**Targeted edits, never a replacement.** `add`, `change` and `remove` each name the items
they touch. The obvious alternative — hand the tool the complete new list of lines — has a
failure mode that would be invisible and expensive: the user says "add five more laptops",
the model sends back a list containing only laptops, and the monitors are gone. Nothing
here can drop a line that was not named.

**ERPNext prices it, not the model.** Quantities and item codes go in; rates, discounts,
taxes and totals come back out of `save()`, which runs the same pricing engine a human
gets. A rate may be forced, and when it is that is recorded as such — but the default is
that the model does not get to decide what anything costs.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt

from sales_ai.guard import GuardError, may_change
from sales_ai.guard.quotations import summarise
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

# Quotation and Sales Order only. A draft Sales Invoice or Delivery Note is a step in a
# chain — it was mapped from something upstream, and editing its lines by hand quietly
# breaks the link back to the order it is billing for.
REVISABLE = ("Quotation", "Sales Order")

MAX_LINES = 50


def revise_lines(
	doctype: str,
	name: str,
	*,
	add: list[dict[str, Any]] | None = None,
	change: list[dict[str, Any]] | None = None,
	remove: list[str] | None = None,
	discount_percentage: float | None = None,
	tool: str,
) -> dict[str, Any]:
	"""Add, change or remove lines on a draft, and let ERPNext re-price the result."""
	if doctype not in REVISABLE:
		raise GuardError(
			f"{doctype!r} cannot have its lines changed by the assistant. "
			f"Available: {', '.join(REVISABLE)}."
		)

	add, change, remove = list(add or []), list(change or []), list(remove or [])
	if not (add or change or remove or discount_percentage is not None):
		raise GuardError("Nothing to change was given. Say what to add, change or remove.")

	doc = may_change(doctype, name, "write", "Revise")
	if doc.docstatus != 0:
		raise GuardError(
			f"{doctype} {name} is {_state(doc)}, so its lines cannot be changed. "
			"A submitted document has to be cancelled and re-made, which is a person's call."
		)

	before = summarise(doc)
	changes: dict[str, Any] = {}

	if remove:
		changes["removed"] = _remove(doc, remove)
	if change:
		changes["changed"] = _change(doc, change)
	if add:
		changes["added"] = _add(doc, add, doctype)
	if discount_percentage is not None:
		changes["discount_percentage"] = _discount(doc, discount_percentage)

	if not doc.items:
		raise GuardError(
			f"That would leave {doctype} {name} with no items on it. "
			"A document with nothing on it should be cancelled or deleted in ERPNext, not emptied."
		)
	if len(doc.items) > MAX_LINES:
		raise GuardError(f"That would put more than {MAX_LINES} lines on one document.")

	# Everything downstream of the item table — rates, discounts, taxes, totals — is
	# recalculated by ERPNext's own controller here, exactly as it would be for a human
	# editing the form. The model supplies quantities; it does not supply arithmetic.
	doc.save()

	doc.add_comment(
		"Info",
		_("Lines changed by Sales AI for {0}: {1}.").format(
			frappe.session.user, ", ".join(sorted(changes))
		),
	)
	record_action(
		action="Update",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes={
			**changes,
			"grand_total": {"from": before["totals"], "to": flt(doc.grand_total)},
		},
	)

	return {"revised": doctype, **summarise(doc)}


def preview(doctype: str, name: str) -> dict[str, Any]:
	"""The document as it stands, before the change.

	What the approval card can honestly show: this is the thing about to be altered, and
	its current total is what the amount threshold is judged on. The permission check stays
	here so a card never shows figures from a document the approver may not see.
	"""
	if doctype not in REVISABLE:
		raise GuardError(f"{doctype!r} cannot have its lines changed by the assistant.")
	doc = may_change(doctype, name, "read", "Read")
	return {"doctype": doctype, "state": _state(doc), **summarise(doc)}


# -- internals -----------------------------------------------------------------------


def _remove(doc: Any, item_codes: list[str]) -> list[str]:
	wanted = {code.strip() for code in item_codes if code and code.strip()}
	present = {row.item_code for row in doc.items}
	missing = sorted(wanted - present)
	if missing:
		raise GuardError(
			f"{', '.join(missing)} is not on {doc.doctype} {doc.name}. "
			f"It has: {', '.join(sorted(present))}."
		)

	doc.items = [row for row in doc.items if row.item_code not in wanted]
	# Frappe numbers child rows itself and gets confused by a gap in the sequence.
	for index, row in enumerate(doc.items, start=1):
		row.idx = index
	return sorted(wanted)


def _change(doc: Any, lines: list[dict[str, Any]]) -> dict[str, Any]:
	by_code = {row.item_code: row for row in doc.items}
	changed: dict[str, Any] = {}

	for line in lines:
		code = (line.get("item_code") or "").strip()
		row = by_code.get(code)
		if row is None:
			raise GuardError(
				f"{code!r} is not on {doc.doctype} {doc.name}, so there is nothing to change. "
				f"It has: {', '.join(sorted(by_code))}. Add it instead if it should be there."
			)

		was = {"qty": flt(row.qty), "rate": flt(row.rate)}
		qty = line.get("qty")
		if qty is not None:
			row.qty = _qty(qty)
		if line.get("rate") is not None:
			row.rate = _rate(line["rate"])
		if line.get("uom"):
			row.uom = line["uom"]

		changed[code] = {"from": was, "to": {"qty": flt(row.qty), "rate": flt(row.rate)}}

	return changed


def _add(doc: Any, lines: list[dict[str, Any]], doctype: str) -> list[dict[str, Any]]:
	present = {row.item_code for row in doc.items}
	added = []

	for line in lines:
		code = (line.get("item_code") or "").strip()
		if not code:
			raise GuardError("Every line to add needs an item_code.")
		if code in present:
			raise GuardError(
				f"{code!r} is already on {doc.doctype} {doc.name}. "
				"Change its quantity rather than adding it a second time."
			)

		row = {"item_code": code, "qty": _qty(line.get("qty", 1))}
		if line.get("uom"):
			row["uom"] = line["uom"]
		if line.get("rate") is not None:
			row["rate"] = _rate(line["rate"])
		if doctype == "Sales Order":
			# Mandatory per line, and ERPNext will not default it on a row added this way.
			row["delivery_date"] = doc.delivery_date

		doc.append("items", row)
		present.add(code)
		added.append(row)

	return added


def _discount(doc: Any, percentage: float) -> float:
	value = flt(percentage)
	if value < 0 or value > 100:
		raise GuardError("A discount has to be between 0 and 100 percent.")

	# ERPNext needs telling what the percentage is a percentage *of*. Left unset it
	# silently applies nothing, and the agent would report a discount that did not happen.
	doc.apply_discount_on = doc.apply_discount_on or "Grand Total"
	doc.additional_discount_percentage = value
	# The two are alternatives in ERPNext, and a stale amount wins over a fresh percentage.
	doc.discount_amount = 0
	return value


def _qty(value: Any) -> float:
	qty = flt(value)
	if qty <= 0:
		raise GuardError("A quantity has to be more than zero. To take a line off, remove it.")
	return qty


def _rate(value: Any) -> float:
	rate = flt(value)
	if rate < 0:
		raise GuardError("A rate cannot be negative.")
	return rate


def _state(doc: Any) -> str:
	return {0: "still a draft", 1: "submitted", 2: "cancelled"}.get(doc.docstatus, "in an odd state")
