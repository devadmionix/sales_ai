# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Turning a priced quote into real documents, one reversible step at a time.

The chain is ERPNext's own: a Quotation is drafted, then submitted, then mapped to a Sales
Order. It is split into three tools rather than one because the three steps do not carry
the same consequence, and a human approving them should be approving the right thing.

- Drafting is cheap. Nothing has been promised; a draft Quotation is a sheet of paper on a
  desk, and deleting it costs nothing.
- Submitting makes the quote the company's official position on price. It can be cancelled
  but not quietly edited.
- Converting to a Sales Order is a commitment to deliver, and the order this module makes
  is left in draft. Submitting it is a separate, approved step in `guard.documents` — a
  different sentence from the user, and a different approval card, because reserving stock
  and committing a delivery date is not something that should happen as a side effect of
  "turn that quote into an order".

Every step reuses ERPNext's controllers and mappers, so pricing, taxes, status, naming and
every `validate` hook run exactly as they do for a human.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, getdate

from sales_ai.guard import NO_SUCH_RECORD, GuardError, _may_read, deny
from sales_ai.guard.permissions import check_ai_permission, get_user_scope
from sales_ai.guard.pricing import LINE_FIELDS, TOTAL_FIELDS, draft
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action


def draft_quotation(
	lines: list[dict[str, Any]],
	*,
	customer: str,
	company: str | None = None,
	price_list: str | None = None,
	date: str | None = None,
	valid_till: str | None = None,
	submit: bool = False,
	tool: str,
) -> dict[str, Any]:
	"""Save the very Quotation that `pricing.draft` built and ERPNext priced.

	Same code path as `price_items`, deliberately: the figure the agent quoted in the
	conversation and the figure on the document a human is asked to approve have to be the
	same figure, and the only way to be sure of that is for there to be one of them.

	When `submit` is true ("create and submit", "create this record as submitted"),
	the draft is submitted right away under the current user's permissions. If the
	user may not submit, the quotation is kept as a draft and the reason is
	returned — the creation is not failed or rolled back.
	"""
	# RBAC pre-check: does the chatbot's role matrix allow creating Quotations?
	result = check_ai_permission(doctype="Quotation", action="create")
	if not result.allowed:
		raise GuardError(result.reason)

	frappe.has_permission("Quotation", "create", throw=True)

	quotation = draft(lines, customer=customer, company=company, price_list=price_list, date=date)
	if valid_till:
		quotation.valid_till = _valid_till(valid_till, quotation.transaction_date)

	quotation.insert()
	quotation.add_comment("Info", _("Drafted by Sales AI for {0}.").format(frappe.session.user))

	record_action(
		action="Draft",
		tool=tool,
		reference_doctype="Quotation",
		reference_name=quotation.name,
		changes={
			"customer": quotation.party_name,
			"price_list": quotation.selling_price_list,
			"lines": [{"item_code": row.item_code, "qty": row.qty, "rate": row.rate} for row in quotation.items],
			"grand_total": flt(quotation.grand_total),
		},
	)

	if not submit:
		return {
			"drafted": "Quotation",
			**summarise(quotation),
			"note": "This is a draft. Nobody has been sent it and nothing is committed yet.",
		}

	# Create-and-submit: the draft above is the success; submission is best-effort
	# under the current user's own permissions. Never bypassed, never rolled back.
	from sales_ai.guard.documents import submit_after_create

	outcome = submit_after_create("Quotation", quotation.name, tool=tool)
	if isinstance(outcome.get("submitted"), str):
		return outcome
	return {
		"created": "Quotation",
		"submitted": False,
		**summarise(frappe.get_doc("Quotation", quotation.name)),
		"note": (
			"The quotation was created successfully but remains in Draft because "
			f"it could not be submitted: {outcome.get('reason')}"
		),
	}


def submit_quotation(name: str, *, tool: str) -> dict[str, Any]:
	"""Make a draft Quotation the company's official position on price."""
	# RBAC pre-check
	result = check_ai_permission(doctype="Quotation", document_name=name, action="submit")
	if not result.allowed:
		raise GuardError(result.reason)

	quotation = _quotation(name, "submit")
	if quotation.docstatus == 1:
		raise GuardError(f"Quotation {name} is already submitted.")
	if quotation.docstatus == 2:
		raise GuardError(f"Quotation {name} is cancelled and cannot be submitted.")
	if quotation.docstatus != 0:
		raise GuardError(
			f"Quotation {name} is already {_state(quotation)} and cannot be submitted again."
		)

	# Runs as the current user. `submit()` re-checks submit permission, User
	# Permissions, ownership and workflow — never bypassed.
	quotation.submit()
	quotation.add_comment("Info", _("Submitted by Sales AI for {0}.").format(frappe.session.user))

	record_action(
		action="Submit",
		tool=tool,
		reference_doctype="Quotation",
		reference_name=quotation.name,
		changes={"status": quotation.status, "grand_total": flt(quotation.grand_total)},
	)

	return {"submitted": "Quotation", **summarise(quotation)}


def convert_to_sales_order(
	name: str, *, delivery_date: str, submit: bool = False, tool: str
) -> dict[str, Any]:
	"""Map a submitted Quotation onto a Sales Order, and leave that order in draft.

	When `submit` is true ("create and submit"), the new order is submitted
	right away under the current user's permissions. A refusal keeps the
	draft and returns the reason instead of failing.
	"""
	# RBAC pre-check: need read on Quotation and create on Sales Order
	result = check_ai_permission(doctype="Sales Order", action="create")
	if not result.allowed:
		raise GuardError(result.reason)

	quotation = _quotation(name, "read")
	if quotation.docstatus != 1:
		raise GuardError(
			f"Quotation {name} is {_state(quotation)}. Only a submitted quotation can become an order."
		)
	if quotation.quotation_to != "Customer":
		# ERPNext's own mapper calls `_make_customer`, which *creates* a Customer when the
		# quote was addressed to a Lead or a Prospect. Creating a customer record is a
		# decision in its own right and must not happen as a side effect of this one.
		raise GuardError(
			f"Quotation {name} was made out to a {quotation.quotation_to}, not a Customer. "
			"Converting it would create a new Customer record, which this tool will not do "
			"as a side effect — convert the lead first."
		)

	frappe.has_permission("Sales Order", "create", throw=True)

	from erpnext.selling.doctype.quotation.quotation import make_sales_order

	# The mapper carries the priced rows across and re-runs ERPNext's own totals, so the
	# order agrees with the quotation it came from rather than being re-priced from scratch.
	order = make_sales_order(name)
	order.delivery_date = _delivery_date(delivery_date, order.transaction_date)
	for row in order.items:
		row.delivery_date = order.delivery_date

	# Left in draft unless the user asked for submission: submitting is
	# `documents.submit_document`, which the user has to ask for. See this
	# module's docstring.
	order.insert()
	order.add_comment(
		"Info", _("Drafted from {0} by Sales AI for {1}.").format(name, frappe.session.user)
	)

	record_action(
		action="Draft",
		tool=tool,
		reference_doctype="Sales Order",
		reference_name=order.name,
		changes={"from_quotation": name, "delivery_date": str(order.delivery_date), "grand_total": flt(order.grand_total)},
	)

	if not submit:
		return {
			"drafted": "Sales Order",
			"from_quotation": name,
			**summarise(order),
			"note": (
				"The order is a draft and reserves no stock. It commits the company to nothing "
				"until it is submitted, which is a separate step the user has to ask for."
			),
		}

	from sales_ai.guard.documents import submit_after_create

	outcome = submit_after_create("Sales Order", order.name, tool=tool)
	if isinstance(outcome.get("submitted"), str):
		return {**outcome, "from_quotation": name}
	return {
		"created": "Sales Order",
		"submitted": False,
		"from_quotation": name,
		**summarise(frappe.get_doc("Sales Order", order.name)),
		"note": (
			"The order was created successfully but remains in Draft because "
			f"it could not be submitted: {outcome.get('reason')}"
		),
	}


def preview(name: str) -> dict[str, Any]:
	"""What a quotation currently comes to, for an approval card. A read, and nothing more.

	Lives here rather than in the tool so the permission check stays in the module that
	owns it: a preview must not show a human figures from a document they may not see.
	"""
	return summarise(_quotation(name, "read"))


def summarise(doc: Any) -> dict[str, Any]:
	"""The same shape `price_items` returns, so a quote and a saved document read alike."""
	return {
		"name": doc.name,
		"customer": doc.party_name if doc.doctype == "Quotation" else doc.customer,
		"company": doc.company,
		"status": doc.status,
		"currency": doc.currency,
		"lines": [
			{key: row.get(key) for key in LINE_FIELDS if row.get(key) not in (None, "", 0, 0.0)}
			for row in doc.items
		],
		"totals": {key: flt(doc.get(key)) for key in TOTAL_FIELDS if flt(doc.get(key))},
	}


# -- internals -----------------------------------------------------------------------


def _quotation(name: str, permission: str) -> Any:
	_may_read("Quotation", name)
	quotation = frappe.get_doc("Quotation", name)
	# Same owner-only scope `may_change` enforces on every other write path:
	# an "own"-scoped user cannot submit, convert or preview somebody else's
	# quotation. Denied exactly like a record that does not exist, so
	# guessing IDs teaches nothing. (Native `has_permission` hooks enforce
	# this too; this keeps the guard correct even without them.)
	if get_user_scope(doctype="Quotation") == "own" and quotation.owner != frappe.session.user:
		action = {"submit": "Submit", "read": "Read"}.get(permission, permission.title())
		raise deny(
			action, "Quotation", name, NO_SUCH_RECORD.format(doctype="Quotation", name=name)
		)
	quotation.check_permission(permission)
	return quotation


def _state(doc: Any) -> str:
	return {0: "still a draft", 1: "submitted", 2: "cancelled"}.get(doc.docstatus, "in an odd state")


def _valid_till(valid_till: str, transaction_date: Any) -> str:
	try:
		until = getdate(valid_till)
	except Exception:
		raise GuardError(f"valid_till must be a date as YYYY-MM-DD, not {valid_till!r}.")
	if until < getdate(transaction_date):
		raise GuardError(f"A quotation cannot expire on {until}, before the day it was made.")
	return str(until)


def _delivery_date(delivery_date: str, transaction_date: Any) -> str:
	try:
		date = getdate(delivery_date)
	except Exception:
		raise GuardError(f"delivery_date must be a date as YYYY-MM-DD, not {delivery_date!r}.")
	if date < getdate(transaction_date):
		raise GuardError(f"The delivery date {date} is before the order date {transaction_date}.")
	return str(date)
