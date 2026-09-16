# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Submit and cancel, the two irreversible verbs.

Both are `risk="high"` so an approval card is shown, and both carry a preview, so the card
names the customer and the amount rather than an ID nobody can read back. Approving
"cancel SAL-ORD-2026-00031" is a coin toss; approving "cancel a ₹4,20,000 order for ABC
Medical Store" is a decision.

There is deliberately no delete tool. See `sales_ai.guard.documents`.
"""

from __future__ import annotations

from typing import Annotated, Any

from sales_ai.guard import documents
from sales_ai.llm.tool import tool
from sales_ai.tools import register

_DOCTYPES = ", ".join(documents.SUBMITTABLE)


def _preview(arguments: dict[str, Any]) -> dict[str, Any]:
	"""The document as it stands, read back rather than recalculated.

	These tools act on what was saved, so a freshly computed price could show the approver
	a figure the document does not actually contain.
	"""
	return documents.preview(arguments.get("doctype"), arguments.get("name"))


@tool(
	writes=True,
	# Submitting reserves stock, posts to the ledger and fixes a commitment to the customer.
	risk="high",
	action="submit {doctype} {name}",
	preview=_preview,
	description=f"""Submit a draft sales document.

Works on: {_DOCTYPES}.

Submitting is what makes a document real. A Sales Order reserves stock and commits a
delivery date; a Sales Invoice posts to the accounts. After this the document cannot be
edited, only cancelled.

Read the document back first and tell the user the customer and the total before doing
this. Only a draft can be submitted.""",
)
def submit_document(
	doctype: Annotated[str, f"The document's type. One of: {_DOCTYPES}."],
	name: Annotated[str, "The document's ID, e.g. 'SAL-ORD-2026-00031'."],
) -> dict[str, Any]:
	return documents.submit_document(doctype, name, tool="submit_document")


@tool(
	writes=True,
	# Reverses ledger entries and releases stock. ERPNext has no un-cancel.
	risk="high",
	action="cancel {doctype} {name}",
	preview=_preview,
	description=f"""Cancel a submitted sales document, reversing what it committed.

Works on: {_DOCTYPES}.

This is the tool to use when somebody asks to delete, remove, void or scrap a submitted
document. There is no delete: cancelling reverses the stock and the accounting entries and
leaves the record in place, which is what an audit needs and what lets a mistake be traced.
Say so plainly rather than claiming the document was deleted.

Cancelling cannot be undone — a cancelled document is amended into a new one, not revived.
Be sure you have the right document: confirm the customer and the total with the user
first, by ID, before calling this.

Only a submitted document can be cancelled. A draft commits nothing and has nothing to
reverse.""",
)
def cancel_document(
	doctype: Annotated[str, f"The document's type. One of: {_DOCTYPES}."],
	name: Annotated[str, "The document's ID, e.g. 'SAL-ORD-2026-00031'."],
	reason: Annotated[
		str,
		"Why it is being cancelled, in the user's own words. Goes on the document's "
		"timeline and into the audit log. Ask the user if they have not said.",
	],
) -> dict[str, Any]:
	return documents.cancel_document(doctype, name, reason, tool="cancel_document")


register(submit_document)
register(cancel_document)
