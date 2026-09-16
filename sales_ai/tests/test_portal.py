# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Letting a stranger create a record, without letting them create any other.

`guard/portal.py` is the one module in the app that calls `insert(ignore_permissions=True)`
on behalf of somebody who has no permission at all. That is the whole reason it exists and
the whole reason it is dangerous, so these tests are less about the happy path than about
the edges of the hole it opens:

- it creates one record, for the caller, and links it to them and nobody else
- it never attaches the caller to a Customer that already exists, which would hand them
  another company's orders and invoices
- it does not let the caller choose anything that decides what they are charged
- it grants no role, because a chatbot widening a stranger's access on request is the
  thing this whole layer is built to prevent
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.guard import GuardError
from sales_ai.guard.portal import NEEDS_A_HUMAN, my_customer, register_customer

SHOPPER = "portal-shopper@example.com"
OTHER = "portal-other@example.com"
TAKEN = "Portal Test Existing Company"


class TestPortal(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

		for email in (SHOPPER, OTHER):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"user_type": "Website User",
					}
				).insert(ignore_permissions=True)

		if not frappe.db.exists("Customer", {"customer_name": TAKEN}):
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": TAKEN, "customer_type": "Company"}
			).insert(ignore_permissions=True)

		# The shopper starts with nothing, whatever an earlier test left behind. Cleaned up
		# by the link rather than by name: the escaping test creates a customer called
		# `&lt;b&gt;...`, which no sensible name pattern matches, and one survivor is enough
		# to make every later test think this login already has an account.
		mine = frappe.get_all(
			"Portal User",
			filters={"user": ("in", (SHOPPER, OTHER)), "parenttype": "Customer"},
			pluck="parent",
			parent_doctype="Customer",
		)
		for name in set(mine):
			frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)

		frappe.set_user(SHOPPER)

	def _created(self, title: str = "Portal Test Mine") -> dict:
		return register_customer(title, tool="register_customer")

	def test_a_customer_can_set_up_their_own_account(self) -> None:
		result = self._created()

		self.assertEqual(result["created"], "Customer")
		self.assertTrue(frappe.db.exists("Customer", result["name"]))

	def test_the_account_is_linked_to_the_person_who_asked(self) -> None:
		name = self._created()["name"]
		frappe.set_user("Administrator")

		users = frappe.get_all(
			"Portal User", filters={"parent": name}, pluck="user", parent_doctype="Customer"
		)
		self.assertEqual(users, [SHOPPER])

	def test_a_second_attempt_returns_the_first_account(self) -> None:
		"""One per login. Otherwise the tool is a way to fill the customer list with rubbish."""
		first = self._created()["name"]
		again = self._created("Portal Test Mine Again")

		self.assertEqual(again["unchanged"], "Customer")
		self.assertEqual(again["name"], first)
		self.assertFalse(frappe.db.exists("Customer", {"customer_name": "Portal Test Mine Again"}))

	def test_an_existing_company_is_never_joined(self) -> None:
		"""The dangerous case: linking to a real customer hands over their whole history."""
		with self.assertRaises(GuardError) as caught:
			register_customer(TAKEN, tool="register_customer")

		self.assertEqual(str(caught.exception), NEEDS_A_HUMAN)

		frappe.set_user("Administrator")
		users = frappe.get_all(
			"Portal User",
			filters={"parent": frappe.db.get_value("Customer", {"customer_name": TAKEN}, "name")},
			pluck="user",
			parent_doctype="Customer",
		)
		self.assertNotIn(SHOPPER, users)

	def test_the_refusal_does_not_confirm_the_company_is_a_customer(self) -> None:
		"""Otherwise a signup form answers "do you supply my competitor?"."""
		with self.assertRaises(GuardError) as caught:
			register_customer(TAKEN, tool="register_customer")

		message = str(caught.exception).lower()
		for giveaway in ("exist", "taken", "already", "duplicate", TAKEN.lower()):
			self.assertNotIn(giveaway, message, f"{message!r} hints via {giveaway!r}")

	def test_no_role_is_granted(self) -> None:
		"""A chatbot handing out roles on request is the thing this layer exists to stop."""
		self._created()
		frappe.set_user("Administrator")

		roles = frappe.get_all("Has Role", filters={"parent": SHOPPER}, pluck="role")
		self.assertNotIn("Customer", roles)

	def test_the_caller_cannot_choose_what_they_pay(self) -> None:
		"""Customer group and price list decide the price. They come from ERPNext's defaults."""
		with self.assertRaises(TypeError):
			register_customer(
				"Portal Test Mine", default_price_list="Secret Cheap", tool="register_customer"
			)

	def test_a_signed_out_visitor_gets_nothing(self) -> None:
		frappe.set_user("Guest")
		with self.assertRaises(GuardError):
			register_customer("Portal Test Guest Co", tool="register_customer")

		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Customer", {"customer_name": "Portal Test Guest Co"}))

	def test_an_empty_name_is_asked_about_rather_than_invented(self) -> None:
		with self.assertRaises(GuardError):
			register_customer("   ", tool="register_customer")

	def test_markup_in_the_name_does_not_survive(self) -> None:
		"""The name is echoed all over the desk, and a stranger chose it."""
		name = register_customer("<b>Portal Test Mine</b>", tool="register_customer")["name"]
		frappe.set_user("Administrator")

		self.assertNotIn("<b>", frappe.db.get_value("Customer", name, "customer_name"))

	def test_another_login_does_not_see_this_account(self) -> None:
		self._created()
		frappe.set_user(OTHER)

		self.assertIsNone(my_customer())

	def test_setting_up_an_account_is_written_down(self) -> None:
		name = self._created()["name"]
		frappe.set_user("Administrator")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"action": "Create", "reference_name": name, "tool": "register_customer"},
			)
		)
