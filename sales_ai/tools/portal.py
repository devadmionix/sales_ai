# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tools for the assistant that customers talk to, not the one the sales team talks to.

Nothing here is meant to appear on a staff agent, and nothing from the staff modules is
meant to appear on the portal one. That separation is not enforced by this file — an agent
profile is a whitelist of tool names, so it is enforced by which names a profile lists, and
by `api.portal_chat` refusing to let the browser choose the profile.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sales_ai.guard import portal
from sales_ai.llm.tool import tool
from sales_ai.tools import register


@tool(
	writes=True,
	# Creates a record, for somebody with no permission to create one, on a surface open to
	# anybody who can register on the website. Nothing about that belongs in a band that
	# runs unattended.
	risk="high",
	action="set up the customer account {customer_name}",
	description="""Set up the customer account for the person you are talking to.

Use this once they have told you the name the account should be in. It creates the account
for *them* — you cannot use it to look up, join or alter anybody else's.

If the account cannot be created automatically, say so plainly and tell them a colleague
will be in touch. Do not guess at the reason and do not try again with a different name.""",
)
def register_customer(
	customer_name: Annotated[str, "The name the account should be in, as they gave it."],
	customer_type: Annotated[
		Literal["Company", "Individual"], "Whether they are buying as a business or a person."
	] = "Company",
) -> dict[str, Any]:
	return portal.register_customer(
		customer_name, customer_type=customer_type, tool="register_customer"
	)


@tool(
	writes=False,
	risk="none",
	action="check whether this account is set up",
	description="""Check whether the person you are talking to already has an account.

Worth doing before offering to set one up, so they are not asked for details they have
already given.""",
)
def my_account() -> dict[str, Any]:
	name = portal.my_customer()
	return {"set_up": bool(name), "name": name}


PortalDocType = Literal["Quotation", "Sales Order", "Sales Invoice", "Customer"]


@tool(
	writes=False,
	risk="none",
	description="""List your own sales documents: quotations, orders, or invoices.

Only shows records linked to your customer account. If you are not linked to a customer
yet, it will tell you. Use my_document to get the full detail of a single record.""",
)
def my_documents(
	doctype: Annotated[PortalDocType, "Type of record to list."],
	limit: Annotated[int, "How many records to return, at most 50."] = 20,
) -> dict[str, Any]:
	from sales_ai.guard.portal_reads import portal_read_list
	return portal_read_list(doctype, limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Read one of your own documents in detail, including line items.

Use this after my_documents has given you the record's name, or when you already
know the record ID. Only your own records are accessible.""",
)
def my_document(
	doctype: Annotated[PortalDocType, "Type of record to read."],
	name: Annotated[str, "The record's ID, e.g. 'SAL-ORD-2026-00014'."],
) -> dict[str, Any]:
	from sales_ai.guard.portal_reads import portal_read_document
	return portal_read_document(doctype, name)


register(register_customer)
register(my_account)
register(my_documents)
register(my_document)
