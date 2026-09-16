# Who can make the agent do what

This document is the answer to "which roles can the assistant do X for?". It is a map, not
a mechanism. **Nothing in this file is enforced by reading it** — every row below is what
ERPNext already decides, written down so it can be reviewed without reading Python.

The rule the whole app is built on is one sentence:

> The assistant can do exactly what the person talking to it could do by hand in the desk,
> and nothing else.

There is no service account, no elevated worker, no `ignore_permissions=True` on any path
the model can reach. Every read goes through `frappe.get_list`, every write through a
document controller. So if you want to change what the assistant can do for somebody, you
change their roles and User Permissions in ERPNext. There is no separate permission list
here to keep in step, and that is deliberate — a second list is a second thing to get wrong.

---

## 1. The two layers

Every tool call passes two gates before it touches anything.

**Layer 1 — policy.** `sales_ai/guard/policy.py` decides whether the *agent* may act at all:
the profile's autonomy setting, the tool's risk band, the amount on the document, and
whether a human has approved this particular call. This layer can only ever say *no*. It
never grants anything.

**Layer 2 — ERPNext.** `sales_ai/guard/*` then asks Frappe the same questions the desk asks:
`frappe.has_permission`, `doc.check_permission`, User Permissions, permission levels, and
every `validate` hook in every installed app.

A request has to survive both. Layer 1 stops an agent doing something it was not trusted to
do unsupervised; layer 2 stops it doing something the user was never allowed to do at all.

---

## 2. What each tool needs

27 tools. The "needs" column is the ERPNext permission checked on the DocType, on that
specific record, for the logged-in user.

### Reading — no write permission, ever

| Tool | Needs | Notes |
|---|---|---|
| `get_record` | `read` or `select` on the record | Fields limited to the DocType's read spec |
| `search_records` | `read` | Goes through `get_list`, so User Permissions scope it |
| `measure_records` | `read` | Counts and sums over the same scoped query |
| `run_sales_report` | `read` + the report on the allowlist | 10 named ERPNext reports only |
| `check_availability` | `read` on Bin / Warehouse | |
| `price_items` | `read` | Prices via ERPNext; saves nothing |
| `get_churn_risk` | `read` on Customer | |
| `list_follow_ups` | ToDo `read` | Ordinary users see only their own |
| `list_recipients` | `read` on the record | |

### Writing

| Tool | Needs | Risk |
|---|---|---|
| `create_record` | `create` on the DocType | high |
| `update_record` | `write` on the record | high |
| `add_note` | `write` on the record | low |
| `create_follow_up` | `read` on the record; ToDo `create` | low |
| `update_follow_up` | The ToDo is yours — allocated to you or raised by you | medium |
| `assign_record` | `write` on the record; **assignee must already have `read`** | medium |
| `revise_lines` | `write`, and the document must be a draft | medium |
| `draft_quotation` | `create` on Quotation | medium |
| `convert_lead_to_opportunity` | `write` on Lead; `create` on Opportunity | medium |
| `convert_lead_to_customer` | `write` on Lead; `create` on Customer | high |
| `convert_quotation_to_order` | `create` on Sales Order | high |
| `draft_email` | `email` on the record | low |
| `send_email` | `email` on the record | high |

### Submitting and cancelling

| Tool | Needs | Risk |
|---|---|---|
| `submit_quotation` | `submit` on Quotation | high |
| `submit_document` | `submit` on Quotation / Sales Order / Sales Invoice / Delivery Note | high |
| `cancel_document` | `cancel` on the same four | high |

**There is no delete tool.** Not for any DocType, at any risk band, for any role. "Delete
the Sharma order" becomes a cancellation: the numbers come out of the ledger and the record
of what happened stays. A destroyed document cannot be audited, and an agent that misread
which Sharma was meant has destroyed the wrong one. `sales_ai/tests/test_documents.py`
scans the live registry for any tool whose name contains "delete" or "remove" and fails if
one ever appears.

### The customer-facing agent

| Tool | Needs |
|---|---|
| `my_account` | A logged-in Website User, scoped to their own linked Customer |
| `register_customer` | A logged-in Website User with no Customer yet |

These are registered but not available to the internal agent. Registering a tool makes it
*nameable* by an agent profile; each profile lists the tools it may call.

---

## 3. Roles, in practice

ERPNext's own selling roles are the ones that matter. The assistant adds none of its own for
sales work.

| Role | What the assistant can do for them |
|---|---|
| **Sales User** | Read and search within their User Permissions. Create and update leads, customers, contacts and opportunities. Draft quotations, revise draft lines, add notes, set follow-ups, assign work, draft emails. Cannot submit or cancel unless their role profile grants `submit`. |
| **Sales Manager** | Everything a Sales User can, plus submit and cancel, and usually a wider or unrestricted record scope. |
| **Accounts User / Manager** | Sales Invoice submit and cancel, if their role has it. Reading invoices needs nothing extra. |
| **Stock User / Manager** | Delivery Note submit and cancel, if their role has it. |
| **Website User (portal)** | `my_account` and `register_customer` only. Never reaches any tool above. |
| **System Manager** | Everything, because ERPNext gives them everything. Note that layer 1 still applies: a high-risk tool still asks for approval unless the profile's autonomy says otherwise. |

The assistant's *own* administration is separate:

| Role | Purpose |
|---|---|
| **Sales AI Manager** | Configure agent profiles, action policies, playbooks and settings. Read the action log and the eval cases. |
| **Sales AI User** | Use the chat panel. Does not on its own grant any access to sales data — the user's ERPNext roles do that. |

---

## 4. Scoping records: User Permissions

This is where most real deployments do their work, and where the most common mistake lives.

To keep a salesperson to their own accounts, add **User Permission** records against the
DocType you actually want to restrict:

```
User Permission
  user:      priya@example.com
  allow:     Customer
  for_value: ABC Medical Store
```

Because every read goes through `frappe.get_list`, that restriction applies to the
assistant's searches, its counts, its reports and its record reads, with no extra
configuration in this app.

> **The mistake to avoid.** A User Permission on **Territory** does *not* restrict a Lead
> whose `territory` field is empty. Frappe can only apply a link-based restriction to
> records that carry the link. If a doctype's scoping field is optional — and on Lead,
> Territory is — then restrict on the DocType itself, or make the field mandatory. This is
> tested in `sales_ai/tests/test_record_access.py`.

Two things the agent does deliberately **outside** the user's scope, both read-only and both
for the same reason:

- **Duplicate detection** (`guard/writes._not_a_duplicate`, `guard/leads._existing_customer`)
  looks for an existing record ignoring permissions, because a duplicate you cannot see is
  still a duplicate. It does *not* hand back the name if you cannot read it — it says one
  exists and stops.
- **Assignment** checks whether the *assignee* can read the record, using their permissions
  rather than yours.

---

## 5. Things the assistant refuses regardless of role

Not even a System Manager gets these, because they are not permission questions:

| Refused | Why |
|---|---|
| Deleting anything | Cancellation is reversible in the way that matters and leaves an audit trail |
| Running SQL, Python or shell | One such tool makes every control in this document decorative |
| Creating DocTypes, fields, workflows or server scripts | Same reason; the tool registry is a closed world by design |
| Emailing an address not already on the record | Otherwise an injected instruction in a customer's notes field becomes an exfiltration channel |
| Assigning to somebody who cannot see the record | `frappe.desk.form.assign_to` would silently share it, making assignment a way to grant read access |
| Changing a submitted document's lines | Cancel-and-amend is a person's job with the history in front of them |
| Choosing which company a record belongs to | Company comes from the user's own defaults |
| Saying whether a record it cannot show you exists | One phrase — "No {DocType} called X is available to you" — for both cases |

---

## 6. What gets written down

Every action and every refusal lands in **Sales AI Action Log**: who, which tool, which
record, what changed, and `Allowed` or `Denied`. Denied rows are the interesting ones — a
run of them is either a misconfigured permission or somebody probing.

Actions logged: `Read`, `Create`, `Update`, `Note`, `Follow Up`, `Assign`, `Draft`,
`Submit`, `Cancel`, `Email`.

The log is written by the system and is read-only in the desk. The **Sales AI Unsupervised
Actions** report lists every write that ran without a human approving it, which is the
report to read first if you are asked to audit the thing.

---

## 7. Checking it yourself

The claims above are tests, not prose:

| Claim | Test |
|---|---|
| The agent sees only what the user sees | `tests/test_record_access.py` |
| Refusals do not distinguish missing from forbidden | `tests/test_record_access.py`, `tests/test_leads.py` |
| Assignment cannot grant access | `tests/test_assign.py` |
| Follow-ups are bounded by ownership | `tests/test_followups.py` |
| Email cannot reach an address off the record | `tests/test_email.py` |
| There is no delete tool | `tests/test_documents.py` |
| Every write tool is gated by policy | `tests/test_policy.py` |

Run them with:

```bash
bench --site <site> run-tests --app sales_ai
```
