# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The two verbs that cannot be taken back.

Submitting posts to the ledger and reserves stock; cancelling reverses that and leaves a
document ERPNext will never revive. So these tests are mostly about the ways the agent is
stopped short of either:

- only the four sales doctypes, whatever else ERPNext would happily submit
- only a draft can be submitted, only a submitted document can be cancelled
- a cancellation without a stated reason does not happen
- and there is no delete tool at all, which is asserted against the live registry rather
  than trusted, because the whole "cancel, never delete" decision rests on it
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from sales_ai import tools
from sales_ai.guard import GuardError, read_document
from sales_ai.guard.documents import SUBMITTABLE, cancel_document, preview, submit_document
from sales_ai.guard.specs import SPECS

CUSTOMER = "ABC Medical Store"
ITEM = "AI-GADGET"


class TestDocuments(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

	def _draft(self):
		quotation = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": CUSTOMER,
				"items": [{"item_code": ITEM, "qty": 2}],
			}
		).insert()
		return quotation

	def _draft_order(self):
		"""A real Sales Order. Heavier than a quotation, and the point of the exercise:
		this is the one that reserves stock and starts the money moving."""
		return frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": CUSTOMER,
				"delivery_date": add_days(nowdate(), 7),
				"items": [{"item_code": ITEM, "qty": 2, "delivery_date": add_days(nowdate(), 7)}],
			}
		).insert()

	# -- the happy path ----------------------------------------------------------------

	def test_a_draft_can_be_submitted(self) -> None:
		quotation = self._draft()

		result = submit_document("Quotation", quotation.name, tool="submit_document")

		self.assertEqual(result["submitted"], "Quotation")
		self.assertEqual(frappe.db.get_value("Quotation", quotation.name, "docstatus"), 1)

	def test_a_submitted_document_can_be_cancelled(self) -> None:
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")

		result = cancel_document(
			"Quotation", quotation.name, "Customer went elsewhere.", tool="cancel_document"
		)

		self.assertEqual(result["cancelled"], "Quotation")
		self.assertEqual(frappe.db.get_value("Quotation", quotation.name, "docstatus"), 2)

	def test_cancelling_does_not_delete(self) -> None:
		"""The whole reason there is no delete tool: the record has to survive being wrong."""
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")
		cancel_document("Quotation", quotation.name, "Priced wrong.", tool="cancel_document")

		self.assertTrue(frappe.db.exists("Quotation", quotation.name))

	# -- the refusals ------------------------------------------------------------------

	def test_only_sales_documents_can_be_submitted(self) -> None:
		"""A Purchase Order and a Journal Entry are submittable too. Not from here."""
		with self.assertRaises(GuardError) as caught:
			submit_document("Journal Entry", "any", tool="submit_document")

		self.assertIn("Journal Entry", str(caught.exception))

	def test_only_sales_documents_can_be_cancelled(self) -> None:
		with self.assertRaises(GuardError):
			cancel_document("Purchase Order", "any", "because", tool="cancel_document")

	def test_a_submitted_document_is_not_submitted_twice(self) -> None:
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")

		with self.assertRaises(GuardError) as caught:
			submit_document("Quotation", quotation.name, tool="submit_document")

		self.assertIn("submitted", str(caught.exception))

	def test_a_draft_cannot_be_cancelled(self) -> None:
		"""It commits nothing, so there is nothing to reverse — and saying so stops the
		model from reaching for cancel when the user meant 'throw this away'."""
		quotation = self._draft()

		with self.assertRaises(GuardError) as caught:
			cancel_document("Quotation", quotation.name, "Not needed.", tool="cancel_document")

		self.assertIn("draft", str(caught.exception))

	def test_a_cancelled_document_is_not_cancelled_again(self) -> None:
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")
		cancel_document("Quotation", quotation.name, "Done once.", tool="cancel_document")

		with self.assertRaises(GuardError):
			cancel_document("Quotation", quotation.name, "Twice.", tool="cancel_document")

	def test_a_cancellation_without_a_reason_is_refused(self) -> None:
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")

		with self.assertRaises(GuardError):
			cancel_document("Quotation", quotation.name, "   ", tool="cancel_document")

		self.assertEqual(frappe.db.get_value("Quotation", quotation.name, "docstatus"), 1)

	def test_a_document_that_does_not_exist_is_not_distinguished(self) -> None:
		"""Same phrase as a document the user may not see. Guessing IDs must teach nothing."""
		with self.assertRaises(GuardError) as caught:
			submit_document("Sales Order", "SAL-ORD-9999-99999", tool="submit_document")

		self.assertIn("available to you", str(caught.exception))

	# -- what the approver is shown ----------------------------------------------------

	def test_the_preview_carries_the_customer_and_the_amount(self) -> None:
		"""An approval card saying only an ID is a rubber stamp."""
		quotation = self._draft()

		card = preview("Quotation", quotation.name)

		self.assertEqual(card["customer"], CUSTOMER)
		self.assertEqual(card["state"], "still a draft")
		self.assertTrue(card["totals"])

	def test_the_preview_shape_carries_the_amount_policy_reads(self) -> None:
		"""`policy._value_of` looks for these two keys. If the shape drifts, every amount
		threshold silently stops applying and high-value writes stop asking."""
		quotation = self._draft()

		card = preview("Quotation", quotation.name)

		self.assertIn("currency", card)
		self.assertTrue({"grand_total", "rounded_total", "total"} & set(card["totals"]))

	# -- the shape of the whole thing --------------------------------------------------

	def test_both_tools_are_registered_and_high_risk(self) -> None:
		for name in ("submit_document", "cancel_document"):
			tool = tools.get(name)
			self.assertIsNotNone(tool, f"{name} is not registered")
			self.assertTrue(tool.meta["writes"])
			self.assertEqual(tool.meta["risk"], "high")
			self.assertIsNotNone(
				tool.meta.get("preview"), f"{name} would ask for a blind approval"
			)

	def test_there_is_no_way_to_delete_anything(self) -> None:
		"""The user asked for delete and got cancel. This is the assertion that keeps it
		that way when somebody adds a tool in a hurry next year."""
		suspect = [name for name in tools.names() if "delete" in name or "remove" in name]
		self.assertEqual(suspect, [])

	def test_the_allowlist_is_the_sales_side_only(self) -> None:
		self.assertEqual(
			set(SUBMITTABLE),
			{"Quotation", "Sales Order", "Sales Invoice", "Delivery Note"},
		)

	def test_nothing_can_be_cancelled_that_cannot_be_read(self) -> None:
		"""The dangerous power must never be granted without the safe one. Otherwise the
		approval card has nothing to show and the model confirms a document sight unseen."""
		self.assertEqual(set(SUBMITTABLE) - set(SPECS), set())

	# -- the ones that actually cost something -----------------------------------------

	def test_a_sales_order_can_be_submitted_and_cancelled(self) -> None:
		"""Not a quotation. This one reserves stock and shows up in the ledger."""
		order = self._draft_order()

		submit_document("Sales Order", order.name, tool="submit_document")
		self.assertEqual(frappe.db.get_value("Sales Order", order.name, "docstatus"), 1)

		cancel_document("Sales Order", order.name, "Customer pulled out.", tool="cancel_document")
		self.assertEqual(frappe.db.get_value("Sales Order", order.name, "docstatus"), 2)
		self.assertTrue(frappe.db.exists("Sales Order", order.name))

	def test_the_preview_works_for_a_sales_order(self) -> None:
		order = self._draft_order()

		card = preview("Sales Order", order.name)

		self.assertEqual(card["customer"], CUSTOMER)
		self.assertTrue(card["lines"])
		self.assertTrue(card["totals"])

	def test_an_invoice_and_a_delivery_note_can_be_read_back(self) -> None:
		"""Both are newly readable, and both go through `summarise` in the preview. A
		fieldname wrong in either spec would only ever show up here."""
		for doctype in ("Sales Invoice", "Delivery Note"):
			with self.subTest(doctype=doctype):
				spec = SPECS[doctype]
				self.assertIn("customer", spec.filter_fields)
				self.assertIn("items", spec.children)

	def test_the_new_specs_name_real_fields(self) -> None:
		"""`_clean` drops anything that comes back empty, so a typo in `detail_fields`
		would be invisible — the record would just quietly be missing a field."""
		for doctype in ("Sales Invoice", "Delivery Note"):
			meta = frappe.get_meta(doctype)
			spec = SPECS[doctype]
			real = {f.fieldname for f in meta.fields} | {"name", "docstatus", "creation", "modified", "owner"}
			with self.subTest(doctype=doctype):
				self.assertEqual(set(spec.detail_fields) - real, set())
				self.assertEqual(set(spec.filter_fields) - real, set())

	def test_a_submitted_invoice_reads_back_through_the_normal_path(self) -> None:
		"""End to end: the spec is not just well-formed, `get_record` actually works."""
		order = self._draft_order()
		submit_document("Sales Order", order.name, tool="submit_document")

		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		invoice = make_sales_invoice(order.name)
		invoice.insert()

		record = read_document("Sales Invoice", invoice.name)

		self.assertEqual(record["customer"], CUSTOMER)
		self.assertTrue(record["items"])

	def test_submitting_is_written_down(self) -> None:
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"action": "Submit", "reference_name": quotation.name, "tool": "submit_document"},
			)
		)

	def test_the_cancellation_reason_reaches_the_audit_log(self) -> None:
		"""Months later somebody asks why. "The assistant did it" is not an answer."""
		quotation = self._draft()
		submit_document("Quotation", quotation.name, tool="submit_document")
		cancel_document("Quotation", quotation.name, "Duplicate of QTN-1.", tool="cancel_document")

		changes = frappe.db.get_value(
			"Sales AI Action Log",
			{"action": "Cancel", "reference_name": quotation.name},
			"changes",
		)
		self.assertIn("Duplicate of QTN-1.", changes)
