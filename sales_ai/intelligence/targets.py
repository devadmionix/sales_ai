# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Target vs actual analysis.

Compares revenue against configured sales targets, identifies the gap, and
analyses which customers, products, and periods are driving the variance.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate, get_first_day, get_last_day, add_months


def target_vs_actual(
	company: str | None = None,
	period: str = "month",
) -> dict:
	"""Compare actual revenue against sales target for the period.

	period: "month", "quarter", or "year"
	"""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())

	if period == "month":
		from_date = get_first_day(today)
		to_date = get_last_day(today)
		label = today.strftime("%B %Y")
	elif period == "quarter":
		q_month = ((today.month - 1) // 3) * 3 + 1
		from_date = getdate(f"{today.year}-{q_month:02d}-01")
		to_date = get_last_day(add_months(from_date, 2))
		label = f"Q{(q_month - 1) // 3 + 1} {today.year}"
	else:  # year
		from_date = getdate(f"{today.year}-01-01")
		to_date = getdate(f"{today.year}-12-31")
		label = str(today.year)

	# Actual revenue
	actual = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS revenue,
		       COUNT(*) AS order_count
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		""",
		(company, from_date, today),
		as_dict=True,
	)[0]

	actual_revenue = flt(actual.revenue, 2)

	# Target — from Target Detail child table across all Sales Persons
	target_value = _get_target(company, from_date, to_date, period)

	# Calculate variance
	if target_value and target_value > 0:
		variance = flt(actual_revenue - target_value, 2)
		achievement_pct = flt(actual_revenue / target_value * 100, 1)
		status = "exceeded" if variance > 0 else "on_track" if achievement_pct >= 80 else "behind"
	else:
		variance = 0
		achievement_pct = 0
		status = "no_target"

	# What's driving the gap? Top products and customers in this period
	top_customers = frappe.db.sql(
		"""
		SELECT customer_name, SUM(base_grand_total) AS revenue
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		GROUP BY customer_name
		ORDER BY revenue DESC
		LIMIT 5
		""",
		(company, from_date, today),
		as_dict=True,
	)

	top_products = frappe.db.sql(
		"""
		SELECT soi.item_name, SUM(soi.base_amount) AS revenue
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s
		  AND so.transaction_date BETWEEN %s AND %s
		GROUP BY soi.item_name
		ORDER BY revenue DESC
		LIMIT 5
		""",
		(company, from_date, today),
		as_dict=True,
	)

	# Days remaining and projected run rate
	days_elapsed = max(1, (today - from_date).days + 1)
	days_total = (to_date - from_date).days + 1
	days_remaining = max(0, (to_date - today).days)
	daily_rate = flt(actual_revenue / days_elapsed, 2)
	projected_revenue = flt(daily_rate * days_total, 2)

	return {
		"period": period,
		"label": label,
		"date_range": {"from": str(from_date), "to": str(to_date)},
		"actual_revenue": actual_revenue,
		"target": flt(target_value, 2) if target_value else None,
		"variance": variance,
		"achievement_pct": achievement_pct,
		"status": status,
		"days_elapsed": days_elapsed,
		"days_remaining": days_remaining,
		"daily_run_rate": daily_rate,
		"projected_revenue": projected_revenue,
		"projected_achievement_pct": flt(projected_revenue / target_value * 100, 1) if target_value else None,
		"order_count": actual.order_count or 0,
		"top_customers": [{"customer": c.customer_name, "revenue": flt(c.revenue, 2)} for c in top_customers],
		"top_products": [{"product": p.item_name, "revenue": flt(p.revenue, 2)} for p in top_products],
		"company": company,
	}


def _get_target(company, from_date, to_date, period):
	"""Sum sales targets from Target Detail across all Sales Persons for the period."""

	# ERPNext stores targets in the fiscal year's target distribution
	# Target Detail is a child of Sales Person with monthly targets
	fiscal_year = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ("<=", from_date), "year_end_date": (">=", to_date)},
		"name",
	)

	if not fiscal_year:
		return None

	# Try to get targets from Sales Person target details
	targets = frappe.db.sql(
		"""
		SELECT SUM(td.target_amount) AS total
		FROM `tabTarget Detail` td
		WHERE td.parenttype = 'Sales Person'
		  AND td.fiscal_year = %s
		""",
		(fiscal_year,),
		as_dict=True,
	)

	annual_target = flt(targets[0].total) if targets and targets[0].total else 0

	if not annual_target:
		return None

	# Prorate based on period
	if period == "month":
		return flt(annual_target / 12, 2)
	elif period == "quarter":
		return flt(annual_target / 4, 2)
	else:
		return flt(annual_target, 2)
