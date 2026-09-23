"""Backend KPI calculations for the Sales Dashboard page."""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate, add_days, get_first_day, get_last_day


@frappe.whitelist()
def get_kpis(company: str | None = None, from_date: str | None = None, to_date: str | None = None):
	"""Return all dashboard KPIs for the given filters."""
	from sales_ai.guard.permissions import check_ai_permission
	from sales_ai.guard.ownership import sql_owner_clause, is_owner_restricted

	# Only users with Sales Order read access may view the dashboard.
	result = check_ai_permission(doctype="Sales Order", action="read")
	if not result.allowed:
		frappe.throw("You do not have permission to view the Sales Dashboard.", frappe.PermissionError)

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected"}

	today = getdate(nowdate())
	from_date = getdate(from_date) if from_date else get_first_day(today)
	to_date = getdate(to_date) if to_date else get_last_day(today)

	return {
		"revenue": _revenue(company, from_date, to_date),
		"target": _target(company, from_date, to_date),
		"pipeline": _pipeline(company),
		"won_lost": _won_lost(company, from_date, to_date),
		"conversion_rate": _conversion_rate(company, from_date, to_date),
		"aov": _aov(company, from_date, to_date),
		"sales_cycle": _sales_cycle(company, from_date, to_date),
		"top_items": _top_items(company, from_date, to_date),
		"monthly_trend": _monthly_trend(company, from_date, to_date),
	}


def _revenue(company, from_date, to_date):
	"""Total revenue from submitted Sales Orders in the period."""
	owner_sql, owner_params = sql_owner_clause("Sales Order")
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS total,
		       COUNT(*) AS count,
		       COALESCE(SUM(base_grand_total), 0) / NULLIF(COUNT(*), 0) AS avg_value
		FROM `tabSales Order`
		WHERE docstatus = 1
		  AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		{owner_sql}
		""",
		(company, from_date, to_date, *owner_params),
		as_dict=True,
	)
	row = result[0] if result else {}
	return {
		"total": flt(row.get("total"), 2),
		"count": row.get("count", 0),
		"avg_value": flt(row.get("avg_value"), 2),
	}


def _target(company, from_date, to_date):
	"""Sales target from Monthly Distribution or Target Detail."""
	# Sales targets are not owner-based Frappe records.  For an owner-scoped user, do
	# not expose company-wide target totals until a Sales Person → User mapping is present.
	if is_owner_restricted(frappe.session.user, "Sales Order"):
		return {"amount": 0, "fiscal_year": "", "scope": "own"}

	# Try to get target from Sales Person Target Detail
	target_amount = 0

	# Check if there's a target in the current fiscal year
	fiscal_year = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ("<=", from_date), "year_end_date": (">=", to_date)},
		"name",
	)

	if fiscal_year:
		targets = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(td.target_amount), 0) AS total
			FROM `tabTarget Detail` td
			WHERE td.fiscal_year = %s
			  AND td.parenttype = 'Sales Person'
			""",
			(fiscal_year,),
			as_dict=True,
		)
		if targets:
			yearly_target = flt(targets[0].get("total"))
			# Prorate target to the selected period
			fy_start = frappe.db.get_value("Fiscal Year", fiscal_year, "year_start_date")
			fy_end = frappe.db.get_value("Fiscal Year", fiscal_year, "year_end_date")
			if fy_start and fy_end:
				total_days = (getdate(fy_end) - getdate(fy_start)).days + 1
				period_days = (getdate(to_date) - getdate(from_date)).days + 1
				target_amount = flt(yearly_target * period_days / total_days, 2) if total_days else 0

	return {"amount": target_amount, "fiscal_year": fiscal_year or ""}


def _pipeline(company):
	"""Open opportunity pipeline: total and weighted (amount × probability)."""
	owner_sql, owner_params = sql_owner_clause("Opportunity")
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(opportunity_amount), 0) AS total,
		       COALESCE(SUM(opportunity_amount * COALESCE(probability, 0) / 100), 0) AS weighted,
		       COUNT(*) AS count
		FROM `tabOpportunity`
		WHERE status NOT IN ('Lost', 'Closed')
		  AND company = %s
		  AND docstatus < 2
		{owner_sql}
		""",
		(company, *owner_params),
		as_dict=True,
	)
	row = result[0] if result else {}
	return {
		"total": flt(row.get("total"), 2),
		"weighted": flt(row.get("weighted"), 2),
		"count": row.get("count", 0),
	}


def _won_lost(company, from_date, to_date):
	"""Won and Lost opportunity counts and values in the period."""
	owner_sql, owner_params = sql_owner_clause("Opportunity")
	result = frappe.db.sql(
		f"""
		SELECT status,
		       COUNT(*) AS count,
		       COALESCE(SUM(opportunity_amount), 0) AS amount
		FROM `tabOpportunity`
		WHERE status IN ('Converted', 'Lost')
		  AND company = %s
		  AND modified BETWEEN %s AND %s
		{owner_sql}
		GROUP BY status
		""",
		(company, from_date, to_date, *owner_params),
		as_dict=True,
	)
	won = next((r for r in result if r.status == "Converted"), {})
	lost = next((r for r in result if r.status == "Lost"), {})
	return {
		"won_count": won.get("count", 0),
		"won_amount": flt(won.get("amount"), 2),
		"lost_count": lost.get("count", 0),
		"lost_amount": flt(lost.get("amount"), 2),
	}


def _conversion_rate(company, from_date, to_date):
	"""Opportunity to Sales Order conversion rate in the period."""
	from sales_ai.guard.ownership import owned_condition
	base_filters = {
		"company": company,
		"creation": ("between", [from_date, to_date]),
		"docstatus": ("<", 2),
	}
	for field, operator, value in owned_condition("Opportunity"):
		base_filters[field] = value
	total = frappe.db.count("Opportunity", filters=base_filters)
	converted_filters = dict(base_filters)
	converted_filters["status"] = "Converted"
	converted = frappe.db.count("Opportunity", filters=converted_filters)
	rate = flt(converted * 100 / total, 1) if total else 0
	return {"rate": rate, "converted": converted, "total": total}


def _aov(company, from_date, to_date):
	"""Average Order Value from submitted Sales Orders."""
	owner_sql, owner_params = sql_owner_clause("Sales Order")
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(AVG(base_grand_total), 0) AS aov,
		       COUNT(*) AS count
		FROM `tabSales Order`
		WHERE docstatus = 1
		  AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		{owner_sql}
		""",
		(company, from_date, to_date, *owner_params),
		as_dict=True,
	)
	row = result[0] if result else {}
	return {"value": flt(row.get("aov"), 2), "order_count": row.get("count", 0)}


def _sales_cycle(company, from_date, to_date):
	"""Median sales cycle in days (Opportunity creation → Converted)."""
	owner_sql, owner_params = sql_owner_clause("Opportunity")
	rows = frappe.db.sql(
		f"""
		SELECT DATEDIFF(modified, creation) AS cycle_days
		FROM `tabOpportunity`
		WHERE status = 'Converted'
		  AND company = %s
		  AND modified BETWEEN %s AND %s
		{owner_sql}
		ORDER BY cycle_days
		""",
		(company, from_date, to_date, *owner_params),
		as_list=True,
	)
	if not rows:
		return {"median_days": 0, "count": 0}

	days_list = [r[0] for r in rows]
	n = len(days_list)
	mid = n // 2
	median = days_list[mid] if n % 2 else (days_list[mid - 1] + days_list[mid]) / 2

	return {"median_days": flt(median, 1), "count": n}


def _top_items(company, from_date, to_date, limit=5):
	"""Top selling items by revenue in the period."""
	owner_sql, owner_params = sql_owner_clause("Sales Order", "so")
	return frappe.db.sql(
		f"""
		SELECT soi.item_code, soi.item_name,
		       SUM(soi.base_amount) AS revenue,
		       SUM(soi.qty) AS qty
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1
		  AND so.company = %s
		  AND so.transaction_date BETWEEN %s AND %s
		{owner_sql}
		GROUP BY soi.item_code, soi.item_name
		ORDER BY revenue DESC
		LIMIT %s
		""",
		(company, from_date, to_date, *owner_params, limit),
		as_dict=True,
	)


def _monthly_trend(company, from_date, to_date):
	"""Monthly revenue trend from submitted Sales Orders."""
	owner_sql, owner_params = sql_owner_clause("Sales Order")
	return frappe.db.sql(
		f"""
		SELECT DATE_FORMAT(transaction_date, '%%Y-%%m') AS month,
		       COALESCE(SUM(base_grand_total), 0) AS revenue,
		       COUNT(*) AS count
		FROM `tabSales Order`
		WHERE docstatus = 1
		  AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		{owner_sql}
		GROUP BY DATE_FORMAT(transaction_date, '%%Y-%%m')
		ORDER BY month
		""",
		(company, from_date, to_date, *owner_params),
		as_dict=True,
	)
