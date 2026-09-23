# Sales AI — Record-Level Access Control

## What was fixed

The application now enforces ownership at the Frappe server permission layer in addition to the existing Sales AI guard.

### Normal owner-scoped user

For a DocType whose Sales AI scope is `own`:

- list/search queries are restricted with `owner = current user`
- direct read access is denied when the document owner is another user
- write/update access is denied for another user's record
- delete access is denied for another user's record
- submit/cancel access is denied for another user's record
- the same ownership rule applies to Sales AI read/write paths
- existing Frappe Role/User Permission rules remain in force; ownership only narrows access
- client-supplied ownership is not used by the Sales AI write allowlist

### Manager/Admin

Roles whose existing Sales AI scope is `team`, `company`, or `all` are not owner-filtered by the new hook. Frappe's normal role/User Permission rules continue to apply.

## Important fail-closed protections

Some Sales AI intelligence modules execute raw SQL over several business tables. They are not automatically safe merely because normal list permissions are safe. Owner-scoped users are therefore blocked from those raw-SQL intelligence tools until each query is explicitly owner-scoped.

The generic `measure_records` analytics path remains permission-aware and should be used for owner-scoped analytics.

Similarly, query reports that do not expose a trustworthy server-enforceable owner/salesperson filter are blocked for owner-scoped users. Reports with an `assigned_to` filter are forced to the authenticated user; reports with a `sales_person` filter require a Sales Person linked to the current user.

## Files changed

- `sales_ai/guard/ownership.py` — new centralized server-side ownership layer
- `sales_ai/guard/__init__.py` — strict owner scope no longer yields to User Permissions
- `sales_ai/hooks.py` — registers Frappe `permission_query_conditions` and `has_permission` hooks for managed DocTypes
- `sales_ai/guard/reports.py` — fail-closed report scoping for owner users
- `sales_ai/tools/advisor.py` — prevents raw-SQL intelligence leakage for owner-scoped users
- `sales_ai/sales_ai/page/sales_dashboard/sales_dashboard.py` — owner filters added to dashboard transaction/opportunity metrics; company-wide target is not exposed to owner-scoped users
- `sales_ai/tests/test_ownership.py` — ownership regression tests

## Database changes

No new ownership field or database column is introduced. The implementation reuses Frappe's existing `owner` field.

## Deployment

After installing/updating the app:

```bash
bench --site <site> migrate
bench --site <site> clear-cache
bench restart
```

Then test with four accounts:

1. User 1 — Sales User
2. User 2 — Sales User
3. Manager — existing Sales Manager/Sales Master Manager/Sales Director role
4. Administrator/System Manager

Create records as User 1 and User 2 and verify direct URLs, list/search, edit, delete, API access, exports and reports.
