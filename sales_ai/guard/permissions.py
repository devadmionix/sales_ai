# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Central permission service for the Sales AI chatbot.

Every permission decision in the chatbot funnels through ``check_ai_permission``.
It answers one question: "may *this user* perform *this action* on *this DocType*
(and optionally *this document*)?"

The answer is always a ``PermissionResult`` — a structured dict that says what was
decided, why, and who was asking. The caller never has to interpret an exception or a
boolean; the result is the whole story.

Design principles:

1. **ERPNext is the authority.** This module never grants access that ERPNext would
   refuse. It calls ``frappe.has_permission`` for every decision, so role permissions,
   User Permissions, company restrictions, territory restrictions and ownership all
   apply exactly as they do in the desk.

2. **The chatbot may *narrow* but never *widen*.** The ``ROLE_PERMISSIONS`` matrix
   below is a ceiling, not a floor. A Sales User whose matrix entry says
   ``submit=False`` is refused even if ERPNext would technically allow it — because
   giving the AI assistant the power to submit is a different risk profile from giving
   the human that power in the desk.

3. **One function, one log.** Every check is recorded in the action log when it is a
   denial, so an admin can see what was refused and why. Allowed checks are not logged
   here — the tool that runs afterwards logs the action it took.

4. **Scope matters.** Each role–DocType pair has a ``scope`` that says how wide the
   user's view is: ``own`` (only their records), ``team`` (their reports' records too),
   ``company`` (the whole company), or ``all``. The scope is enforced by ERPNext's own
   User Permissions, not by SQL added here — but the chatbot reads it to give honest
   answers when the user asks "can I see all invoices?" rather than letting them find
   out the hard way.

Extending this:

- To add a new role, add its entry to ``ROLE_PERMISSIONS``.
- To add a new DocType, add its entry under every role that should see it.
- To change what a role may do, change its flags. The change takes effect on the
  next request; nothing is cached across requests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import frappe
from frappe import _

from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import (
    record_denial,
)

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionResult:
    """The outcome of a permission check — always returned, never raised."""

    allowed: bool
    reason: str | None = None
    user: str = ""
    doctype: str = ""
    document: str | None = None
    action: str = "read"
    # Which role produced the decision, so a log reader can trace it.
    matched_role: str | None = None
    # The scope the user has on this DocType (own / team / company / all).
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Role permission matrix
# ---------------------------------------------------------------------------
# Each entry says what the *chatbot* will allow for a role, which is always ≤
# what ERPNext allows.  ``scope`` is informational — it describes the breadth
# of the user's view and is enforced by ERPNext's User Permissions, not here.
#
# Actions: read, create, write, delete, submit, cancel, report
#
# Missing DocTypes default to read-only (if ERPNext allows read at all).
# Missing roles are not refused — the function falls through to ERPNext.

_RO = {"read": True, "create": False, "write": False, "delete": False,
       "submit": False, "cancel": False, "report": False}

ROLE_PERMISSIONS: dict[str, dict[str, dict[str, Any]]] = {
    # -----------------------------------------------------------------
    # Sales User
    # -----------------------------------------------------------------
    "Sales User": {
        "Lead": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Opportunity": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Quotation": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Sales Order": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Sales Invoice": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Delivery Note": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Customer": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Contact": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "own",
        },
        "Item": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "all",
        },
        "Item Price": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "all",
        },
        "Payment Entry": {
            **_RO, "scope": "own",
        },
    },

    # -----------------------------------------------------------------
    # Sales Manager
    # -----------------------------------------------------------------
    "Sales Manager": {
        "Lead": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "team",
        },
        "Opportunity": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "team",
        },
        "Quotation": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "team",
        },
        "Sales Order": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "team",
        },
        "Sales Invoice": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "team",
        },
        "Delivery Note": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "team",
        },
        "Customer": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "team",
        },
        "Contact": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "team",
        },
        "Item": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "all",
        },
        "Item Price": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "all",
        },
        "Payment Entry": {
            **_RO, "scope": "team",
        },
    },

    # -----------------------------------------------------------------
    # Sales Master Manager  (Sales Administrator / Sales Operations)
    # -----------------------------------------------------------------
    "Sales Master Manager": {
        "Lead": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Opportunity": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Quotation": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Sales Order": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Sales Invoice": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Delivery Note": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Customer": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Contact": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Item": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "all",
        },
        "Item Price": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": False, "cancel": False, "report": True,
            "scope": "all",
        },
        "Payment Entry": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": True, "cancel": False, "report": True,
            "scope": "company",
        },
    },

    # -----------------------------------------------------------------
    # Accounts User
    # -----------------------------------------------------------------
    "Accounts User": {
        "Sales Invoice": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Payment Entry": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": True, "cancel": False, "report": True,
            "scope": "company",
        },
        "Sales Order": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Quotation": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Customer": {
            **_RO, "report": True, "scope": "company",
        },
        "Item": {
            **_RO, "report": True, "scope": "all",
        },
        "Item Price": {
            **_RO, "report": True, "scope": "all",
        },
    },

    # -----------------------------------------------------------------
    # Accounts Manager
    # -----------------------------------------------------------------
    "Accounts Manager": {
        "Sales Invoice": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Payment Entry": {
            "read": True, "create": True, "write": True, "delete": True,
            "submit": True, "cancel": True, "report": True,
            "scope": "company",
        },
        "Sales Order": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Quotation": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Customer": {
            "read": True, "create": True, "write": True, "delete": False,
            "submit": False, "cancel": False, "report": True,
            "scope": "company",
        },
        "Item": {
            **_RO, "report": True, "scope": "all",
        },
        "Item Price": {
            **_RO, "report": True, "scope": "all",
        },
    },

    # -----------------------------------------------------------------
    # Customer (portal user)
    # -----------------------------------------------------------------
    "Customer": {
        "Quotation": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": False,
            "scope": "own",
        },
        "Sales Order": {
            "read": True, "create": True, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": False,
            "scope": "own",
        },
        "Sales Invoice": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": False,
            "scope": "own",
        },
        "Customer": {
            "read": True, "create": False, "write": False, "delete": False,
            "submit": False, "cancel": False, "report": False,
            "scope": "own",
        },
    },
}

# -----------------------------------------------------------------
# Convenience alias: "System Manager" and "Administrator" get everything
# without needing a matrix entry — they fall through to ERPNext, which
# already grants them full access.
# -----------------------------------------------------------------

# Roles that are considered "sales director" level — broad visibility,
# limited destructive actions through the chatbot.
_DIRECTOR_DOCTYPES = (
    "Lead", "Opportunity", "Quotation", "Sales Order", "Sales Invoice",
    "Delivery Note", "Customer", "Contact", "Item", "Item Price",
)

# DocTypes that a Customer/portal user must NEVER reach through the chatbot.
CUSTOMER_FORBIDDEN = frozenset({
    "Lead", "Opportunity", "Delivery Note", "Contact", "Item", "Item Price",
    "Payment Entry", "Sales Person", "Employee", "Company",
})


# Every DocType that appears under *any* role in the matrix.  Used to tell a
# sales-domain DocType the user was deliberately excluded from (deny) apart
# from an internal DocType the matrix has no opinion about (fall through).
_ALL_MATRIX_DOCTYPES: frozenset[str] = frozenset(
    dt for role_map in ROLE_PERMISSIONS.values() for dt in role_map
)

# ---------------------------------------------------------------------------
# Priority order for role resolution
# ---------------------------------------------------------------------------
# When a user has multiple roles, the most privileged one wins.  This is the
# order from most to least privileged.  Roles not listed here are treated as
# lower priority and fall through to ERPNext.

ROLE_PRIORITY = (
    "Administrator",
    "System Manager",
    "Sales Master Manager",
    "Accounts Manager",
    "Sales Manager",
    "Accounts User",
    "Sales User",
    "Customer",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_ai_permission(
    user: str | None = None,
    doctype: str = "",
    document_name: str | None = None,
    action: str = "read",
) -> PermissionResult:
    """Central permission check for every chatbot operation.

    Parameters
    ----------
    user
        The login email.  Defaults to ``frappe.session.user``.
    doctype
        The DocType being acted on (e.g. ``"Quotation"``).
    document_name
        The specific record, if one is named.
    action
        One of: ``read``, ``create``, ``write``, ``delete``, ``submit``,
        ``cancel``, ``report``.

    Returns
    -------
    PermissionResult
        Always returned, never raised.  ``allowed=False`` carries a reason.
    """
    user = user or frappe.session.user

    # --- Guest is never allowed ---
    if not user or user == "Guest":
        return _deny(user, doctype, document_name, action,
                     _("Please log in to use the assistant."))

    # --- Resolve the user's best role ---
    user_roles = frappe.get_roles(user)
    best_role = _best_role(user_roles)

    # --- Portal / Customer check ---
    user_type = frappe.db.get_value("User", user, "user_type")
    if user_type != "System User":
        if "Customer" not in user_roles:
            return _deny(user, doctype, document_name, action,
                         _("The assistant is not available for this account."),
                         matched_role="(none)")
        best_role = "Customer"

    # --- Customer-specific blocks ---
    if best_role == "Customer":
        if doctype in CUSTOMER_FORBIDDEN:
            return _deny(user, doctype, document_name, action,
                         _("This information is not available to customer accounts."),
                         matched_role="Customer", scope="own")

    # --- Matrix lookup ---
    matrix_entry = _matrix_entry(best_role, doctype)

    if matrix_entry is not None:
        scope = matrix_entry.get("scope", "own")
        action_allowed = matrix_entry.get(action, False)

        if not action_allowed:
            reason = _(
                "Your role ({role}) does not allow {action} on {doctype} "
                "through the assistant."
            ).format(role=best_role, action=action, doctype=doctype)
            return _deny(user, doctype, document_name, action, reason,
                         matched_role=best_role, scope=scope)
    else:
        # No matrix entry for this role + DocType pair.
        #
        # Two cases:
        # 1. The DocType appears under *some other* role in the matrix — it is a
        #    sales-domain DocType that this role was deliberately not given.  Deny.
        # 2. The DocType does not appear in the matrix at all (e.g. ToDo, Comment,
        #    Address) — it is an internal DocType the guard uses behind the scenes.
        #    Fall through to ERPNext's native permission check.
        scope = None
        if best_role in ROLE_PERMISSIONS and doctype and doctype in _ALL_MATRIX_DOCTYPES:
            reason = _(
                "Your role ({role}) does not have access to {doctype} "
                "through the assistant."
            ).format(role=best_role, doctype=doctype)
            return _deny(user, doctype, document_name, action, reason,
                         matched_role=best_role)

    # --- ERPNext native check (the real authority) ---
    if doctype:
        ptype = _erpnext_ptype(action)
        try:
            if document_name:
                if not frappe.has_permission(doctype, ptype, doc=document_name, user=user):
                    return _deny(
                        user, doctype, document_name, action,
                        _("You do not have {action} permission on {doctype} {name}.").format(
                            action=action, doctype=doctype, name=document_name),
                        matched_role=best_role,
                        scope=scope,
                    )
            else:
                if not frappe.has_permission(doctype, ptype, user=user):
                    return _deny(
                        user, doctype, document_name, action,
                        _("You do not have {action} permission on {doctype}.").format(
                            action=action, doctype=doctype),
                        matched_role=best_role,
                        scope=scope,
                    )
        except frappe.PermissionError:
            return _deny(
                user, doctype, document_name, action,
                _("You do not have {action} permission on {doctype}.").format(
                    action=action, doctype=doctype),
                matched_role=best_role,
                scope=scope,
            )

    # --- Allowed ---
    return PermissionResult(
        allowed=True,
        reason=None,
        user=user,
        doctype=doctype,
        document=document_name,
        action=action,
        matched_role=best_role,
        scope=scope if matrix_entry else None,
    )


def get_user_scope(user: str | None = None, doctype: str = "") -> str | None:
    """What breadth of data this user sees for a DocType: own / team / company / all.

    Returns ``None`` when the matrix has no opinion (Administrator, unknown role).
    """
    user = user or frappe.session.user
    best_role = _best_role(frappe.get_roles(user))
    entry = _matrix_entry(best_role, doctype)
    return entry.get("scope") if entry else None


def get_user_permissions_summary(user: str | None = None) -> dict[str, Any]:
    """A summary of what the calling user may do, for diagnostics and the prompt.

    Returns a dict keyed by DocType, each holding the allowed actions and scope.
    Only DocTypes the user has at least ``read`` on are included.
    """
    user = user or frappe.session.user
    user_roles = frappe.get_roles(user)
    best_role = _best_role(user_roles)

    matrix = ROLE_PERMISSIONS.get(best_role, {})
    summary: dict[str, dict[str, Any]] = {}

    for dt, perms in matrix.items():
        if not perms.get("read"):
            continue
        # Only include if ERPNext also allows read.
        try:
            if not frappe.has_permission(dt, "read", user=user):
                continue
        except (frappe.PermissionError, frappe.DoesNotExistError):
            continue

        actions = [
            action for action in ("read", "create", "write", "delete",
                                  "submit", "cancel", "report")
            if perms.get(action)
        ]
        summary[dt] = {
            "actions": actions,
            "scope": perms.get("scope", "own"),
        }

    return {
        "user": user,
        "role": best_role,
        "doctypes": summary,
    }


def allowed_doctypes(user: str | None = None, action: str = "read") -> list[str]:
    """DocTypes this user may perform ``action`` on, per the matrix + ERPNext."""
    user = user or frappe.session.user
    best_role = _best_role(frappe.get_roles(user))
    matrix = ROLE_PERMISSIONS.get(best_role, {})

    result = []
    for dt, perms in matrix.items():
        if not perms.get(action):
            continue
        try:
            ptype = _erpnext_ptype(action)
            if frappe.has_permission(dt, ptype, user=user):
                result.append(dt)
        except (frappe.PermissionError, frappe.DoesNotExistError):
            continue
    return sorted(result)


def is_customer_user(user: str | None = None) -> bool:
    """Whether this user is a portal / Customer user."""
    user = user or frappe.session.user
    user_type = frappe.db.get_value("User", user, "user_type")
    return user_type != "System User"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _best_role(user_roles: list[str]) -> str | None:
    """Pick the most privileged role from the user's role list."""
    for role in ROLE_PRIORITY:
        if role in user_roles:
            return role
    # No known role — will fall through to ERPNext checks only.
    return None


def _matrix_entry(role: str | None, doctype: str) -> dict[str, Any] | None:
    """Look up the matrix entry for a role + DocType.  ``None`` if not found."""
    if not role or not doctype:
        return None
    role_map = ROLE_PERMISSIONS.get(role)
    if role_map is None:
        return None
    return role_map.get(doctype)


def _erpnext_ptype(action: str) -> str:
    """Map a chatbot action name to the ERPNext permission type string.

    ERPNext uses ``"read"``, ``"write"``, ``"create"``, ``"delete"``,
    ``"submit"``, ``"cancel"``.  ``"report"`` is checked as ``"report"``
    in ERPNext (it controls Script Report access).
    """
    return action if action != "report" else "report"


def _deny(
    user: str,
    doctype: str,
    document_name: str | None,
    action: str,
    reason: str,
    *,
    matched_role: str | None = None,
    scope: str | None = None,
) -> PermissionResult:
    """Build a denial result and log it."""
    record_denial(
        action=action.title(),
        tool=frappe.flags.get("sales_ai_tool"),
        reference_doctype=doctype or None,
        reference_name=document_name,
        reason=reason,
    )
    return PermissionResult(
        allowed=False,
        reason=reason,
        user=user,
        doctype=doctype,
        document=document_name,
        action=action,
        matched_role=matched_role,
        scope=scope,
    )
