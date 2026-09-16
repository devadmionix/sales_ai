# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Churn risk, measured against each customer's own rhythm rather than a calendar.

A customer who orders every fortnight and has not ordered in six weeks is in trouble. A
customer who orders twice a year and has not ordered in six weeks is fine. Any model that
asks "how long since the last order" without asking "long compared to what" will flag the
second one and miss the first, so the unit here is not days — it is how many of *their own*
typical gaps have gone by.

Two things move the number. Lateness, which is the reading above. And whether the gaps have
been getting longer, because a customer drifting away does it gradually and the drift shows
up before any single gap looks alarming.

One thing brings it down: an open opportunity, or a quotation raised recently. Both mean
somebody is still talking to them. That is applied as a discount rather than a subtraction,
so it softens a risk without ever erasing it — a customer three times overdue with an open
opportunity is still a customer three times overdue.

And a customer with too short a history gets no score at all. Three gaps is the floor,
because two gaps cannot tell a rhythm from a coincidence, and a confident number derived
from a coincidence is worse than no number. See `base.record` for why that is the rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import frappe
from frappe.utils import add_days, date_diff, today

from sales_ai.intelligence.base import Factor, Score, clamp, record

KIND = "Churn Risk"

# Bump this when any constant below moves. A score computed under rules that no longer
# exist should be identifiable, not silently trusted, which is the whole job of the field.
MODEL_VERSION = "churn-2026.09.1"

# Three gaps, so four orders. Below this there is no rhythm to be late against.
MIN_GAPS = 3

# A customer exactly one typical gap overdue scores zero from lateness — that is what "due"
# means. Each further gap that goes by adds this much, to a ceiling, so being twice overdue
# is the worst lateness reading and three times over does not pretend to be worse news.
LATENESS_PER_GAP = 60.0
LATENESS_CEILING = 60.0

# Gaps that have doubled are the worst trend reading. Capped well below lateness because a
# widening rhythm is a warning, not an event.
TREND_PER_DOUBLING = 20.0
TREND_CEILING = 20.0

# Kept as fractions of the risk rather than points off it, so engagement cannot take a badly
# overdue customer down to nothing.
OPEN_OPPORTUNITY_DISCOUNT = 0.25
RECENT_QUOTATION_DISCOUNT = 0.20

# How recent a quotation has to be to count as somebody still talking to them.
QUOTATION_WINDOW = 90


@dataclass(frozen=True)
class History:
	"""Everything the model looks at, with the database left behind.

	Days and counts rather than documents, so the arithmetic below can be checked against
	numbers typed into a test and nobody has to build a customer to find out what a widening
	gap scores.
	"""

	days_since_last: int
	gaps: tuple[int, ...]
	open_opportunities: int = 0
	recent_quotations: int = 0


def score(history: History) -> Score | None:
	"""The whole model. No database, no model call, no randomness.

	Returns `None` when the history is too thin to read, which is not the same as a low
	score and must not be stored as one.
	"""
	if len(history.gaps) < MIN_GAPS:
		return None

	typical = float(median(history.gaps))
	if typical <= 0:
		# Every order on the same day. There is a history but no rhythm in it.
		return None

	factors = [_lateness(history, typical), _trend(history.gaps)]
	risk = sum(factor.contribution for factor in factors)
	factors.extend(_engagement(history, risk))

	value = clamp(sum(factor.contribution for factor in factors))
	return Score(
		value=round(value, 1),
		factors=tuple(factors),
		summary=_summary(history, typical, factors),
	)


def _lateness(history: History, typical: float) -> Factor:
	overdue = history.days_since_last / typical
	contribution = max(0.0, min((overdue - 1.0) * LATENESS_PER_GAP, LATENESS_CEILING))
	return Factor(
		label="Overdue against own rhythm",
		contribution=round(contribution, 3),
		detail=(
			f"{history.days_since_last} days since the last order; "
			f"typical gap {typical:.0f} days across {len(history.gaps)} gaps "
			f"({overdue:.2f} gaps elapsed)."
		),
	)


def _trend(gaps: tuple[int, ...]) -> Factor:
	"""Are the gaps getting longer?

	Compared as the median of the earlier half against the median of the later half, with
	the middle gap dropped when there is an odd number so that no single gap sits on both
	sides of the comparison. Medians rather than means because one holiday shutdown should
	not read as a customer drifting away.
	"""
	half = len(gaps) // 2
	earlier = float(median(gaps[:half]))
	later = float(median(gaps[len(gaps) - half :]))

	widening = later / earlier if earlier > 0 else 1.0
	contribution = max(0.0, min((widening - 1.0) * TREND_PER_DOUBLING, TREND_CEILING))
	return Factor(
		label="Ordering gap widening",
		contribution=round(contribution, 3),
		detail=(
			f"Earlier gaps median {earlier:.0f} days, later gaps median {later:.0f} days "
			f"({widening:.2f}x)."
		),
	)


def _engagement(history: History, risk: float) -> list[Factor]:
	"""Signs somebody is still in the conversation, priced as a discount on the risk.

	Reported as the points it actually removed rather than as a percentage, so the factor
	rows still add up to the score and the explanation stays checkable by hand.
	"""
	factors = []
	remaining = risk

	if history.open_opportunities:
		removed = remaining * OPEN_OPPORTUNITY_DISCOUNT
		remaining -= removed
		factors.append(
			Factor(
				label="Open opportunity",
				contribution=-round(removed, 3),
				detail=(
					f"{history.open_opportunities} open opportunit"
					f"{'y' if history.open_opportunities == 1 else 'ies'}; "
					f"risk discounted {OPEN_OPPORTUNITY_DISCOUNT:.0%}."
				),
			)
		)

	if history.recent_quotations:
		removed = remaining * RECENT_QUOTATION_DISCOUNT
		remaining -= removed
		factors.append(
			Factor(
				label="Recent quotation",
				contribution=-round(removed, 3),
				detail=(
					f"{history.recent_quotations} quotation"
					f"{'' if history.recent_quotations == 1 else 's'} "
					f"in the last {QUOTATION_WINDOW} days; "
					f"risk discounted {RECENT_QUOTATION_DISCOUNT:.0%}."
				),
			)
		)

	return factors


def _summary(history: History, typical: float, factors: list[Factor]) -> str:
	"""One sentence, built from the factors that moved the number.

	Assembled rather than written, so it cannot claim anything the rows below it do not.
	"""
	opening = (
		f"{history.days_since_last} days since the last order "
		f"against a typical gap of {typical:.0f} days"
	)
	moved = [
		f"{factor.label.lower()} ({factor.contribution:+.1f})"
		for factor in factors
		if factor.contribution
	]
	if not moved:
		return f"{opening}; nothing else moved the score."
	return f"{opening}, with {_join(moved)}."


def _join(parts: list[str]) -> str:
	if len(parts) == 1:
		return parts[0]
	return f"{', '.join(parts[:-1])} and {parts[-1]}"


def gather(customer: str) -> History:
	"""Read the rows the model needs. The only function here that knows a site exists."""
	dates = frappe.get_all(
		"Sales Order",
		filters={"customer": customer, "docstatus": 1},
		pluck="transaction_date",
		order_by="transaction_date asc",
	)
	gaps = tuple(date_diff(later, earlier) for earlier, later in zip(dates, dates[1:]))

	return History(
		days_since_last=date_diff(today(), dates[-1]) if dates else 0,
		gaps=gaps,
		open_opportunities=frappe.db.count(
			"Opportunity",
			{"opportunity_from": "Customer", "party_name": customer, "status": "Open"},
		),
		recent_quotations=frappe.db.count(
			"Quotation",
			{
				"quotation_to": "Customer",
				"party_name": customer,
				"docstatus": ["<", 2],
				"transaction_date": [">=", add_days(today(), -QUOTATION_WINDOW)],
			},
		),
	)


def recompute(customer: str | None = None) -> int:
	"""Score every enabled customer, or one of them. Returns how many were scored.

	Customers with too thin a history are skipped rather than scored, so the count returned
	is smaller than the number of customers and that is the model working.
	"""
	names = (
		[customer]
		if customer
		else frappe.get_all("Customer", filters={"disabled": 0}, pluck="name")
	)

	scored = 0
	for name in names:
		result = score(gather(name))
		if result is None:
			continue
		record(KIND, "Customer", name, result, MODEL_VERSION)
		scored += 1
	return scored
