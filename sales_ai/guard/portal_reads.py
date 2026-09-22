# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Read-only data access for portal (Customer) users.

Portal users are Website Users — they have no ERPNext DocType permissions at all.
``frappe.has_permission`` will deny every call.  ERPNext's own portal pages solve
this by querying ``ignore_permissions=True`` and filtering to the customer linked
to the login.  We do the same, with these constraints:

- **Only the caller's own Customer** — determined by ``portal.my_customer()``, never
  from the request.
- **Only the DocTypes listed here** — Quotation, Sales Order, Sales Invoice, Customer.
- **Only the fields listed here** — a strict allowlist, nothing internal.
- ``CUSTOMER_FORBIDDEN`` from the RBAC matrix is enforced: Leads, Opportunities,
  Payment Entries, Employees, etc. are blocked at the tool level.

This module is the *only* place ``ignore_permissions`` appears in portal read paths.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import flt

from sales_ai.guard import GuardError
from sales_ai.guard.portal import my_customer

# ── DocTypes and fields a portal user may see ──────────────────────────

_PORTAL_SPECS: dict[str, dict[str, Any]] = {
    "Quotation": {
        "list_fields": [
            "name", "party_name", "transaction_date", "valid_till",
            "grand_total", "currency", "status",
        ],
        "detail_fields": [
            "name", "party_name", "transaction_date", "valid_till",
            "grand_total", "currency", "status", "terms",
        ],
        "child_table": "Quotation Item",
        "child_fields": ["item_code", "item_name", "qty", "rate", "amount"],
        "customer_field": "party_name",
        "extra_filters": {"quotation_to": "Customer"},
    },
    "Sales Order": {
        "list_fields": [
            "name", "customer_name", "transaction_date", "delivery_date",
            "grand_total", "currency", "status", "per_delivered", "per_billed",
        ],
        "detail_fields": [
            "name", "customer_name", "transaction_date", "delivery_date",
            "grand_total", "currency", "status", "per_delivered", "per_billed",
            "terms",
        ],
        "child_table": "Sales Order Item",
        "child_fields": ["item_code", "item_name", "qty", "rate", "amount", "delivered_qty"],
        "customer_field": "customer",
    },
    "Sales Invoice": {
        "list_fields": [
            "name", "customer_name", "posting_date", "due_date",
            "grand_total", "outstanding_amount", "currency", "status",
        ],
        "detail_fields": [
            "name", "customer_name", "posting_date", "due_date",
            "grand_total", "outstanding_amount", "currency", "status",
        ],
        "child_table": "Sales Invoice Item",
        "child_fields": ["item_code", "item_name", "qty", "rate", "amount"],
        "customer_field": "customer",
    },
    "Customer": {
        "list_fields": ["name", "customer_name", "customer_type", "territory"],
        "detail_fields": [
            "name", "customer_name", "customer_type", "territory",
            "customer_group", "default_currency",
        ],
        "customer_field": None,  # Filtered by name directly.
    },
}


def portal_read_list(
    doctype: str,
    limit: int = 20,
) -> dict[str, Any]:
    """List records belonging to the portal user's customer."""
    spec = _portal_spec(doctype)
    customer = _require_customer()

    filters = _customer_filter(spec, customer, doctype)
    # Only show submitted or draft — never cancelled.
    if doctype != "Customer":
        filters["docstatus"] = ["<", 2]

    rows = frappe.get_all(
        doctype,
        filters=filters,
        fields=spec["list_fields"],
        order_by="modified desc",
        limit=min(limit, 50),
        ignore_permissions=True,
    )

    # Sanitise monetary values.
    for row in rows:
        for key in ("grand_total", "outstanding_amount"):
            if key in row:
                row[key] = flt(row[key], 2)

    return {"doctype": doctype, "count": len(rows), "records": rows}


def portal_read_document(doctype: str, name: str) -> dict[str, Any]:
    """Read a single record belonging to the portal user's customer."""
    spec = _portal_spec(doctype)
    customer = _require_customer()

    filters = _customer_filter(spec, customer, doctype)
    filters["name"] = name
    if doctype != "Customer":
        filters["docstatus"] = ["<", 2]

    rows = frappe.get_all(
        doctype,
        filters=filters,
        fields=spec["detail_fields"],
        limit=1,
        ignore_permissions=True,
    )

    if not rows:
        raise GuardError("This record does not exist or is not accessible.")

    record = rows[0]
    for key in ("grand_total", "outstanding_amount"):
        if key in record:
            record[key] = flt(record[key], 2)

    # Attach child table items if applicable.
    child_table = spec.get("child_table")
    if child_table:
        items = frappe.get_all(
            child_table,
            filters={"parent": name},
            fields=spec.get("child_fields", ["item_code", "qty", "rate", "amount"]),
            order_by="idx asc",
            parent_doctype=doctype,
            ignore_permissions=True,
        )
        for item in items:
            for key in ("rate", "amount"):
                if key in item:
                    item[key] = flt(item[key], 2)
        record["items"] = items

    return {"doctype": doctype, "name": name, "record": record}


# ── Internals ──────────────────────────────────────────────────────────


def _portal_spec(doctype: str) -> dict[str, Any]:
    """Look up the portal spec, or refuse."""
    spec = _PORTAL_SPECS.get(doctype)
    if not spec:
        raise GuardError(
            f"You can view your Quotations, Sales Orders, and Invoices. "
            f"{doctype} is not available."
        )
    return spec


def _require_customer() -> str:
    """The customer linked to this portal login, or refuse."""
    customer = my_customer()
    if not customer:
        raise GuardError(
            "Your account is not linked to a customer record yet. "
            "Please ask someone from the team to set it up."
        )
    return customer


def _customer_filter(spec: dict[str, Any], customer: str, doctype: str) -> dict:
    """Build the filter dict that restricts to this customer only."""
    filters: dict[str, Any] = {}
    filters.update(spec.get("extra_filters", {}))

    customer_field = spec.get("customer_field")
    if customer_field is None:
        # Customer DocType — filter by primary key.
        filters["name"] = customer
    else:
        filters[customer_field] = customer

    return filters
