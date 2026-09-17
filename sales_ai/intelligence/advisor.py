# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Business advisor: generates actionable next-step recommendations from real data.

This is not AI guessing. Each recommendation is derived from a specific data signal
(stale leads, declining revenue, at-risk customers, expiring quotations, idle pipeline).
The agent quotes these recommendations and their evidence — it does not invent its own.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate, add_days, add_months, get_first_day, get_last_day, date_diff


def get_recommendations(company: str | None = None) -> dict:
	"""Analyse the business and return prioritised, actionable recommendations."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	recommendations = []

	# 1. Expiring quotations — money about to walk away
	_check_expiring_quotations(company, today, recommendations)

	# 2. Stale opportunities — deals going cold
	_check_stale_opportunities(company, today, recommendations)

	# 3. At-risk customers — churn signals
	_check_churn_risks(company, recommendations)

	# 4. Revenue trend — growing or declining?
	_check_revenue_trend(company, today, recommendations)

	# 5. Unconverted leads — leads sitting idle
	_check_idle_leads(company, today, recommendations)

	# 6. Pipeline health — enough to hit targets?
	_check_pipeline_health(company, today, recommendations)

	# 7. Top performing products — double down on winners
	_check_top_products(company, today, recommendations)

	# 8. Inactive customers — re-engage opportunities
	_check_inactive_customers(company, today, recommendations)

	# Sort by priority (critical first)
	priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
	recommendations.sort(key=lambda r: priority_order.get(r["priority"], 99))

	return {
		"recommendations": recommendations,
		"count": len(recommendations),
		"company": company,
		"as_of": str(today),
	}


def _check_expiring_quotations(company, today, recs):
	"""Quotations expiring in the next 7 days."""
	expiring = frappe.db.sql(
		"""
		SELECT name, party_name, grand_total, valid_till, currency
		FROM `tabQuotation`
		WHERE docstatus = 1
		  AND status NOT IN ('Ordered', 'Cancelled', 'Lost')
		  AND company = %s
		  AND valid_till BETWEEN %s AND %s
		""",
		(company, today, add_days(today, 7)),
		as_dict=True,
	)
	for q in expiring:
		days_left = date_diff(q.valid_till, today)
		recs.append({
			"priority": "critical" if days_left <= 2 else "high",
			"category": "Expiring Quotation",
			"action": f"Follow up on quotation {q.name} for {q.party_name} — expires in {days_left} day(s). Value: {q.currency} {flt(q.grand_total, 2)}.",
			"next_step": "Call the customer today and ask for their decision. Offer to extend validity if they need more time.",
			"record": q.name,
			"doctype": "Quotation",
		})


def _check_stale_opportunities(company, today, recs):
	"""Open opportunities with no activity in 14+ days."""
	stale = frappe.db.sql(
		"""
		SELECT name, party_name, opportunity_amount, currency, modified, sales_stage
		FROM `tabOpportunity`
		WHERE status NOT IN ('Lost', 'Closed', 'Converted')
		  AND company = %s
		  AND modified < %s
		ORDER BY opportunity_amount DESC
		LIMIT 10
		""",
		(company, add_days(today, -14)),
		as_dict=True,
	)
	for opp in stale:
		days_stale = date_diff(today, opp.modified)
		recs.append({
			"priority": "high" if flt(opp.opportunity_amount) > 50000 else "medium",
			"category": "Stale Opportunity",
			"action": f"Opportunity {opp.name} ({opp.party_name}) has had no activity for {days_stale} days. Value: {opp.currency or ''} {flt(opp.opportunity_amount, 2)}. Stage: {opp.sales_stage or 'Unknown'}.",
			"next_step": "Schedule a call or send a check-in email. If the deal is dead, mark it Lost to keep the pipeline clean.",
			"record": opp.name,
			"doctype": "Opportunity",
		})


def _check_churn_risks(company, recs):
	"""Customers with high churn risk scores."""
	insights = frappe.db.sql(
		"""
		SELECT i.subject_name AS customer, i.value, i.band
		FROM `tabSales AI Insight` i
		WHERE i.kind = 'churn_risk'
		  AND i.band = 'High'
		  AND i.subject_doctype = 'Customer'
		ORDER BY i.value DESC
		LIMIT 5
		""",
		as_dict=True,
	)
	for c in insights:
		recs.append({
			"priority": "high",
			"category": "Customer At Risk",
			"action": f"Customer '{c.customer}' has a churn risk score of {flt(c.value, 0)}/100. They are overdue compared to their usual ordering pattern.",
			"next_step": "Reach out with a personal call. Offer a special deal or ask about their needs. Losing an existing customer costs 5x more than acquiring a new one.",
			"record": c.customer,
			"doctype": "Customer",
		})


def _check_revenue_trend(company, today, recs):
	"""Compare this month's revenue to the previous month."""
	this_month_start = get_first_day(today)
	last_month_start = get_first_day(add_months(today, -1))
	last_month_end = get_last_day(add_months(today, -1))

	this_month = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS total
		FROM `tabSales Order` WHERE docstatus = 1 AND company = %s
		AND transaction_date BETWEEN %s AND %s
		""",
		(company, this_month_start, today),
		as_dict=True,
	)[0].total

	last_month = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS total
		FROM `tabSales Order` WHERE docstatus = 1 AND company = %s
		AND transaction_date BETWEEN %s AND %s
		""",
		(company, last_month_start, last_month_end),
		as_dict=True,
	)[0].total

	if last_month > 0:
		change = flt((this_month - last_month) / last_month * 100, 1)
		if change < -20:
			recs.append({
				"priority": "critical",
				"category": "Revenue Decline",
				"action": f"Revenue is down {abs(change)}% compared to last month ({flt(this_month, 0)} vs {flt(last_month, 0)}).",
				"next_step": "Review lost deals and stale quotations. Push pending quotations to close. Consider a promotional campaign to boost orders.",
			})
		elif change < -5:
			recs.append({
				"priority": "high",
				"category": "Revenue Slowing",
				"action": f"Revenue is down {abs(change)}% compared to last month.",
				"next_step": "Identify which customers or products dropped and focus sales efforts there.",
			})
		elif change > 20:
			recs.append({
				"priority": "low",
				"category": "Revenue Growing",
				"action": f"Revenue is up {change}% compared to last month. Good momentum.",
				"next_step": "Identify what's working and do more of it. Ensure you can fulfil the increased demand.",
			})


def _check_idle_leads(company, today, recs):
	"""Leads older than 7 days with status still Open."""
	idle_count = frappe.db.count(
		"Lead",
		filters={
			"company": company,
			"status": "Open",
			"creation": ("<", add_days(today, -7)),
		},
	)
	if idle_count > 0:
		recs.append({
			"priority": "medium",
			"category": "Idle Leads",
			"action": f"{idle_count} lead(s) have been sitting in 'Open' status for over a week without being contacted.",
			"next_step": "Assign these leads to sales reps and make first contact within 24 hours. Speed-to-lead is the #1 factor in conversion.",
		})


def _check_pipeline_health(company, today, recs):
	"""Is the pipeline large enough relative to recent revenue?"""
	monthly_revenue = frappe.db.sql(
		"""
		SELECT COALESCE(AVG(monthly_total), 0) AS avg_monthly
		FROM (
			SELECT SUM(base_grand_total) AS monthly_total
			FROM `tabSales Order`
			WHERE docstatus = 1 AND company = %s
			  AND transaction_date >= %s
			GROUP BY DATE_FORMAT(transaction_date, '%%Y-%%m')
		) AS monthly
		""",
		(company, add_months(today, -6)),
		as_dict=True,
	)
	avg_monthly = flt(monthly_revenue[0].avg_monthly) if monthly_revenue else 0

	pipeline = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(opportunity_amount), 0) AS total
		FROM `tabOpportunity`
		WHERE status NOT IN ('Lost', 'Closed', 'Converted')
		  AND company = %s
		""",
		(company,),
		as_dict=True,
	)[0].total

	if avg_monthly > 0:
		ratio = flt(pipeline / avg_monthly, 1)
		if ratio < 2:
			recs.append({
				"priority": "high",
				"category": "Pipeline Too Thin",
				"action": f"Pipeline ({flt(pipeline, 0)}) is only {ratio}x your average monthly revenue ({flt(avg_monthly, 0)}). A healthy pipeline is 3–5x.",
				"next_step": "Increase lead generation. Attend industry events, run email campaigns, or ask existing customers for referrals.",
			})


def _check_top_products(company, today, recs):
	"""Identify top product to double down on."""
	top = frappe.db.sql(
		"""
		SELECT soi.item_name, SUM(soi.base_amount) AS revenue, SUM(soi.qty) AS qty
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s
		  AND so.transaction_date >= %s
		GROUP BY soi.item_name
		ORDER BY revenue DESC
		LIMIT 1
		""",
		(company, add_months(today, -3)),
		as_dict=True,
	)
	if top:
		item = top[0]
		recs.append({
			"priority": "low",
			"category": "Top Product Insight",
			"action": f"Your best seller in the last 3 months is '{item.item_name}' with {flt(item.revenue, 0)} in revenue ({flt(item.qty, 0)} units).",
			"next_step": "Create targeted campaigns around this product. Bundle it with complementary items. Train your team to lead with it.",
		})


def _check_inactive_customers(company, today, recs):
	"""Customers who ordered before but not in the last 90 days."""
	inactive = frappe.db.sql(
		"""
		SELECT c.name AS customer, c.customer_name, MAX(so.transaction_date) AS last_order
		FROM `tabCustomer` c
		JOIN `tabSales Order` so ON so.customer = c.name AND so.docstatus = 1
		WHERE c.disabled = 0
		  AND so.company = %s
		GROUP BY c.name, c.customer_name
		HAVING last_order < %s
		ORDER BY last_order ASC
		LIMIT 5
		""",
		(company, add_days(today, -90)),
		as_dict=True,
	)
	if inactive:
		names = ", ".join(c.customer_name for c in inactive[:3])
		recs.append({
			"priority": "medium",
			"category": "Inactive Customers",
			"action": f"{len(inactive)} customer(s) haven't ordered in 90+ days, including {names}.",
			"next_step": "Send a re-engagement email or call them. Offer a loyalty discount. Ask if their needs have changed.",
		})
