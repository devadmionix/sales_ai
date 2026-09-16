# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The gate that decides whether the agent may act without asking.

Every test here is about failing closed. An amount threshold is the only rule that hands
the agent permission to write unsupervised, so the cases that matter most are the ones
where something is missing or unclear — those must end up in front of a human, not
through the gate on a technicality.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.guard import policy
from sales_ai.llm.agent import Question, Refusal
from sales_ai.llm.tool import Tool
from sales_ai.llm.types import ToolCall

COMPANY = "Widgets Ltd"
OTHER_COMPANY = "Someone Else Ltd"


def _tool(name: str, *, writes: bool = True, preview: Any = None, risk: str | None = None) -> Tool:
	"""A stand-in for a registered tool. The policy layer only ever reads `name` and `meta`."""
	meta: dict[str, Any] = {"writes": writes}
	if preview is not None:
		meta["preview"] = preview
	if risk is not None:
		meta["risk"] = risk
	return Tool(name=name, description="", parameters={}, func=lambda **kw: None, meta=meta)


def _facts(total: float | None, currency: str = "INR", company: str | None = COMPANY) -> dict:
	totals = {} if total is None else {"grand_total": total, "rounded_total": total}
	return {"currency": currency, "company": company, "totals": totals}


class PolicyTestCase(IntegrationTestCase):
	"""Shared fixtures. Holds no tests of its own, so subclasses do not re-run each other's."""

	def setUp(self) -> None:
		frappe.flags.pop("sales_ai_unattended", None)
		frappe.db.delete("Sales AI Action Policy", {"tool": ("like", "t\\_%")})

	def tearDown(self) -> None:
		frappe.db.delete("Sales AI Action Policy", {"tool": ("like", "t\\_%")})
		frappe.flags.pop("sales_ai_unattended", None)

	def _policy(self, tool: str, **kwargs: Any) -> str:
		row = frappe.get_doc(
			{
				"doctype": "Sales AI Action Policy",
				"enabled": 1,
				"tool": tool,
				**kwargs,
			}
		)
		# The doctype checks the tool is registered and the companies are real; both are
		# invented here, because what is under test is the matching, not the bookkeeping.
		row.flags.ignore_validate = True
		row.flags.ignore_links = True
		row.insert(ignore_permissions=True)
		return row.name


class TestThresholds(PolicyTestCase):
	"""`Require Approval Above Amount`: small things go through, big things get asked about."""

	# -- the default, which no test below is allowed to quietly change ----------------

	def test_a_write_with_no_rule_asks(self) -> None:
		self.assertEqual(policy.decide(_tool("t_write")).mode, policy.REQUIRE_APPROVAL)

	def test_a_read_with_no_rule_runs(self) -> None:
		self.assertEqual(policy.decide(_tool("t_read", writes=False)).mode, policy.ALLOW)

	# -- the threshold itself ---------------------------------------------------------

	def test_at_or_below_the_limit_the_agent_acts_alone(self) -> None:
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		for amount in (96.0, 4999.99, 5000.0):
			with self.subTest(amount=amount):
				rule = policy.decide(_tool("t_quote"), _facts(amount))
				self.assertEqual(rule.mode, policy.ALLOW)

	def test_above_the_limit_a_human_is_asked(self) -> None:
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		rule = policy.decide(_tool("t_quote"), _facts(5000.01))
		self.assertEqual(rule.mode, policy.REQUIRE_APPROVAL)

	def test_the_note_names_the_amount_and_the_limit(self) -> None:
		"""Whoever is asked should not have to open the record to see why they were asked."""
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		note = policy.decide(_tool("t_quote"), _facts(10120.0)).note
		self.assertIn("10,120", note)
		self.assertIn("5,000", note)

	# -- everything that must fail closed ---------------------------------------------

	def test_an_unknown_value_asks(self) -> None:
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		rule = policy.decide(_tool("t_quote"), _facts(None))
		self.assertEqual(rule.mode, policy.REQUIRE_APPROVAL)
		self.assertTrue(rule.note)

	def test_no_facts_at_all_asks(self) -> None:
		"""`decide()` called as the general question "what governs this tool" must not allow."""
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		self.assertEqual(policy.decide(_tool("t_quote")).mode, policy.REQUIRE_APPROVAL)

	def test_a_failed_preview_asks(self) -> None:
		"""A preview that blew up reports an error and no totals, which must not read as zero."""
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		rule = policy.decide(_tool("t_quote"), {"error": "could not price this"})
		self.assertEqual(rule.mode, policy.REQUIRE_APPROVAL)

	def test_a_different_currency_is_never_converted(self) -> None:
		"""An exchange rate must not be what decides whether something needed approval."""
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="USD")

		rule = policy.decide(_tool("t_quote"), _facts(100.0, currency="INR"))
		self.assertEqual(rule.mode, policy.REQUIRE_APPROVAL)
		self.assertIn("INR", rule.note)

	def test_zero_is_a_real_amount_and_not_a_missing_one(self) -> None:
		self.assertEqual(policy._value_of({"totals": {"grand_total": 0.0}, "currency": "INR"}), (0.0, "INR"))
		self.assertEqual(policy._value_of({"totals": {}, "currency": "INR"}), (None, "INR"))
		self.assertEqual(policy._value_of(None), (None, None))

	def test_rounded_total_wins_because_it_is_what_is_charged(self) -> None:
		facts = {"currency": "INR", "totals": {"total": 100.0, "grand_total": 118.0, "rounded_total": 120.0}}
		self.assertEqual(policy._value_of(facts), (120.0, "INR"))


class TestCompanyNarrowing(PolicyTestCase):
	"""A rule written about one company must not decide another company's call."""

	def test_a_rule_for_another_company_is_skipped(self) -> None:
		self._policy("t_quote", mode=policy.DENY, message="no", for_company=OTHER_COMPANY)

		# Falls through to the default for a write, rather than picking up the Deny.
		self.assertEqual(policy.decide(_tool("t_quote"), _facts(10.0)).mode, policy.REQUIRE_APPROVAL)

	def test_a_rule_for_this_company_applies(self) -> None:
		self._policy("t_quote", mode=policy.DENY, message="no", for_company=COMPANY)

		self.assertEqual(policy.decide(_tool("t_quote"), _facts(10.0)).mode, policy.DENY)

	def test_an_unknown_company_does_not_match_a_company_rule(self) -> None:
		self._policy("t_quote", mode=policy.ALLOW, for_company=COMPANY)

		rule = policy.decide(_tool("t_quote"), _facts(10.0, company=None))
		self.assertEqual(rule.mode, policy.REQUIRE_APPROVAL)


class TestGate(PolicyTestCase):
	"""What the agent loop actually receives, and what the human actually sees."""

	def test_the_card_shows_the_figure_the_threshold_was_judged_against(self) -> None:
		"""The whole point of computing the preview once. These two must not be able to differ."""
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")
		facts = _facts(10120.0)

		asked = policy.gate(
			_tool("t_quote", preview=lambda args: facts),
			ToolCall(id="c1", name="t_quote", arguments={}),
		)
		self.assertIsInstance(asked, Question)
		self.assertEqual(asked.preview["totals"]["grand_total"], 10120.0)

	def test_under_the_limit_the_gate_stands_aside(self) -> None:
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		verdict = policy.gate(
			_tool("t_quote", preview=lambda args: _facts(96.0)),
			ToolCall(id="c1", name="t_quote", arguments={}),
		)
		self.assertIsNone(verdict)

	def test_a_preview_that_raises_does_not_take_the_run_down(self) -> None:
		"""It becomes an approval request carrying the bad news, not an exception."""
		self._policy("t_quote", mode=policy.THRESHOLD, threshold=5000, currency="INR")

		def explode(args: dict) -> dict:
			raise ValueError("pricing is broken")

		asked = policy.gate(
			_tool("t_quote", preview=explode), ToolCall(id="c1", name="t_quote", arguments={})
		)
		self.assertIsInstance(asked, Question)
		self.assertTrue(asked.preview.get("error"))

	def test_a_denial_refuses_rather_than_asking(self) -> None:
		self._policy("t_quote", mode=policy.DENY, message="Not from this account.")

		verdict = policy.gate(_tool("t_quote"), ToolCall(id="c1", name="t_quote", arguments={}))
		self.assertIsInstance(verdict, Refusal)
		self.assertIn("Not from this account.", verdict.message)


class TestRisk(PolicyTestCase):
	"""Risk is declared in code, so the tests guard the declarations themselves."""

	def test_every_registered_tool_declares_a_known_risk(self) -> None:
		"""Catches a new tool added without one, and a typo in an existing one."""
		from sales_ai import tools

		for name in tools.names():
			with self.subTest(tool=name):
				self.assertIn(tools.get(name).meta.get("risk"), policy.RISKS)

	def test_a_write_tool_that_forgot_is_treated_as_dangerous(self) -> None:
		self.assertEqual(policy.risk_of(_tool("t_undeclared")), "high")

	def test_a_read_tool_that_forgot_is_not(self) -> None:
		self.assertEqual(policy.risk_of(_tool("t_undeclared", writes=False)), "none")

	def test_a_nonsense_risk_is_not_taken_at_face_value(self) -> None:
		bogus = _tool("t_bogus")
		bogus.meta["risk"] = "harmless"
		self.assertEqual(policy.risk_of(bogus), "high")

	def test_no_read_tool_claims_to_be_risky(self) -> None:
		"""A read that looks dangerous on the card trains people to click through warnings."""
		from sales_ai import tools

		for name in tools.names():
			handle = tools.get(name)
			if not handle.meta.get("writes"):
				with self.subTest(tool=name):
					self.assertEqual(handle.meta.get("risk"), "none")

	def test_the_approval_card_is_told_the_risk(self) -> None:
		self._policy("t_quote", mode=policy.REQUIRE_APPROVAL)

		asked = policy.gate(
			_tool("t_quote", risk="high"), ToolCall(id="c1", name="t_quote", arguments={})
		)
		self.assertEqual(asked.risk, "high")


class TestUnattended(PolicyTestCase):
	"""A run nobody is watching is not a run that is pre-approved."""

	def test_an_override_replaces_the_threshold_outright(self) -> None:
		self._policy(
			"t_quote",
			mode=policy.THRESHOLD,
			threshold=5000,
			currency="INR",
			autonomous_override=policy.DENY,
			message="Not while unattended.",
		)
		frappe.flags["sales_ai_unattended"] = True

		# Well under the limit, so attended it would have been allowed.
		self.assertEqual(policy.decide(_tool("t_quote"), _facts(96.0)).mode, policy.DENY)

	def test_same_as_mode_leaves_the_threshold_alone(self) -> None:
		self._policy(
			"t_quote",
			mode=policy.THRESHOLD,
			threshold=5000,
			currency="INR",
			autonomous_override=policy.SAME_AS_MODE,
		)
		frappe.flags["sales_ai_unattended"] = True

		self.assertEqual(policy.decide(_tool("t_quote"), _facts(96.0)).mode, policy.ALLOW)
		self.assertEqual(policy.decide(_tool("t_quote"), _facts(99999.0)).mode, policy.REQUIRE_APPROVAL)
