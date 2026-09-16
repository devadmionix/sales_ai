# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Converting a lead, and refusing to convert it twice.

The thing worth testing here is not that ERPNext's mappers work — they do, and they are
tested in ERPNext. It is the refusal layered on top: a second customer account with the
same name is indistinguishable from the first afterwards, and nothing in ERPNext objects
to it. So the assertions below are mostly about what does *not* get created.

The opportunity side is the opposite decision on purpose. One lead can produce two real
deals, so a second is allowed and the open ones are reported rather than refused.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai import tools
from sales_ai.guard import GuardError
from sales_ai.guard.leads import convert_to_customer, convert_to_opportunity


class TestLeads(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

	def _lead(self, company_name: str):
		return frappe.get_doc(
			{
				"doctype": "Lead",
				"lead_name": "Convert Test Person",
				"company_name": company_name,
				"status": "Lead",
			}
		).insert()

	def _customers(self, name: str) -> int:
		return frappe.db.count("Customer", {"customer_name": name})

	# -- the ordinary cases ------------------------------------------------------------

	def test_a_lead_becomes_a_customer(self) -> None:
		lead = self._lead("Convert Test Fresh Co")

		result = convert_to_customer(lead.name, tool="convert_lead_to_customer")

		self.assertEqual(result["created"], "Customer")
		self.assertEqual(result["from_lead"], lead.name)
		self.assertEqual(self._customers("Convert Test Fresh Co"), 1)

	def test_the_new_customer_is_readable_straight_back(self) -> None:
		"""The record comes back in the reply, so the agent need not go and fetch it."""
		lead = self._lead("Convert Test Readback Co")

		result = convert_to_customer(lead.name, tool="convert_lead_to_customer")

		self.assertEqual(result["record"]["customer_name"], "Convert Test Readback Co")

	def test_a_lead_becomes_an_opportunity(self) -> None:
		lead = self._lead("Convert Test Opp Co")

		result = convert_to_opportunity(lead.name, tool="convert_lead_to_opportunity")

		self.assertEqual(result["created"], "Opportunity")
		self.assertEqual(
			frappe.db.get_value("Opportunity", result["name"], "party_name"), lead.name
		)

	# -- the refusals ------------------------------------------------------------------

	def test_a_second_customer_of_the_same_name_is_not_created(self) -> None:
		"""The whole reason this module exists. ERPNext would have made a second one."""
		first = self._lead("Convert Test Twice Co")
		convert_to_customer(first.name, tool="convert_lead_to_customer")

		second = self._lead("Convert Test Twice Co")
		result = convert_to_customer(second.name, tool="convert_lead_to_customer")

		self.assertEqual(result["unchanged"], "Lead")
		self.assertEqual(self._customers("Convert Test Twice Co"), 1)

	def test_the_refusal_names_the_customer_to_use_instead(self) -> None:
		first = self._lead("Convert Test Named Co")
		created = convert_to_customer(first.name, tool="convert_lead_to_customer")

		second = self._lead("Convert Test Named Co")
		result = convert_to_customer(second.name, tool="convert_lead_to_customer")

		self.assertEqual(result["customer"], created["name"])

	def test_converting_an_already_converted_lead_changes_nothing(self) -> None:
		lead = self._lead("Convert Test Again Co")
		first = convert_to_customer(lead.name, tool="convert_lead_to_customer")

		again = convert_to_customer(lead.name, tool="convert_lead_to_customer")

		self.assertEqual(again["unchanged"], "Lead")
		self.assertEqual(again["customer"], first["name"])
		self.assertEqual(self._customers("Convert Test Again Co"), 1)

	def test_a_lead_that_does_not_exist_is_not_distinguished(self) -> None:
		"""Same phrase as every other unreadable record, so it cannot be used to probe."""
		with self.assertRaises(GuardError) as caught:
			convert_to_customer("CRM-LEAD-9999-99999", tool="convert_lead_to_customer")

		self.assertIn("available to you", str(caught.exception))

	# -- the one that is deliberately not a refusal ------------------------------------

	def test_a_second_opportunity_is_allowed_and_mentioned(self) -> None:
		"""Two deals off one lead is normal. Silently refusing it would be wrong."""
		lead = self._lead("Convert Test Two Deals Co")
		first = convert_to_opportunity(lead.name, tool="convert_lead_to_opportunity")

		second = convert_to_opportunity(lead.name, tool="convert_lead_to_opportunity")

		self.assertEqual(second["created"], "Opportunity")
		self.assertNotEqual(second["name"], first["name"])
		self.assertIn(first["name"], second["note"])

	# -- what is written down, and what the approver sees ------------------------------

	def test_the_conversion_is_written_down(self) -> None:
		lead = self._lead("Convert Test Logged Co")

		result = convert_to_customer(lead.name, tool="convert_lead_to_customer")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{
					"action": "Create",
					"reference_doctype": "Customer",
					"reference_name": result["name"],
					"tool": "convert_lead_to_customer",
				},
			)
		)

	def test_both_tools_are_registered_with_a_preview(self) -> None:
		for name in ("convert_lead_to_customer", "convert_lead_to_opportunity"):
			registered = tools.get(name)

			self.assertIsNotNone(registered, name)
			self.assertTrue(registered.meta["writes"], name)
			self.assertIsNotNone(registered.meta.get("preview"), name)

	def test_the_preview_shows_the_lead_being_converted(self) -> None:
		"""Approving "convert CRM-LEAD-2026-00004" is not consent; approving a name is."""
		from sales_ai.tools.leads import _preview

		lead = self._lead("Convert Test Preview Co")

		card = _preview({"name": lead.name})

		self.assertEqual(card["company_name"], "Convert Test Preview Co")
