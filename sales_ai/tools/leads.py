# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Moving a lead on to the next thing.

Two tools rather than one with a `target` parameter, because they are not variations of one
another. A customer is an account that outlives the deal; an opportunity is the deal. Asking
the model to pick between them from an enum invites it to pick the wrong one from a sentence
that does not say.
"""

from __future__ import annotations

from typing import Annotated, Any

from sales_ai.guard import leads, read_document
from sales_ai.llm.tool import tool
from sales_ai.tools import register


def _preview(arguments: dict[str, Any]) -> dict[str, Any]:
	"""The lead itself, so the approver sees who is about to be converted.

	Read through the usual path, which means the same permission check and the same
	untrusted-field marking the model got — the approval card cannot show more than the
	agent was allowed to see.
	"""
	return read_document("Lead", arguments.get("name"))


@tool(
	writes=True,
	# Creates a customer account other people will trade against, and a duplicate is a
	# mess somebody has to unpick by hand afterwards.
	risk="high",
	action="convert lead {name} into a customer",
	preview=_preview,
	description="""Turn a lead into a customer account.

Use this when the lead is going to buy — a customer is needed before an order can be
raised against them. ERPNext copies the name, address and contact across.

If a customer of that name already exists, or the lead was converted before, this does
nothing and names the existing customer. Use that one; do not try to force a second.""",
)
def convert_lead_to_customer(
	name: Annotated[str, "The lead's ID, e.g. 'CRM-LEAD-2026-00001'."],
) -> dict[str, Any]:
	return leads.convert_to_customer(name, tool="convert_lead_to_customer")


@tool(
	writes=True,
	# Creates a pipeline record with a value on it, which is what forecasts are read off.
	risk="medium",
	action="create an opportunity from lead {name}",
	preview=_preview,
	description="""Turn a lead into an opportunity.

Use this once there is a real deal to track — an opportunity carries a value, a close date
and a stage, which a lead does not.

The lead stays a lead. One lead can have several opportunities, so this is not refused
when one already exists; any open ones are reported so you can mention them.""",
)
def convert_lead_to_opportunity(
	name: Annotated[str, "The lead's ID, e.g. 'CRM-LEAD-2026-00001'."],
) -> dict[str, Any]:
	return leads.convert_to_opportunity(name, tool="convert_lead_to_opportunity")


register(convert_lead_to_customer)
register(convert_lead_to_opportunity)
