# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Read-only sales tools.

Two tools cover reading, because a third one would only be the same query with different
words. What keeps this safe is not the number of tools but what they refuse: the DocType
is a fixed enum, the fields are an allowlist, the filters are checked against that
allowlist, and the query runs as the current user.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sales_ai.guard import DEFAULT_LIMIT, MAX_LIMIT, Filter, describe, read_document, read_list
from sales_ai.guard.specs import SPECS
from sales_ai.llm.tool import tool
from sales_ai.tools import register

SalesDocType = Literal[
	"Contact",
	"Customer",
	"Item",
	"Lead",
	"Opportunity",
	"Quotation",
	"Sales Order",
]

# Keeps the enum the model sees and the allowlist the guard enforces in step.
assert set(SalesDocType.__args__) == set(SPECS), "SalesDocType and SPECS have drifted apart"

_CATALOGUE = "\n".join(f"- {describe(doctype)}" for doctype in SalesDocType.__args__)


@tool(
	writes=False,
	description=f"""Search sales records the current user is allowed to see.

Returns a small set of summary fields per record. Use get_record for the full detail of
one record. Results already respect the user's permissions, so an empty result means
there is nothing matching that this user may see.

Record types:
{_CATALOGUE}""",
)
def search_records(
	doctype: Annotated[SalesDocType, "Type of record to search."],
	query: Annotated[
		str | None,
		"Free text matched against names, contact details and similar identifying fields.",
	] = None,
	filters: Annotated[
		list[Filter] | None,
		"Conditions combined with AND. Only the fields listed for this record type are allowed.",
	] = None,
	order_by: Annotated[
		str | None,
		"Sort order as '<fieldname> asc' or '<fieldname> desc'. Defaults to most recently changed.",
	] = None,
	limit: Annotated[int, f"How many records to return, at most {MAX_LIMIT}."] = DEFAULT_LIMIT,
) -> dict[str, Any]:
	return read_list(doctype, query=query, filters=filters, order_by=order_by, limit=limit)


@tool(
	writes=False,
	description="""Read one sales record in full, including its line items where it has them.

Use this after search_records has given you the record's name (its ID), or when the user
has named a record directly.""",
)
def get_record(
	doctype: Annotated[SalesDocType, "Type of record to read."],
	name: Annotated[str, "The record's ID, e.g. 'CRM-LEAD-2026-00001' or 'SAL-ORD-2026-00014'."],
) -> dict[str, Any]:
	return read_document(doctype, name)


register(search_records)
register(get_record)
