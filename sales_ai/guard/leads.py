# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Moving a lead along: into an opportunity, or into a customer.

Both are ERPNext's own mappers (`make_opportunity`, `make_customer`), not a field-by-field
copy written here. A lead carries an address, a contact, a territory and a source, and the
mapper knows which of those belong on the target and which do not. Re-implementing it would
produce a customer that looks right and is subtly missing the links.

The one thing added on top is a refusal ERPNext does not make. `make_customer` will happily
create a second "ABC Medical Store" — a company can genuinely have two customer records —
and afterwards nobody can tell which one the orders should go on. Converting the same lead
twice, or converting a lead whose company is already a customer, is almost always a mistake,
so it names the record that already exists instead of quietly making another.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from sales_ai.guard import GuardError, deny, may_change, read_document
from sales_ai.guard.permissions import check_ai_permission
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action


def convert_to_customer(name: str, *, tool: str) -> dict[str, Any]:
	"""Turn a lead into a customer account, or point at the one that already exists."""
	# RBAC pre-check: need write on Lead and create on Customer
	result = check_ai_permission(doctype="Customer", action="create")
	if not result.allowed:
		raise GuardError(result.reason)

	lead = may_change("Lead", name, "write", "Convert")

	if lead.customer:
		return {
			"unchanged": "Lead",
			"name": lead.name,
			"customer": lead.customer,
			"note": f"This lead was already converted to customer {lead.customer}.",
		}

	if not frappe.has_permission("Customer", "create"):
		raise deny("Convert", "Lead", name, "You cannot create a Customer.")

	existing = _existing_customer(lead)
	if existing:
		return existing

	from erpnext.crm.doctype.lead.lead import make_customer

	customer = make_customer(lead.name)
	customer.insert()
	customer.add_comment(
		"Info", _("Converted from lead {0} by Sales AI for {1}.").format(lead.name, frappe.session.user)
	)

	record_action(
		action="Create",
		tool=tool,
		reference_doctype="Customer",
		reference_name=customer.name,
		changes={"from_lead": lead.name, "customer_name": customer.customer_name},
	)

	return {
		"created": "Customer",
		"name": customer.name,
		"from_lead": lead.name,
		"record": read_document("Customer", customer.name),
	}


def convert_to_opportunity(name: str, *, tool: str) -> dict[str, Any]:
	"""Turn a lead into an opportunity, so it can carry a value and a close date.

	Unlike the customer conversion this is not refused when one already exists. A lead
	can legitimately produce two opportunities — two deals, two budgets, two decisions —
	and the open ones are reported so the model can say so rather than guessing.
	"""
	# RBAC pre-check: need write on Lead and create on Opportunity
	result = check_ai_permission(doctype="Opportunity", action="create")
	if not result.allowed:
		raise GuardError(result.reason)

	lead = may_change("Lead", name, "write", "Convert")

	if not frappe.has_permission("Opportunity", "create"):
		raise deny("Convert", "Lead", name, "You cannot create an Opportunity.")

	open_already = frappe.get_all(
		"Opportunity",
		filters={"opportunity_from": "Lead", "party_name": lead.name, "status": "Open"},
		pluck="name",
		limit=5,
	)

	from erpnext.crm.doctype.lead.lead import make_opportunity

	opportunity = make_opportunity(lead.name)
	opportunity.insert()
	opportunity.add_comment(
		"Info", _("Created from lead {0} by Sales AI for {1}.").format(lead.name, frappe.session.user)
	)

	record_action(
		action="Create",
		tool=tool,
		reference_doctype="Opportunity",
		reference_name=opportunity.name,
		changes={"from_lead": lead.name},
	)

	result = {
		"created": "Opportunity",
		"name": opportunity.name,
		"from_lead": lead.name,
		"record": read_document("Opportunity", opportunity.name),
	}
	if open_already:
		result["note"] = (
			f"This lead already had open opportunities: {', '.join(open_already)}. "
			"Tell the user, in case they meant one of those."
		)
	return result


# -- internals -----------------------------------------------------------------------


def _existing_customer(lead: Any) -> dict[str, Any] | None:
	"""A customer this lead would duplicate, if there is one.

	The lookup ignores permissions, because a duplicate the user cannot see is still a
	duplicate and creating a second one still breaks the reporting. What is gated is the
	*name*: telling somebody a customer exists that they have no access to is a disclosure
	in its own right, so that case gets the fact without the identifier.
	"""
	candidate = (lead.company_name or lead.lead_name or "").strip()
	if not candidate:
		return None

	existing = frappe.db.get_value("Customer", {"customer_name": candidate}, "name")
	if not existing:
		return None

	if not frappe.has_permission("Customer", "read", doc=existing):
		raise GuardError(
			f"A customer called {candidate!r} already exists, but is not one you have access "
			"to. Somebody will need to sort out which account this lead belongs on."
		)

	return {
		"unchanged": "Lead",
		"name": lead.name,
		"customer": existing,
		"note": (
			f"Customer {existing!r} is already called {candidate!r}, so this lead was not "
			"converted — a second account would split their orders across two records. "
			"Work with that customer, or a person can convert this lead under a different name."
		),
	}
