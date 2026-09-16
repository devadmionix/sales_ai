# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tools that change records.

Every one of these is declared `writes=True`, which is what makes the policy layer stop
and ask a human before it runs. Nothing here decides that for itself.

`values` is a mapping rather than a long list of named parameters because the legal keys
differ per DocType. That is not a loophole: the keys are checked against the DocType's
write allowlist before anything is set, the values may only be scalars, and the same
allowlist is spelled out in the tool description so the model is not guessing.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sales_ai.guard import writes
from sales_ai.guard.specs import WRITE_SPECS
from sales_ai.llm.tool import tool
from sales_ai.tools import register
from sales_ai.tools.read import SalesDocType

WritableDocType = Literal["Contact", "Customer", "Lead", "Opportunity"]

# Keeps the enum the model sees and the allowlist the guard enforces in step.
assert set(WritableDocType.__args__) == set(WRITE_SPECS), (
	"WritableDocType and WRITE_SPECS have drifted apart"
)

Scalar = str | float | bool | None

_CREATABLE = "\n".join(
	f"- {writes.describe_writable(doctype, creating=True)}"
	for doctype in WritableDocType.__args__
)
_UPDATABLE = "\n".join(
	f"- {writes.describe_writable(doctype, creating=False)}"
	for doctype in WritableDocType.__args__
)


@tool(
	writes=True,
	# Bounded by an allow-list of doctypes and fields, but within it the model chooses both,
	# so the blast radius is wider than any single-purpose tool's.
	risk="high",
	action="create a new {doctype}",
	description=f"""Create a new sales record.

When the user asks for a record, create it. Only `*` fields must be supplied; ERPNext
fills in the rest itself, so do not ask the user for values they did not offer. A
duplicate is refused and the existing record named, so there is no need to search first.

The record is created as the current user, so it lands in their company and territory
automatically — do not try to set a company.

Fields you may set, `*` where required, by record type:
{_CREATABLE}""",
)
def create_record(
	doctype: Annotated[WritableDocType, "Type of record to create."],
	values: Annotated[
		dict[str, Scalar],
		"Field values to set. Only the fields listed for this record type are accepted.",
	],
) -> dict[str, Any]:
	return writes.create_record(doctype, values, tool="create_record")


@tool(
	writes=True,
	# Overwrites what is already there, so unlike the others it can destroy information.
	risk="high",
	action="change {doctype} {name}",
	description=f"""Change fields on an existing sales record.

Only send the fields that should change; everything else is left alone. Read the record
first if you are not certain of its current values.

Fields you may change, by record type:
{_UPDATABLE}""",
)
def update_record(
	doctype: Annotated[WritableDocType, "Type of record to change."],
	name: Annotated[str, "The record's ID, e.g. 'CRM-LEAD-2026-00001'."],
	values: Annotated[
		dict[str, Scalar],
		"Fields to change and their new values.",
	],
) -> dict[str, Any]:
	return writes.update_record(doctype, name, values, tool="update_record")


@tool(
	writes=True,
	# Additive and internal: it replaces nothing and leaves the record itself untouched.
	risk="low",
	action="add a note to {doctype} {name}",
	description="""Add a note to a record's timeline.

Use this to record what was discussed or decided. Notes are additive and never replace
anything, so this is the safe way to write down something that has no field of its own.""",
)
def add_note(
	doctype: Annotated[SalesDocType, "Type of record to annotate."],
	name: Annotated[str, "The record's ID."],
	note: Annotated[str, "The note, in plain text."],
) -> dict[str, Any]:
	return writes.add_note(doctype, name, note, tool="add_note")


@tool(
	writes=True,
	# A reminder in the user's own to-do list. It cannot reach anyone else.
	risk="low",
	action="schedule a follow-up on {doctype} {name}",
	description="""Schedule a follow-up reminder about a record.

The reminder goes into the current user's own to-do list. You cannot assign work to
anyone else.""",
)
def create_follow_up(
	doctype: Annotated[SalesDocType, "Type of record the follow-up is about."],
	name: Annotated[str, "The record's ID."],
	date: Annotated[str, "When to follow up, as YYYY-MM-DD."],
	description: Annotated[str, "What to do, in plain text."],
) -> dict[str, Any]:
	return writes.create_follow_up(doctype, name, date, description, tool="create_follow_up")


register(create_record)
register(update_record)
register(add_note)
register(create_follow_up)
