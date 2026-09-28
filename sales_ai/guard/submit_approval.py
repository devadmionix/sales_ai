# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Amount-based approval for submitting sales documents.

A Sales User may submit a Quotation, Sales Order or Delivery Note on their own
only while its total stays under ``SUBMIT_APPROVAL_LIMIT``. At or above the
limit, only a manager-role user may submit — the manager's own submission *is*
the approval.

Enforced in ``before_submit``, so the rule applies identically in the desk,
the REST API and the chatbot: all three go through the document controller.
The chatbot implements no amount check of its own; a blocked submission
surfaces as the usual created-but-Draft refusal with the reason, exactly like
any other submit denial.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt

# Totals at or above this need a manager to submit. Compared in company
# currency (`base_grand_total`) so a threshold of 5000 means the same value
# whether the document is billed in rupees, dollars or anything else.
SUBMIT_APPROVAL_LIMIT = 5000.0

# The gate applies to these DocTypes only. Sales Invoice is deliberately
# absent: invoicing is owned by Accounts, not by the sales submit flow.
GATED_DOCTYPES = ("Quotation", "Sales Order", "Delivery Note")

# Roles whose submission counts as the approval.
APPROVER_ROLES = frozenset(
	{"Sales Manager", "Sales Master Manager", "System Manager", "Administrator"}
)


def enforce_submit_approval(doc: Any, method: str | None = None) -> None:
	"""Block a non-manager from submitting a document at/above the limit.

	Wired as ``before_submit`` in ``hooks.py``. Raises nothing when the
	document is below the limit, when the doctype is not gated, or when the
	current user carries an approver role.
	"""
	if doc.doctype not in GATED_DOCTYPES:
		return

	total = flt(doc.get("base_grand_total") or doc.get("grand_total"))
	if total < SUBMIT_APPROVAL_LIMIT:
		return

	if frappe.session.user == "Administrator":
		return
	if set(frappe.get_roles(frappe.session.user)) & APPROVER_ROLES:
		return

	currency = doc.get("currency") or ""
	frappe.throw(
		_("This {0} totals {1} {2}, which is at or above the {3} limit for "
			"direct submission by a sales user. Please ask a Sales Manager to "
			"review and submit it — their submission is the approval.").format(
			doc.doctype,
			flt(total, 2),
			currency,
			f"{flt(SUBMIT_APPROVAL_LIMIT, 2)} {currency}".strip(),
		)
	)
