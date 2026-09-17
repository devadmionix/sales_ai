# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Anomaly detection for sales data.

Detects unusual patterns by comparing recent values against historical averages and
standard deviations. An anomaly is anything more than 2 standard deviations from the
mean — a sudden spike, a sharp drop, or an outlier order.

This is simple statistical detection, not machine learning. It works reliably with
small datasets and is fully explainable: every anomaly says what the normal range is,
what the actual value was, and how far outside normal it falls.
"""

from __future__ import annotations

from math import sqrt

import frappe
from frappe.utils import flt, getdate, nowdate, add_months, get_first_day, get_last_day


THRESHOLD_SIGMAS = 2.0  # How many standard deviations = "anomalous"


def detect_anomalies(
	company: str | None = None,
	months_back: int = 6,
) -> dict:
	"""Detect anomalies in revenue, order volume, customer behaviour, and products."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	from_date = get_first_day(add_months(today, -months_back))

	anomalies = []

	# 1. Monthly revenue anomalies
	_check_revenue_anomalies(company, from_date, today, anomalies)

	# 2. Unusually large or small orders
	_check_order_size_anomalies(company, from_date, today, anomalies)

	# 3. Customer ordering pattern anomalies
	_check_customer_anomalies(company, from_date, today, anomalies)

	# 4. Product volume anomalies
	_check_product_anomalies(company, from_date, today, anomalies)

	# Sort by severity
	anomalies.sort(key=lambda a: abs(a.get("deviation", 0)), reverse=True)

	return {
		"anomalies": anomalies,
		"count": len(anomalies),
		"period_analysed": f"Last {months_back} months",
		"company": company,
	}


def _mean_std(values):
	"""Return (mean, std_dev) for a list of numbers."""
	if len(values) < 2:
		return (values[0] if values else 0, 0)
	m = sum(values) / len(values)
	variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
	return (m, sqrt(variance))


def _check_revenue_anomalies(company, from_date, today, anomalies):
	"""Flag months where revenue is unusually high or low."""
	rows = frappe.db.sql(
		"""
		SELECT DATE_FORMAT(transaction_date, '%%Y-%%m') AS month,
		       SUM(base_grand_total) AS revenue
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date >= %s
		GROUP BY month ORDER BY month
		""",
		(company, from_date),
		as_dict=True,
	)
	if len(rows) < 3:
		return

	values = [flt(r.revenue) for r in rows]
	mean, std = _mean_std(values[:-1])  # compare last month against prior history

	if std > 0:
		last = values[-1]
		z = (last - mean) / std
		if abs(z) >= THRESHOLD_SIGMAS:
			direction = "spike" if z > 0 else "drop"
			anomalies.append({
				"type": "revenue",
				"severity": "high" if abs(z) >= 3 else "medium",
				"description": f"Revenue {direction} in {rows[-1].month}: {flt(last, 0)} vs normal range {flt(mean - THRESHOLD_SIGMAS * std, 0)}–{flt(mean + THRESHOLD_SIGMAS * std, 0)}",
				"actual": flt(last, 2),
				"expected_mean": flt(mean, 2),
				"expected_range": [flt(mean - THRESHOLD_SIGMAS * std, 2), flt(mean + THRESHOLD_SIGMAS * std, 2)],
				"deviation": flt(z, 2),
				"month": rows[-1].month,
			})


def _check_order_size_anomalies(company, from_date, today, anomalies):
	"""Flag individual orders that are unusually large or small."""
	stats = frappe.db.sql(
		"""
		SELECT AVG(base_grand_total) AS mean_val,
		       STDDEV_SAMP(base_grand_total) AS std_val
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s AND transaction_date >= %s
		""",
		(company, from_date),
		as_dict=True,
	)
	if not stats or not stats[0].std_val or flt(stats[0].std_val) == 0:
		return

	mean = flt(stats[0].mean_val)
	std = flt(stats[0].std_val)
	threshold_high = mean + THRESHOLD_SIGMAS * std
	threshold_low = max(0, mean - THRESHOLD_SIGMAS * std)

	# Recent outlier orders (last 30 days)
	outliers = frappe.db.sql(
		"""
		SELECT name, customer_name, base_grand_total, transaction_date
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date >= DATE_SUB(%s, INTERVAL 30 DAY)
		  AND (base_grand_total > %s OR base_grand_total < %s)
		ORDER BY base_grand_total DESC
		LIMIT 5
		""",
		(company, today, threshold_high, threshold_low),
		as_dict=True,
	)
	for o in outliers:
		z = (flt(o.base_grand_total) - mean) / std
		anomalies.append({
			"type": "order_size",
			"severity": "medium",
			"description": f"Order {o.name} ({o.customer_name}) is unusually {'large' if z > 0 else 'small'}: {flt(o.base_grand_total, 0)} vs average {flt(mean, 0)}",
			"actual": flt(o.base_grand_total, 2),
			"expected_mean": flt(mean, 2),
			"deviation": flt(z, 2),
			"record": o.name,
			"doctype": "Sales Order",
		})


def _check_customer_anomalies(company, from_date, today, anomalies):
	"""Flag customers whose recent ordering is very different from their norm."""
	rows = frappe.db.sql(
		"""
		SELECT customer, customer_name,
		       COUNT(*) AS total_orders,
		       SUM(CASE WHEN transaction_date >= DATE_SUB(%s, INTERVAL 30 DAY) THEN 1 ELSE 0 END) AS recent_orders,
		       SUM(CASE WHEN transaction_date < DATE_SUB(%s, INTERVAL 30 DAY) THEN 1 ELSE 0 END) AS older_orders,
		       TIMESTAMPDIFF(MONTH, MIN(transaction_date), %s) AS months_active
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s AND transaction_date >= %s
		GROUP BY customer, customer_name
		HAVING months_active >= 2 AND total_orders >= 4
		""",
		(today, today, today, company, from_date),
		as_dict=True,
	)
	for r in rows:
		if not r.months_active or r.months_active < 2:
			continue
		expected_monthly = r.older_orders / max(r.months_active - 1, 1)
		if expected_monthly > 0:
			ratio = r.recent_orders / expected_monthly
			if ratio >= 3:
				anomalies.append({
					"type": "customer_surge",
					"severity": "medium",
					"description": f"{r.customer_name} placed {r.recent_orders} orders this month vs usual ~{flt(expected_monthly, 1)}/month — a sudden surge.",
					"customer": r.customer,
					"recent_orders": r.recent_orders,
					"expected_monthly": flt(expected_monthly, 1),
				})
			elif ratio == 0 and expected_monthly >= 1:
				anomalies.append({
					"type": "customer_silence",
					"severity": "high",
					"description": f"{r.customer_name} usually orders ~{flt(expected_monthly, 1)}/month but had 0 orders this month.",
					"customer": r.customer,
					"expected_monthly": flt(expected_monthly, 1),
				})


def _check_product_anomalies(company, from_date, today, anomalies):
	"""Flag products with unusual recent sales volume."""
	rows = frappe.db.sql(
		"""
		SELECT soi.item_code, soi.item_name,
		       SUM(soi.qty) AS total_qty,
		       SUM(CASE WHEN so.transaction_date >= DATE_SUB(%s, INTERVAL 30 DAY) THEN soi.qty ELSE 0 END) AS recent_qty,
		       TIMESTAMPDIFF(MONTH, MIN(so.transaction_date), %s) AS months_active
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s AND so.transaction_date >= %s
		GROUP BY soi.item_code, soi.item_name
		HAVING months_active >= 2 AND total_qty >= 10
		""",
		(today, today, company, from_date),
		as_dict=True,
	)
	for r in rows:
		if not r.months_active or r.months_active < 2:
			continue
		older_qty = r.total_qty - r.recent_qty
		expected_monthly = older_qty / max(r.months_active - 1, 1)
		if expected_monthly > 0:
			ratio = r.recent_qty / expected_monthly
			if ratio >= 3:
				anomalies.append({
					"type": "product_surge",
					"severity": "medium",
					"description": f"'{r.item_name}' sold {flt(r.recent_qty, 0)} units this month vs usual ~{flt(expected_monthly, 1)}/month.",
					"item_code": r.item_code,
				})
			elif ratio <= 0.2 and expected_monthly >= 3:
				anomalies.append({
					"type": "product_decline",
					"severity": "medium",
					"description": f"'{r.item_name}' sold only {flt(r.recent_qty, 0)} units this month vs usual ~{flt(expected_monthly, 1)}/month.",
					"item_code": r.item_code,
				})
