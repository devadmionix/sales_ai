# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What is actually on the shelf, so a promise to a customer can be kept.

A salesperson asking "can we ship five of these this week" is asking about `Bin`, the row
ERPNext keeps per item per warehouse. This module reads those rows through
`frappe.get_list`, which means a user restricted to one warehouse or one company sees that
warehouse's stock and nobody else's — the same scoping the desk applies.

What it deliberately does not read is `valuation_rate` and `stock_value`, which also live
on `Bin`. Those are what the stock cost us, not what it is worth to the customer, and the
agent has no business quoting either.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt

from sales_ai.guard import _may_read

# The quantities a seller can act on. `actual` is on the shelf, `reserved` is already
# promised to somebody else, `ordered` is on its way in, and `projected` is ERPNext's own
# arithmetic over all of it including production and material requests.
QUANTITIES = ("actual_qty", "reserved_qty", "ordered_qty", "indented_qty", "planned_qty", "projected_qty")

# A warehouse with no Bin row and no movement is noise in an answer.
MAX_WAREHOUSES = 20


def availability(item_code: str, warehouse: str | None = None) -> dict[str, Any]:
	item = _item(item_code)

	summary: dict[str, Any] = {
		"item_code": item.name,
		"item_name": item.item_name,
		"stock_uom": item.stock_uom,
		"tracks_stock": bool(item.is_stock_item),
	}
	if item.disabled:
		summary["disabled"] = True
	if item.lead_time_days:
		summary["lead_time_days"] = item.lead_time_days

	if not item.is_stock_item:
		# A service or a subscription has no shelf. Returning zeros here would read as
		# "out of stock" and lose a sale that was never constrained by stock at all.
		summary["note"] = (
			f"{item.name} is not stock-tracked, so there is no quantity to check. "
			"It can be sold without a stock check."
		)
		return summary

	rows = frappe.get_list(
		"Bin",
		fields=["warehouse", *QUANTITIES],
		filters=_filters(item.name, warehouse),
		order_by="actual_qty desc",
		limit_page_length=MAX_WAREHOUSES,
	)

	summary["warehouses"] = [_warehouse(row) for row in rows]
	summary["total"] = _total(rows)
	if warehouse:
		summary["warehouse_filter"] = warehouse
	if not rows:
		summary["note"] = (
			f"{item.name} has never been stocked"
			+ (f" in {warehouse}" if warehouse else "")
			+ ", so there is nothing on hand."
		)
	return summary


# -- internals -----------------------------------------------------------------------


def _item(item_code: str):
	# `get_cached_doc` checks nothing on its own, so the permission is checked first.
	return frappe.get_cached_doc("Item", _may_read("Item", item_code))


def _filters(item_code: str, warehouse: str | None) -> dict[str, Any]:
	filters: dict[str, Any] = {"item_code": item_code}
	if not warehouse:
		return filters

	_may_read("Warehouse", warehouse)
	if frappe.db.get_value("Warehouse", warehouse, "is_group"):
		# A group warehouse holds no stock itself; asking about "All Warehouses" means
		# asking about everything filed under it.
		from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses

		filters["warehouse"] = ["in", get_child_warehouses(warehouse)]
	else:
		filters["warehouse"] = warehouse
	return filters


def _warehouse(row: dict[str, Any]) -> dict[str, Any]:
	out = {"warehouse": row["warehouse"], **{key: flt(row.get(key)) for key in QUANTITIES}}
	out["available_qty"] = flt(row.get("actual_qty")) - flt(row.get("reserved_qty"))
	return {key: value for key, value in out.items() if value or key == "warehouse"}


def _total(rows: list[dict[str, Any]]) -> dict[str, Any]:
	total = {key: flt(sum(flt(row.get(key)) for row in rows)) for key in QUANTITIES}
	# The one number a salesperson is really after: on the shelf and not already promised.
	total["available_qty"] = total["actual_qty"] - total["reserved_qty"]
	return total
