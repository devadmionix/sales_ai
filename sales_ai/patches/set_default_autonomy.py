# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Give sites installed before the autonomy dial existed a value for it.

Frappe only applies a field's default when a document is created, and a Single that
already exists is never created again — so on an existing site the new field stays empty.
The gate reads an empty value as `Ask Before Every Change` already, so nothing is unsafe
in the meantime; this is so the stored setting says what the code is doing, and so a
required field is not blank the first time an admin opens the form.
"""

import frappe

from sales_ai.guard.policy import ASK_ALWAYS, READ_ONLY, ACT_ON_LOW_RISK


def execute() -> None:
	if frappe.db.get_single_value("Sales AI Settings", "autonomy") in (
		READ_ONLY,
		ASK_ALWAYS,
		ACT_ON_LOW_RISK,
	):
		return
	frappe.db.set_single_value("Sales AI Settings", "autonomy", ASK_ALWAYS)
