# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tools that answer with a number rather than a list.

One tool, not one per question. "Pipeline by stage", "won value this quarter" and "top ten
products" are the same query with different arguments, and writing three tools would only
give the model three chances to pick the wrong one.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from sales_ai.guard import Filter
from sales_ai.guard.analytics import BUCKETS, DEFAULT_GROUPS, aggregate, describe
from sales_ai.guard.reports import MAX_ROWS, report_names, run_report
from sales_ai.guard.reports import describe as describe_report
from sales_ai.guard.specs import AGGREGATES
from sales_ai.llm.tool import tool
from sales_ai.tools import register

MeasurableDocType = Literal[
	"Customer",
	"Lead",
	"Opportunity",
	"Quotation",
	"Quotation Item",
	"Sales Order",
	"Sales Order Item",
]

# Keeps the enum the model sees and the allowlist the guard enforces in step.
assert set(MeasurableDocType.__args__) == set(AGGREGATES), (
	"MeasurableDocType and AGGREGATES have drifted apart"
)

Bucket = Literal["day", "month", "quarter", "year"]
assert set(Bucket.__args__) == set(BUCKETS), "Bucket and BUCKETS have drifted apart"

_CATALOGUE = "\n".join(f"- {describe(doctype)}" for doctype in MeasurableDocType.__args__)


@tool(
	writes=False,
	risk="none",
	description=f"""Count, total or average sales records, optionally broken down.

Use this instead of search_records whenever the answer is a number rather than a list of
records — pipeline value, win rates, totals by month, best selling products. Searching and
adding up the rows yourself is wrong: the search is capped at fifty records, so the total
would be silently incomplete.

Money measures are in the company's own currency, never a mix of currencies.

Results respect the user's permissions, so the numbers cover only what this user may see.

Record types, as `measures / by / dates` — the legal values for the measures, group_by and
date_field arguments. Dates are YYYY-MM-DD.
{_CATALOGUE}""",
)
def measure_records(
	doctype: Annotated[MeasurableDocType, "Type of record to measure."],
	measures: Annotated[
		list[str],
		"Which numbers to return, from the measures listed for this record type.",
	],
	group_by: Annotated[
		list[str] | None,
		"Fields to break the numbers down by. Omit for a single overall figure.",
	] = None,
	date_field: Annotated[
		str | None,
		"Which date to measure over, from the date fields listed for this record type.",
	] = None,
	date_bucket: Annotated[
		Bucket | None,
		"Break the numbers down into periods of this size. Needs date_field. "
		"'day' only works for fields that hold a date without a time.",
	] = None,
	date_from: Annotated[str | None, "Only include records on or after this date (YYYY-MM-DD)."] = None,
	date_to: Annotated[str | None, "Only include records on or before this date (YYYY-MM-DD)."] = None,
	filters: Annotated[
		list[Filter] | None,
		"Conditions combined with AND, using the same fields search_records allows.",
	] = None,
	order_by: Annotated[
		str | None,
		"Sort as '<measure or group> asc|desc', or 'period asc' for a time series. "
		"Defaults to oldest period first, or largest measure first.",
	] = None,
	limit: Annotated[int, "How many groups to return. Use with order_by for a top-N."] = DEFAULT_GROUPS,
) -> dict[str, Any]:
	return aggregate(
		doctype,
		measures=measures,
		group_by=group_by,
		date_field=date_field,
		date_bucket=date_bucket,
		date_from=date_from,
		date_to=date_to,
		filters=filters,
		order_by=order_by,
		limit=limit,
	)


SalesReport = Literal[tuple(report_names())]  # type: ignore[valid-type]

_REPORTS = "\n".join(f"- {describe_report(name)}" for name in report_names())


@tool(
	writes=False,
	risk="none",
	description=f"""Run one of ERPNext's own sales reports.

Prefer this over measure_records when the question matches a report, because the report is
the same figure the sales team sees on their own screen — a hand-rolled total that
disagrees with it is worse than useless.

Returns at most {MAX_ROWS} rows, and says so when there were more.

Reports and their filters. `*` must be supplied, `=` lists the only accepted values, `:`
names the DocType a value must exist in. Anything else is a date (YYYY-MM-DD), a whole
number, or true/false.
{_REPORTS}""",
)
def run_sales_report(
	report: Annotated[SalesReport, "Which report to run."],
	filters: Annotated[
		dict[str, Any] | None,
		"Filter values keyed by filter name. Only the filters listed for the report are "
		"allowed, and required ones must be supplied.",
	] = None,
) -> dict[str, Any]:
	return run_report(report, filters)


register(measure_records)
register(run_sales_report)
