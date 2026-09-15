# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What the desk needs to know about Sales AI before the user asks for anything."""

from __future__ import annotations

from typing import Any

import frappe


def extend_bootinfo(bootinfo: Any) -> None:
	"""Decide whether the desk shows the assistant at all.

	The bootinfo is cached per session, so flipping the switch in Sales AI Settings
	reaches an open desk on its next reload.
	"""
	bootinfo.sales_ai = {
		"enabled": bool(frappe.db.get_single_value("Sales AI Settings", "enabled")),
	}
