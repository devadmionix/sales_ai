# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Handing a lead to a colleague, without handing them anything else.

`frappe.desk.form.assign_to` shares a document with an assignee who cannot see it — see
`_add`, "if assignee does not have permissions, share or inform". That turns assignment
into a way to grant read access, through a tool nobody would think to audit for it. So the
test that matters most here is not that assigning works; it is that assigning to somebody
who cannot already see the lead is refused and leaves no DocShare behind.

The rest is the shape the user asked for: adding, never replacing, and one refusal phrase
for every reason a colleague might not be assignable.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.guard import GuardError
from sales_ai.guard.writes import assign_lead

OWNER = "assign-owner@example.com"
COLLEAGUE = "assign-colleague@example.com"
OUTSIDER = "assign-outsider@example.com"
DISABLED = "assign-disabled@example.com"


class TestAssignLead(IntegrationTestCase):
	def setUp(self) -> None:
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

		for email, enabled in ((OWNER, 1), (COLLEAGUE, 1), (OUTSIDER, 1), (DISABLED, 0)):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"enabled": enabled,
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)

		self.lead = self._lead("Assign Test Lead")
		# The outsider is confined to one other lead, which is how a real deployment keeps
		# salespeople off each other's accounts. Everyone else sees everything.
		decoy = self._lead("Assign Test Decoy")
		frappe.db.delete("User Permission", {"user": OUTSIDER, "allow": "Lead"})
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": OUTSIDER,
				"allow": "Lead",
				"for_value": decoy.name,
			}
		).insert(ignore_permissions=True)

		# ToDos and log rows outlive the rollback, and the lead is reused by name, so
		# without this each test starts with the previous one's assignments still on it.
		frappe.db.delete("ToDo", {"reference_type": "Lead", "reference_name": self.lead.name})
		frappe.db.delete("Sales AI Action Log", {"reference_name": self.lead.name})

		frappe.set_user(OWNER)

	def _lead(self, title: str):
		name = frappe.db.get_value("Lead", {"lead_name": title}, "name")
		if name:
			return frappe.get_doc("Lead", name)
		return frappe.get_doc(
			{"doctype": "Lead", "lead_name": title, "company_name": title, "status": "Lead"}
		).insert(ignore_permissions=True)

	def _assignees(self) -> set[str]:
		was = frappe.session.user
		frappe.set_user("Administrator")
		try:
			return set(
				frappe.get_all(
					"ToDo",
					filters={
						"reference_type": "Lead",
						"reference_name": self.lead.name,
						"status": "Open",
					},
					pluck="allocated_to",
				)
			)
		finally:
			frappe.set_user(was)

	def _shares(self, user: str) -> int:
		was = frappe.session.user
		frappe.set_user("Administrator")
		try:
			return frappe.db.count(
				"DocShare",
				{"share_doctype": "Lead", "share_name": self.lead.name, "user": user},
			)
		finally:
			frappe.set_user(was)

	def test_a_colleague_ends_up_with_the_lead(self) -> None:
		result = assign_lead(self.lead.name, COLLEAGUE, tool="assign_lead")

		self.assertEqual(result["to"], COLLEAGUE)
		self.assertIn(COLLEAGUE, self._assignees())

	def test_assigning_adds_and_does_not_replace(self) -> None:
		"""The promise the return value makes out loud, checked."""
		assign_lead(self.lead.name, COLLEAGUE, tool="assign_lead")
		assign_lead(self.lead.name, OWNER, tool="assign_lead")

		self.assertEqual(self._assignees(), {COLLEAGUE, OWNER})

	def test_assigning_twice_changes_nothing_and_says_so(self) -> None:
		assign_lead(self.lead.name, COLLEAGUE, tool="assign_lead")
		again = assign_lead(self.lead.name, COLLEAGUE, tool="assign_lead")

		self.assertIn("unchanged", again)
		self.assertEqual(self._assignees(), {COLLEAGUE})

	def test_someone_who_cannot_see_the_lead_is_refused(self) -> None:
		with self.assertRaises(GuardError) as caught:
			assign_lead(self.lead.name, OUTSIDER, tool="assign_lead")

		self.assertIn("cannot see this Lead", str(caught.exception))

	def test_the_refused_assignment_grants_no_access(self) -> None:
		"""The whole reason `_assignable` exists. `assign_to.add` would have shared it."""
		with self.assertRaises(GuardError):
			assign_lead(self.lead.name, OUTSIDER, tool="assign_lead")

		self.assertEqual(self._shares(OUTSIDER), 0)
		self.assertNotIn(OUTSIDER, self._assignees())

	def test_an_unknown_and_a_disabled_colleague_read_the_same(self) -> None:
		"""Otherwise the refusal wording is a way to test staff email addresses."""
		messages = []
		for email in ("assign-nobody@example.com", DISABLED):
			with self.assertRaises(GuardError) as caught:
				assign_lead(self.lead.name, email, tool="assign_lead")
			messages.append(str(caught.exception).replace(email, "X"))

		self.assertEqual(messages[0], messages[1])

	def test_the_assignment_is_written_down(self) -> None:
		assign_lead(self.lead.name, COLLEAGUE, note="Priya knows them", tool="assign_lead")
		frappe.set_user("Administrator")

		row = frappe.get_all(
			"Sales AI Action Log",
			filters={"action": "Assign", "reference_name": self.lead.name},
			fields=["outcome", "tool"],
			ignore_permissions=True,
		)
		self.assertEqual(len(row), 1)
		self.assertEqual(row[0].outcome, "Allowed")
		self.assertEqual(row[0].tool, "assign_lead")

	def test_a_refused_assignment_is_written_down_too(self) -> None:
		with self.assertRaises(GuardError):
			assign_lead(self.lead.name, OUTSIDER, tool="assign_lead")
		frappe.set_user("Administrator")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"outcome": "Denied", "action": "Assign", "reference_name": self.lead.name},
			)
		)

	def test_the_note_cannot_carry_markup_onto_the_todo(self) -> None:
		"""The description renders as HTML, and the model wrote it after reading the lead."""
		assign_lead(
			self.lead.name, COLLEAGUE, note="<img src=x onerror=alert(1)>", tool="assign_lead"
		)
		frappe.set_user("Administrator")

		description = frappe.db.get_value(
			"ToDo",
			{
				"reference_type": "Lead",
				"reference_name": self.lead.name,
				"allocated_to": COLLEAGUE,
			},
			"description",
		)
		self.assertNotIn("<img", description)
