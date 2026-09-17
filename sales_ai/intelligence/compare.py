# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Period-over-period comparison: this month vs last, this quarter vs last, YoY.

Compares revenue, order count, average order value, new customers, and pipeline
between two periods. Computes the change in absolute and percentage terms, and
flags which customers and products drove the biggest swings.
"""

from __future__ import annotations

import frappe
from frappe.utils import (
	flt, getdate, nowdate, add_months, add_days,
	get_first_day, get_last_day,
)


def compare_periods(
	company: str | None = None,
	period: str = "month",
) -> dict:
	"""Compare current period with previous period.

	period: "month", "quarter", or "year"
	"""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())

	if period == "month":
		current_start = get_first_day(today)
		current_end = today
		prev_start = get_first_day(add_months(today, -1))
		prev_end = get_last_day(add_months(today, -1))
		label_current = today.strftime("%B %Y")
		label_previous = add_months(today, -1).strftime("%B %Y")
	elif period == "quarter":
		q_month = ((today.month - 1) // 3) * 3 + 1
		current_start = getdate(f"{today.year}-{q_month:02d}-01")
		current_end = today
		prev_q_start = add_months(current_start, -3)
		prev_start = prev_q_start
		prev_end = add_days(current_start, -1)
		label_current = f"Q{(q_month - 1) // 3 + 1} {today.year}"
		pq = ((prev_q_start.month - 1) // 3) + 1
		label_previous = f"Q{pq} {prev_q_start.year}"
	else:  # year
		current_start = getdate(f"{today.year}-01-01")
		current_end = today
		prev_start = getdate(f"{today.year - 1}-01-01")
		prev_end = getdate(f"{today.year - 1}-12-31")
		label_current = str(today.year)
		label_previous = str(today.year - 1)

	current = _period_metrics(company, current_start, current_end)
	previous = _period_metrics(company, prev_start, prev_end)

	# Compute changes
	changes = {}
	for key in current:
		cur_val = current[key]
		prev_val = previous[key]
		if isinstance(cur_val, (int, float)):
			diff = cur_val - prev_val
			pct = flt(diff / prev_val * 100, 1) if prev_val else (100 if cur_val > 0 else 0)
			changes[key] = {
				"current": cur_val,
				"previous": prev_val,
				"change": flt(diff, 2),
				"change_pct": pct,
				"direction": "up" if diff > 0 else "down" if diff < 0 else "flat",
			}

	# Find top movers — customers who gained or lost the most
	top_gainers = _top_customer_changes(company, current_start, current_end, prev_start, prev_end, direction="gain")
	top_losers = _top_customer_changes(company, current_start, current_end, prev_start, prev_end, direction="loss")

	# Product changes
	product_changes = _top_product_changes(company, current_start, current_end, prev_start, prev_end)

	return {
		"period": period,
		"current_label": label_current,
		"previous_label": label_previous,
		"current_range": {"from": str(current_start), "to": str(current_end)},
		"previous_range": {"from": str(prev_start), "to": str(prev_end)},
		"changes": changes,
		"top_gainers": top_gainers,
		"top_losers": top_losers,
		"product_changes": product_changes,
		"company": company,
	}


def _period_metrics(company, from_date, to_date):
	"""Core metrics for a single period."""
	so = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS revenue,
		       COUNT(*) AS order_count,
		       COALESCE(AVG(base_grand_total), 0) AS avg_order_value
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		""",
		(company, from_date, to_date),
		as_dict=True,
	)[0]

	new_customers = frappe.db.count(
		"Customer",
		filters={
			"creation": ("between", [from_date, to_date]),
		},
	)

	pipeline = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(opportunity_amount), 0) AS value,
		       COUNT(*) AS count
		FROM `tabOpportunity`
		WHERE company = %s AND docstatus < 2
		  AND creation BETWEEN %s AND %s
		""",
		(company, from_date, to_date),
		as_dict=True,
	)[0]

	quotation = frappe.db.sql(
		"""
		SELECT COUNT(*) AS count, COALESCE(SUM(base_grand_total), 0) AS value
		FROM `tabQuotation`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		""",
		(company, from_date, to_date),
		as_dict=True,
	)[0]

	return {
		"revenue": flt(so.revenue, 2),
		"order_count": so.order_count or 0,
		"avg_order_value": flt(so.avg_order_value, 2),
		"new_customers": new_customers or 0,
		"new_opportunities": pipeline.count or 0,
		"pipeline_value": flt(pipeline.value, 2),
		"quotations_sent": quotation.count or 0,
		"quotation_value": flt(quotation.value, 2),
	}


def _top_customer_changes(company, cur_start, cur_end, prev_start, prev_end, direction="gain", limit=5):
	"""Customers with the biggest revenue change between periods."""
	rows = frappe.db.sql(
		"""
		SELECT customer, customer_name, current_rev, previous_rev
		FROM (
			SELECT customer, customer_name,
			       SUM(CASE WHEN transaction_date BETWEEN %s AND %s THEN base_grand_total ELSE 0 END) AS current_rev,
			       SUM(CASE WHEN transaction_date BETWEEN %s AND %s THEN base_grand_total ELSE 0 END) AS previous_rev
			FROM `tabSales Order`
			WHERE docstatus = 1 AND company = %s
			  AND (transaction_date BETWEEN %s AND %s OR transaction_date BETWEEN %s AND %s)
			GROUP BY customer, customer_name
		) t
		WHERE current_rev != previous_rev
		ORDER BY (current_rev - previous_rev) {order}
		LIMIT %s
		""".format(order="DESC" if direction == "gain" else "ASC"),
		(cur_start, cur_end, prev_start, prev_end, company,
		 cur_start, cur_end, prev_start, prev_end, limit),
		as_dict=True,
	)
	return [
		{
			"customer": r.customer,
			"customer_name": r.customer_name,
			"current": flt(r.current_rev, 2),
			"previous": flt(r.previous_rev, 2),
			"change": flt(r.current_rev - r.previous_rev, 2),
		}
		for r in rows
	]


def _top_product_changes(company, cur_start, cur_end, prev_start, prev_end, limit=5):
	"""Products with the biggest revenue change between periods."""
	rows = frappe.db.sql(
		"""
		SELECT item_code, item_name, current_rev, previous_rev
		FROM (
			SELECT soi.item_code, soi.item_name,
			       SUM(CASE WHEN so.transaction_date BETWEEN %s AND %s THEN soi.base_amount ELSE 0 END) AS current_rev,
			       SUM(CASE WHEN so.transaction_date BETWEEN %s AND %s THEN soi.base_amount ELSE 0 END) AS previous_rev
			FROM `tabSales Order Item` soi
			JOIN `tabSales Order` so ON so.name = soi.parent
			WHERE so.docstatus = 1 AND so.company = %s
			  AND (so.transaction_date BETWEEN %s AND %s OR so.transaction_date BETWEEN %s AND %s)
			GROUP BY soi.item_code, soi.item_name
		) t
		WHERE current_rev != previous_rev
		ORDER BY ABS(current_rev - previous_rev) DESC
		LIMIT %s
		""",
		(cur_start, cur_end, prev_start, prev_end, company,
		 cur_start, cur_end, prev_start, prev_end, limit),
		as_dict=True,
	)
	return [
		{
			"item_code": r.item_code,
			"item_name": r.item_name,
			"current": flt(r.current_rev, 2),
			"previous": flt(r.previous_rev, 2),
			"change": flt(r.current_rev - r.previous_rev, 2),
			"direction": "up" if r.current_rev > r.previous_rev else "down",
		}
		for r in rows
	]
