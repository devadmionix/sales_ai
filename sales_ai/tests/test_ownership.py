# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Regression tests for server-side record ownership.

These tests focus on the ownership layer itself so they remain independent of the data
already present in a customer's ERPNext site.  Full-site tests should additionally create
User 1/User 2/Manager fixtures and exercise Desk/API/export/report paths.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sales_ai.guard import ownership


class TestSalesAIOwnership(FrappeTestCase):
	def test_owner_scoped_query_condition_contains_current_user(self):
		with patch.object(ownership, "get_user_scope", return_value="own"):
			condition = ownership.get_permission_query_conditions("Lead", "user1@example.com")

		self.assertIn("owner", condition)
		self.assertIn("user1@example.com", condition)

	def test_manager_scope_does_not_add_owner_filter(self):
		with patch.object(ownership, "get_user_scope", return_value="all"):
			condition = ownership.get_permission_query_conditions("Lead", "manager@example.com")

		self.assertIsNone(condition)

	def test_owner_can_read_their_record(self):
		doc = frappe._dict({"doctype": "Lead", "name": "LEAD-OWN"})
		with (\
			patch.object(ownership, "get_user_scope", return_value="own"),\
			patch.object(frappe.db, "get_value", return_value="user1@example.com"),\
		):
			self.assertTrue(\
				ownership.has_permission(doc, "user1@example.com", "read")\
			)

	def test_owner_cannot_read_another_users_record(self):
		doc = frappe._dict({"doctype": "Lead", "name": "LEAD-OTHER"})
		with (\
			patch.object(ownership, "get_user_scope", return_value="own"),\
			patch.object(frappe.db, "get_value", return_value="user2@example.com"),\
		):
			self.assertFalse(\
				ownership.has_permission(doc, "user1@example.com", "read")\
			)

	def test_create_is_left_to_native_frappe_owner_assignment(self):
		doc = frappe._dict({"doctype": "Lead", "name": "LEAD-NEW"})
		with patch.object(ownership, "get_user_scope", return_value="own"):
			self.assertIsNone(\
				ownership.has_permission(doc, "user1@example.com", "create")\
			)
