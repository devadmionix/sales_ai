# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Post-install setup: a working default agent profile so the app is usable immediately."""

from __future__ import annotations

import frappe

from sales_ai import tools as tool_registry

PROFILE_NAME = "Sales Advisor"


def after_install():
    """Create a default agent profile with every registered tool enabled."""
    _ensure_profile()


def after_migrate():
    """Keep the shipped profile up to date with newly added tools."""
    _ensure_profile()


def _ensure_profile():
    tool_registry._load()
    all_tools = tool_registry.names()

    if frappe.db.exists("Sales AI Agent Profile", PROFILE_NAME):
        _update_profile(PROFILE_NAME, all_tools)
    else:
        _create_profile(all_tools)

    # Point settings at it if nothing else is configured.
    settings = frappe.get_single("Sales AI Settings")
    if not settings.default_agent_profile:
        settings.default_agent_profile = PROFILE_NAME
        settings.save(ignore_permissions=True)

    frappe.db.commit()


def _create_profile(all_tools: list[str]) -> None:
    profile = frappe.new_doc("Sales AI Agent Profile")
    profile.update(
        {
            "name": PROFILE_NAME,
            "title": PROFILE_NAME,
            "enabled": 1,
            "selectable": 1,
            "max_iterations": 8,
        }
    )
    # Use whichever model exists and is enabled.
    model = frappe.db.get_value("Sales AI Model", {"enabled": 1}, "name")
    if model:
        profile.model = model

    for tool_name in all_tools:
        profile.append("tools", {"tool": tool_name, "enabled": 1})

    profile.insert(ignore_permissions=True)
    frappe.msgprint(f"Created agent profile '{PROFILE_NAME}' with {len(all_tools)} tools.")


def _update_profile(name: str, all_tools: list[str]) -> None:
    profile = frappe.get_doc("Sales AI Agent Profile", name)
    existing = {row.tool for row in profile.tools}
    added = 0
    for tool_name in all_tools:
        if tool_name not in existing:
            profile.append("tools", {"tool": tool_name, "enabled": 1})
            added += 1
    if added:
        profile.save(ignore_permissions=True)
