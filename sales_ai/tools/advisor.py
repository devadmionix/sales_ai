# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tools that give the agent forecasting, lead scoring and business advice capabilities.

Each tool calls into the intelligence layer, which does deterministic arithmetic on real
data. The agent quotes the results — it did not produce them, so they are evidence-based.
"""

from __future__ import annotations

from typing import Annotated, Any

from sales_ai.llm.tool import tool
from sales_ai.tools import register


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
	from sales_ai.intelligence.anomalies import detect_anomalies as _detect
	return _detect(months_back=months_back)


register(forecast_revenue)
register(score_leads)
register(get_recommendations)
register(segment_customers)
register(compare_periods)
register(detect_anomalies)
