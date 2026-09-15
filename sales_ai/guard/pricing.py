# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What ERPNext would charge, worked out by ERPNext.

A price in this system is not a column. It is a price list, plus whatever Item Prices and
Pricing Rules apply to this customer at this quantity on this date, plus the tax template
that follows from the customer's tax category, plus rounding at the company's own
precision. Reading `Item Price` and multiplying by quantity gets a number that is usually
close and occasionally wrong, and a quote that is occasionally wrong is worse than no quote.

So this module does not compute anything. It builds a Quotation in memory, hands it to
ERPNext's own `set_missing_values` and `calculate_taxes_and_totals`, and reads the answer
back. The document is never saved and never given a name — `price_for` is a read.

The same shape is what Phase 7c will save, which is the point: the figure the agent quotes
and the figure on the draft it later puts in front of a human come from one code path.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt, getdate, nowdate

from sales_ai.guard import GuardError, _may_read, _plain_text

# A quote is a conversation, not a catalogue dump. Ten lines is more than any sales call
# needs and keeps a mistyped loop from building a thousand-row document in memory.
MAX_LINES = 10

# What comes back per line. `get_item_details` also returns `valuation_rate`, the last
# purchase rate and the accounts the sale will post to — margin and cost, which the agent
# must never be in a position to disclose. Naming the fields we want is how they stay out.
LINE_FIELDS = (
	"item_code",
	"item_name",
	"uom",
	"qty",
	"price_list_rate",
	"discount_percentage",
	"discount_amount",
	"rate",
	"amount",
	"net_rate",
	"net_amount",
)

TOTAL_FIELDS = ("total", "net_total", "total_taxes_and_charges", "grand_total", "rounded_total")


def price_for(
	lines: list[dict[str, Any]],
	*,
	customer: str | None = None,
	company: str | None = None,
	price_list: str | None = None,
	date: str | None = None,
) -> dict[str, Any]:
	quotation = draft(lines, customer=customer, company=company, price_list=price_list, date=date)

	priced = {
		"company": quotation.company,
		"currency": quotation.currency,
		"price_list": quotation.selling_price_list,
		"date": str(quotation.transaction_date),
		"lines": [_line(row) for row in quotation.items],
		"totals": {
			key: flt(quotation.get(key)) for key in TOTAL_FIELDS if flt(quotation.get(key))
		},
	}
	if customer:
		priced["customer"] = quotation.party_name
	if quotation.taxes:
		priced["taxes"] = [
			{"description": _plain_text(tax.description or ""), "amount": flt(tax.tax_amount)}
			for tax in quotation.taxes
			if flt(tax.tax_amount)
		]
	return priced


def draft(
	lines: list[dict[str, Any]],
	*,
	customer: str | None = None,
	company: str | None = None,
	price_list: str | None = None,
	date: str | None = None,
) -> Any:
	"""An unsaved Quotation with ERPNext's own prices and totals filled in.

	Kept separate from `price_for` because a draft the human is asked to approve has to be
	the very document that was priced, not a second one built from the same arguments.
	"""
	quotation = frappe.new_doc("Quotation")
	quotation.company = _company(company)
	quotation.transaction_date = _date(date)

	if customer:
		# Customer-specific Item Prices and Pricing Rules are what make naming a customer
		# worth doing, which is exactly why a user fenced out of one may not name it.
		_may_read("Customer", customer)
		quotation.quotation_to = "Customer"
		quotation.party_name = customer

	if price_list:
		quotation.selling_price_list = _price_list(price_list)

	for line in _lines(lines):
		quotation.append("items", line)

	# ERPNext fills in the rates, the taxes, the currency and the exchange rate here, and
	# throws if the item is disabled, past its end of life or not a sales item.
	quotation.set_missing_values()
	quotation.calculate_taxes_and_totals()
	return quotation


# -- internals -----------------------------------------------------------------------


def _lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
	if not lines:
		raise GuardError("A price needs at least one line: an item code and a quantity.")
	if len(lines) > MAX_LINES:
		raise GuardError(f"A quote may have at most {MAX_LINES} lines; {len(lines)} were given.")

	checked = []
	for line in lines:
		unknown = set(line) - {"item_code", "qty", "uom"}
		if unknown:
			raise GuardError(
				f"A line takes item_code, qty and uom only. Remove: {', '.join(sorted(unknown))}."
			)

		item_code = _may_read("Item", (line.get("item_code") or "").strip())

		# `or 1` would quietly turn an explicit zero into one and quote a line the caller
		# asked not to have. A missing quantity defaults; a stated one is taken at its word.
		given = line.get("qty")
		qty = flt(1 if given is None else given)
		if qty <= 0:
			raise GuardError(f"{item_code} needs a quantity greater than zero, not {line.get('qty')!r}.")

		row = {"item_code": item_code, "qty": qty}
		if uom := (line.get("uom") or "").strip():
			# A rate is per UOM, so a wrong one is a wrong price rather than a failed lookup.
			if not frappe.db.exists("UOM", uom):
				raise GuardError(f"There is no unit of measure called {uom!r}.")
			row["uom"] = uom
		checked.append(row)
	return checked


def _company(company: str | None) -> str:
	name = company or frappe.defaults.get_user_default("Company")
	if not name:
		raise GuardError("A price needs a company, and this user has no default one.")
	return _may_read("Company", name)


def _price_list(price_list: str) -> str:
	_may_read("Price List", price_list)
	details = frappe.db.get_value("Price List", price_list, ["name", "selling", "enabled"], as_dict=True)
	if not details.selling:
		# A buying price list is what we pay a supplier. Quoting it to a customer would
		# disclose cost, and would do it while looking like a normal answer.
		raise GuardError(f"{price_list!r} is a buying price list and cannot be used to quote.")
	if not details.enabled:
		raise GuardError(f"The Price List {price_list!r} is disabled.")
	return details.name


def _date(date: str | None) -> str:
	if not date:
		return nowdate()
	try:
		return str(getdate(date))
	except Exception:
		raise GuardError(f"The date must be YYYY-MM-DD, not {date!r}.")


def _line(row: Any) -> dict[str, Any]:
	values = {key: row.get(key) for key in LINE_FIELDS}
	return {key: value for key, value in values.items() if value not in (None, "", 0, 0.0)}
