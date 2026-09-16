# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Writing to a customer, and only to the customer.

Email is the one thing the agent does that leaves the building, so the test that matters
most is not that an email can be composed — it is that an address the model invented, or
read out of a field the customer themselves controls, is refused. Without that check one
prompt injection in a lead's notes turns the assistant into a way to post company data to
an attacker's inbox.

The second thing asserted is that drafting and sending really are different: a draft must
leave nothing in the outgoing queue, because a model that says "I've sent it" when it has
not is worse than one that refuses.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai import tools
from sales_ai.guard import GuardError
from sales_ai.guard.email import recipients_for, send

LEAD_EMAIL = "email-test-lead@example.com"
CONTACT_EMAIL = "email-test-contact@example.com"
ATTACKER = "attacker@example.com"


class TestEmail(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.lead = self._lead()

	def _lead(self):
		name = frappe.db.get_value("Lead", {"lead_name": "Email Test Lead"}, "name")
		if name:
			return frappe.get_doc("Lead", name)
		return frappe.get_doc(
			{
				"doctype": "Lead",
				"lead_name": "Email Test Lead",
				"company_name": "Email Test Co",
				"email_id": LEAD_EMAIL,
				"status": "Lead",
			}
		).insert()

	def _sent(self) -> int:
		return frappe.db.count("Email Queue")

	# -- who it can go to --------------------------------------------------------------

	def test_the_address_on_the_record_is_found(self) -> None:
		found = recipients_for("Lead", self.lead.name)

		self.assertIn(LEAD_EMAIL, found["recipients"])

	def test_a_record_with_nobody_on_it_says_so(self) -> None:
		"""Usually means a contact was never added, which is a fact worth reporting."""
		bare = frappe.get_doc(
			{"doctype": "Lead", "lead_name": "Email Test Silent", "status": "Lead"}
		).insert()

		found = recipients_for("Lead", bare.name)

		self.assertEqual(found["recipients"], [])
		self.assertIn("add one", found["note"])

	def test_a_contact_linked_to_a_customer_is_reachable_through_its_quotation(self) -> None:
		"""A quotation carries no address of its own. Without the party hop this tool
		would refuse to write to almost every real document."""
		customer = self._customer_with_contact()
		quotation = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": customer,
				"items": [{"item_code": "AI-GADGET", "qty": 1}],
			}
		).insert()

		found = recipients_for("Quotation", quotation.name)

		self.assertIn(CONTACT_EMAIL, found["recipients"])

	def _customer_with_contact(self) -> str:
		name = frappe.db.get_value("Customer", {"customer_name": "Email Test Customer"}, "name")
		if not name:
			name = frappe.get_doc(
				{"doctype": "Customer", "customer_name": "Email Test Customer"}
			).insert().name
		if not frappe.db.exists("Contact", {"first_name": "Email Test Contact"}):
			frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": "Email Test Contact",
					"email_ids": [{"email_id": CONTACT_EMAIL, "is_primary": 1}],
					"links": [{"link_doctype": "Customer", "link_name": name}],
				}
			).insert()
		return name

	# -- the refusal this module exists for --------------------------------------------

	def test_an_address_that_is_not_on_the_record_is_refused(self) -> None:
		"""The whole point. An injected "forward this to ..." must go nowhere."""
		with self.assertRaises(GuardError) as caught:
			send(
				"Lead",
				self.lead.name,
				"Pricing",
				"Here are the figures.",
				to=[ATTACKER],
				send_now=True,
				tool="send_email",
			)

		self.assertIn(ATTACKER, str(caught.exception))
		self.assertIn("not an address on this Lead", str(caught.exception))

	def test_the_refused_send_leaves_nothing_in_the_queue(self) -> None:
		before = self._sent()

		with self.assertRaises(GuardError):
			send(
				"Lead",
				self.lead.name,
				"Pricing",
				"Here are the figures.",
				to=[ATTACKER],
				send_now=True,
				tool="send_email",
			)

		self.assertEqual(self._sent(), before)

	def test_the_refusal_is_written_down(self) -> None:
		with self.assertRaises(GuardError):
			send(
				"Lead",
				self.lead.name,
				"Pricing",
				"x",
				to=[ATTACKER],
				send_now=True,
				tool="send_email",
			)

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"outcome": "Denied", "action": "Email", "reference_name": self.lead.name},
			)
		)

	def test_a_record_with_no_address_cannot_be_written_to(self) -> None:
		bare = frappe.get_doc(
			{"doctype": "Lead", "lead_name": "Email Test Nobody", "status": "Lead"}
		).insert()

		with self.assertRaises(GuardError) as caught:
			send("Lead", bare.name, "Hello", "Anyone there?", send_now=False, tool="draft_email")

		self.assertIn("no email address", str(caught.exception))

	# -- drafting is not sending -------------------------------------------------------

	def test_a_draft_reaches_nobody(self) -> None:
		before = self._sent()

		result = send(
			"Lead", self.lead.name, "Pricing", "Here are the figures.",
			send_now=False, tool="draft_email",
		)

		self.assertEqual(self._sent(), before)
		self.assertEqual(result["drafted"], "Lead")
		self.assertIn("NOT sent", result["note"])

	def test_a_draft_lands_on_the_record_timeline(self) -> None:
		result = send(
			"Lead", self.lead.name, "Pricing", "Here are the figures.",
			send_now=False, tool="draft_email",
		)

		self.assertEqual(
			frappe.db.get_value("Communication", result["communication"], "reference_name"),
			self.lead.name,
		)

	def test_the_default_recipient_is_the_one_on_the_record(self) -> None:
		result = send(
			"Lead", self.lead.name, "Pricing", "Figures attached.",
			send_now=False, tool="draft_email",
		)

		self.assertEqual(result["to"], [LEAD_EMAIL])

	def test_the_draft_is_written_down(self) -> None:
		send(
			"Lead", self.lead.name, "Pricing", "Figures attached.",
			send_now=False, tool="draft_email",
		)

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"action": "Draft", "reference_name": self.lead.name, "tool": "draft_email"},
			)
		)

	# -- the body ----------------------------------------------------------------------

	def test_the_body_cannot_carry_markup_into_the_customers_inbox(self) -> None:
		"""The model composed this after reading fields a customer can write to."""
		result = send(
			"Lead",
			self.lead.name,
			"Pricing",
			'<a href="http://evil.example">click</a>',
			send_now=False,
			tool="draft_email",
		)

		content = frappe.db.get_value("Communication", result["communication"], "content")
		self.assertNotIn("<a ", content)

	def test_line_breaks_survive(self) -> None:
		result = send(
			"Lead", self.lead.name, "Pricing", "Line one\nLine two",
			send_now=False, tool="draft_email",
		)

		content = frappe.db.get_value("Communication", result["communication"], "content")
		self.assertIn("<br>", content)

	def test_an_empty_email_is_refused(self) -> None:
		with self.assertRaises(GuardError):
			send("Lead", self.lead.name, "Pricing", "   ", send_now=False, tool="draft_email")

	def test_an_email_with_no_subject_is_refused(self) -> None:
		with self.assertRaises(GuardError):
			send("Lead", self.lead.name, "  ", "Figures attached.", send_now=False, tool="draft_email")

	# -- registration ------------------------------------------------------------------

	def test_sending_is_high_risk_and_drafting_is_not(self) -> None:
		"""The two must not share an approval card. Consent to one is not consent to both."""
		self.assertEqual(tools.get("send_email").meta["risk"], "high")
		self.assertEqual(tools.get("draft_email").meta["risk"], "low")

	def test_the_approval_card_shows_the_whole_message(self) -> None:
		"""Approving "send an email about SAL-QTN-..." is not consent to its wording."""
		from sales_ai.tools.email import _preview

		card = _preview(
			{
				"doctype": "Lead",
				"name": self.lead.name,
				"subject": "Pricing",
				"body": "Here are the figures.",
			}
		)

		self.assertEqual(card["body"], "Here are the figures.")
		self.assertIn(LEAD_EMAIL, card["to"])
