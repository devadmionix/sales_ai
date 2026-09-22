# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Reminders, and whose they are.

A ToDo carries no company and no territory, so ERPNext's usual scoping does nothing for it.
That makes ownership the only boundary there is, and the tests that matter here are the ones
that prove it holds: one salesperson cannot close, move or read another's list, and the
refusal for somebody else's reminder reads the same as for one that does not exist.

The rest is the ordinary behaviour — moving a date, closing versus cancelling, and handing
one over under the same rule that governs handing a record over.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from sales_ai import tools
from sales_ai.guard import GuardError
from sales_ai.guard.followups import list_follow_ups, update_follow_up
from sales_ai.guard.writes import create_follow_up

MINE = "followup-mine@example.com"
THEIRS = "followup-theirs@example.com"
OUTSIDER = "followup-outsider@example.com"


class TestFollowUps(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

		for email in (MINE, THEIRS, OUTSIDER):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)

		self.lead = self._lead("Follow Up Test Lead", owner=MINE)
		decoy = self._lead("Follow Up Test Decoy")
		# The outsider is confined to one other lead, which is how a real deployment keeps
		# salespeople off each other's accounts.
		frappe.db.delete("User Permission", {"user": OUTSIDER, "allow": "Lead"})
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": OUTSIDER,
				"allow": "Lead",
				"for_value": decoy.name,
			}
		).insert(ignore_permissions=True)

		# ToDos outlive the rollback and the lead is reused by name, so without this each
		# test starts with the previous one's reminders still on it.
		frappe.db.delete("ToDo", {"reference_type": "Lead", "reference_name": self.lead.name})

		frappe.set_user(MINE)

	def _lead(self, title: str, owner: str | None = None):
		name = frappe.db.get_value("Lead", {"lead_name": title}, "name")
		if name:
			if owner:
				frappe.db.set_value("Lead", name, "owner", owner, update_modified=False)
			return frappe.get_doc("Lead", name)
		doc = frappe.get_doc(
			{"doctype": "Lead", "lead_name": title, "company_name": title, "status": "Lead"}
		).insert(ignore_permissions=True)
		if owner:
			frappe.db.set_value("Lead", doc.name, "owner", owner, update_modified=False)
		return doc

	def _reminder(self, when: str | None = None) -> str:
		return create_follow_up(
			"Lead",
			self.lead.name,
			when or add_days(nowdate(), 3),
			"Call them back",
			tool="create_follow_up",
		)["follow_up"]

	# -- the ordinary cases ------------------------------------------------------------

	def test_a_reminder_can_be_moved(self) -> None:
		todo = self._reminder()
		later = add_days(nowdate(), 10)

		result = update_follow_up(todo, date=later, tool="update_follow_up")

		self.assertEqual(result["date"], later)
		self.assertEqual(str(frappe.db.get_value("ToDo", todo, "date")), later)

	def test_a_reminder_can_be_closed(self) -> None:
		todo = self._reminder()

		update_follow_up(todo, status="Closed", tool="update_follow_up")

		self.assertEqual(frappe.db.get_value("ToDo", todo, "status"), "Closed")

	def test_closed_and_cancelled_are_kept_apart(self) -> None:
		"""Done and never-happening are different answers to "what happened to that"."""
		done, dropped = self._reminder(), self._reminder(add_days(nowdate(), 4))

		update_follow_up(done, status="Closed", tool="update_follow_up")
		update_follow_up(dropped, status="Cancelled", tool="update_follow_up")

		self.assertEqual(frappe.db.get_value("ToDo", done, "status"), "Closed")
		self.assertEqual(frappe.db.get_value("ToDo", dropped, "status"), "Cancelled")

	def test_several_things_change_in_one_call(self) -> None:
		""""Push the Sharma call to Friday and reword it" is one sentence."""
		todo = self._reminder()
		later = add_days(nowdate(), 5)

		result = update_follow_up(
			todo, date=later, description="Send the revised quote", tool="update_follow_up"
		)

		self.assertEqual(sorted(result["changed"]), ["date", "description"])

	def test_listing_finds_what_is_outstanding_on_a_record(self) -> None:
		self._reminder()

		found = list_follow_ups(doctype="Lead", name=self.lead.name)

		self.assertEqual(found["count"], 1)
		self.assertEqual(found["follow_ups"][0]["about"], f"Lead {self.lead.name}")

	def test_a_closed_reminder_is_not_in_the_open_list(self) -> None:
		todo = self._reminder()
		update_follow_up(todo, status="Closed", tool="update_follow_up")

		self.assertEqual(list_follow_ups(doctype="Lead", name=self.lead.name)["count"], 0)
		self.assertEqual(
			list_follow_ups(doctype="Lead", name=self.lead.name, status="Closed")["count"], 1
		)

	# -- handing one over --------------------------------------------------------------

	def test_a_reminder_can_be_made_for_a_colleague(self) -> None:
		result = create_follow_up(
			"Lead",
			self.lead.name,
			add_days(nowdate(), 2),
			"You know them better",
			for_user=THEIRS,
			tool="create_follow_up",
		)

		self.assertEqual(frappe.db.get_value("ToDo", result["follow_up"], "allocated_to"), THEIRS)

	def test_a_reminder_can_be_handed_over(self) -> None:
		todo = self._reminder()

		update_follow_up(todo, allocate_to=THEIRS, tool="update_follow_up")

		self.assertEqual(frappe.db.get_value("ToDo", todo, "allocated_to"), THEIRS)

	def test_handing_it_to_somebody_who_cannot_see_the_lead_is_refused(self) -> None:
		"""The same leak as assignment: the reminder names a lead they cannot see."""
		todo = self._reminder()

		with self.assertRaises(GuardError) as caught:
			update_follow_up(todo, allocate_to=OUTSIDER, tool="update_follow_up")

		self.assertIn("cannot see this Lead", str(caught.exception))
		self.assertEqual(frappe.db.get_value("ToDo", todo, "allocated_to"), MINE)

	def test_making_one_for_somebody_who_cannot_see_the_lead_is_refused(self) -> None:
		with self.assertRaises(GuardError):
			create_follow_up(
				"Lead",
				self.lead.name,
				add_days(nowdate(), 2),
				"Chase this",
				for_user=OUTSIDER,
				tool="create_follow_up",
			)

	# -- whose reminder it is ----------------------------------------------------------

	def test_somebody_elses_reminder_cannot_be_closed(self) -> None:
		"""The boundary this module exists to hold. ToDo has no company to scope it."""
		todo = create_follow_up(
			"Lead",
			self.lead.name,
			add_days(nowdate(), 2),
			"Theirs to do",
			for_user=THEIRS,
			tool="create_follow_up",
		)["follow_up"]
		frappe.db.set_value("ToDo", todo, "owner", THEIRS)

		with self.assertRaises(GuardError):
			update_follow_up(todo, status="Closed", tool="update_follow_up")

		self.assertEqual(frappe.db.get_value("ToDo", todo, "status"), "Open")

	def test_somebody_elses_reminder_reads_like_one_that_does_not_exist(self) -> None:
		"""Otherwise walking IDs counts a colleague's workload."""
		todo = create_follow_up(
			"Lead",
			self.lead.name,
			add_days(nowdate(), 2),
			"Theirs to do",
			for_user=THEIRS,
			tool="create_follow_up",
		)["follow_up"]
		frappe.db.set_value("ToDo", todo, "owner", THEIRS)

		with self.assertRaises(GuardError) as theirs:
			update_follow_up(todo, status="Closed", tool="update_follow_up")
		with self.assertRaises(GuardError) as nobodys:
			update_follow_up("ToDo-nonexistent", status="Closed", tool="update_follow_up")

		self.assertEqual(
			str(theirs.exception).replace(todo, "X"),
			str(nobodys.exception).replace("ToDo-nonexistent", "X"),
		)

	def test_the_one_who_raised_it_can_still_change_it(self) -> None:
		"""Handing it over should not lock the person who asked for it out of it."""
		todo = create_follow_up(
			"Lead",
			self.lead.name,
			add_days(nowdate(), 2),
			"Over to you",
			for_user=THEIRS,
			tool="create_follow_up",
		)["follow_up"]

		update_follow_up(todo, status="Cancelled", tool="update_follow_up")

		self.assertEqual(frappe.db.get_value("ToDo", todo, "status"), "Cancelled")

	# -- the refusals ------------------------------------------------------------------

	def test_a_call_that_changes_nothing_is_refused(self) -> None:
		todo = self._reminder()

		with self.assertRaises(GuardError):
			update_follow_up(todo, tool="update_follow_up")

	def test_a_status_that_is_not_one_of_the_three_is_refused(self) -> None:
		todo = self._reminder()

		with self.assertRaises(GuardError):
			update_follow_up(todo, status="Deleted", tool="update_follow_up")

	def test_a_date_that_is_not_a_date_is_refused_in_words(self) -> None:
		"""Left to ERPNext this is a save failure, which reaches the user as "something
		went wrong" and tells the model nothing it can correct."""
		todo = self._reminder()

		with self.assertRaises(GuardError) as caught:
			update_follow_up(todo, date="next Tuesday", tool="update_follow_up")

		self.assertIn("YYYY-MM-DD", str(caught.exception))

	def test_the_description_cannot_be_emptied(self) -> None:
		todo = self._reminder()

		with self.assertRaises(GuardError):
			update_follow_up(todo, description="   ", tool="update_follow_up")

	def test_the_description_cannot_carry_markup(self) -> None:
		"""It renders as HTML in the sidebar, and the model wrote it after reading the lead."""
		todo = self._reminder()

		update_follow_up(todo, description="<img src=x onerror=alert(1)>", tool="update_follow_up")

		self.assertNotIn("<img", frappe.db.get_value("ToDo", todo, "description"))

	# -- registration and logging ------------------------------------------------------

	def test_both_tools_are_registered(self) -> None:
		self.assertFalse(tools.get("list_follow_ups").meta["writes"])
		self.assertTrue(tools.get("update_follow_up").meta["writes"])

	def test_the_change_is_written_down(self) -> None:
		todo = self._reminder()

		update_follow_up(todo, status="Closed", tool="update_follow_up")
		frappe.set_user("Administrator")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{
					"action": "Follow Up",
					"reference_name": self.lead.name,
					"tool": "update_follow_up",
					"outcome": "Allowed",
				},
			)
		)
