# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Product intelligence: cross-sell, upsell, repeat purchase detection, and product performance.

Analyses customer purchase history to find patterns:
- Cross-sell: products frequently bought together by other customers
- Upsell: higher-value alternatives to what a customer already buys
- Repeat purchase: customers whose reorder interval has elapsed
- Product performance: rank products by revenue, volume, growth, and customer reach
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate, add_months, date_diff


def cross_sell(customer: str, company: str | None = None, limit: int = 5) -> dict:
	"""Suggest products this customer hasn't bought but similar customers have."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	# What has this customer already bought?
	bought = frappe.db.sql(
		"""
		SELECT DISTINCT soi.item_code
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s AND so.customer = %s
		""",
		(company, customer),
		pluck="item_code",
	)

	if not bought:
		return {
			"suggestions": [],
			"customer": customer,
			"message": "No purchase history — cannot recommend products without evidence.",
		}

	# Find other customers who bought the same items
	placeholders = ", ".join(["%s"] * len(bought))
	peers_with_overlap = frappe.db.sql(
		f"""
		SELECT DISTINCT so.customer
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s
		  AND so.customer != %s
		  AND soi.item_code IN ({placeholders})
		LIMIT 50
		""",
		[company, customer] + bought,
		pluck="customer",
	)

	if not peers_with_overlap:
		return {
			"suggestions": [],
			"customer": customer,
			"message": "No similar customers found to base recommendations on.",
		}

	# What did those peers buy that this customer hasn't?
	peer_placeholders = ", ".join(["%s"] * len(peers_with_overlap))
	bought_placeholders = ", ".join(["%s"] * len(bought))
	suggestions = frappe.db.sql(
		f"""
		SELECT soi.item_code, soi.item_name,
		       COUNT(DISTINCT so.customer) AS peer_count,
		       SUM(soi.qty) AS total_qty,
		       SUM(soi.base_amount) AS total_revenue
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s
		  AND so.customer IN ({peer_placeholders})
		  AND soi.item_code NOT IN ({bought_placeholders})
		GROUP BY soi.item_code, soi.item_name
		HAVING peer_count >= 2
		ORDER BY peer_count DESC, total_revenue DESC
		LIMIT %s
		""",
		[company] + peers_with_overlap + bought + [limit],
		as_dict=True,
	)

	# Filter out disabled items
	active_suggestions = []
	for s in suggestions:
		disabled = frappe.db.get_value("Item", s.item_code, "disabled")
		if not disabled:
			active_suggestions.append({
				"item_code": s.item_code,
				"item_name": s.item_name,
				"bought_by_peers": s.peer_count,
				"evidence": f"{s.peer_count} similar customers bought this item",
				"peer_total_qty": flt(s.total_qty, 0),
				"peer_total_revenue": flt(s.total_revenue, 2),
			})

	return {
		"suggestions": active_suggestions,
		"customer": customer,
		"based_on": f"{len(bought)} products purchased, {len(peers_with_overlap)} similar customers analysed",
	}


def upsell(customer: str, company: str | None = None, limit: int = 5) -> dict:
	"""Suggest higher-value alternatives for products this customer buys."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	# Customer's current purchases with their item groups
	purchases = frappe.db.sql(
		"""
		SELECT soi.item_code, soi.item_name, i.item_group,
		       AVG(soi.rate) AS avg_rate,
		       SUM(soi.qty) AS total_qty
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		JOIN `tabItem` i ON i.name = soi.item_code
		WHERE so.docstatus = 1 AND so.company = %s AND so.customer = %s
		GROUP BY soi.item_code, soi.item_name, i.item_group
		ORDER BY total_qty DESC
		LIMIT 20
		""",
		(company, customer),
		as_dict=True,
	)

	if not purchases:
		return {
			"suggestions": [],
			"customer": customer,
			"message": "No purchase history for upsell analysis.",
		}

	suggestions = []
	seen_items = {p.item_code for p in purchases}

	for p in purchases:
		# Find higher-value items in the same group
		upgrades = frappe.db.sql(
			"""
			SELECT soi.item_code, soi.item_name,
			       AVG(soi.rate) AS avg_rate,
			       COUNT(DISTINCT so.customer) AS customer_count
			FROM `tabSales Order Item` soi
			JOIN `tabSales Order` so ON so.name = soi.parent
			JOIN `tabItem` i ON i.name = soi.item_code
			WHERE so.docstatus = 1 AND so.company = %s
			  AND i.item_group = %s
			  AND soi.item_code != %s
			  AND i.disabled = 0
			GROUP BY soi.item_code, soi.item_name
			HAVING avg_rate > %s
			ORDER BY customer_count DESC
			LIMIT 3
			""",
			(company, p.item_group, p.item_code, flt(p.avg_rate)),
			as_dict=True,
		)

		for u in upgrades:
			if u.item_code not in seen_items:
				seen_items.add(u.item_code)
				suggestions.append({
					"item_code": u.item_code,
					"item_name": u.item_name,
					"replaces": p.item_code,
					"replaces_name": p.item_name,
					"current_avg_rate": flt(p.avg_rate, 2),
					"upgrade_avg_rate": flt(u.avg_rate, 2),
					"uplift_pct": flt((u.avg_rate - p.avg_rate) / p.avg_rate * 100, 1) if p.avg_rate else 0,
					"other_customers": u.customer_count,
					"evidence": f"Higher-value alternative in {p.item_group}, bought by {u.customer_count} other customers",
				})

	suggestions.sort(key=lambda s: s["uplift_pct"], reverse=True)

	return {
		"suggestions": suggestions[:limit],
		"customer": customer,
	}


def repeat_purchase_due(company: str | None = None, limit: int = 20) -> dict:
	"""Find customers whose typical reorder interval has elapsed."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())

	# Find customers with 3+ orders and calculate their average interval
	customers = frappe.db.sql(
		"""
		SELECT customer, customer_name,
		       COUNT(*) AS order_count,
		       MIN(transaction_date) AS first_order,
		       MAX(transaction_date) AS last_order,
		       DATEDIFF(MAX(transaction_date), MIN(transaction_date)) / (COUNT(*) - 1) AS avg_interval_days
		FROM `tabSales Order`
		WHERE docstatus = 1 AND company = %s
		GROUP BY customer, customer_name
		HAVING order_count >= 3 AND avg_interval_days > 0
		ORDER BY avg_interval_days ASC
		""",
		(company,),
		as_dict=True,
	)

	due = []
	for c in customers:
		days_since = date_diff(today, c.last_order)
		interval = flt(c.avg_interval_days)
		if interval > 0 and days_since >= interval * 0.9:  # Due or overdue
			overdue_days = max(0, days_since - int(interval))
			due.append({
				"customer": c.customer,
				"customer_name": c.customer_name,
				"avg_interval_days": int(interval),
				"days_since_last_order": days_since,
				"overdue_days": overdue_days,
				"last_order": str(c.last_order),
				"order_count": c.order_count,
				"urgency": "overdue" if overdue_days > interval * 0.5 else "due" if overdue_days > 0 else "approaching",
			})

	due.sort(key=lambda d: d["overdue_days"], reverse=True)

	return {
		"customers": due[:limit],
		"count": len(due),
		"company": company,
	}


def product_performance(
	company: str | None = None,
	period_months: int = 6,
	limit: int = 20,
) -> dict:
	"""Rank products by revenue, volume, growth, and customer reach."""

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		return {"error": "No company selected."}

	today = getdate(nowdate())
	from_date = add_months(today, -period_months)
	mid_date = add_months(today, -(period_months // 2))

	products = frappe.db.sql(
		"""
		SELECT soi.item_code, soi.item_name,
		       SUM(soi.base_amount) AS total_revenue,
		       SUM(soi.qty) AS total_qty,
		       COUNT(DISTINCT so.customer) AS customer_count,
		       COUNT(DISTINCT so.name) AS order_count,
		       SUM(CASE WHEN so.transaction_date >= %s THEN soi.base_amount ELSE 0 END) AS recent_revenue,
		       SUM(CASE WHEN so.transaction_date < %s THEN soi.base_amount ELSE 0 END) AS older_revenue
		FROM `tabSales Order Item` soi
		JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1 AND so.company = %s
		  AND so.transaction_date >= %s
		GROUP BY soi.item_code, soi.item_name
		ORDER BY total_revenue DESC
		LIMIT %s
		""",
		(mid_date, mid_date, company, from_date, limit),
		as_dict=True,
	)

	total_revenue = sum(flt(p.total_revenue) for p in products) or 1

	result = []
	for p in products:
		rev = flt(p.total_revenue)
		recent = flt(p.recent_revenue)
		older = flt(p.older_revenue)
		growth = flt((recent - older) / older * 100, 1) if older > 0 else (100 if recent > 0 else 0)

		result.append({
			"item_code": p.item_code,
			"item_name": p.item_name,
			"total_revenue": flt(rev, 2),
			"revenue_share_pct": flt(rev / total_revenue * 100, 1),
			"total_qty": flt(p.total_qty, 0),
			"customer_count": p.customer_count,
			"order_count": p.order_count,
			"avg_order_value": flt(rev / p.order_count, 2) if p.order_count else 0,
			"growth_pct": growth,
			"trend": "growing" if growth > 10 else "declining" if growth < -10 else "stable",
		})

	return {
		"products": result,
		"period_months": period_months,
		"company": company,
	}
