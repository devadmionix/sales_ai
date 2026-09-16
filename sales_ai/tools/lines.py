# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Changing what is on a draft.

One tool rather than four, because add, change and remove are almost always asked for in
the same breath — "drop the monitors and make it twenty laptops" is one sentence and should
be one approval, not three.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from sales_ai.guard import lines
from sales_ai.llm.tool import tool
from sales_ai.tools import register

_DOCTYPES = ", ".join(lines.REVISABLE)


class ReviseLine(BaseModel):
	"""An item and how much of it."""

	item_code: str = Field(description="The item's code, e.g. 'AI-GADGET'.")
	qty: float | None = Field(
		default=None, description="How many. Leave out when only the rate is changing."
	)
	uom: str | None = Field(default=None, description="Unit of measure. Omit for the default.")
	rate: float | None = Field(
		default=None,
		description=(
			"Force a specific rate per unit. Leave this out unless the user gave a "
			"figure — ERPNext works out the right price from the price list, the "
			"customer's own terms and any pricing rules, and setting it here overrides "
			"all of that."
		),
	)


def _preview(arguments: dict[str, Any]) -> dict[str, Any]:
	"""The document as it stands now — what is about to be changed."""
	return lines.preview(arguments.get("doctype"), arguments.get("name"))


@tool(
	writes=True,
	# A draft, so nothing is committed — but it changes a priced document somebody may
	# already be looking at, and a forced rate is a real discount.
	risk="medium",
	action="change the lines on {doctype} {name}",
	preview=_preview,
	description=f"""Add, change or remove items on a DRAFT quotation or sales order.

Works on: {_DOCTYPES}. Only while the document is still a draft — once it is submitted it
has to be cancelled and re-made, which you cannot do.

Use this for "change the quantity to 20", "add 5 more monitors", "take the cables off",
"apply a 5% discount". Read the document first so you know what is on it.

Three separate lists, and they are targeted on purpose:
- add: items not already on the document
- change: items already on it, with the new quantity
- remove: item codes to take off entirely

Only the items you name are touched. Anything you leave out stays exactly as it is, so
never re-send the whole list to change one line.

ERPNext re-prices everything afterwards — rates, discounts, taxes and totals all come back
from it. Do not calculate a total yourself; report the one that comes back.""",
)
def revise_lines(
	doctype: Annotated[str, f"The document's type. One of: {_DOCTYPES}."],
	name: Annotated[str, "The document's ID, e.g. 'SAL-QTN-2026-00007'."],
	add: Annotated[
		list[ReviseLine] | None, "Items to put on the document that are not on it yet."
	] = None,
	change: Annotated[
		list[ReviseLine] | None, "Items already on the document, with their new quantity."
	] = None,
	remove: Annotated[list[str] | None, "Item codes to take off the document entirely."] = None,
	discount_percentage: Annotated[
		float | None,
		"A discount on the whole document, as a percentage. Replaces any discount "
		"already set rather than adding to it.",
	] = None,
) -> dict[str, Any]:
	return lines.revise_lines(
		doctype,
		name,
		add=[line.model_dump(exclude_none=True) for line in add] if add else None,
		change=[line.model_dump(exclude_none=True) for line in change] if change else None,
		remove=remove,
		discount_percentage=discount_percentage,
		tool="revise_lines",
	)


register(revise_lines)
