# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Pipeline analytics: weighted pipeline, sales cycle, and deal health.

Weighted pipeline multiplies each opportunity's value by its probability to give a
more realistic revenue forecast than raw pipeline totals. Sales cycle measures how
long deals take from creation to close. Deal health flags at-risk deals.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate, date_diff, add_months, get_first_day


def weighted_pipeline(company: str | None = None) -> dict:
	"""Calculate weighted pipeline value (amount × probability) for open opportunities."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	opps = frappe.db.sql(
		"""
		SELECT name, opportunity_from, party_name, opportunity_amount,
		       probability, sales_stage, expected_closing, currency
		FROM `tabOpportunity`
		WHERE company = %s AND status NOT IN ('Lost', 'Closed')
		  AND docstatus < 2
		ORDER BY opportunity_amount DESC
		""",
		(company,),
		as_dict=True,
	)

	today = getdate(nowdate())
	total_raw = 0
	total_weighted = 0
	by_stage = {}
	deals = []

	for o in opps:
		amount = flt(o.opportunity_amount)
		prob = flt(o.probability, 2)
		# Default probability if missing: use 50% as neutral fallback
		if not prob and prob != 0:
			prob = 50.0
		weighted = flt(amount * prob / 100, 2)

		total_raw += amount
		total_weighted += weighted

		stage = o.sales_stage or "Unknown"
		if stage not in by_stage:
			by_stage[stage] = {"count": 0, "raw": 0, "weighted": 0}
		by_stage[stage]["count"] += 1
		by_stage[stage]["raw"] += amount
		by_stage[stage]["weighted"] += weighted

		days_to_close = date_diff(o.expected_closing, today) if o.expected_closing else None

		deals.append({
			"name": o.name,
			"party_name": o.party_name,
			"amount": flt(amount, 2),
			"probability": prob,
			"weighted_value": weighted,
			"sales_stage": stage,
			"expected_closing": str(o.expected_closing) if o.expected_closing else None,
			"days_to_close": days_to_close,
			"overdue": days_to_close is not None and days_to_close < 0,
			"currency": o.currency,
		})

	# Round stage totals
	for stage in by_stage:
		by_stage[stage]["raw"] = flt(by_stage[stage]["raw"], 2)
		by_stage[stage]["weighted"] = flt(by_stage[stage]["weighted"], 2)

	return {
		"total_raw_pipeline": flt(total_raw, 2),
		"total_weighted_pipeline": flt(total_weighted, 2),
		"deal_count": len(deals),
		"by_stage": by_stage,
		"deals": deals[:30],
		"company": company,
	}


def sales_cycle(company: str | None = None, months_back: int = 12) -> dict:
	"""Calculate how long deals take from creation to Won status."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	from_date = add_months(today, -months_back)

	# Won opportunities with their creation and modified dates
	won = frappe.db.sql(
		"""
		SELECT name, party_name, opportunity_amount, creation,
		       modified, sales_stage, expected_closing
		FROM `tabOpportunity`
		WHERE company = %s AND status = 'Converted'
		  AND docstatus < 2
		  AND creation >= %s
		ORDER BY modified DESC
		""",
		(company, from_date),
		as_dict=True,
	)

	if not won:
		# Try "Won" status as well
		won = frappe.db.sql(
			"""
			SELECT name, party_name, opportunity_amount, creation,
			       modified, sales_stage, expected_closing
			FROM `tabOpportunity`
			WHERE company = %s AND status IN ('Converted', 'Won')
			  AND docstatus < 2
			  AND creation >= %s
			ORDER BY modified DESC
			""",
			(company, from_date),
			as_dict=True,
		)

	if not won:
		return {
			"message": "No won opportunities found in the period.",
			"company": company,
			"period_months": months_back,
		}

	cycles = []
	for o in won:
		days = date_diff(getdate(o.modified), getdate(o.creation))
		cycles.append({
			"name": o.name,
			"party_name": o.party_name,
			"amount": flt(o.opportunity_amount, 2),
			"days": max(0, days),
		})

	days_list = sorted(c["days"] for c in cycles)
	n = len(days_list)
	median = days_list[n // 2] if n % 2 == 1 else (days_list[n // 2 - 1] + days_list[n // 2]) / 2
	avg = sum(days_list) / n

	return {
		"median_days": flt(median, 1),
		"average_days": flt(avg, 1),
		"min_days": days_list[0],
		"max_days": days_list[-1],
		"won_count": n,
		"deals": cycles[:20],
		"period_months": months_back,
		"company": company,
	}
