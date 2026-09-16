# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Writing to a customer: who it would go to, drafting it, and sending it.

Three tools rather than one, because they are three different decisions and only the last
one is irreversible. `list_recipients` lets the model check before it commits to anything,
`draft_email` puts the text somewhere a person can read it, and `send_email` is the one
that leaves the building.
"""

from __future__ import annotations

from typing import Annotated, Any

from sales_ai.guard import email
from sales_ai.llm.tool import tool
from sales_ai.tools import register
from sales_ai.tools.read import SalesDocType


@tool(
	writes=False,
	risk="none",
	description="""List the email addresses a record can be written to.

Use this before drafting or sending, so you can tell the user who it will go to. The
addresses come from the record and from the contacts linked to it — you cannot write to
anything that is not on this list, so do not offer to.""",
)
def list_recipients(
	doctype: Annotated[SalesDocType, "Type of record."],
	name: Annotated[str, "The record's ID."],
) -> dict[str, Any]:
	return email.recipients_for(doctype, name)


def _preview(arguments: dict[str, Any]) -> dict[str, Any]:
	"""The whole email, to whom, before it goes.

	The entire message rather than a summary of it, because the thing being approved is
	the wording. A card that said "send an email about SAL-QTN-2026-00007" would be asking
	the user to consent to text they have not read.
	"""
	return {
		"to": email.recipients_for(arguments.get("doctype"), arguments.get("name"))["recipients"]
		if not arguments.get("to")
		else arguments.get("to"),
		"about": f"{arguments.get('doctype')} {arguments.get('name')}",
		"subject": arguments.get("subject"),
		"body": arguments.get("body"),
	}


@tool(
	writes=True,
	# Nobody receives it. It is a note on the timeline that happens to look like an email.
	risk="low",
	action="draft an email about {doctype} {name}",
	preview=_preview,
	description="""Write an email and save it on the record's timeline WITHOUT sending it.

Use this when the user wants to see the wording first, or when you are not certain they
want it sent. Nobody receives it. Say so plainly — never imply the customer has it.

Sending it afterwards is send_email.""",
)
def draft_email(
	doctype: Annotated[SalesDocType, "Type of record the email is about."],
	name: Annotated[str, "The record's ID."],
	subject: Annotated[str, "Subject line."],
	body: Annotated[str, "The email itself, in plain text. Line breaks are kept."],
	to: Annotated[
		list[str] | None,
		"Who to write to. Must be addresses from list_recipients. Omit for the main one.",
	] = None,
) -> dict[str, Any]:
	return email.send(
		doctype, name, subject, body, to=to, send_now=False, tool="draft_email"
	)


@tool(
	writes=True,
	# The only thing the agent does that a colleague cannot undo. It is read by somebody
	# outside the company and there is no taking it back.
	risk="high",
	action="email {doctype} {name} to the customer",
	preview=_preview,
	description="""Send an email to the people on a record, from the current user's account.

This actually goes out and cannot be recalled. Show the user the wording and who it is
going to, and get a clear yes, before calling this — "email them the quote" on its own is
usually a request to draft it.

You can only write to addresses already on the record or its contacts. If the person the
user named is not one of them, say so; do not send it to somebody else instead.""",
)
def send_email(
	doctype: Annotated[SalesDocType, "Type of record the email is about."],
	name: Annotated[str, "The record's ID."],
	subject: Annotated[str, "Subject line."],
	body: Annotated[str, "The email itself, in plain text. Line breaks are kept."],
	to: Annotated[
		list[str] | None,
		"Who to write to. Must be addresses from list_recipients. Omit for the main one.",
	] = None,
	attach_print: Annotated[
		bool, "Attach the document itself as a PDF. Use for quotations and invoices."
	] = False,
) -> dict[str, Any]:
	return email.send(
		doctype,
		name,
		subject,
		body,
		to=to,
		attach_print=attach_print,
		send_now=True,
		tool="send_email",
	)


register(list_recipients)
register(draft_email)
register(send_email)
