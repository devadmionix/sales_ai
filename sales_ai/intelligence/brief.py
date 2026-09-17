# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Sales day brief and manager brief.

Morning review: hot leads, expiring quotations, due follow-ups, at-risk customers,
pipeline milestones, and deal priorities — everything a salesperson needs to start
their day.

Manager brief: team-wide KPIs, individual performance, pipeline health, and risks.
"""

from __future__ import annotations

import frappe
from frappe.utils import (
	flt, getdate, nowdate, add_days, add_months,
	get_first_day, get_last_day, date_diff,
)


def sales_day_brief(company: str | None = None) -> dict:
	"""Generate a morning sales brief: what needs attention today."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	user = frappe.session.user
	sections = {}

	# 1. Follow-ups due today or overdue
	todos = frappe.db.sql(
		"""
		SELECT name, description, reference_type, reference_name, date, allocated_to
		FROM `tabToDo`
		WHERE status = 'Open'
		  AND (allocated_to = %s OR assigned_by = %s)
		  AND date <= %s
		ORDER BY date ASC
		LIMIT 10
		""",
		(user, user, today),
		as_dict=True,
	)
	sections["follow_ups_due"] = [{
		"name": t.name,
		"description": (t.description or "")[:120],
		"reference_type": t.reference_type,
		"reference_name": t.reference_name,
		"due_date": str(t.date) if t.date else None,
		"overdue": t.date and getdate(t.date) < today,
	} for t in todos]

	# 2. Quotations expiring soon (next 7 days)
	expiring = frappe.db.sql(
		"""
		SELECT name, party_name, base_grand_total, valid_till, currency
		FROM `tabQuotation`
		WHERE docstatus = 1 AND company = %s
		  AND valid_till BETWEEN %s AND %s
		  AND status NOT IN ('Ordered', 'Lost', 'Cancelled')
		ORDER BY valid_till ASC
		LIMIT 10
		""",
		(company, today, add_days(today, 7)),
		as_dict=True,
	)
	sections["expiring_quotations"] = [{
		"name": q.name,
		"customer": q.party_name,
		"amount": flt(q.base_grand_total, 2),
		"expires": str(q.valid_till),
		"days_left": date_diff(q.valid_till, today),
		"currency": q.currency,
	} for q in expiring]

	# 3. Deals closing this week
	week_end = add_days(today, 7)
	closing_deals = frappe.db.sql(
		"""
		SELECT name, party_name, opportunity_amount, expected_closing, sales_stage, probability
		FROM `tabOpportunity`
		WHERE company = %s AND status NOT IN ('Lost', 'Closed')
		  AND docstatus < 2
		  AND expected_closing BETWEEN %s AND %s
		ORDER BY opportunity_amount DESC
		LIMIT 10
		""",
		(company, today, week_end),
		as_dict=True,
	)
	sections["deals_closing_soon"] = [{
		"name": d.name,
		"party_name": d.party_name,
		"amount": flt(d.opportunity_amount, 2),
		"expected_closing": str(d.expected_closing),
		"days_left": date_diff(d.expected_closing, today),
		"sales_stage": d.sales_stage,
		"probability": flt(d.probability),
	} for d in closing_deals]

	# 4. Overdue opportunities (expected_closing already passed)
	overdue = frappe.db.sql(
		"""
		SELECT name, party_name, opportunity_amount, expected_closing, sales_stage
		FROM `tabOpportunity`
		WHERE company = %s AND status NOT IN ('Lost', 'Closed', 'Converted')
		  AND docstatus < 2
		  AND expected_closing < %s
		ORDER BY opportunity_amount DESC
		LIMIT 10
		""",
		(company, today),
		as_dict=True,
	)
	sections["overdue_deals"] = [{
		"name": o.name,
		"party_name": o.party_name,
		"amount": flt(o.opportunity_amount, 2),
		"expected_closing": str(o.expected_closing),
		"days_overdue": date_diff(today, o.expected_closing),
		"sales_stage": o.sales_stage,
	} for o in overdue]

	# 5. New leads (last 3 days)
	new_leads = frappe.db.sql(
		"""
		SELECT name, lead_name, company_name, utm_source, territory
		FROM `tabLead`
		WHERE creation >= %s AND docstatus < 2
		ORDER BY creation DESC
		LIMIT 10
		""",
		(add_days(today, -3),),
		as_dict=True,
	)
	sections["new_leads"] = [{
		"name": l.name,
		"lead_name": l.lead_name,
		"company_name": l.company_name,
		"source": l.utm_source,
		"territory": l.territory,
	} for l in new_leads]

	# 6. Quick KPI snapshot — this month so far
	month_start = get_first_day(today)
	revenue = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS revenue,
		       COUNT(*) AS order_count
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		""",
		(company, month_start, today),
		as_dict=True,
	)[0]

	pipeline = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(opportunity_amount), 0) AS value,
		       COUNT(*) AS count
		FROM `tabOpportunity`
		WHERE company = %s AND status NOT IN ('Lost', 'Closed')
		  AND docstatus < 2
		""",
		(company,),
		as_dict=True,
	)[0]

	sections["kpi_snapshot"] = {
		"revenue_this_month": flt(revenue.revenue, 2),
		"orders_this_month": revenue.order_count or 0,
		"open_pipeline_value": flt(pipeline.value, 2),
		"open_pipeline_count": pipeline.count or 0,
	}

	# Count urgent items
	urgent_count = (
		len([t for t in sections["follow_ups_due"] if t.get("overdue")])
		+ len(sections["expiring_quotations"])
		+ len(sections["overdue_deals"])
	)

	return {
		"date": str(today),
		"user": user,
		"company": company,
		"urgent_items": urgent_count,
		**sections,
	}


def manager_brief(company: str | None = None, period_months: int = 1) -> dict:
	"""Generate a manager's overview: team performance, pipeline health, risks."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	from_date = get_first_day(add_months(today, -(period_months - 1)))

	# Team performance — by sales person
	team = frappe.db.sql(
		"""
		SELECT st.sales_person,
		       SUM(so.base_grand_total * st.allocated_percentage / 100) AS revenue,
		       COUNT(DISTINCT so.name) AS order_count
		FROM `tabSales Team` st
		JOIN `tabSales Order` so ON so.name = st.parent
		WHERE so.docstatus = 1 AND so.company = %s
		  AND so.transaction_date BETWEEN %s AND %s
		  AND st.parenttype = 'Sales Order'
		GROUP BY st.sales_person
		ORDER BY revenue DESC
		""",
		(company, from_date, today),
		as_dict=True,
	)

	# Pipeline by sales person
	pipeline_by_person = frappe.db.sql(
		"""
		SELECT opportunity_owner AS sales_person,
		       COUNT(*) AS deal_count,
		       SUM(opportunity_amount) AS pipeline_value
		FROM `tabOpportunity`
		WHERE company = %s AND status NOT IN ('Lost', 'Closed')
		  AND docstatus < 2
		GROUP BY opportunity_owner
		ORDER BY pipeline_value DESC
		""",
		(company,),
		as_dict=True,
	)

	# Overall KPIs
	total_revenue = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total), 0) AS revenue,
		       COUNT(*) AS orders,
		       COALESCE(AVG(base_grand_total), 0) AS aov
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		  AND transaction_date BETWEEN %s AND %s
		""",
		(company, from_date, today),
		as_dict=True,
	)[0]

	# Conversion rate
	total_quotes = frappe.db.count("Quotation", {
		"docstatus": 1, "company": company,
		"transaction_date": ("between", [from_date, today]),
	})
	ordered_quotes = frappe.db.count("Quotation", {
		"docstatus": 1, "company": company, "status": "Ordered",
		"transaction_date": ("between", [from_date, today]),
	})
	conversion_rate = flt(ordered_quotes / total_quotes * 100, 1) if total_quotes else 0

	# Stale deals (no activity in 14+ days)
	stale_deals = frappe.db.sql(
		"""
		SELECT name, party_name, opportunity_amount, expected_closing,
		       DATEDIFF(%s, modified) AS days_stale
		FROM `tabOpportunity`
		WHERE company = %s AND status NOT IN ('Lost', 'Closed', 'Converted')
		  AND docstatus < 2
		  AND DATEDIFF(%s, modified) > 14
		ORDER BY opportunity_amount DESC
		LIMIT 10
		""",
		(today, company, today),
		as_dict=True,
	)

	# At-risk customers (from Sales AI Insight)
	at_risk = frappe.db.sql(
		"""
		SELECT subject_name AS customer, value AS risk_score, summary
		FROM `tabSales AI Insight`
		WHERE kind = 'Churn' AND value >= 60
		ORDER BY value DESC
		LIMIT 10
		""",
		as_dict=True,
	)

	return {
		"period": f"Last {period_months} month(s)",
		"company": company,
		"kpis": {
			"revenue": flt(total_revenue.revenue, 2),
			"orders": total_revenue.orders or 0,
			"aov": flt(total_revenue.aov, 2),
			"conversion_rate": conversion_rate,
			"quotes_sent": total_quotes or 0,
		},
		"team_performance": [{
			"sales_person": t.sales_person,
			"revenue": flt(t.revenue, 2),
			"order_count": t.order_count,
		} for t in team],
		"pipeline_by_person": [{
			"sales_person": p.sales_person,
			"deal_count": p.deal_count,
			"pipeline_value": flt(p.pipeline_value, 2),
		} for p in pipeline_by_person],
		"stale_deals": [{
			"name": d.name,
			"party_name": d.party_name,
			"amount": flt(d.opportunity_amount, 2),
			"days_stale": d.days_stale,
		} for d in stale_deals],
		"at_risk_customers": [{
			"customer": r.customer,
			"risk_score": flt(r.risk_score),
			"summary": r.summary,
		} for r in at_risk],
	}
