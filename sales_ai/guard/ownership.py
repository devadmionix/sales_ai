# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Server-side record ownership enforcement for Sales AI managed DocTypes.

This module is deliberately separate from the AI guard.  Frappe calls the functions in
this file for normal Desk/API permission checks as well, so the same ownership rule applies
whether a record is reached through Sales AI, the Desk, a REST call, search, export, or a
report/list query.

Rules:
- Administrator/System Manager and manager-level sales roles are not owner restricted.
- A role whose Sales AI scope is ``own`` may only access documents whose ``owner`` is the
  authenticated user.
- Create is not restricted here because Frappe assigns ``owner`` when the document is
  inserted.  The application must never trust a client-supplied owner for normal users.
- Returning ``False`` from ``has_permission`` is an explicit deny. Returning ``None`` lets
  Frappe continue with its normal permission evaluation.
"""

from __future__ import annotations

from typing import Any

import frappe

from sales_ai.guard.permissions import ROLE_PERMISSIONS, get_user_scope

# All DocTypes for which the Sales AI permission matrix has an explicit opinion.  The
# runtime scope check decides whether the current user is actually owner-restricted.
MANAGED_DOCTYPES = frozenset(
    doctype
    for role_map in ROLE_PERMISSIONS.values()
    for doctype in role_map
)

# Frappe permission types that operate on an existing document.  Create is intentionally
# excluded: ownership is assigned by Frappe during insert and is not accepted from the
# normal user's request payload.
DOCUMENT_ACTIONS = frozenset({"read", "write", "delete", "submit", "cancel"})


def _scope(user: str | None, doctype: str) -> str | None:
    """Return the Sales AI scope for a user without widening native permissions."""
    if not user or user == "Guest":
        return None
    try:
        return get_user_scope(user=user, doctype=doctype)
    except Exception:
        # Permission hooks must never turn an unrelated login/request error into a 500.
        # Native Frappe permission handling remains the fallback.
        return None


def is_owner_restricted(user: str | None, doctype: str) -> bool:
    """Whether this user/DocType pair requires owner-only access."""
    return doctype in MANAGED_DOCTYPES and _scope(user, doctype) == "own"


def permission_query_conditions(user: str | None = None) -> str | None:
    """Return a SQL condition used by Frappe list/search/export queries.

    Frappe combines this condition with its normal permission conditions.  It therefore
    narrows access for owner-scoped users without granting anything to managers/admins.
    """
    user = user or frappe.session.user
    doctype = frappe.local.flags.get("permission_query_doctype")

    # Frappe normally supplies the DocType through the hook dispatch context.  Some Frappe
    # versions call the hook with the doctype in a local flag; the per-DocType wrapper below
    # is the reliable path and is what hooks.py registers.
    if not doctype or not is_owner_restricted(user, doctype):
        return None

    return f"`tab{doctype}`.`owner` = {frappe.db.escape(user)}"


def get_permission_query_conditions(doctype: str, user: str | None = None) -> str | None:
    """Reliable per-DocType query-condition entry point for hooks.py."""
    user = user or frappe.session.user
    if not is_owner_restricted(user, doctype):
        return None
    return f"`tab{doctype}`.`owner` = {frappe.db.escape(user)}"


def _query_hook_for(doctype: str):
    """Build the one-argument hook Frappe expects for a mapped DocType."""
    def _hook(user: str | None = None) -> str | None:
        return get_permission_query_conditions(doctype, user)
    return _hook




# Frappe's permission_query_conditions hook is invoked with only ``user``.  Because the
# hooks.py mapping is per DocType, expose a small stable wrapper for each managed DocType.
for _doctype in MANAGED_DOCTYPES:
    _slug = "query_" + "_".join(part.lower() for part in _doctype.replace("-", " ").split())
    globals()[_slug] = _query_hook_for(_doctype)

del _doctype, _slug

def has_permission(
    doc: Any | None = None,
    user: str | None = None,
    permission_type: str = "read",
) -> bool | None:
    """Enforce ownership for direct document operations.

    ``None`` is intentional: Frappe still evaluates its standard Role/User Permission
    rules.  This hook only adds the Sales AI ownership restriction.
    """
    user = user or frappe.session.user
    if isinstance(doc, str):
        # A few Frappe versions may pass the document name to the hook.  The hook is
        # registered per DocType, so there is no reliable DocType on a bare name; let
        # native Frappe handling decide in that case.
        return None

    doctype = getattr(doc, "doctype", None)

    if not doctype or permission_type not in DOCUMENT_ACTIONS:
        return None
    if not is_owner_restricted(user, doctype):
        return None

    name = getattr(doc, "name", None)
    if not name:
        return None

    try:
        owner = frappe.db.get_value(doctype, name, "owner")
    except Exception:
        return None

    if not owner:
        # Let native Frappe permission handling decide whether the record exists/accesses.
        return None

    return owner == user




def sql_owner_clause(doctype: str, alias: str | None = None, user: str | None = None) -> tuple[str, tuple[str, ...]]:
    """Return a safe SQL WHERE fragment for raw SQL used by Sales AI analytics.

    Application code should place the returned fragment in an existing WHERE clause.
    Managers/admins receive an empty fragment, preserving their existing visibility.
    """
    user = user or frappe.session.user
    if not is_owner_restricted(user, doctype):
        return "", ()
    column = f"{alias}.owner" if alias else "owner"
    return f" AND {column} = %s", (user,)

def owned_condition(doctype: str, user: str | None = None) -> list[list[Any]]:
    """Return a Frappe filter condition for application-level list/analytics code."""
    user = user or frappe.session.user
    if is_owner_restricted(user, doctype):
        return [["owner", "=", user]]
    return []
