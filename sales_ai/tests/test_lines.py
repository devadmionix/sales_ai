# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Amending a draft without losing anything.

The design decision these tests defend is that edits are *targeted*. The tempting
alternative — hand the tool the complete new list of lines — fails silently and
expensively: the user says "add five more laptops", the model sends a list containing
only laptops, and the monitors are gone with nothing to show it happened.

So the first thing asserted here is that an untouched line survives every operation, and
the last is that the model cannot empty a document or price it itself.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, nowdate

from sales_ai import tools
from sales_ai.guard import GuardError
from sales_ai.guard.documents import submit_document
from sales_ai.guard.lines import REVISABLE, preview, revise_lines

CUSTOMER = "ABC Medical Store"
ITEM = "AI-GADGET"
OTHER = "AI-BOLT"
THIRD = "AI-PANEL"


class TestLines(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

	def _quotation(self, *item_codes: str):
		return frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": CUSTOMER,
				"items": [{"item_code": code, "qty": 2} for code in (item_codes or (ITEM,))],
			}
		).insert()

	def _order(self):
		when = add_days(nowdate(), 7)
		return frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": CUSTOMER,
				"delivery_date": when,
				"items": [{"item_code": ITEM, "qty": 2, "delivery_date": when}],
			}
		).insert()

	def _lines(self, name: str, doctype: str = "Quotation") -> dict[str, float]:
		doc = frappe.get_doc(doctype, name)
		return {row.item_code: flt(row.qty) for row in doc.items}

	# -- the thing this design exists to prevent ---------------------------------------

	def test_an_untouched_line_survives_an_addition(self) -> None:
		"""The whole reason edits are targeted rather than a replacement."""
		quotation = self._quotation(ITEM, OTHER)

		revise_lines("Quotation", quotation.name, add=[{"item_code": THIRD, "qty": 1}], tool="t")

		self.assertEqual(self._lines(quotation.name), {ITEM: 2, OTHER: 2, THIRD: 1})

	def test_an_untouched_line_survives_a_change(self) -> None:
		quotation = self._quotation(ITEM, OTHER)

		revise_lines("Quotation", quotation.name, change=[{"item_code": ITEM, "qty": 20}], tool="t")

		self.assertEqual(self._lines(quotation.name), {ITEM: 20, OTHER: 2})

	def test_an_untouched_line_survives_a_removal(self) -> None:
		quotation = self._quotation(ITEM, OTHER, THIRD)

		revise_lines("Quotation", quotation.name, remove=[OTHER], tool="t")

		self.assertEqual(self._lines(quotation.name), {ITEM: 2, THIRD: 2})

	# -- the ordinary cases ------------------------------------------------------------

	def test_all_three_operations_in_one_call(self) -> None:
		""""Drop the bolts, make it twenty gadgets and add a panel" is one sentence."""
		quotation = self._quotation(ITEM, OTHER)

		revise_lines(
			"Quotation",
			quotation.name,
			remove=[OTHER],
			change=[{"item_code": ITEM, "qty": 20}],
			add=[{"item_code": THIRD, "qty": 3}],
			tool="t",
		)

		self.assertEqual(self._lines(quotation.name), {ITEM: 20, THIRD: 3})

	def test_a_sales_order_line_gets_a_delivery_date(self) -> None:
		"""Mandatory per row, and ERPNext will not default it on a row added this way."""
		order = self._order()

		revise_lines("Sales Order", order.name, add=[{"item_code": OTHER, "qty": 4}], tool="t")

		rows = frappe.get_doc("Sales Order", order.name).items
		self.assertTrue(all(row.delivery_date for row in rows))

	def test_erpnext_reprices_rather_than_the_model(self) -> None:
		"""Quantities in, money out. The totals must come back from the controller."""
		quotation = self._quotation()

		result = revise_lines(
			"Quotation", quotation.name, change=[{"item_code": ITEM, "qty": 10}], tool="t"
		)

		saved = frappe.get_doc("Quotation", quotation.name)
		self.assertEqual(result["totals"]["grand_total"], flt(saved.grand_total))

	def test_a_discount_is_applied_to_something(self) -> None:
		"""ERPNext silently applies nothing when apply_discount_on is unset, and the
		agent would then report a discount that never happened."""
		quotation = self._quotation()

		revise_lines("Quotation", quotation.name, discount_percentage=10, tool="t")

		saved = frappe.get_doc("Quotation", quotation.name)
		self.assertEqual(flt(saved.additional_discount_percentage), 10)
		self.assertTrue(saved.apply_discount_on)
		self.assertLess(flt(saved.grand_total), flt(saved.total))

	# -- the refusals ------------------------------------------------------------------

	def test_a_submitted_document_cannot_be_revised(self) -> None:
		quotation = self._quotation()
		submit_document("Quotation", quotation.name, tool="submit_document")

		with self.assertRaises(GuardError) as caught:
			revise_lines(
				"Quotation", quotation.name, change=[{"item_code": ITEM, "qty": 99}], tool="t"
			)

		self.assertIn("submitted", str(caught.exception))
		self.assertEqual(self._lines(quotation.name), {ITEM: 2})

	def test_a_document_cannot_be_emptied(self) -> None:
		quotation = self._quotation()

		with self.assertRaises(GuardError):
			revise_lines("Quotation", quotation.name, remove=[ITEM], tool="t")

		self.assertEqual(self._lines(quotation.name), {ITEM: 2})

	def test_changing_an_item_that_is_not_there_is_refused_by_name(self) -> None:
		"""Silently adding it instead is how a typo becomes an extra line on a quote."""
		quotation = self._quotation()

		with self.assertRaises(GuardError) as caught:
			revise_lines("Quotation", quotation.name, change=[{"item_code": OTHER, "qty": 5}], tool="t")

		self.assertIn(OTHER, str(caught.exception))

	def test_adding_an_item_twice_is_refused(self) -> None:
		quotation = self._quotation()

		with self.assertRaises(GuardError):
			revise_lines("Quotation", quotation.name, add=[{"item_code": ITEM, "qty": 5}], tool="t")

	def test_removing_an_item_that_is_not_there_is_refused(self) -> None:
		quotation = self._quotation()

		with self.assertRaises(GuardError):
			revise_lines("Quotation", quotation.name, remove=[OTHER], tool="t")

	def test_a_quantity_of_zero_is_refused(self) -> None:
		"""It reads as "take it off", and guessing that is how a line disappears."""
		quotation = self._quotation()

		with self.assertRaises(GuardError):
			revise_lines("Quotation", quotation.name, change=[{"item_code": ITEM, "qty": 0}], tool="t")

	def test_a_call_that_changes_nothing_is_refused(self) -> None:
		quotation = self._quotation()

		with self.assertRaises(GuardError):
			revise_lines("Quotation", quotation.name, tool="t")

	def test_a_discount_over_a_hundred_percent_is_refused(self) -> None:
		quotation = self._quotation()

		with self.assertRaises(GuardError):
			revise_lines("Quotation", quotation.name, discount_percentage=150, tool="t")

	def test_only_drafts_of_two_doctypes_can_be_revised(self) -> None:
		"""A draft invoice was mapped from an order. Editing its lines breaks that link."""
		self.assertEqual(set(REVISABLE), {"Quotation", "Sales Order"})

		with self.assertRaises(GuardError):
			revise_lines("Sales Invoice", "anything", remove=["x"], tool="t")

	def test_a_document_that_does_not_exist_is_not_distinguished(self) -> None:
		with self.assertRaises(GuardError) as caught:
			revise_lines("Quotation", "SAL-QTN-9999-99999", remove=["x"], tool="t")

		self.assertIn("available to you", str(caught.exception))

	# -- what the approver sees, and what is written down ------------------------------

	def test_the_preview_shows_the_document_before_the_change(self) -> None:
		quotation = self._quotation()

		card = preview("Quotation", quotation.name)

		self.assertEqual(card["customer"], CUSTOMER)
		self.assertEqual(card["state"], "still a draft")
		self.assertTrue(card["totals"])

	def test_the_tool_is_registered_with_a_preview(self) -> None:
		revise = tools.get("revise_lines")

		self.assertIsNotNone(revise)
		self.assertTrue(revise.meta["writes"])
		self.assertIsNotNone(revise.meta.get("preview"))

	def test_the_revision_is_written_down(self) -> None:
		quotation = self._quotation(ITEM, OTHER)

		revise_lines("Quotation", quotation.name, remove=[OTHER], tool="revise_lines")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"action": "Update", "reference_name": quotation.name, "tool": "revise_lines"},
			)
		)
