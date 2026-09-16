# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The arithmetic behind a churn score, checked without a customer in sight.

A scoring model is the easiest thing in an app to get quietly wrong. It always returns a
number, the number always looks plausible, and nobody notices that a customer who orders
twice a year is being flagged as lapsed until somebody acts on it. So these tests pin the
readings that separate this model from a naive one: the same absence of orders scoring
differently for different rhythms, and a history too short to read scoring nothing at all.

No database, because `score` takes a `History` and not a name. That is the reason it does.
"""

from __future__ import annotations

from frappe.tests import UnitTestCase

from sales_ai.intelligence import churn
from sales_ai.intelligence.churn import History


class TestChurnScore(UnitTestCase):
	def test_a_customer_on_their_own_schedule_is_not_a_risk(self) -> None:
		result = churn.score(History(days_since_last=14, gaps=(14, 14, 14, 14)))
		assert result is not None
		self.assertEqual(result.value, 0.0)

	def test_lateness_is_relative_to_the_customers_own_rhythm(self) -> None:
		"""The whole reason this model exists.

		Two customers, both silent for sixty days. One orders fortnightly and has missed
		three of them; the other orders quarterly and is not yet due. A model counting days
		would score them the same.
		"""
		frequent = churn.score(History(days_since_last=60, gaps=(14, 14, 14, 14)))
		occasional = churn.score(History(days_since_last=60, gaps=(90, 90, 90, 90)))
		assert frequent is not None and occasional is not None

		self.assertGreater(frequent.value, occasional.value)
		self.assertEqual(occasional.value, 0.0)

	def test_being_twice_overdue_is_the_worst_lateness_reading(self) -> None:
		"""Three times overdue is not worse news than twice, so it does not score worse.

		The cap exists because the difference between a customer who has gone quiet and one
		who has gone very quiet is not information anybody acts on differently.
		"""
		twice = churn.score(History(days_since_last=60, gaps=(30, 30, 30, 30)))
		thrice = churn.score(History(days_since_last=90, gaps=(30, 30, 30, 30)))
		assert twice is not None and thrice is not None
		self.assertEqual(twice.value, thrice.value)
		self.assertEqual(twice.value, churn.LATENESS_CEILING)

	def test_a_widening_gap_counts_even_when_nothing_is_overdue(self) -> None:
		"""A customer drifting away does it gradually. This is the part that sees it early."""
		result = churn.score(History(days_since_last=5, gaps=(10, 10, 40, 40)))
		assert result is not None
		self.assertGreater(result.value, 0)
		self.assertEqual(
			[factor.label for factor in result.factors if factor.contribution > 0],
			["Ordering gap widening"],
		)

	def test_a_narrowing_gap_is_not_negative_news(self) -> None:
		result = churn.score(History(days_since_last=5, gaps=(40, 40, 10, 10)))
		assert result is not None
		self.assertEqual(result.value, 0.0)

	def test_the_middle_gap_is_dropped_so_it_is_never_on_both_sides(self) -> None:
		odd = churn.score(History(days_since_last=1, gaps=(10, 999, 20)))
		assert odd is not None
		trend = next(f for f in odd.factors if f.label == "Ordering gap widening")
		self.assertIn("Earlier gaps median 10", trend.detail)
		self.assertIn("later gaps median 20", trend.detail)


class TestChurnEngagement(UnitTestCase):
	def test_an_open_opportunity_softens_the_risk(self) -> None:
		bare = churn.score(History(days_since_last=60, gaps=(30, 30, 30, 30)))
		talking = churn.score(
			History(days_since_last=60, gaps=(30, 30, 30, 30), open_opportunities=1)
		)
		assert bare is not None and talking is not None
		self.assertLess(talking.value, bare.value)

	def test_engagement_can_never_erase_a_risk(self) -> None:
		"""The reason it is a discount and not a subtraction.

		A customer three times overdue with somebody still chasing them is still three times
		overdue, and a model that lets an open opportunity zero that out is a model that
		hides exactly the account a manager needed to see.
		"""
		result = churn.score(
			History(
				days_since_last=120,
				gaps=(30, 30, 30, 30),
				open_opportunities=3,
				recent_quotations=3,
			)
		)
		assert result is not None
		self.assertGreater(result.value, 0)

	def test_the_factors_add_up_to_the_score(self) -> None:
		"""Otherwise the explanation is decoration rather than an explanation."""
		result = churn.score(
			History(days_since_last=75, gaps=(10, 20, 30, 40), open_opportunities=1, recent_quotations=2)
		)
		assert result is not None
		self.assertAlmostEqual(
			result.value, sum(factor.contribution for factor in result.factors), places=1
		)


class TestChurnRefusesToGuess(UnitTestCase):
	def test_too_few_gaps_produces_no_score_at_all(self) -> None:
		"""Not a low score. No score.

		Two gaps cannot tell a rhythm from a coincidence, and a number derived from a
		coincidence still gets acted on like one that was not.
		"""
		self.assertIsNone(churn.score(History(days_since_last=400, gaps=(30, 30))))

	def test_a_brand_new_customer_produces_no_score(self) -> None:
		self.assertIsNone(churn.score(History(days_since_last=0, gaps=())))

	def test_every_order_on_one_day_is_a_history_with_no_rhythm(self) -> None:
		self.assertIsNone(churn.score(History(days_since_last=90, gaps=(0, 0, 0, 0))))

	def test_a_score_always_carries_its_reasons(self) -> None:
		"""The doctype refuses a score with no factors, so the model must never build one."""
		result = churn.score(History(days_since_last=1, gaps=(30, 30, 30)))
		assert result is not None
		self.assertTrue(result.factors)
		self.assertTrue(all(factor.detail for factor in result.factors))


class TestChurnSummary(UnitTestCase):
	def test_the_summary_names_the_factors_that_moved_it(self) -> None:
		result = churn.score(
			History(days_since_last=60, gaps=(10, 10, 40, 40), open_opportunities=1)
		)
		assert result is not None
		self.assertIn("60 days since the last order", result.summary)
		self.assertIn("ordering gap widening", result.summary)
		self.assertIn("open opportunity", result.summary)

	def test_a_quiet_score_says_so_rather_than_trailing_off(self) -> None:
		result = churn.score(History(days_since_last=5, gaps=(30, 30, 30, 30)))
		assert result is not None
		self.assertIn("nothing else moved the score", result.summary)
