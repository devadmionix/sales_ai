# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What a customer signed in to the website may do for themselves.

This is a different world from `sales_ai.guard.writes`, and the difference is worth stating
once. There, the agent acts *as a member of staff*, and every check is "could this person
have done it themselves in the desk?" — so `ignore_permissions` never appears. Here the
caller is a Website User with no permission on Customer at all, and the whole point is to
let them create one. So this module does use `ignore_permissions`, and everything below
exists to make that narrow enough to be safe:

- one record, of one DocType, for the caller and nobody else
- a field allowlist of exactly what a stranger may choose about themselves, which excludes
  every field that decides what they pay
- one per account, so the tool cannot be used to fill the customer list with rubbish
- never a link to a Customer that already exists, because that is somebody else's data

It grants no role. ERPNext hands out the "Customer" role when staff add a portal user, and
a chatbot doing that on request would be a stranger widening their own access. A human
decides that, and the record created here is what they look at when they do.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import escape_html

from sales_ai.guard import GuardError
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

MAX_NAME = 140
CUSTOMER_TYPES = ("Company", "Individual")

# Said whenever the record cannot be created automatically, whatever the reason. It is one
# phrase on purpose: "that name is taken" tells an anonymous signup form whether a company
# is a customer of yours, which is a competitor's question, not a customer's.
NEEDS_A_HUMAN = (
	"This could not be set up automatically. Somebody from the team will be in touch."
)


def register_customer(
	customer_name: str, *, customer_type: str = "Company", tool: str
) -> dict[str, Any]:
	"""Create the Customer record for the person asking, and link their login to it."""
	user = _portal_user()

	name = escape_html((customer_name or "").strip())
	if not name:
		raise GuardError("What name should the account be in?")
	if len(name) > MAX_NAME:
		raise GuardError(f"That name is too long; keep it under {MAX_NAME} characters.")
	if customer_type not in CUSTOMER_TYPES:
		raise GuardError(f"Type must be one of {', '.join(CUSTOMER_TYPES)}.")

	mine = my_customer()
	if mine:
		# Not an error. They asked for something they already have, and the useful answer
		# is to say so rather than to refuse.
		return {"unchanged": "Customer", "name": mine, "note": "This account is already set up."}

	# Deliberately ignores permissions: the check is "does this name exist at all", and a
	# Website User can see almost nothing. Linking them to a Customer somebody else already
	# trades with would hand over that company's orders and invoices, so this never links to
	# an existing record — it stops and asks for a person.
	if frappe.db.exists("Customer", {"customer_name": name}):
		raise GuardError(NEEDS_A_HUMAN)

	doc = frappe.new_doc("Customer")
	# Field by field, not `update()`. Everything absent from these three lines is left to
	# ERPNext's own defaults, which is the point: customer group, territory and price list
	# decide what this person is charged, and they are not a stranger's to choose.
	doc.customer_name = name
	doc.customer_type = customer_type
	doc.append("portal_users", {"user": user})
	doc.insert(ignore_permissions=True)

	record_action(
		action="Create",
		tool=tool,
		reference_doctype="Customer",
		reference_name=doc.name,
		changes={"customer_name": name, "customer_type": customer_type, "portal_user": user},
	)

	return {
		"created": "Customer",
		"name": doc.name,
		"note": "Set up. A colleague will confirm the account before ordering begins.",
	}


def my_customer() -> str | None:
	"""The Customer this login is already attached to, if there is one.

	Two ways in, because ERPNext has two. `portal_users` is the modern link and the one this
	module writes; a Contact carrying the same email is the older one, and a customer who
	was set up by staff years ago will have that and not the other.
	"""
	user = _portal_user()

	# `get_all` ignores permissions by design, which is what this needs: the caller cannot
	# read Customer, and the answer decides whether they are allowed to create one.
	linked = frappe.get_all(
		"Portal User",
		filters={"user": user, "parenttype": "Customer"},
		pluck="parent",
		parent_doctype="Customer",
		limit=1,
	)
	if linked:
		return linked[0]

	contacts = frappe.get_all("Contact", filters={"user": user}, pluck="name")
	if not contacts:
		return None

	links = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": "Customer",
			"parenttype": "Contact",
			"parent": ("in", contacts),
		},
		pluck="link_name",
		parent_doctype="Contact",
		limit=1,
	)
	return links[0] if links else None


# -- internals -----------------------------------------------------------------------


def _portal_user() -> str:
	"""The caller, refused if there is nobody to speak of.

	Guest is the case that matters. Everything in this module writes on the caller's behalf,
	and "the caller" being an anonymous visitor would make it a public record-creation
	endpoint pointed at the site's own database.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		raise GuardError("Please sign in first.")
	return user
