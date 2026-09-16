# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Writing to a customer.

This is the only thing the agent does that leaves the building. Everything else is a row in
a database that a colleague can correct; an email is read by somebody outside the company
and cannot be taken back. So it is the most tightly bounded tool in the app, in three ways:

- **The agent cannot choose who to write to.** Recipients come from the record — the lead's
  own address, the contact linked to the customer, the address already on the quotation.
  An address the model supplies is checked against that list and refused if it is not on it.
  Without this, one prompt injection in a customer's own notes field turns the assistant
  into a way to send company data to an attacker's inbox.
- **It goes out as the user, from the user's account, onto the record's timeline.** Nothing
  here sends anonymously or on behalf of the company at large. The Communication is linked
  to the document, so the email is visible in the desk next to everything else that happened.
- **Sending is a separate, approved step from drafting.** Drafting is cheap and reversible;
  sending is neither. They are two tools so that the approval card for one is not consent
  for the other.

`frappe.core.doctype.communication.email.make` does the work, which means Frappe's own
`email` permission on the document is checked, the outgoing account and signature are the
user's, and threading and reply-tracking behave as they do in the desk.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import escape_html

from sales_ai.guard import GuardError, deny, may_change
from sales_ai.guard.specs import SPECS
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

MAX_SUBJECT = 200
MAX_BODY = 10000

# Where each DocType keeps the address it was already going to be sent to. Customer is
# absent on purpose: it has no email field of its own, and its address comes from the
# linked Contact like everything else.
_EMAIL_FIELDS: dict[str, tuple[str, ...]] = {
	"Lead": ("email_id",),
	"Opportunity": ("contact_email",),
	"Quotation": ("contact_email",),
	"Sales Order": ("contact_email",),
	"Sales Invoice": ("contact_email",),
	"Delivery Note": ("contact_email",),
	"Contact": ("email_id",),
}


def recipients_for(doctype: str, name: str) -> dict[str, Any]:
	"""Every address this record could legitimately be written to.

	Public because it is a tool in its own right: the model has to be able to ask "who
	would this go to" without sending anything, and a user has to be able to see the list
	before approving. A record with nobody on it is a fact worth reporting rather than a
	failure to hide — it usually means somebody forgot to add the contact.
	"""
	if doctype not in SPECS:
		raise GuardError(f"{doctype!r} is not a record the assistant works with.")

	doc = may_change(doctype, name, "read", "Email")
	found = _addresses(doc)

	return {
		"doctype": doctype,
		"name": name,
		"recipients": found,
		"note": (
			f"No email address is on this {doctype} or on any contact linked to it. "
			"Somebody will need to add one in ERPNext before it can be written to."
		)
		if not found
		else None,
	}


def send(
	doctype: str,
	name: str,
	subject: str,
	body: str,
	*,
	to: list[str] | None = None,
	attach_print: bool = False,
	send_now: bool,
	tool: str,
) -> dict[str, Any]:
	"""Write to the people already on a record — as a draft, or actually out the door.

	One function for both because the difference is a single flag and every check before it
	is identical. Two functions would be two places for the recipient rule to live, and the
	second copy is the one that would eventually be written more loosely.
	"""
	if doctype not in SPECS:
		raise GuardError(f"{doctype!r} is not a record the assistant works with.")

	heading = (subject or "").strip()
	if not heading:
		raise GuardError("The email needs a subject.")
	text = (body or "").strip()
	if not text:
		raise GuardError("The email is empty.")
	if len(text) > MAX_BODY:
		raise GuardError(f"The email is too long; keep it under {MAX_BODY} characters.")

	# `email` rather than `write`: Frappe has a permission for exactly this, and a role
	# that may read a quotation is not necessarily one that may write to the customer.
	doc = may_change(doctype, name, "email", "Email")

	known = _addresses(doc)
	if not known:
		raise GuardError(
			f"There is no email address on {doctype} {name}, or on any contact linked to "
			"it. Somebody will need to add one in ERPNext first."
		)

	chosen = _checked_recipients(to, known, doctype, name)

	communication = frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"subject": heading[:MAX_SUBJECT],
			"content": _as_html(text),
			"sender": frappe.session.user,
			"recipients": ", ".join(chosen),
			"sent_or_received": "Sent",
			"reference_doctype": doctype,
			"reference_name": doc.name,
			"status": "Linked" if not send_now else "Open",
		}
	).insert(ignore_permissions=False)

	if send_now:
		_deliver(communication, doc, attach_print)

	record_action(
		action="Email" if send_now else "Draft",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes={
			"communication": communication.name,
			"recipients": chosen,
			"subject": heading[:MAX_SUBJECT],
			"sent": send_now,
		},
	)

	return {
		"sent" if send_now else "drafted": doctype,
		"name": doc.name,
		"communication": communication.name,
		"to": chosen,
		"subject": heading[:MAX_SUBJECT],
		"note": (
			"Sent. It is on the record's timeline in ERPNext."
			if send_now
			else "Saved on the record's timeline but NOT sent. Nobody has received this. "
			"Sending it is a separate step the user has to ask for."
		),
	}


# -- internals -----------------------------------------------------------------------


def _addresses(doc) -> list[str]:
	"""Addresses on the record itself, then on every Contact linked to it and to its party.

	The party hop matters: a quotation usually carries no address of its own, and the person
	it is going to is a Contact against the Customer. Without it the tool would refuse to
	write to almost every real document.
	"""
	found: list[str] = []

	for fieldname in _EMAIL_FIELDS.get(doc.doctype, ()):
		value = (doc.get(fieldname) or "").strip()
		if value:
			found.append(value)

	links = [(doc.doctype, doc.name)]
	party = doc.get("customer") or doc.get("party_name")
	if party and (doc.get("quotation_to") or "Customer") == "Customer":
		links.append(("Customer", party))

	for link_doctype, link_name in links:
		found.extend(_contact_emails(link_doctype, link_name))

	# Deduplicated case-insensitively, order preserved, so the first address — the one on
	# the document — stays the default the model sees first.
	seen: set[str] = set()
	return [a for a in found if a.lower() not in seen and not seen.add(a.lower())]


def _contact_emails(doctype: str, name: str) -> list[str]:
	"""Emails of the Contacts linked to a record, primary ones first."""
	contacts = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": doctype, "link_name": name, "parenttype": "Contact"},
		pluck="parent",
		parent_doctype="Contact",
	)
	if not contacts:
		return []

	rows = frappe.get_all(
		"Contact Email",
		filters={"parent": ["in", contacts]},
		fields=["email_id", "is_primary"],
		order_by="is_primary desc",
		parent_doctype="Contact",
	)
	return [row.email_id.strip() for row in rows if (row.email_id or "").strip()]


def _checked_recipients(
	to: list[str] | None, known: list[str], doctype: str, name: str
) -> list[str]:
	"""The addresses to write to: the record's own, or a subset of them the model named.

	The refusal is the point of the whole module. An address the model invented, or read out
	of a field a customer controls, is not on this list, and the only way onto it is for a
	person to have put it on the record in ERPNext.
	"""
	if not to:
		# The primary one only. Copying in everybody on file is not what "email them" means,
		# and a model that defaults to all of them will eventually reply-all a private price.
		return known[:1]

	lookup = {address.lower(): address for address in known}
	unknown = [address for address in to if address.strip().lower() not in lookup]
	if unknown:
		raise deny(
			"Email",
			doctype,
			name,
			f"Cannot write to {', '.join(sorted(unknown))}: not an address on this "
			f"{doctype}. You may only write to {', '.join(known)}. To reach somebody "
			"else, a person has to add them as a contact in ERPNext first.",
		)

	return [lookup[address.strip().lower()] for address in to]


def _as_html(text: str) -> str:
	"""Plain text in, safe HTML out.

	The model writes prose and Frappe sends HTML. Escaping first and adding the line breaks
	afterwards means nothing the model read on the record — or was told to write by it —
	can become markup or a link in a customer's inbox.
	"""
	return "<br>".join(escape_html(line) for line in text.splitlines())


def _deliver(communication, doc, attach_print: bool) -> None:
	"""Hand it to Frappe's outgoing queue, and translate the failures worth naming.

	A missing outgoing account is the common one and reaches the user as the agent loop's
	generic phrase otherwise, which is the one failure they could actually have fixed.
	"""
	try:
		communication.send_email(
			print_html=None,
			print_format=doc.meta.default_print_format or "Standard" if attach_print else None,
			attachments=None,
		)
	except frappe.OutgoingEmailError as exc:
		raise GuardError(
			"The email could not be sent because this site has no outgoing email account "
			f"set up for {frappe.session.user}. It has been saved on the record's timeline "
			f"but not delivered. ERPNext says: {exc}"
		) from None
