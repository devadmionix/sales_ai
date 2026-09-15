# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The two questions that come up in every sales conversation: have we got it, and
what does it cost.

Both are answered by ERPNext rather than by the model. Stock comes from `Bin`, price comes
from a Quotation ERPNext prices itself. A model that guesses either one sounds exactly as
confident as one that does not, which is why neither is left to it.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from sales_ai.guard import quotations
from sales_ai.guard.pricing import MAX_LINES, price_for
from sales_ai.guard.stock import availability
from sales_ai.llm.tool import tool
from sales_ai.tools import register


class QuoteLine(BaseModel):
	"""One line of a quote: an item and how many of it."""

	item_code: str = Field(description="The item's code, e.g. 'AI-GADGET'.")
	qty: float = Field(default=1, gt=0, description="How many, in the unit of measure below.")
	uom: str | None = Field(
		default=None,
		description="Unit of measure. Omit to use the item's own default selling unit.",
	)


@tool(
	writes=False,
	description="""Check how much of an item is in stock.

Use this before promising a delivery. Returns, per warehouse, what is physically on hand,
what is already reserved for other orders, what is on order from suppliers, and ERPNext's
own projected quantity.

available_qty is the number to quote from: on hand minus already promised. Never quote
actual_qty on its own, and never add quantities up across warehouses the user did not ask
about — the totals here already do that.

Items that are not stock-tracked, such as services, say so rather than reporting zero.""",
)
def check_availability(
	item_code: Annotated[str, "The item's code, e.g. 'AI-GADGET'."],
	warehouse: Annotated[
		str | None,
		"Limit to one warehouse. A group warehouse includes everything under it. "
		"Omit for every warehouse this user can see.",
	] = None,
) -> dict[str, Any]:
	return availability(item_code, warehouse)


@tool(
	writes=False,
	description=f"""Work out what ERPNext would charge for a set of items.

Use this whenever a price is asked for. Do not read a rate off an item record and multiply
it yourself: the real price depends on the price list, the customer's own negotiated
prices, quantity breaks, pricing rules and tax, and ERPNext applies all of them here.

Pass the customer whenever you know it, because customer-specific prices and taxes will
otherwise be missed and the quote will be wrong in the customer's favour or ours.

Returns a rate and amount per line and the totals including tax, in the quoting currency.
Nothing is saved — this answers "what would it cost", not "please quote it".

At most {MAX_LINES} lines.""",
)
def price_items(
	lines: Annotated[list[QuoteLine], "The items and quantities to price."],
	customer: Annotated[str | None, "Customer the quote is for, if known."] = None,
	company: Annotated[str | None, "Company selling. Defaults to the user's own."] = None,
	price_list: Annotated[
		str | None,
		"Selling price list to quote from. Defaults to the customer's or the company's.",
	] = None,
	date: Annotated[
		str | None,
		"Date the price applies on (YYYY-MM-DD). Defaults to today. Prices can be dated.",
	] = None,
) -> dict[str, Any]:
	return price_for(
		[line.model_dump(exclude_none=True) for line in lines],
		customer=customer,
		company=company,
		price_list=price_list,
		date=date,
	)


def _pricing_preview(arguments: dict[str, Any]) -> dict[str, Any]:
	"""Price the lines before the human is asked to approve drafting them.

	Deliberately the same call `price_items` makes, so the card, the saved document and
	anything the agent said in the conversation are all the one figure. Quantities alone
	hide the thing that matters: 200 bolts at a bulk discount is not 200 times list price.
	"""
	return {
		"customer": arguments.get("customer"),
		**price_for(
			arguments.get("lines") or [],
			customer=arguments.get("customer"),
			company=arguments.get("company"),
			price_list=arguments.get("price_list"),
			date=arguments.get("date"),
		),
	}


def _quotation_preview(arguments: dict[str, Any]) -> dict[str, Any]:
	"""The totals already on the quotation being submitted or converted.

	Read back from the document rather than recalculated: these two steps act on what was
	saved, so showing a fresh price could show a figure the document does not contain.
	"""
	return quotations.preview(arguments.get("name"))


@tool(
	writes=True,
	action="draft a quotation for {customer}",
	preview=_pricing_preview,
	description=f"""Save a quotation as a draft in ERPNext.

Use this once the customer and the items are settled. Price it with price_items first and
tell the user what it comes to — do not draft a quotation to find out the price.

The quotation is saved as a draft: it is not sent to anyone, commits nothing, and can be
deleted. ERPNext works out the rates and totals, exactly as price_items does. Submitting it
is a separate step.

At most {MAX_LINES} lines.""",
)
def draft_quotation(
	lines: Annotated[list[QuoteLine], "The items and quantities to quote."],
	customer: Annotated[str, "Customer the quotation is for."],
	company: Annotated[str | None, "Company selling. Defaults to the user's own."] = None,
	price_list: Annotated[str | None, "Selling price list to quote from."] = None,
	date: Annotated[str | None, "Date of the quotation (YYYY-MM-DD). Defaults to today."] = None,
	valid_till: Annotated[str | None, "Date the quotation expires (YYYY-MM-DD)."] = None,
) -> dict[str, Any]:
	return quotations.draft_quotation(
		[line.model_dump(exclude_none=True) for line in lines],
		customer=customer,
		company=company,
		price_list=price_list,
		date=date,
		valid_till=valid_till,
		tool="draft_quotation",
	)


@tool(
	writes=True,
	action="submit quotation {name}",
	preview=_quotation_preview,
	description="""Submit a draft quotation.

Submitting makes the quotation the company's official position on price. It stops being
editable, and the customer can be sent it. Read it back first and check the totals are
what the user expects — after this, changing it means cancelling and starting again.

Only a draft can be submitted.""",
)
def submit_quotation(
	name: Annotated[str, "The quotation's ID, e.g. 'SAL-QTN-2026-00007'."],
) -> dict[str, Any]:
	return quotations.submit_quotation(name, tool="submit_quotation")


@tool(
	writes=True,
	action="turn quotation {name} into a draft sales order",
	preview=_quotation_preview,
	description="""Create a draft sales order from a submitted quotation.

Use this when the customer has accepted the quotation. ERPNext copies the lines and
totals across, so the order agrees with the quotation rather than being re-priced.

The sales order is left as a DRAFT. It reserves no stock and commits no delivery. A
person has to submit it in ERPNext — you cannot, and should say so rather than implying
the order is placed.

The quotation must be submitted, and must be made out to a Customer rather than a lead.""",
)
def convert_quotation_to_order(
	name: Annotated[str, "The submitted quotation's ID."],
	delivery_date: Annotated[str, "When the customer expects delivery (YYYY-MM-DD)."],
) -> dict[str, Any]:
	return quotations.convert_to_sales_order(
		name, delivery_date=delivery_date, tool="convert_quotation_to_order"
	)


register(check_availability)
register(price_items)
register(draft_quotation)
register(submit_quotation)
register(convert_quotation_to_order)
