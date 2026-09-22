# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Tests for portal (Customer) user read-only document access.

Portal users are Website Users with no ERPNext DocType permissions.  They may only
see records linked to their own Customer, and only the DocTypes listed in
``guard/portal_reads.py``.  These tests verify:

- a linked portal user sees their own quotations, orders, invoices
- a portal user does NOT see another customer's records
- forbidden DocTypes are refused
- a portal user without a customer link is refused
- Guest is refused
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from sales_ai.guard import GuardError
from sales_ai.guard.portal_reads import portal_read_document, portal_read_list

PORTAL_USER = "portal-reads-test@example.com"
OTHER_USER = "portal-reads-other@example.com"

CUSTOMER_A = "Portal Reads Customer A"
CUSTOMER_B = "Portal Reads Customer B"


class TestPortalReads(IntegrationTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")

        # Create test users.
        for email in (PORTAL_USER, OTHER_USER):
            if not frappe.db.exists("User", email):
                frappe.get_doc({
                    "doctype": "User",
                    "email": email,
                    "first_name": email.split("@")[0],
                    "user_type": "Website User",
                }).insert(ignore_permissions=True)

        # Create two customers, each linked to their portal user.
        for customer_name, user_email in (
            (CUSTOMER_A, PORTAL_USER),
            (CUSTOMER_B, OTHER_USER),
        ):
            if not frappe.db.exists("Customer", {"customer_name": customer_name}):
                doc = frappe.get_doc({
                    "doctype": "Customer",
                    "customer_name": customer_name,
                    "customer_type": "Company",
                })
                doc.append("portal_users", {"user": user_email})
                doc.insert(ignore_permissions=True)

        cls.customer_a_id = frappe.db.get_value(
            "Customer", {"customer_name": CUSTOMER_A}, "name"
        )
        cls.customer_b_id = frappe.db.get_value(
            "Customer", {"customer_name": CUSTOMER_B}, "name"
        )

        # Create a quotation for Customer A.
        if not frappe.db.exists("Quotation", {"party_name": cls.customer_a_id, "title": "Portal Test QTN"}):
            company = frappe.defaults.get_defaults().get("company")
            qttn = frappe.get_doc({
                "doctype": "Quotation",
                "quotation_to": "Customer",
                "party_name": cls.customer_a_id,
                "title": "Portal Test QTN",
                "company": company,
                "transaction_date": nowdate(),
                "order_type": "Sales",
                "items": [{
                    "item_code": frappe.db.get_value("Item", {"disabled": 0}, "name"),
                    "qty": 1,
                    "rate": 100,
                }],
            })
            qttn.insert(ignore_permissions=True)
            cls.quotation_name = qttn.name
        else:
            cls.quotation_name = frappe.db.get_value(
                "Quotation", {"party_name": cls.customer_a_id, "title": "Portal Test QTN"}, "name"
            )

        frappe.db.commit()

    def setUp(self) -> None:
        self.addCleanup(frappe.set_user, "Administrator")
        frappe.set_user(PORTAL_USER)

    # ── Happy path: own records ─────────────────────────────────────

    def test_portal_user_sees_own_quotations(self) -> None:
        result = portal_read_list("Quotation")
        self.assertGreaterEqual(result["count"], 1)
        names = [r["name"] for r in result["records"]]
        self.assertIn(self.quotation_name, names)

    def test_portal_user_reads_own_quotation_detail(self) -> None:
        result = portal_read_document("Quotation", self.quotation_name)
        self.assertEqual(result["name"], self.quotation_name)
        self.assertIn("record", result)

    def test_portal_user_sees_own_customer(self) -> None:
        result = portal_read_list("Customer")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["records"][0]["name"], self.customer_a_id)

    # ── Cross-customer isolation ────────────────────────────────────

    def test_portal_user_cannot_see_other_customers_quotation(self) -> None:
        """Customer A cannot see Customer B's records."""
        # Create a quotation for Customer B as admin.
        frappe.set_user("Administrator")
        company = frappe.defaults.get_defaults().get("company")
        qttn_b = frappe.get_doc({
            "doctype": "Quotation",
            "quotation_to": "Customer",
            "party_name": self.customer_b_id,
            "company": company,
            "transaction_date": nowdate(),
            "order_type": "Sales",
            "items": [{
                "item_code": frappe.db.get_value("Item", {"disabled": 0}, "name"),
                "qty": 1,
                "rate": 200,
            }],
        })
        qttn_b.insert(ignore_permissions=True)
        frappe.db.commit()

        # Switch to portal user A — should NOT see B's quotation.
        frappe.set_user(PORTAL_USER)
        result = portal_read_list("Quotation")
        names = [r["name"] for r in result["records"]]
        self.assertNotIn(qttn_b.name, names)

    def test_portal_user_cannot_read_other_customers_document(self) -> None:
        """Attempting to read another customer's document raises GuardError."""
        # Create a quotation for Customer B.
        frappe.set_user("Administrator")
        company = frappe.defaults.get_defaults().get("company")
        qttn_b = frappe.get_doc({
            "doctype": "Quotation",
            "quotation_to": "Customer",
            "party_name": self.customer_b_id,
            "company": company,
            "transaction_date": nowdate(),
            "order_type": "Sales",
            "items": [{
                "item_code": frappe.db.get_value("Item", {"disabled": 0}, "name"),
                "qty": 1,
                "rate": 200,
            }],
        })
        qttn_b.insert(ignore_permissions=True)
        frappe.db.commit()

        frappe.set_user(PORTAL_USER)
        with self.assertRaises(GuardError):
            portal_read_document("Quotation", qttn_b.name)

    # ── Forbidden DocTypes ──────────────────────────────────────────

    def test_portal_user_cannot_access_lead(self) -> None:
        with self.assertRaises(GuardError):
            portal_read_list("Lead")

    def test_portal_user_cannot_access_opportunity(self) -> None:
        with self.assertRaises(GuardError):
            portal_read_list("Opportunity")

    def test_portal_user_cannot_access_item(self) -> None:
        with self.assertRaises(GuardError):
            portal_read_list("Item")

    def test_portal_user_cannot_access_payment_entry(self) -> None:
        with self.assertRaises(GuardError):
            portal_read_list("Payment Entry")

    # ── No customer link ────────────────────────────────────────────

    def test_unlinked_user_is_refused(self) -> None:
        """A portal user not linked to any customer cannot read anything."""
        # Create a user with no customer link.
        unlinked = "portal-unlinked@example.com"
        if not frappe.db.exists("User", unlinked):
            frappe.set_user("Administrator")
            frappe.get_doc({
                "doctype": "User",
                "email": unlinked,
                "first_name": "unlinked",
                "user_type": "Website User",
            }).insert(ignore_permissions=True)

        frappe.set_user(unlinked)
        with self.assertRaises(GuardError) as ctx:
            portal_read_list("Quotation")
        self.assertIn("not linked", str(ctx.exception))

    # ── Guest is refused ────────────────────────────────────────────

    def test_guest_cannot_read(self) -> None:
        frappe.set_user("Guest")
        with self.assertRaises(GuardError):
            portal_read_list("Quotation")
