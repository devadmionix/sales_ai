# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Who gets to reach the assistant at all.

`@frappe.whitelist()` asks one thing: that somebody is logged in. A customer with a portal
account is logged in. So every endpoint in `sales_ai.api` was reachable by any account on
the site, and the permission layer underneath — which would have refused to show them
another customer's records — is not the thing being tested here. What is being tested is
that a portal login never gets as far as a salesperson's assistant, its system prompt, its
tool list or the site's model budget.

The last test is the one that will still be earning its keep in a year: it walks the module
and fails if a newly added endpoint forgot the gate.
"""

from __future__ import annotations

import inspect

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai import api

PORTAL = "entry-portal@example.com"
STAFF = "entry-staff@example.com"


class TestEntry(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

		for email, user_type, roles in (
			(PORTAL, "Website User", []),
			(STAFF, "System User", [{"role": "Sales User"}]),
		):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"user_type": user_type,
						"roles": roles,
					}
				).insert(ignore_permissions=True)
			else:
				frappe.db.set_value("User", email, "user_type", user_type)

	def test_a_portal_customer_is_refused(self) -> None:
		frappe.set_user(PORTAL)
		with self.assertRaises(frappe.PermissionError):
			api.guard_entry()

	def test_a_salesperson_is_let_through(self) -> None:
		"""Shutting the door is only worth anything if the staff still get in."""
		frappe.set_user(STAFF)
		api.guard_entry()

	def test_guest_is_refused(self) -> None:
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			api.guard_entry()

	def test_the_refusal_does_not_explain_itself(self) -> None:
		"""It says the account cannot, not that the assistant exists and is worth probing."""
		frappe.set_user(PORTAL)
		with self.assertRaises(frappe.PermissionError) as caught:
			api.guard_entry()

		message = str(caught.exception).lower()
		for giveaway in ("role", "website user", "system user", "permission"):
			self.assertNotIn(giveaway, message, f"{message!r} hints via {giveaway!r}")

	def test_the_portal_is_shut_until_it_is_switched_on(self) -> None:
		"""Its own switch. Turning the assistant on for staff must not open it to the web."""
		frappe.db.set_single_value("Sales AI Settings", "portal_enabled", 0)
		frappe.set_user(PORTAL)

		with self.assertRaises(frappe.PermissionError):
			api.portal_chat("hello")

	def test_the_portal_refuses_rather_than_falling_back_to_the_staff_agent(self) -> None:
		"""A missing setting must never widen what a customer reaches."""
		frappe.db.set_single_value("Sales AI Settings", "portal_enabled", 1)
		frappe.db.set_single_value("Sales AI Settings", "portal_agent_profile", None)
		frappe.set_user(PORTAL)

		with self.assertRaises(frappe.PermissionError):
			api.portal_chat("hello")

	def test_the_portal_does_not_let_the_browser_pick_the_agent(self) -> None:
		"""The signature is the control: there is no argument to pass."""
		self.assertEqual(
			sorted(inspect.signature(api.portal_chat).parameters), ["prompt", "session"]
		)

	def test_every_endpoint_calls_the_gate(self) -> None:
		"""The one that catches the tenth endpoint somebody adds next year.

		Reads the source rather than calling anything: the endpoints stream, queue jobs and
		spend tokens, and none of that needs to happen to know whether the gate is there.
		"""
		missing = [
			name
			for name, fn in _endpoints()
			if name not in OWN_GATE and "guard_entry()" not in inspect.getsource(fn)
		]
		self.assertEqual(missing, [], f"whitelisted but ungated: {missing}")

	def test_the_exception_list_is_not_stale(self) -> None:
		"""An endpoint excused from the shared gate must still exist to be excused."""
		self.assertTrue(OWN_GATE <= {name for name, _ in _endpoints()})


def _endpoints():
	"""Every whitelisted function in `sales_ai.api`, with its name."""
	for name, fn in vars(api).items():
		if name.startswith("_") or not callable(fn):
			continue
		if not getattr(fn, "__name__", "").startswith(name):
			continue
		# Frappe marks a whitelisted function by putting it in this set.
		if fn in frappe.whitelisted:
			yield name, fn


# Endpoints that deliberately do not use `guard_entry`, because they gate themselves more
# strictly. Listing them by hand is the point: a new endpoint cannot land here by accident.
OWN_GATE = {"portal_chat"}
