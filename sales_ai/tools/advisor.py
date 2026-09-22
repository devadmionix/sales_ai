# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tools that give the agent forecasting, lead scoring and business advice capabilities.

Each tool calls into the intelligence layer, which does deterministic arithmetic on real
data. The agent quotes the results — it did not produce them, so they are evidence-based.
"""

from __future__ import annotations

from typing import Annotated, Any

from sales_ai.guard import GuardError
from sales_ai.guard.permissions import check_ai_permission
from sales_ai.llm.tool import tool
from sales_ai.tools import register


def _require_intelligence_access() -> None:
	"""Gate: the user must have report permission on at least Sales Order to use intelligence tools.

	Intelligence tools aggregate data from Sales Orders, Quotations, Opportunities, Leads,
	and Customers. Rather than checking every DocType individually, we require report access
	on Sales Order — the core DocType these analytics are built around. If the RBAC matrix
	says no, the tool refuses before any SQL runs.
	"""
	result = check_ai_permission(doctype="Sales Order", action="report")
	if not result.allowed:
		raise GuardError(
			"You do not have permission to access sales intelligence tools."
		)


@tool(
	writes=False,
	risk="none",
	description="""Forecast future revenue based on historical Sales Order trends.

Uses the last N months of submitted Sales Orders to project revenue for the next few months
using linear trend extrapolation. Returns the trend direction (growing/declining/flat),
monthly growth rate, projected figures, and a confidence score (0–1) showing how reliable
the trend is.

Needs at least 3 months of data to produce a forecast. If there isn't enough history, it
says so. The projection is arithmetic (a straight line through the data), not a promise —
always mention the confidence score when quoting the forecast.""",
)
def forecast_revenue(
	months_back: Annotated[int, "How many months of history to analyse. Default 6."] = 6,
	months_forward: Annotated[int, "How many months to project forward. Default 3."] = 3,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.forecast import forecast_revenue as _forecast
	return _forecast(months_back=months_back, months_forward=months_forward)


@tool(
	writes=False,
	risk="none",
	description="""Score open leads by their likelihood to convert, based on historical patterns.

Looks at which lead sources, territories and industries have converted best in the past,
then scores each open lead 0–100 against those patterns. Newer leads score higher because
they haven't gone cold yet.

Each lead comes back with its score, a band (High/Medium/Low), and the factors that drove
the score — quote those factors so the user knows *why* a lead ranks where it does.

This is pattern matching on the company's own conversion history, not a generic model.""",
)
def score_leads(
	limit: Annotated[int, "How many leads to return. Default 20."] = 20,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.lead_score import score_leads as _score
	return _score(limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Get actionable business recommendations based on current data.

Analyses the business right now and returns prioritised next steps. Each recommendation is
backed by a specific data signal — expiring quotations, stale deals, at-risk customers,
revenue trends, idle leads, thin pipeline, top products, or inactive customers.

Present these as a prioritised action list. Each item has a priority (critical/high/medium/low),
what the data says, and a concrete next step the business owner should take.

This is the tool to call when someone asks "what should I do next?" or "how do I grow?".""",
)
def get_recommendations() -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.advisor import get_recommendations as _recs
	return _recs()


@tool(
	writes=False,
	risk="none",
	description="""Segment customers into High / Medium / Low value tiers based on revenue.

Ranks all customers by their total submitted Sales Order revenue over a period (default 12
months) and divides them into three tiers:
  - High: top 20% of customers by revenue
  - Medium: next 30%
  - Low: bottom 50%

Each customer shows their segment, total revenue, order count, average order value, last
order date, and whether they are at risk (no order in 90+ days).

Returns a summary showing how much revenue each tier contributes — typically, High-value
customers generate the majority of revenue.

Use this when the user asks about customer value, customer importance, who their best
customers are, or customer segmentation.""",
)
def segment_customers(
	period_months: Annotated[int, "How many months of history to consider. Default 12."] = 12,
	limit: Annotated[int, "How many customers to return. Default 50."] = 50,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.segments import segment_customers as _segment
	return _segment(period_months=period_months, limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Compare sales performance between the current and previous period.

Compares revenue, order count, average order value, new customers, pipeline, and quotations
between two periods. Use period="month" for this month vs last, "quarter" for this quarter
vs last, or "year" for this year vs last.

For each metric, returns: current value, previous value, absolute change, percentage change,
and direction (up/down/flat).

Also identifies the top customers and products that drove the biggest changes — so you can
explain *why* revenue went up or down, not just by how much.

Use this for any question involving growth, decline, comparison, trend, or "how are we doing
compared to".""",
)
def compare_periods(
	period: Annotated[str, "'month', 'quarter', or 'year'. Default 'month'."] = "month",
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.compare import compare_periods as _compare
	return _compare(period=period)


@tool(
	writes=False,
	risk="none",
	description="""Detect unusual patterns and anomalies in sales data.

Scans the last N months of data for statistical outliers — things that deviate significantly
from normal patterns. Detects:
  - Revenue spikes or drops (months that are 2+ standard deviations from the mean)
  - Unusually large or small individual orders
  - Customers who suddenly stopped ordering or started ordering much more
  - Products with sudden volume surges or declines

Each anomaly comes with what happened, what was expected, and how far from normal it is.

Use this when the user asks about unusual activity, things that look wrong, surprises,
anomalies, or "what's different this month".""",
)
def detect_anomalies(
	months_back: Annotated[int, "How many months of history to analyse. Default 6."] = 6,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.anomalies import detect_anomalies as _detect
	return _detect(months_back=months_back)


@tool(
	writes=False,
	risk="none",
	description="""Suggest products a customer hasn't bought but similar customers have (cross-sell).

Looks at what this customer has purchased, finds other customers who bought the same things,
and recommends products those peers also bought. Only suggests active (non-disabled) items.

Each suggestion includes how many similar customers bought it and the total revenue it generated
across those peers — so you can explain *why* the recommendation is made.

Returns nothing if the customer has no purchase history (can't recommend without evidence).""",
)
def cross_sell(
	customer: Annotated[str, "Customer ID (e.g. 'CUST-00001')."],
	limit: Annotated[int, "How many suggestions to return. Default 5."] = 5,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.products import cross_sell as _cross_sell
	return _cross_sell(customer=customer, limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Suggest higher-value alternatives for products a customer currently buys (upsell).

For each product the customer has ordered, finds higher-priced items in the same item group
that other customers have bought. Shows the price uplift percentage and how many other
customers chose the upgrade.

Use this when someone asks about upsell opportunities or how to increase order value.""",
)
def upsell(
	customer: Annotated[str, "Customer ID (e.g. 'CUST-00001')."],
	limit: Annotated[int, "How many suggestions to return. Default 5."] = 5,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.products import upsell as _upsell
	return _upsell(customer=customer, limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Find customers whose typical reorder interval has elapsed.

Looks at customers with 3+ orders, calculates their average time between orders, and flags
those who are due or overdue for a reorder. Each customer shows their average interval,
days since last order, and urgency (approaching / due / overdue).

Use this for repeat purchase analysis, reorder reminders, or finding customers who might
be slipping away.""",
)
def repeat_purchase_due(
	limit: Annotated[int, "How many customers to return. Default 20."] = 20,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.products import repeat_purchase_due as _repeat
	return _repeat(limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Rank products by revenue, volume, growth, and customer reach.

Returns each product's total revenue, revenue share %, quantity sold, number of distinct
customers, and growth trend (comparing recent half vs older half of the period).

Use this for product performance analysis, identifying star products, declining products,
or answering "what are our best/worst selling products?".""",
)
def product_performance(
	period_months: Annotated[int, "How many months of history. Default 6."] = 6,
	limit: Annotated[int, "How many products to return. Default 20."] = 20,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.products import product_performance as _perf
	return _perf(period_months=period_months, limit=limit)


@tool(
	writes=False,
	risk="none",
	description="""Calculate the weighted pipeline: each deal's value × its probability.

Raw pipeline totals are misleading — a $100k deal at 10% probability is worth $10k, not $100k.
This tool multiplies each open opportunity's amount by its probability to give a realistic
expected revenue figure.

Returns total raw pipeline, total weighted pipeline, breakdown by sales stage, and individual
deals with their weighted values. Deals with missing probability default to 50%.

Also flags overdue deals (expected_closing has passed). Use this for any pipeline analysis,
revenue forecasting from pipeline, or "how much pipeline do we really have?".""",
)
def weighted_pipeline() -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.pipeline import weighted_pipeline as _wp
	return _wp()


@tool(
	writes=False,
	risk="none",
	description="""Measure how long deals take from creation to Won/Converted.

Calculates the median and average number of days between opportunity creation and close
for all won opportunities in the period. Shows individual deals with their cycle time.

Use this when someone asks about sales cycle length, how long deals take, deal velocity,
or time to close.""",
)
def sales_cycle(
	months_back: Annotated[int, "How many months of history. Default 12."] = 12,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.pipeline import sales_cycle as _sc
	return _sc(months_back=months_back)


@tool(
	writes=False,
	risk="none",
	description="""Generate a morning sales brief: everything that needs attention today.

Returns a prioritised summary of:
- Follow-ups due today or overdue
- Quotations expiring in the next 7 days
- Deals expected to close this week
- Overdue opportunities (expected close date has passed)
- New leads from the last 3 days
- Month-to-date revenue and pipeline snapshot

This is the tool to call when someone says "what's on my plate?", "morning review",
"what should I focus on today?", or "give me my daily brief".""",
)
def sales_day_brief() -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.brief import sales_day_brief as _brief
	return _brief()


@tool(
	writes=False,
	risk="none",
	description="""Generate a manager's overview: team performance, pipeline health, and risks.

Returns:
- Overall KPIs: revenue, orders, AOV, conversion rate
- Performance by sales person (revenue and order count)
- Pipeline by sales person
- Stale deals (no activity in 14+ days)
- At-risk customers (high churn scores)

Use this when a manager asks for a team review, performance summary, "how is the team doing?",
or "weekly/monthly manager brief".""",
)
def manager_brief(
	period_months: Annotated[int, "How many months to cover. Default 1 (current month)."] = 1,
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.brief import manager_brief as _mgr
	return _mgr(period_months=period_months)


@tool(
	writes=False,
	risk="none",
	description="""Compare actual revenue against sales target for a period.

Shows: actual revenue, target, variance, achievement %, and whether the business is
ahead, on track, or behind. Also shows daily run rate and projected end-of-period revenue.

Identifies top customers and products driving the actual revenue, so you can explain
*why* the target was missed or exceeded.

Use period="month" for this month, "quarter" for this quarter, "year" for this year.

Use this when someone asks "are we hitting target?", "why did we miss target?",
"how are we tracking?", or "target vs actual".""",
)
def target_vs_actual(
	period: Annotated[str, "'month', 'quarter', or 'year'. Default 'month'."] = "month",
) -> dict[str, Any]:
	_require_intelligence_access()
	from sales_ai.intelligence.targets import target_vs_actual as _tva
	return _tva(period=period)


register(forecast_revenue)
register(score_leads)
register(get_recommendations)
register(segment_customers)
register(compare_periods)
register(detect_anomalies)
register(cross_sell)
register(upsell)
register(repeat_purchase_due)
register(product_performance)
register(weighted_pipeline)
register(sales_cycle)
register(sales_day_brief)
register(manager_brief)
register(target_vs_actual)
