# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The amount gate on submitting: small figures go through, big ones need a manager.

These tests pin the rule itself — below the limit a sales user submits, at or
above it they are refused with a reason that names the remedy, a manager's
submission is the approval, and doctypes outside the gate are untouched. They
use mock documents so they run anywhere, without price lists or fixtures.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sales_ai.guard import submit_approval
from sales_ai.guard.submit_approval import (
	APPROVER_ROLES,
	GATED_DOCTYPES,
	SUBMIT_APPROVAL_LIMIT,
	enforce_submit_approval,
)


def _doc(doctype="Quotation", total=100.0, currency="INR"):
	return frappe._dict(
		{"doctype": doctype, "base_grand_total": total, "grand_total": total, "currency": currency}
	)


@contextmanager
def _as_user(*roles):
	"""Run as a non-Administrator session user with the given roles.

	Needed because the test session itself is Administrator, who bypasses
	the gate — without this every refusal case would vacuously pass.
	"""
	with (
		patch.dict(frappe.session, {"user": "submit-gate-probe@example.com"}),
		patch.object(frappe, "get_roles", return_value=list(roles)),
	):
		yield


class TestSubmitApproval(FrappeTestCase):
	def test_limit_is_five_thousand(self):
		self.assertEqual(SUBMIT_APPROVAL_LIMIT, 5000.0)

	def test_gate_covers_quotation_order_and_delivery_note(self):
		self.assertEqual(set(GATED_DOCTYPES), {"Quotation", "Sales Order", "Delivery Note"})

	def test_below_limit_a_sales_user_submits(self):
		with _as_user("Sales User"):
			enforce_submit_approval(_doc(total=4999.99))  # raises nothing

	def test_at_the_limit_needs_a_manager(self):
		with _as_user("Sales User"):
			with self.assertRaises(frappe.ValidationError) as caught:
				enforce_submit_approval(_doc(total=5000.0))
		self.assertIn("Sales Manager", str(caught.exception))

	def test_above_the_limit_needs_a_manager(self):
		with _as_user("Sales User"):
			with self.assertRaises(frappe.ValidationError) as caught:
				enforce_submit_approval(_doc(total=7500.0))
		self.assertIn("5000", str(caught.exception))

	def test_a_manager_is_the_approval(self):
		for role in ("Sales Manager", "Sales Master Manager", "System Manager", "Administrator"):
			with self.subTest(role=role):
				with _as_user(role):
					enforce_submit_approval(_doc(total=250000.0))  # raises nothing

	def test_grand_total_is_used_when_base_is_missing(self):
		with _as_user("Sales User"):
			with self.assertRaises(frappe.ValidationError):
				enforce_submit_approval(
					frappe._dict({"doctype": "Sales Order", "grand_total": 9000.0, "currency": "USD"})
				)
			enforce_submit_approval(
				frappe._dict({"doctype": "Sales Order", "grand_total": 10.0, "currency": "USD"})
			)

	def test_doctypes_outside_the_gate_are_untouched(self):
		with _as_user("Sales User"):
			enforce_submit_approval(_doc(doctype="Sales Invoice", total=999999.0))
			enforce_submit_approval(_doc(doctype="Lead", total=999999.0))

	def test_approver_roles_are_exactly_the_managers(self):
		self.assertEqual(
			set(APPROVER_ROLES),
			{"Sales Manager", "Sales Master Manager", "System Manager", "Administrator"},
		)
