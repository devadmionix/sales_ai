# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Comprehensive RBAC tests for every role in the permission matrix.

These tests verify the central permission service (``check_ai_permission``) and
the tool filtering layer (``filter_tools_for_user``) against every role the spec
defines.  They do NOT call ERPNext's full permission system — that is tested
elsewhere — they verify the chatbot's own RBAC overlay:

1. The matrix allows what the spec says it should.
2. The matrix denies what the spec says it should.
3. Tool visibility matches the role.
4. Cross-role escalation is blocked.
5. Unknown roles fall through to ERPNext (not blocked).

Each test is a pure assertion against the ``ROLE_PERMISSIONS`` dict and
``check_ai_permission`` — no network, no model calls, fast enough to run on
every commit.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.guard.permissions import (
    CUSTOMER_FORBIDDEN,
    ROLE_PERMISSIONS,
    ROLE_TOOL_ACCESS,
    PermissionResult,
    check_ai_permission,
    filter_tools_for_user,
    get_user_scope,
    is_customer_user,
)

# ── Test users (one per role) ──────────────────────────────────────────

_ROLE_USERS = {
    "Sales User":          "rbac-sales-user@example.com",
    "Sales Manager":       "rbac-sales-mgr@example.com",
    "Sales Master Manager": "rbac-sales-admin@example.com",
    "Accounts User":       "rbac-accounts-user@example.com",
    "Accounts Manager":    "rbac-accounts-mgr@example.com",
}

ALL_TOOLS = [
    "search_records", "get_record",
    "create_record", "update_record", "add_note", "create_follow_up", "assign_record",
    "check_availability", "price_items",
    "draft_quotation", "submit_quotation", "convert_quotation_to_order",
    "convert_lead_to_customer", "convert_lead_to_opportunity",
    "list_recipients", "draft_email", "send_email",
    "list_follow_ups", "update_follow_up",
    "measure_records", "run_sales_report",
    "forecast_revenue", "score_leads", "get_recommendations",
    "segment_customers", "compare_periods", "detect_anomalies",
    "cross_sell", "upsell", "repeat_purchase_due",
    "product_performance", "weighted_pipeline", "sales_cycle",
    "sales_day_brief", "manager_brief", "target_vs_actual",
    "get_churn_risk",
    "revise_lines",
    "submit_document", "cancel_document",
    "add_opening_stock",
    "register_customer", "my_account", "my_documents", "my_document",
]


class TestRBACMatrix(IntegrationTestCase):
    """Tests against the in-memory ROLE_PERMISSIONS dict — no users needed."""

    # ── Sales User matrix ───────────────────────────────────────────

    def test_sales_user_can_read_quotation(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales User"]["Quotation"]["read"])

    def test_sales_user_can_create_quotation(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales User"]["Quotation"]["create"])

    def test_sales_user_cannot_submit_quotation(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales User"]["Quotation"]["submit"])

    def test_sales_user_cannot_cancel_quotation(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales User"]["Quotation"]["cancel"])

    def test_sales_user_can_read_sales_order(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales User"]["Sales Order"]["read"])

    def test_sales_user_cannot_submit_sales_order(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales User"]["Sales Order"]["submit"])

    def test_sales_user_cannot_submit_sales_invoice(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales User"]["Sales Invoice"]["submit"])

    def test_sales_user_can_read_item_but_not_create(self):
        perms = ROLE_PERMISSIONS["Sales User"]["Item"]
        self.assertTrue(perms["read"])
        self.assertFalse(perms["create"])
        self.assertFalse(perms["write"])
        self.assertFalse(perms["delete"])

    def test_sales_user_payment_entry_is_read_only(self):
        perms = ROLE_PERMISSIONS["Sales User"]["Payment Entry"]
        self.assertTrue(perms["read"])
        self.assertFalse(perms["create"])
        self.assertFalse(perms["write"])

    def test_sales_user_scope_is_own(self):
        self.assertEqual(ROLE_PERMISSIONS["Sales User"]["Quotation"]["scope"], "own")

    def test_sales_user_cannot_delete_customer(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales User"]["Customer"]["delete"])

    # ── Sales Manager matrix ────────────────────────────────────────

    def test_sales_manager_can_submit_quotation(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales Manager"]["Quotation"]["submit"])

    def test_sales_manager_can_cancel_quotation(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales Manager"]["Quotation"]["cancel"])

    def test_sales_manager_can_submit_sales_order(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales Manager"]["Sales Order"]["submit"])

    def test_sales_manager_can_submit_sales_invoice(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales Manager"]["Sales Invoice"]["submit"])

    def test_sales_manager_scope_is_team(self):
        self.assertEqual(ROLE_PERMISSIONS["Sales Manager"]["Quotation"]["scope"], "team")

    def test_sales_manager_cannot_delete_customer(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales Manager"]["Customer"]["delete"])

    def test_sales_manager_cannot_create_item(self):
        self.assertFalse(ROLE_PERMISSIONS["Sales Manager"]["Item"]["create"])

    # ── Sales Master Manager (Admin/Ops) ────────────────────────────

    def test_sales_admin_can_submit_everything(self):
        for dt in ("Quotation", "Sales Order", "Sales Invoice", "Delivery Note"):
            self.assertTrue(
                ROLE_PERMISSIONS["Sales Master Manager"][dt]["submit"],
                f"Sales Master Manager should be able to submit {dt}",
            )

    def test_sales_admin_can_cancel_everything(self):
        for dt in ("Quotation", "Sales Order", "Sales Invoice", "Delivery Note"):
            self.assertTrue(
                ROLE_PERMISSIONS["Sales Master Manager"][dt]["cancel"],
                f"Sales Master Manager should be able to cancel {dt}",
            )

    def test_sales_admin_scope_is_company(self):
        self.assertEqual(
            ROLE_PERMISSIONS["Sales Master Manager"]["Quotation"]["scope"], "company"
        )

    def test_sales_admin_can_delete_customer(self):
        self.assertTrue(ROLE_PERMISSIONS["Sales Master Manager"]["Customer"]["delete"])

    # ── Accounts User ───────────────────────────────────────────────

    def test_accounts_user_can_read_sales_invoice(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts User"]["Sales Invoice"]["read"])

    def test_accounts_user_can_create_sales_invoice(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts User"]["Sales Invoice"]["create"])

    def test_accounts_user_can_submit_sales_invoice(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts User"]["Sales Invoice"]["submit"])

    def test_accounts_user_cannot_create_quotation(self):
        self.assertFalse(ROLE_PERMISSIONS["Accounts User"]["Quotation"]["create"])

    def test_accounts_user_cannot_write_quotation(self):
        self.assertFalse(ROLE_PERMISSIONS["Accounts User"]["Quotation"]["write"])

    def test_accounts_user_quotation_is_read_only(self):
        perms = ROLE_PERMISSIONS["Accounts User"]["Quotation"]
        self.assertTrue(perms["read"])
        self.assertFalse(perms["create"])
        self.assertFalse(perms["write"])
        self.assertFalse(perms["delete"])

    def test_accounts_user_can_read_payment_entry(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts User"]["Payment Entry"]["read"])

    def test_accounts_user_can_submit_payment_entry(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts User"]["Payment Entry"]["submit"])

    # ── Accounts Manager ────────────────────────────────────────────

    def test_accounts_manager_can_cancel_sales_invoice(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts Manager"]["Sales Invoice"]["cancel"])

    def test_accounts_manager_can_cancel_payment_entry(self):
        self.assertTrue(ROLE_PERMISSIONS["Accounts Manager"]["Payment Entry"]["cancel"])

    def test_accounts_manager_quotation_is_read_only(self):
        perms = ROLE_PERMISSIONS["Accounts Manager"]["Quotation"]
        self.assertTrue(perms["read"])
        self.assertFalse(perms["write"])

    # ── Customer (portal) matrix ────────────────────────────────────

    def test_customer_can_read_own_quotation(self):
        self.assertTrue(ROLE_PERMISSIONS["Customer"]["Quotation"]["read"])

    def test_customer_cannot_create_quotation(self):
        self.assertFalse(ROLE_PERMISSIONS["Customer"]["Quotation"]["create"])

    def test_customer_cannot_write_quotation(self):
        self.assertFalse(ROLE_PERMISSIONS["Customer"]["Quotation"]["write"])

    def test_customer_can_read_own_sales_order(self):
        self.assertTrue(ROLE_PERMISSIONS["Customer"]["Sales Order"]["read"])

    def test_customer_can_create_sales_order(self):
        """Portal customers may place orders."""
        self.assertTrue(ROLE_PERMISSIONS["Customer"]["Sales Order"]["create"])

    def test_customer_cannot_submit_anything(self):
        for dt in ROLE_PERMISSIONS["Customer"]:
            self.assertFalse(
                ROLE_PERMISSIONS["Customer"][dt]["submit"],
                f"Customer should not be able to submit {dt}",
            )

    def test_customer_scope_is_own(self):
        for dt in ROLE_PERMISSIONS["Customer"]:
            self.assertEqual(
                ROLE_PERMISSIONS["Customer"][dt]["scope"], "own",
                f"Customer scope for {dt} must be 'own'",
            )

    def test_customer_forbidden_doctypes_not_in_matrix(self):
        """Forbidden DocTypes must not appear in the Customer matrix."""
        for dt in CUSTOMER_FORBIDDEN:
            self.assertNotIn(
                dt, ROLE_PERMISSIONS.get("Customer", {}),
                f"{dt} must not be in Customer matrix",
            )


class TestRBACToolAccess(IntegrationTestCase):
    """Tests that tool visibility is correct per role."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")

        for role, email in _ROLE_USERS.items():
            if not frappe.db.exists("User", email):
                frappe.get_doc({
                    "doctype": "User",
                    "email": email,
                    "first_name": email.split("@")[0],
                    "user_type": "System User",
                    "roles": [{"role": role}],
                }).insert(ignore_permissions=True)

        frappe.db.commit()

    def setUp(self) -> None:
        self.addCleanup(frappe.set_user, "Administrator")

    def _tools_for(self, role: str) -> list[str]:
        frappe.set_user(_ROLE_USERS[role])
        return filter_tools_for_user(ALL_TOOLS)

    # ── Sales User tool visibility ──────────────────────────────────

    def test_sales_user_sees_read_tools(self):
        tools = self._tools_for("Sales User")
        self.assertIn("search_records", tools)
        self.assertIn("get_record", tools)

    def test_sales_user_sees_write_tools(self):
        tools = self._tools_for("Sales User")
        self.assertIn("create_record", tools)
        self.assertIn("update_record", tools)

    def test_sales_user_sees_sell_tools(self):
        tools = self._tools_for("Sales User")
        self.assertIn("draft_quotation", tools)
        self.assertIn("submit_quotation", tools)

    def test_sales_user_does_not_see_submit_cancel(self):
        tools = self._tools_for("Sales User")
        self.assertNotIn("submit_document", tools)
        self.assertNotIn("cancel_document", tools)

    def test_sales_user_does_not_see_manager_brief(self):
        tools = self._tools_for("Sales User")
        self.assertNotIn("manager_brief", tools)

    def test_sales_user_does_not_see_opening_stock(self):
        tools = self._tools_for("Sales User")
        self.assertNotIn("add_opening_stock", tools)

    def test_sales_user_does_not_see_portal_tools(self):
        tools = self._tools_for("Sales User")
        self.assertNotIn("register_customer", tools)
        self.assertNotIn("my_account", tools)
        self.assertNotIn("my_documents", tools)
        self.assertNotIn("my_document", tools)

    # ── Sales Manager tool visibility ───────────────────────────────

    def test_sales_manager_sees_submit_cancel(self):
        tools = self._tools_for("Sales Manager")
        self.assertIn("submit_document", tools)
        self.assertIn("cancel_document", tools)

    def test_sales_manager_sees_manager_brief(self):
        tools = self._tools_for("Sales Manager")
        self.assertIn("manager_brief", tools)

    def test_sales_manager_does_not_see_opening_stock(self):
        tools = self._tools_for("Sales Manager")
        self.assertNotIn("add_opening_stock", tools)

    # ── Sales Master Manager tool visibility ────────────────────────

    def test_sales_admin_sees_opening_stock(self):
        tools = self._tools_for("Sales Master Manager")
        self.assertIn("add_opening_stock", tools)

    def test_sales_admin_sees_all_staff_tools(self):
        tools = self._tools_for("Sales Master Manager")
        for tool_name in ("submit_document", "cancel_document", "manager_brief",
                          "draft_quotation", "convert_lead_to_customer"):
            self.assertIn(tool_name, tools, f"Sales Master Manager should see {tool_name}")

    # ── Accounts User tool visibility ───────────────────────────────

    def test_accounts_user_sees_read_and_analyse(self):
        tools = self._tools_for("Accounts User")
        self.assertIn("search_records", tools)
        self.assertIn("measure_records", tools)
        self.assertIn("run_sales_report", tools)

    def test_accounts_user_sees_submit_cancel(self):
        tools = self._tools_for("Accounts User")
        self.assertIn("submit_document", tools)
        self.assertIn("cancel_document", tools)

    def test_accounts_user_does_not_see_sell_tools(self):
        tools = self._tools_for("Accounts User")
        self.assertNotIn("draft_quotation", tools)
        self.assertNotIn("submit_quotation", tools)
        self.assertNotIn("convert_quotation_to_order", tools)

    def test_accounts_user_does_not_see_lead_tools(self):
        tools = self._tools_for("Accounts User")
        self.assertNotIn("convert_lead_to_customer", tools)
        self.assertNotIn("convert_lead_to_opportunity", tools)

    def test_accounts_user_does_not_see_advisor_tools(self):
        tools = self._tools_for("Accounts User")
        self.assertNotIn("forecast_revenue", tools)
        self.assertNotIn("segment_customers", tools)

    # ── Accounts Manager tool visibility ────────────────────────────

    def test_accounts_manager_sees_write_tools(self):
        tools = self._tools_for("Accounts Manager")
        self.assertIn("create_record", tools)
        self.assertIn("update_record", tools)

    def test_accounts_manager_sees_manager_brief(self):
        tools = self._tools_for("Accounts Manager")
        self.assertIn("manager_brief", tools)


class TestRBACPermissionChecks(IntegrationTestCase):
    """Integration tests: check_ai_permission with real users."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")

        for role, email in _ROLE_USERS.items():
            if not frappe.db.exists("User", email):
                frappe.get_doc({
                    "doctype": "User",
                    "email": email,
                    "first_name": email.split("@")[0],
                    "user_type": "System User",
                    "roles": [{"role": role}],
                }).insert(ignore_permissions=True)

        frappe.db.commit()

    def setUp(self) -> None:
        self.addCleanup(frappe.set_user, "Administrator")

    # ── Sales User: allowed reads, blocked submits ──────────────────

    def test_sales_user_can_read_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="read",
        )
        self.assertTrue(result.allowed)

    def test_sales_user_cannot_submit_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="submit",
        )
        self.assertFalse(result.allowed)

    def test_sales_user_cannot_cancel_sales_order(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Sales Order", action="cancel",
        )
        self.assertFalse(result.allowed)

    def test_sales_user_can_create_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="create",
        )
        self.assertTrue(result.allowed)

    def test_sales_user_scope_is_own(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="read",
        )
        self.assertEqual(result.scope, "own")

    # ── Sales Manager: can submit/cancel ────────────────────────────

    def test_sales_manager_can_submit_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales Manager"], doctype="Quotation", action="submit",
        )
        self.assertTrue(result.allowed)

    def test_sales_manager_can_cancel_sales_order(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales Manager"], doctype="Sales Order", action="cancel",
        )
        self.assertTrue(result.allowed)

    def test_sales_manager_scope_is_team(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales Manager"], doctype="Quotation", action="read",
        )
        self.assertEqual(result.scope, "team")

    # ── Sales Master Manager: full access + company scope ───────────

    def test_sales_admin_matrix_allows_submit_delivery_note(self):
        """The RBAC matrix allows it — ERPNext may still deny if the role lacks DocPerm."""
        self.assertTrue(ROLE_PERMISSIONS["Sales Master Manager"]["Delivery Note"]["submit"])

    def test_sales_admin_scope_is_company(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales Master Manager"], doctype="Quotation", action="read",
        )
        self.assertEqual(result.scope, "company")

    # ── Accounts User: financial access only ────────────────────────

    def test_accounts_user_can_read_sales_invoice(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts User"], doctype="Sales Invoice", action="read",
        )
        self.assertTrue(result.allowed)

    def test_accounts_user_can_submit_sales_invoice(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts User"], doctype="Sales Invoice", action="submit",
        )
        self.assertTrue(result.allowed)

    def test_accounts_user_cannot_create_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts User"], doctype="Quotation", action="create",
        )
        self.assertFalse(result.allowed)

    def test_accounts_user_cannot_write_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts User"], doctype="Quotation", action="write",
        )
        self.assertFalse(result.allowed)

    # ── Accounts Manager ────────────────────────────────────────────

    def test_accounts_manager_can_cancel_payment_entry(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts Manager"], doctype="Payment Entry", action="cancel",
        )
        self.assertTrue(result.allowed)

    def test_accounts_manager_cannot_write_quotation(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts Manager"], doctype="Quotation", action="write",
        )
        self.assertFalse(result.allowed)

    # ── Administrator: falls through (no matrix restriction) ────────

    def test_administrator_is_always_allowed(self):
        result = check_ai_permission(
            user="Administrator", doctype="Quotation", action="submit",
        )
        self.assertTrue(result.allowed)

    # ── Cross-role escalation checks ────────────────────────────────

    def test_sales_user_cannot_escalate_to_submit(self):
        """A Sales User must not be able to submit, even if ERPNext allows it."""
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Sales Invoice", action="submit",
        )
        self.assertFalse(result.allowed)

    def test_accounts_user_cannot_create_lead(self):
        """Accounts User has no CRM access."""
        result = check_ai_permission(
            user=_ROLE_USERS["Accounts User"], doctype="Lead", action="create",
        )
        self.assertFalse(result.allowed)

    # ── Unknown DocTypes fall through ───────────────────────────────

    def test_unknown_doctype_falls_through_for_known_role(self):
        """A DocType not in the matrix should not be blocked by the RBAC layer."""
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="ToDo", action="read",
        )
        # ToDo is not in the matrix — falls through to ERPNext.
        self.assertTrue(result.allowed)

    # ── PermissionResult structure ──────────────────────────────────

    def test_result_has_matched_role(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="read",
        )
        self.assertEqual(result.matched_role, "Sales User")

    def test_denial_has_reason(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="submit",
        )
        self.assertFalse(result.allowed)
        self.assertIsNotNone(result.reason)
        self.assertIn("submit", result.reason.lower())

    def test_result_to_dict(self):
        result = check_ai_permission(
            user=_ROLE_USERS["Sales User"], doctype="Quotation", action="read",
        )
        d = result.to_dict()
        self.assertIn("allowed", d)
        self.assertIn("user", d)
        self.assertIn("doctype", d)
        self.assertEqual(d["doctype"], "Quotation")


class TestRBACGuardEntry(IntegrationTestCase):
    """Tests that guard_entry() blocks non-sales roles."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")

        # HR-only user — no sales role at all.
        cls.hr_user = "rbac-hr-only@example.com"
        if not frappe.db.exists("User", cls.hr_user):
            frappe.get_doc({
                "doctype": "User",
                "email": cls.hr_user,
                "first_name": "HR Only",
                "user_type": "System User",
                "roles": [{"role": "HR User"}],
            }).insert(ignore_permissions=True)

        frappe.db.commit()

    def setUp(self) -> None:
        self.addCleanup(frappe.set_user, "Administrator")

    def test_hr_user_is_blocked(self):
        from sales_ai.api import guard_entry
        frappe.set_user(self.hr_user)
        with self.assertRaises(frappe.PermissionError):
            guard_entry()

    def test_sales_user_is_allowed(self):
        from sales_ai.api import guard_entry
        frappe.set_user(_ROLE_USERS["Sales User"])
        guard_entry()  # Should not raise.

    def test_accounts_user_is_allowed(self):
        from sales_ai.api import guard_entry
        frappe.set_user(_ROLE_USERS["Accounts User"])
        guard_entry()  # Should not raise.

    def test_administrator_is_allowed(self):
        from sales_ai.api import guard_entry
        frappe.set_user("Administrator")
        guard_entry()  # Should not raise.
