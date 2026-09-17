# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Customer segmentation by revenue value.

Divides customers into High / Medium / Low value tiers based on their total
submitted Sales Order revenue. The thresholds are derived from the data itself
using percentiles (top 20% = High, next 30% = Medium, bottom 50% = Low), so they
adapt to the company's scale without configuration.

Each customer gets a segment, their total revenue, order count, last order date,
and days since last order — everything a salesperson needs to decide who to call.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate, date_diff


def segment_customers(
	company: str | None = None,
	period_months: int = 12,
	limit: int = 50,
) -> dict:
	"""Segment customers into High/Medium/Low value tiers."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	from frappe.utils import add_months
	from_date = add_months(today, -period_months)

	customers = frappe.db.sql(
		"""
		SELECT so.customer, so.customer_name,
		       SUM(so.base_grand_total) AS total_revenue,
		       COUNT(*) AS order_count,
		       MAX(so.transaction_date) AS last_order,
		       AVG(so.base_grand_total) AS avg_order_value
		FROM `tabSales Order` so
		WHERE so.docstatus = 1
		  AND so.company = %s
		  AND so.transaction_date >= %s
		GROUP BY so.customer, so.customer_name
		ORDER BY total_revenue DESC
		""",
		(company, from_date),
		as_dict=True,
	)

	if not customers:
		return {"segments": [], "total_customers": 0, "message": "No customers with orders found."}

	# Calculate percentile thresholds from the data
	revenues = sorted([flt(c.total_revenue) for c in customers], reverse=True)
	n = len(revenues)

	high_threshold = revenues[max(0, int(n * 0.2) - 1)] if n >= 5 else revenues[0]
	medium_threshold = revenues[max(0, int(n * 0.5) - 1)] if n >= 5 else revenues[-1]

	total_revenue = sum(revenues)
	high_count = medium_count = low_count = 0
	high_revenue = medium_revenue = low_revenue = 0

	result = []
	for c in customers:
		rev = flt(c.total_revenue)
		if rev >= high_threshold:
			segment = "High"
			high_count += 1
			high_revenue += rev
		elif rev >= medium_threshold:
			segment = "Medium"
			medium_count += 1
			medium_revenue += rev
		else:
			segment = "Low"
			low_count += 1
			low_revenue += rev

		days_since = date_diff(today, c.last_order) if c.last_order else None

		result.append({
			"customer": c.customer,
			"customer_name": c.customer_name,
			"segment": segment,
			"total_revenue": flt(rev, 2),
			"order_count": c.order_count,
			"avg_order_value": flt(c.avg_order_value, 2),
			"last_order": str(c.last_order) if c.last_order else None,
			"days_since_last_order": days_since,
			"at_risk": days_since is not None and days_since > 90,
		})

	return {
		"segments": result[:limit],
		"total_customers": n,
		"summary": {
			"high": {"count": high_count, "revenue": flt(high_revenue, 2), "pct_of_revenue": flt(high_revenue / total_revenue * 100, 1) if total_revenue else 0},
			"medium": {"count": medium_count, "revenue": flt(medium_revenue, 2), "pct_of_revenue": flt(medium_revenue / total_revenue * 100, 1) if total_revenue else 0},
			"low": {"count": low_count, "revenue": flt(low_revenue, 2), "pct_of_revenue": flt(low_revenue / total_revenue * 100, 1) if total_revenue else 0},
		},
		"thresholds": {
			"high_min": flt(high_threshold, 2),
			"medium_min": flt(medium_threshold, 2),
		},
		"period_months": period_months,
		"company": company,
	}
