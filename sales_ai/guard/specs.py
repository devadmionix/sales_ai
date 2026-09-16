# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What the agent is allowed to read and write, field by field.

These are allowlists, not blocklists. A DocType that is not here cannot be touched at all,
and a field that is not listed is never returned or set — so adding a sensitive field to
ERPNext later cannot quietly widen what the model sees or changes.

Reading uses four separate lists per DocType, because they answer different questions:

- `list_fields`   what a search returns (kept small; every row costs tokens)
- `detail_fields` what reading one record returns
- `search_fields` what free text is matched against
- `filter_fields` what the model may filter or sort on

`untrusted` marks values that someone outside the company can set — a web-form lead, an
email signature, a customer's PO number. Those are the fields a prompt injection would
arrive in, and they get cleaned before the model ever sees them.

Writing has its own, much shorter lists (`WRITE_SPECS`). Creating and updating are
separate allowlists because they are different risks: naming a new lead is routine,
renaming an existing customer is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReadSpec:
	purpose: str
	list_fields: tuple[str, ...]
	detail_fields: tuple[str, ...]
	search_fields: tuple[str, ...]
	filter_fields: tuple[str, ...]
	untrusted: frozenset[str] = frozenset()
	children: dict[str, tuple[str, ...]] = field(default_factory=dict)

	def readable(self, detail: bool) -> tuple[str, ...]:
		return self.detail_fields if detail else self.list_fields


@dataclass(frozen=True)
class Measure:
	"""One number the agent may ask for.

	Money is always summed in the **company's** currency (the `base_` fields). Adding up
	`grand_total` across a multi-currency pipeline would silently add rupees to dollars.
	"""

	op: str
	fieldname: str = "name"
	label: str = ""


@dataclass(frozen=True)
class AggregateSpec:
	"""What may be counted, summed and grouped for one DocType.

	Aggregates are their own allowlist rather than a re-use of `filter_fields`, because
	grouping is a different disclosure: `filter_fields` lets the model ask *whether* rows
	exist, while `group_by` hands back the distinct values themselves. Grouping salaries
	by employee is not the same question as filtering by one.

	When `parent` is set this is a child table — a line on an order. Permission then
	follows the parent, and so do the filters and the date range: "sold last quarter" is a
	property of the order, not of the line on it.
	"""

	purpose: str
	measures: dict[str, Measure]
	group_by: tuple[str, ...]
	date_fields: tuple[str, ...]
	parent: str | None = None


@dataclass(frozen=True)
class ReportFilter:
	"""One knob on a report, and what a legal setting for it looks like.

	The kind is not decoration. A report's filter values are handed to ERPNext's own report
	code, which is trusted but was written expecting a human at a form — so every value is
	checked to be the shape that form would have produced before it goes anywhere near it.
	"""

	kind: str  # "date", "number", "flag", "link" or "choice"
	options: tuple[str, ...] = ()  # link: the DocType; choice: the permitted values
	required: bool = False
	# What the report's own form would have put here. Several ERPNext reports read a
	# filter straight out of a lookup table and fail on a missing key, because on screen
	# the box is never empty. Leaving a filter out has to mean the same thing here.
	default: Any = None


@dataclass(frozen=True)
class ReportSpec:
	"""One of ERPNext's own reports, and the filters the agent may set on it."""

	purpose: str
	filters: dict[str, ReportFilter]


@dataclass(frozen=True)
class WriteSpec:
	label: str
	create_fields: tuple[str, ...]
	update_fields: tuple[str, ...]
	required: tuple[str, ...] = ()
	# The field that says "this is the same one again", where the DocType has such a
	# thing. Left unset where a repeat is legitimate: a party can have two open
	# opportunities, and two people can share a first name.
	identity: str | None = None

	def allowed(self, creating: bool) -> tuple[str, ...]:
		return self.create_fields if creating else self.update_fields


_COMMON_FILTERS = ("creation", "modified", "owner")


SPECS: dict[str, ReadSpec] = {
	"Lead": ReadSpec(
		purpose="A potential customer who has not been qualified into an Opportunity yet.",
		list_fields=(
			"name",
			"lead_name",
			"company_name",
			"status",
			"qualification_status",
			"email_id",
			"mobile_no",
			"territory",
			"lead_owner",
			"modified",
		),
		detail_fields=(
			"name",
			"lead_name",
			"first_name",
			"last_name",
			"company_name",
			"job_title",
			"status",
			"qualification_status",
			"qualified_by",
			"qualified_on",
			"type",
			"request_type",
			"email_id",
			"mobile_no",
			"phone",
			"website",
			"industry",
			"market_segment",
			"no_of_employees",
			"annual_revenue",
			"city",
			"state",
			"country",
			"territory",
			"lead_owner",
			"company",
			"utm_source",
			"utm_campaign",
			"customer",
			"disabled",
			"creation",
			"modified",
		),
		search_fields=("name", "lead_name", "company_name", "email_id", "mobile_no", "phone"),
		filter_fields=(
			"status",
			"qualification_status",
			"territory",
			"lead_owner",
			"industry",
			"market_segment",
			"country",
			"company",
			"type",
			"request_type",
			"annual_revenue",
			"customer",
			"disabled",
			*_COMMON_FILTERS,
		),
		untrusted=frozenset(
			{
				"lead_name",
				"first_name",
				"last_name",
				"company_name",
				"job_title",
				"email_id",
				"mobile_no",
				"phone",
				"website",
				"city",
				"state",
			}
		),
	),
	"Opportunity": ReadSpec(
		purpose="A qualified sales opportunity with an expected value and close date.",
		list_fields=(
			"name",
			"title",
			"opportunity_from",
			"party_name",
			"customer_name",
			"status",
			"sales_stage",
			"opportunity_amount",
			"currency",
			"probability",
			"expected_closing",
			"territory",
			"opportunity_owner",
			"modified",
		),
		detail_fields=(
			"name",
			"title",
			"opportunity_from",
			"party_name",
			"customer_name",
			"status",
			"sales_stage",
			"opportunity_type",
			"opportunity_owner",
			"opportunity_amount",
			"base_opportunity_amount",
			"currency",
			"conversion_rate",
			"probability",
			"expected_closing",
			"transaction_date",
			"total",
			"base_total",
			"customer_group",
			"industry",
			"market_segment",
			"territory",
			"city",
			"state",
			"country",
			"company",
			"contact_email",
			"contact_mobile",
			"contact_person",
			"order_lost_reason",
			"first_response_time",
			"utm_source",
			"utm_campaign",
			"creation",
			"modified",
		),
		search_fields=("name", "title", "customer_name", "party_name", "contact_email"),
		filter_fields=(
			"status",
			"sales_stage",
			"opportunity_type",
			"opportunity_from",
			"party_name",
			"opportunity_owner",
			"territory",
			"customer_group",
			"company",
			"transaction_date",
			"expected_closing",
			"opportunity_amount",
			"probability",
			*_COMMON_FILTERS,
		),
		untrusted=frozenset(
			{"title", "customer_name", "contact_email", "contact_mobile", "order_lost_reason"}
		),
		children={
			"items": ("item_code", "item_name", "qty", "uom", "rate", "amount", "brand"),
		},
	),
	"Quotation": ReadSpec(
		purpose="A priced proposal sent to a customer or lead.",
		list_fields=(
			"name",
			"quotation_to",
			"party_name",
			"customer_name",
			"transaction_date",
			"valid_till",
			"status",
			"grand_total",
			"currency",
			"territory",
			"company",
			"modified",
		),
		detail_fields=(
			"name",
			"quotation_to",
			"party_name",
			"customer_name",
			"status",
			"docstatus",
			"transaction_date",
			"valid_till",
			"order_type",
			"currency",
			"conversion_rate",
			"selling_price_list",
			"total_qty",
			"total",
			"net_total",
			"total_taxes_and_charges",
			"apply_discount_on",
			"additional_discount_percentage",
			"discount_amount",
			"grand_total",
			"rounded_total",
			"customer_group",
			"territory",
			"company",
			"contact_email",
			"contact_mobile",
			"contact_person",
			"opportunity",
			"order_lost_reason",
			"creation",
			"modified",
		),
		search_fields=("name", "customer_name", "party_name"),
		filter_fields=(
			"status",
			"docstatus",
			"quotation_to",
			"party_name",
			"customer_group",
			"territory",
			"company",
			"transaction_date",
			"valid_till",
			"grand_total",
			"opportunity",
			*_COMMON_FILTERS,
		),
		untrusted=frozenset({"customer_name", "contact_email", "contact_mobile", "order_lost_reason"}),
		children={
			"items": (
				"item_code",
				"item_name",
				"qty",
				"uom",
				"price_list_rate",
				"discount_percentage",
				"rate",
				"amount",
			),
		},
	),
	"Sales Order": ReadSpec(
		purpose="A confirmed customer order.",
		list_fields=(
			"name",
			"customer",
			"customer_name",
			"transaction_date",
			"delivery_date",
			"status",
			"grand_total",
			"currency",
			"per_delivered",
			"per_billed",
			"company",
			"territory",
			"modified",
		),
		detail_fields=(
			"name",
			"customer",
			"customer_name",
			"status",
			"docstatus",
			"delivery_status",
			"billing_status",
			"transaction_date",
			"delivery_date",
			"order_type",
			"po_no",
			"po_date",
			"currency",
			"conversion_rate",
			"selling_price_list",
			"total_qty",
			"total",
			"net_total",
			"total_taxes_and_charges",
			"discount_amount",
			"grand_total",
			"rounded_total",
			"advance_paid",
			"per_delivered",
			"per_billed",
			"customer_group",
			"territory",
			"company",
			"project",
			"cost_center",
			"contact_email",
			"contact_mobile",
			"contact_person",
			"creation",
			"modified",
		),
		search_fields=("name", "customer", "customer_name", "po_no"),
		filter_fields=(
			"status",
			"docstatus",
			"delivery_status",
			"billing_status",
			"customer",
			"customer_group",
			"territory",
			"company",
			"project",
			"transaction_date",
			"delivery_date",
			"grand_total",
			"per_delivered",
			"per_billed",
			*_COMMON_FILTERS,
		),
		untrusted=frozenset({"customer_name", "po_no", "contact_email", "contact_mobile"}),
		children={
			"items": (
				"item_code",
				"item_name",
				"qty",
				"delivered_qty",
				"uom",
				"rate",
				"amount",
				"delivery_date",
			),
		},
	),
	"Customer": ReadSpec(
		purpose="An existing customer account.",
		list_fields=(
			"name",
			"customer_name",
			"customer_type",
			"customer_group",
			"territory",
			"email_id",
			"mobile_no",
			"disabled",
			"modified",
		),
		detail_fields=(
			"name",
			"customer_name",
			"customer_type",
			"customer_group",
			"territory",
			"email_id",
			"mobile_no",
			"website",
			"industry",
			"market_segment",
			"default_currency",
			"default_price_list",
			"payment_terms",
			"tax_id",
			"tax_category",
			"account_manager",
			"lead_name",
			"opportunity_name",
			"disabled",
			"is_frozen",
			"so_required",
			"customer_details",
			"creation",
			"modified",
		),
		search_fields=("name", "customer_name", "email_id", "mobile_no", "tax_id"),
		filter_fields=(
			"customer_type",
			"customer_group",
			"territory",
			"account_manager",
			"disabled",
			"is_frozen",
			*_COMMON_FILTERS,
		),
		untrusted=frozenset({"customer_name", "email_id", "mobile_no", "website", "customer_details"}),
	),
	"Contact": ReadSpec(
		purpose="A person at a lead or customer.",
		list_fields=(
			"name",
			"full_name",
			"email_id",
			"mobile_no",
			"phone",
			"designation",
			"company_name",
			"status",
		),
		detail_fields=(
			"name",
			"full_name",
			"first_name",
			"last_name",
			"salutation",
			"designation",
			"department",
			"company_name",
			"email_id",
			"mobile_no",
			"phone",
			"status",
			"is_primary_contact",
			"unsubscribed",
			"creation",
			"modified",
		),
		search_fields=("name", "full_name", "email_id", "mobile_no", "phone"),
		filter_fields=("status", "unsubscribed", *_COMMON_FILTERS),
		untrusted=frozenset(
			{
				"full_name",
				"first_name",
				"last_name",
				"designation",
				"department",
				"company_name",
				"email_id",
				"mobile_no",
				"phone",
			}
		),
	),
	"Item": ReadSpec(
		purpose="A product or service that can be sold.",
		list_fields=(
			"name",
			"item_code",
			"item_name",
			"item_group",
			"brand",
			"stock_uom",
			"sales_uom",
			"is_sales_item",
			"disabled",
		),
		detail_fields=(
			"name",
			"item_code",
			"item_name",
			"item_group",
			"brand",
			"description",
			"stock_uom",
			"sales_uom",
			"is_sales_item",
			"is_stock_item",
			"has_variants",
			"variant_of",
			"max_discount",
			"weight_per_unit",
			"weight_uom",
			"country_of_origin",
			"end_of_life",
			"disabled",
			"creation",
			"modified",
		),
		search_fields=("name", "item_code", "item_name", "item_group", "brand", "description"),
		filter_fields=(
			"item_group",
			"brand",
			"is_sales_item",
			"is_stock_item",
			"has_variants",
			"disabled",
			*_COMMON_FILTERS,
		),
		untrusted=frozenset({"description"}),
	),
}


# Deliberately absent: Quotation and Sales Order. They are submittable documents whose
# totals come out of ERPNext's pricing and tax engine, so they need a build-and-preview
# step of their own rather than a field-by-field setter.
#
# Also absent from every list: `company`, `owner` and the `*_owner` assignment fields.
# Company is filled in from the user's own defaults, never from the model, and handing
# a record to a different person is an action in its own right, not a field edit.
WRITE_SPECS: dict[str, WriteSpec] = {
	"Lead": WriteSpec(
		label="lead",
		create_fields=(
			"salutation",
			"first_name",
			"middle_name",
			"last_name",
			"job_title",
			"company_name",
			"email_id",
			"mobile_no",
			"phone",
			"website",
			"status",
			"type",
			"request_type",
			"industry",
			"market_segment",
			"territory",
			"city",
			"state",
			"country",
			"no_of_employees",
			"annual_revenue",
		),
		update_fields=(
			"status",
			"qualification_status",
			"job_title",
			"company_name",
			"email_id",
			"mobile_no",
			"phone",
			"website",
			"type",
			"request_type",
			"industry",
			"market_segment",
			"territory",
			"city",
			"state",
			"country",
			"no_of_employees",
			"annual_revenue",
		),
		required=("first_name",),
		identity="email_id",
	),
	"Opportunity": WriteSpec(
		label="opportunity",
		create_fields=(
			"opportunity_from",
			"party_name",
			"title",
			"opportunity_type",
			"sales_stage",
			"expected_closing",
			"probability",
			"opportunity_amount",
			"currency",
			"customer_group",
			"industry",
			"market_segment",
			"territory",
			"contact_person",
			"contact_email",
			"contact_mobile",
			"transaction_date",
		),
		update_fields=(
			"status",
			"title",
			"opportunity_type",
			"sales_stage",
			"expected_closing",
			"probability",
			"opportunity_amount",
			"territory",
			"contact_email",
			"contact_mobile",
		),
		required=("opportunity_from", "party_name"),
	),
	"Customer": WriteSpec(
		label="customer",
		create_fields=(
			"customer_name",
			"customer_type",
			"customer_group",
			"territory",
			"tax_id",
			"tax_category",
			"website",
			"industry",
			"market_segment",
			"default_currency",
			"default_price_list",
			"customer_details",
		),
		update_fields=(
			"customer_group",
			"territory",
			"tax_id",
			"tax_category",
			"website",
			"industry",
			"market_segment",
			"default_price_list",
			"payment_terms",
			"customer_details",
			"disabled",
		),
		# Not customer_type: ERPNext marks it mandatory but defaults it to "Company", so
		# demanding it would make "create a customer called ABC Medical Store" — a complete
		# request by ERPNext's own reckoning — cost a clarifying question for nothing.
		required=("customer_name",),
		identity="customer_name",
	),
	"Contact": WriteSpec(
		label="contact",
		create_fields=(
			"salutation",
			"first_name",
			"middle_name",
			"last_name",
			"designation",
			"department",
			"company_name",
			"status",
		),
		update_fields=(
			"designation",
			"department",
			"company_name",
			"status",
			"is_primary_contact",
			"unsubscribed",
		),
		required=("first_name",),
	),
}


# -- what may be counted ----------------------------------------------------------------
#
# A search returns rows and is capped at fifty of them, which is the right shape for "show
# me the leads from Delhi" and useless for "what is the pipeline worth". These are the
# questions answered by a number instead: totals, counts and averages, grouped.
#
# Deliberately absent: anything margin- or cost-related. Cost of goods is an accounting
# figure the selling side is not automatically entitled to, and a sum is a very effective
# way to read a field you cannot read row by row.

_COUNT = Measure(op="count", label="records")

AGGREGATES: dict[str, AggregateSpec] = {
	"Lead": AggregateSpec(
		purpose="How many leads there are, grouped by where they came from or where they got to.",
		measures={"count": _COUNT},
		group_by=(
			"status",
			"qualification_status",
			"territory",
			"lead_owner",
			"industry",
			"market_segment",
			"country",
			"type",
			"request_type",
			"utm_source",
			"utm_campaign",
			"company",
		),
		date_fields=("creation", "modified"),
	),
	"Opportunity": AggregateSpec(
		purpose="Pipeline value and counts. This is what answers questions about the funnel.",
		measures={
			"count": _COUNT,
			"value": Measure(op="sum", fieldname="base_opportunity_amount", label="total value"),
			"average_value": Measure(
				op="avg", fieldname="base_opportunity_amount", label="average value"
			),
			"average_probability": Measure(op="avg", fieldname="probability", label="average probability"),
		},
		group_by=(
			"status",
			"sales_stage",
			"opportunity_type",
			"opportunity_from",
			"opportunity_owner",
			"party_name",
			"territory",
			"customer_group",
			"company",
			"order_lost_reason",
			"utm_source",
			"utm_campaign",
		),
		date_fields=("transaction_date", "expected_closing", "creation", "modified"),
	),
	"Quotation": AggregateSpec(
		purpose="What has been quoted, and what was won or lost.",
		measures={
			"count": _COUNT,
			"value": Measure(op="sum", fieldname="base_grand_total", label="total quoted"),
			"average_value": Measure(op="avg", fieldname="base_grand_total", label="average quote"),
		},
		group_by=(
			"status",
			"docstatus",
			"quotation_to",
			"party_name",
			"customer_group",
			"territory",
			"company",
			"order_type",
			"order_lost_reason",
		),
		date_fields=("transaction_date", "valid_till", "creation", "modified"),
	),
	"Sales Order": AggregateSpec(
		purpose="Booked revenue and order counts. Use this for actual sales rather than pipeline.",
		measures={
			"count": _COUNT,
			"value": Measure(op="sum", fieldname="base_grand_total", label="total ordered"),
			"average_value": Measure(op="avg", fieldname="base_grand_total", label="average order"),
			"quantity": Measure(op="sum", fieldname="total_qty", label="units"),
		},
		group_by=(
			"status",
			"docstatus",
			"delivery_status",
			"billing_status",
			"customer",
			"customer_group",
			"territory",
			"company",
			"project",
			"order_type",
		),
		date_fields=("transaction_date", "delivery_date", "creation", "modified"),
	),
	"Customer": AggregateSpec(
		purpose="How many customers there are, grouped by how they are classified.",
		measures={"count": _COUNT},
		group_by=("customer_type", "customer_group", "territory", "account_manager", "disabled"),
		date_fields=("creation", "modified"),
	),
	"Sales Order Item": AggregateSpec(
		purpose="What was actually sold, line by line. This is what answers 'which products'.",
		measures={
			"count": _COUNT,
			"value": Measure(op="sum", fieldname="base_amount", label="revenue"),
			"quantity": Measure(op="sum", fieldname="qty", label="units"),
			"average_rate": Measure(op="avg", fieldname="base_rate", label="average rate"),
		},
		group_by=("item_code", "item_name", "item_group", "brand", "warehouse", "uom"),
		date_fields=("transaction_date", "delivery_date", "creation", "modified"),
		parent="Sales Order",
	),
	"Quotation Item": AggregateSpec(
		purpose="What was offered, line by line, whether or not it was ordered.",
		measures={
			"count": _COUNT,
			"value": Measure(op="sum", fieldname="base_amount", label="value quoted"),
			"quantity": Measure(op="sum", fieldname="qty", label="units"),
			"average_discount": Measure(
				op="avg", fieldname="discount_percentage", label="average discount"
			),
		},
		group_by=("item_code", "item_name", "item_group", "brand", "uom"),
		date_fields=("transaction_date", "valid_till", "creation", "modified"),
		parent="Quotation",
	),
}


# -- reports ERPNext already knows how to write -------------------------------------------
#
# Some questions have a right answer that somebody has already worked out. "Which customers
# stopped buying" is a query with real subtlety in it, and reimplementing it as a clever
# aggregate would produce a number that quietly disagrees with the report the sales manager
# is looking at on their own screen. Running ERPNext's report means the agent and the human
# are reading the same figure.
#
# The cost is that a report's SQL is ERPNext's, not this app's, and several of these reports
# query directly rather than through `get_list` — so a report is scoped by *role*
# permission but not always by User Permissions, the way `measure_records` is. That is why
# this list is short, and why nothing on it discloses compensation, cost or credit:
# commission summaries, credit balances and customer-wise price lists are all deliberately
# absent.

_COMPANY = ReportFilter(kind="link", options=("Company",), required=True)
_FROM = ReportFilter(kind="date", required=True)
_TO = ReportFilter(kind="date", required=True)

REPORTS: dict[str, ReportSpec] = {
	"Sales Pipeline Analytics": ReportSpec(
		purpose="The open pipeline over time, by owner or by sales stage, counted or valued.",
		filters={
			"company": _COMPANY,
			"from_date": ReportFilter(kind="date"),
			"to_date": ReportFilter(kind="date"),
			"pipeline_by": ReportFilter(
				kind="choice", options=("Owner", "Sales Stage"), default="Owner"
			),
			"range": ReportFilter(
				kind="choice", options=("Monthly", "Quarterly"), default="Monthly"
			),
			"based_on": ReportFilter(
				kind="choice", options=("Number", "Amount"), default="Number"
			),
			"status": ReportFilter(
				kind="choice", options=("Open", "Quotation", "Converted", "Replied")
			),
			"assigned_to": ReportFilter(kind="link", options=("User",)),
			"opportunity_type": ReportFilter(kind="link", options=("Opportunity Type",)),
		},
	),
	"Opportunity Summary by Sales Stage": ReportSpec(
		purpose="How opportunities are spread across the sales stages, by owner, source or type.",
		filters={
			"company": _COMPANY,
			"from_date": ReportFilter(kind="date"),
			"to_date": ReportFilter(kind="date"),
			"based_on": ReportFilter(
				kind="choice",
				options=("Opportunity Owner", "Source", "Opportunity Type"),
				default="Opportunity Owner",
			),
			"data_based_on": ReportFilter(
				kind="choice", options=("Number", "Amount"), default="Number"
			),
			"opportunity_type": ReportFilter(kind="link", options=("Opportunity Type",)),
		},
	),
	"Lost Opportunity": ReportSpec(
		purpose="Opportunities that were lost, with the reason given for each.",
		filters={
			"company": _COMPANY,
			"from_date": _FROM,
			"to_date": _TO,
			"lost_reason": ReportFilter(kind="link", options=("Opportunity Lost Reason",)),
			"territory": ReportFilter(kind="link", options=("Territory",)),
		},
	),
	"Lost Quotations": ReportSpec(
		purpose="Why quotations were lost, grouped by reason or by the competitor who won.",
		filters={
			"company": ReportFilter(kind="link", options=("Company",)),
			"timespan": ReportFilter(
				kind="choice",
				options=(
					"Last Week",
					"Last Month",
					"Last Quarter",
					"Last Year",
					"This Week",
					"This Month",
					"This Quarter",
					"This Year",
				),
				required=True,
			),
			"group_by": ReportFilter(
				kind="choice", options=("Lost Reason", "Competitor"), required=True
			),
		},
	),
	"Lead Details": ReportSpec(
		purpose="Leads with their contact details and status over a period.",
		filters={
			"company": _COMPANY,
			"from_date": _FROM,
			"to_date": _TO,
			"territory": ReportFilter(kind="link", options=("Territory",)),
		},
	),
	"Item-wise Sales History": ReportSpec(
		purpose="What was sold, item by item, with quantities and amounts over a period.",
		filters={
			"company": _COMPANY,
			"from_date": _FROM,
			"to_date": _TO,
			"item_group": ReportFilter(kind="link", options=("Item Group",)),
			"item_code": ReportFilter(kind="link", options=("Item",)),
			"customer": ReportFilter(kind="link", options=("Customer",)),
		},
	),
	"Sales Order Analysis": ReportSpec(
		purpose="Open sales orders and how much of each is still to deliver or bill.",
		filters={
			"company": _COMPANY,
			"from_date": _FROM,
			"to_date": _TO,
			"warehouse": ReportFilter(kind="link", options=("Warehouse",)),
		},
	),
	"Inactive Customers": ReportSpec(
		purpose="Customers who have not ordered for a while, and what they last bought.",
		filters={
			"days_since_last_order": ReportFilter(kind="number", default=60),
			"doctype": ReportFilter(
				kind="choice", options=("Sales Order", "Sales Invoice"), default="Sales Order"
			),
		},
	),
	"Customer Acquisition and Loyalty": ReportSpec(
		purpose="New customers against returning ones, by month or by territory.",
		filters={
			"company": _COMPANY,
			"from_date": _FROM,
			"to_date": _TO,
			"view_type": ReportFilter(
				kind="choice", options=("Monthly", "Territory Wise"), required=True
			),
		},
	),
	"Sales Person-wise Transaction Summary": ReportSpec(
		purpose="What each salesperson sold over a period. Use this for performance questions.",
		filters={
			"company": _COMPANY,
			"from_date": ReportFilter(kind="date"),
			"to_date": ReportFilter(kind="date"),
			"doc_type": ReportFilter(
				kind="choice",
				options=("Sales Order", "Delivery Note", "Sales Invoice"),
				default="Sales Order",
			),
			"sales_person": ReportFilter(kind="link", options=("Sales Person",)),
			"customer": ReportFilter(kind="link", options=("Customer",)),
			"territory": ReportFilter(kind="link", options=("Territory",)),
			"item_group": ReportFilter(kind="link", options=("Item Group",)),
			"brand": ReportFilter(kind="link", options=("Brand",)),
			"show_return_entries": ReportFilter(kind="flag", default=0),
		},
	),
}


# Anything the agent can change, it must also be able to read back to confirm the change.
assert set(WRITE_SPECS) <= set(SPECS), "WRITE_SPECS has a DocType that cannot be read"

# An aggregate is a second way of looking at something the agent may already read, never a
# way into a DocType it cannot. For a child table that check lands on its parent.
assert all(
	(spec.parent or doctype) in SPECS for doctype, spec in AGGREGATES.items()
), "AGGREGATES has a DocType that cannot be read"

# A date range is filtered like any other condition, so the date field has to be one the
# model was already allowed to filter on.
assert all(
	set(spec.date_fields) <= set(SPECS[spec.parent or doctype].filter_fields)
	for doctype, spec in AGGREGATES.items()
), "AGGREGATES has a date field that is not filterable"

# A filter is only as good as its checker. A kind with no checker would be waved through.
assert all(
	f.kind in ("date", "number", "flag", "link", "choice")
	for spec in REPORTS.values()
	for f in spec.filters.values()
), "REPORTS has a filter of an unknown kind"

assert all(
	(f.kind != "link" or len(f.options) == 1) and (f.kind != "choice" or f.options)
	for spec in REPORTS.values()
	for f in spec.filters.values()
), "REPORTS has a link filter without exactly one DocType, or a choice filter with no choices"
