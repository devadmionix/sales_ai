# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Revenue forecasting using simple linear trend extrapolation.

This is arithmetic, not AI. It fits a line to monthly revenue totals and projects
forward. The forecast carries its confidence (R² of the fit) so the agent can say
"based on a strong/weak trend" rather than presenting every projection as gospel.

A forecast with fewer than 3 data points is refused outright — two months cannot
distinguish a trend from noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

import frappe
from frappe.utils import flt, getdate, nowdate, add_months, get_first_day, get_last_day


MIN_MONTHS = 3


@dataclass
class Forecast:
	"""A revenue forecast with its basis."""
	monthly_actuals: list[dict]      # [{month, revenue}]
	forecast_months: list[dict]      # [{month, projected_revenue}]
	trend_direction: str             # "growing", "declining", "flat"
	monthly_growth_rate: float       # average month-over-month % change
	confidence: float                # R² of linear fit, 0–1
	basis_months: int                # how many months of data used


def forecast_revenue(
	company: str | None = None,
	months_back: int = 6,
	months_forward: int = 3,
) -> dict:
	"""Compute a revenue forecast from submitted Sales Order history."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	from_date = get_first_day(add_months(today, -months_back))
	to_date = get_last_day(today)

	rows = frappe.db.sql(
		"""
		SELECT DATE_FORMAT(transaction_date, '%%Y-%%m') AS month,
		       COALESCE(SUM(base_grand_total), 0) AS revenue
		FROM `tabSales Order`
		WHERE docstatus = 1
		  AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		GROUP BY DATE_FORMAT(transaction_date, '%%Y-%%m')
		ORDER BY month
		""",
		(company, from_date, to_date),
		as_dict=True,
	)

	if len(rows) < MIN_MONTHS:
		return {
			"error": f"Only {len(rows)} months of data. Need at least {MIN_MONTHS} to forecast.",
			"monthly_actuals": [{"month": r.month, "revenue": flt(r.revenue, 2)} for r in rows],
		}

	# Simple linear regression: y = a + b*x where x is month index
	revenues = [flt(r.revenue) for r in rows]
	n = len(revenues)
	xs = list(range(n))

	x_mean = mean(xs)
	y_mean = mean(revenues)

	numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, revenues))
	denominator = sum((x - x_mean) ** 2 for x in xs)

	if denominator == 0:
		slope = 0
		intercept = y_mean
	else:
		slope = numerator / denominator
		intercept = y_mean - slope * x_mean

	# R² confidence
	ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, revenues))
	ss_tot = sum((y - y_mean) ** 2 for y in revenues)
	r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

	# Project forward
	forecast_months = []
	last_month_date = getdate(rows[-1].month + "-01")
	for i in range(1, months_forward + 1):
		future_date = add_months(last_month_date, i)
		projected = max(0, intercept + slope * (n - 1 + i))
		forecast_months.append({
			"month": future_date.strftime("%Y-%m"),
			"projected_revenue": flt(projected, 2),
		})

	# Growth rate
	if len(revenues) >= 2 and revenues[0] > 0:
		growth_rates = []
		for i in range(1, len(revenues)):
			if revenues[i - 1] > 0:
				growth_rates.append((revenues[i] - revenues[i - 1]) / revenues[i - 1] * 100)
		monthly_growth_rate = flt(mean(growth_rates), 1) if growth_rates else 0
	else:
		monthly_growth_rate = 0

	if slope > 0 and monthly_growth_rate > 2:
		trend = "growing"
	elif slope < 0 and monthly_growth_rate < -2:
		trend = "declining"
	else:
		trend = "flat"

	return {
		"monthly_actuals": [{"month": r.month, "revenue": flt(r.revenue, 2)} for r in rows],
		"forecast_months": forecast_months,
		"trend_direction": trend,
		"monthly_growth_rate": monthly_growth_rate,
		"confidence": flt(r_squared, 3),
		"basis_months": n,
		"company": company,
	}
