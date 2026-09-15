# Sales AI Agent — Analysis & Architecture Proposal

Status: **ANALYSIS ONLY — awaiting `APPROVED — START IMPLEMENTATION`**
Date: 2026-09-15
Environment: frappe 16.33.1, erpnext 16.34.2 (branch `version-16`), flow @ `develop` 4a3189b (v0.0.1), bench 5.29.1, Python 3.14
Target site: `ai.local` (frappe, erpnext, sales_ai). Flow is currently installed only on `demo.local`.

---

## 0. Executive summary — three findings that change the brief

### Finding 1 — Frappe Flow is NOT a node/DAG workflow engine. It is an agentic loop.

Your brief assumes Flow provides nodes, connections, a visual builder, a DAG execution engine, and node-to-node data passing. **None of that exists.** I verified this directly:

| Brief assumed | Reality in `apps/flow` |
|---|---|
| Node DocTypes, Connection/Edge DocTypes | Do not exist. 16 DocTypes, none graph-related. |
| Visual flow builder canvas | Does not exist. `frontend/package.json` has no vueflow/reactflow/dagre/elkjs/jsplumb. Confirmed by source grep. |
| DAG / topological execution engine | Does not exist. `flow/lib/agent.py:229-278` is a `for iteration in range(1, max_iterations+1)` LLM tool-calling loop. |
| Node input/output mapping, expression resolution | Does not exist. The only templating is Jinja on `Flow Trigger.prompt_template` (`flow/triggers/triggers.py:86`). |
| `BaseNode` abstraction | Does not exist. There is a `Tool` dataclass (`flow/lib/tool.py:25`), which is a different thing. |
| Execution context object passed between nodes | Does not exist. The "context" is the OpenAI message transcript. |
| Branching / IF / Loop / Switch / Retry | Does not exist. The LLM improvises control flow. |

**Consequence:** the "Flow-style node architecture" in sections 3–6, 14 and the visual builder in sections 4 and 30 of your brief are **net-new work**, not adaptation. They are the single largest cost item in the project and the one with the lowest early ROI. My recommendation is to defer them (see Decision D2).

### Finding 2 — Flow already is an AI agent, and its built-in tools are exactly what your rules 9/10/11 forbid.

`flow/tools/builtins.py` registers these tools on every agent by default:

| Tool | What the LLM can do | Your rule |
|---|---|---|
| `read(doctype, filters, fields, limit)` | Read **any** DocType | 9 — "never unrestricted database access" |
| `create(doctype, records)` | Create **any** DocType | 12 — "validate every tool argument" |
| `update(doctype, names, values)` | Update **any** DocType | — |
| `delete(doctype, names)` | Delete **any** DocType | — |
| `run_action(doctype, names, action)` | submit / cancel / amend / rename / any workflow transition / any whitelisted controller method | 14 |
| `execute(code, description)` | **Run arbitrary Python** in `safe_exec` | 10 — "never allow arbitrary Python execution by the LLM" |

They are all permission-checked (`frappe.has_permission`) and the write ones are all `requires_confirmation=True`, so they are *bounded by the acting user's ERPNext permissions* — but for a Sales Manager that is still a very wide blast radius, and `execute` is a literal violation of your rule 10.

**Consequence:** the Sales AI Agent must ship a **narrow, typed, domain-specific tool surface** and must explicitly **not** attach Flow's builtins. This is the core differentiator versus "just install Flow".

### Finding 3 — What Flow gives you for free is genuinely excellent, and rebuilding it would be waste.

These are production-quality and directly reusable:

| Capability | Where | Why it matters |
|---|---|---|
| LLM provider abstraction (100+ models) | `flow/lib/model.py` via `litellm` | Satisfies rule 15 "make LLM providers replaceable" for free |
| Tool contract + **automatic JSON-Schema from type hints** + **pydantic `validate_call` argument validation** | `flow/lib/tool.py:25-106` | Satisfies rule 12 "validate every tool argument" for free |
| Human-in-the-loop pause/resume with `Question` (Approve / Deny / free-text redirect) | `flow/lib/agent.py:131-227, 435-452` | Exactly the HITL model your section 17 describes |
| Run persistence, transcript, token usage, config snapshot, failure states | `Flow Run`, `Flow Session`, `Flow Session Message` | Section 14/19/20 foundations |
| SSE streaming with tool-call events | `flow/api/api.py:179-226`, `frontend/src/api/stream.js` | Section 20/21 observability |
| Trigger system (doc events + cron, `run_as` user, Jinja prompt, Python condition) | `flow/triggers/triggers.py` | Section 3 trigger nodes, section 32 |
| RAG (LanceDB) + agent memory | `flow/knowledge/`, `flow/memory/` | Section 19 business memory |
| Desk side-panel Vue 3 SPA with approval cards | `flow/frontend/` (25 components, ~1000-line store) | Section 25 UI |

Verified installed and importable in the bench venv: `litellm`, `lancedb`.

---

## Part I — PHASE 1: RESEARCH FINDINGS

## 1. Flow architecture (as-built)

### 1.1 Layout

```
apps/flow/flow/
├── lib/            agent.py · model.py · tool.py · resolver.py · session.py   ← the engine
├── tools/          builtins.py                                               ← generic CRUD + execute
├── triggers/       triggers.py                                               ← doc_events + cron dispatch
├── knowledge/      ingest · retriever · chunking (LanceDB)
├── memory/         store.py · memory.py
├── api/            api.py                                                    ← 7 whitelisted endpoints
├── assistant/      sync_builtin_assistant (after_migrate)
├── flow/doctype/   16 doctypes
└── hooks.py
apps/flow/frontend/ Vue 3 + Vite → flow/public/flow_panel/flow_panel.{js,css}
```

Dependencies (`pyproject.toml`): `litellm`, `lancedb`, `python-docx`, `pdfplumber`, `rapidocr`, `onnxruntime`.

### 1.2 Data model (16 DocTypes, no graph)

| Group | DocTypes | Notes |
|---|---|---|
| Agent config | `Flow Agent`, `Flow Agent Tool`(child), `Flow Agent Knowledge Base`(child) | instructions, model, max_iterations, tools |
| LLM | `Flow Model`, `Flow Provider` | model_id, api_key (Password), base_url, params JSON |
| Tools | `Flow Tool` | `type` = `Imported` (dotted `import_path`) \| `Script` (safe_exec Python) |
| Conversation | `Flow Session`, `Flow Session Message`(child), `Flow Session Attachment`(child) | OpenAI-format transcript |
| Execution | `Flow Run` | status, iterations, input, output, tool_calls JSON, questions JSON, usage JSON, error, config_snapshot, feedback |
| Triggers | `Flow Trigger` | DocType Event \| Scheduled; `run_as`; `condition`; `prompt_template` |
| RAG | `Flow Knowledge Base`, `Flow Knowledge Source`, `Flow Knowledge Chunk`, `Flow Knowledge Settings` | |
| Memory | `Flow Agent Memory` | scope agent\|user, injected into system prompt |

### 1.3 Execution model — verified from source

```
Agent.run(input)                                     flow/lib/agent.py:123
  └─ _loop(messages)                                 :229
       for iteration in 1..max_iterations:
         response = model.chat(messages, tools)      ← litellm
         messages.append(assistant_message)
         if no tool_calls:  return RunResult(COMPLETED)
         for call in response.tool_calls:
            result = _invoke(call)                   :382
              ├─ call.error           → {"error": ...}          (malformed args fed back to LLM)
              ├─ unknown tool         → {"error": "Unknown tool"}  (cannot fabricate tools)
              ├─ requires_confirmation and not auto_approve → Question   ← PAUSE
              └─ else _run_tool(call) → tool(**args)  (pydantic-validated)
            messages.append({"role":"tool", tool_call_id, content})
         if any questions: return RunResult(paused=True, questions=[...])
       raise RuntimeError("exceeded max_iterations")
```

Resume (`agent.py:131`): `answers` maps `tool_call_id` → `"Approve"` (runs the tool) / `"Deny"` (halts the whole run) / free text (returned to the LLM as `{"status":"redirect","user_feedback":...}` so it retries differently). This is a genuinely good HITL design and I intend to keep it.

**Run statuses (the real enum):** `Running` → `Paused` | `Completed` | `Failed`. There is **no** `Waiting Approval` vs `Pending` distinction; `Paused` carries both.

**Execution context:** in-memory `messages: list[dict]` only, persisted to `Flow Session Message` rows at run end. No variable store, no `results` dict, no `documents` map. Your section 5 context object does not exist and must be built.

### 1.4 Triggers

- `hooks.py` wires `flow.triggers.dispatch` to `after_insert / on_update / on_submit / on_cancel / on_trash` for **`*` (all DocTypes)**. Self-doctypes are skipped to avoid recursion.
- Match → optional `condition` (safe_exec) → `frappe.enqueue("flow.triggers.fire", enqueue_after_commit=True)`.
- `fire()` does `frappe.set_user(trigger.run_as or owner)`, renders the Jinja `prompt_template` with `{doc, now}`, calls `agent.run(...)` with `auto_approve=trigger.auto_approve`.
- Scheduled: cron parsed every 5 min via `croniter` against `last_fired_at`.
- **No debouncing, no dedup, no rate limit.** An `on_update` trigger on Quotation fires on *every* save.

### 1.5 Background jobs & long-running

- Only `flow.triggers.fire` is enqueued. Default queue → **300 s timeout**.
- Interactive runs execute **entirely inside the HTTP request** as an SSE stream (`api.py:179-226`). No cross-job resumption.
- **No `frappe.publish_realtime` anywhere.** Progress is SSE-only, so a trigger-initiated run produces zero UI feedback.
- Your section 15 ("analyze 10,000 customers") is not supported and must be built.

### 1.6 Security posture

| Control | Status |
|---|---|
| Runs as real user (`frappe.set_user`) | ✅ |
| Tools use `frappe.has_permission` / `frappe.get_list` | ✅ |
| No `ignore_permissions` in tool paths | ✅ |
| Arg validation via pydantic `validate_call` | ✅ |
| Unknown tool name → error, not execution | ✅ (rule: model cannot fabricate tools) |
| API keys as encrypted Password fields | ✅ |
| Script tools sandboxed via `safe_exec` (RestrictedPython) | ✅ |
| **Prompt-injection defence** | ❌ **none** — record content goes straight into the transcript |
| Approval policy | ⚠️ static per-tool boolean only; no amount/role/risk conditioning |
| `auto_approve` on triggers | ⚠️ a single checkbox disables *all* confirmation for autonomous runs |
| Rate limit / token budget / cost cap | ❌ none |
| Audit trail of what the agent actually changed | ⚠️ partial — `tool_calls` JSON on the run, no per-document audit index |

### 1.7 Frontend

Vue 3 + Vite → IIFE bundle at `flow/public/flow_panel/flow_panel.{js,css}`, injected via `app_include_js/css`, mounted into `div#flow-root` (fixed overlay, right side, resizable, Ctrl+I). CSS scoped to `#flow-root` via `postcss-prefix-selector`. Uses `frappe-ui` 1.0.0-beta.3 + Tailwind.

Reusable as-is: `ConfirmCard.vue` (approval UI), `ActivityGroup/ActivityStep.vue` (tool timeline), `ArgsView.vue`, `MarkdownText.vue`, `CodeBlock.vue`, `api/stream.js` (backend-agnostic SSE reader), `lib/toolMeta.js`.

Notable gap: **the panel does not read desk page context** — no `cur_frm` binding. It has no idea which Quotation you are looking at. For a Sales agent that is a major UX miss and a cheap win.

Admin UIs are plain Frappe desk forms + one Workspace JSON. No custom SPA for configuration.

## 2. ERPNext Sales inventory (what to reuse, not reimplement)

### 2.1 Domain DocTypes

| DocType | Module | Submittable | Series |
|---|---|---|---|
| Lead, Opportunity, Prospect, Campaign | CRM | No | `CRM-LEAD-.YYYY.-`, `CRM-OPP-.YYYY.-` |
| Customer, Quotation, Sales Order, Sales Invoice, Sales Team(child) | Selling | Quotation/SO/SI yes | `SAL-QTN-.YYYY.-`, `SAL-ORD-.YYYY.-` |
| Item, Item Price | Stock | No | |
| Pricing Rule | Accounts | No | |
| Sales Person, Territory, Customer Group | Setup | No | Nested Set (lft/rgt) |
| Contact, Address, ToDo, Communication, Task, Event, Notification Log | Frappe core | No | |

### 2.2 Transition helpers — call these, never hand-build documents

| From → To | Dotted path |
|---|---|
| Lead → Opportunity | `erpnext.crm.doctype.lead.lead.make_opportunity` |
| Lead → Customer | `erpnext.crm.doctype.lead.lead.make_customer` |
| Opportunity → Quotation | `erpnext.crm.doctype.opportunity.opportunity.make_quotation` |
| Opportunity → Customer | `erpnext.crm.doctype.opportunity.opportunity.make_customer` |
| Quotation → Sales Order | `erpnext.selling.doctype.quotation.quotation.make_sales_order` |
| Sales Order → Sales Invoice | `erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice` |
| Sales Order → Delivery Note | `erpnext.selling.doctype.sales_order.sales_order.make_delivery_note` |

### 2.3 Pricing / tax — never reimplement

| Purpose | Dotted path |
|---|---|
| Item rate, tax, UoM, warehouse resolution | `erpnext.stock.get_item_details.get_item_details` |
| Item defaults | `erpnext.stock.doctype.item.item.get_item_defaults` |
| Pricing rules / discounts / margins | `erpnext.accounts.doctype.pricing_rule.pricing_rule.get_pricing_rule_for_item` |
| Default tax template | `erpnext.controllers.accounts_controller.get_default_taxes_and_charges` |
| FX | `erpnext.setup.utils.get_exchange_rate` |
| Link search (perm-aware) | `erpnext.controllers.queries.customer_query` / `.lead_query` / `.item_query` |

### 2.4 Analytics backing

Script/Query reports available to wrap as analytics tools: Sales Analytics, Sales Order Analysis, Sales Order Trends, Quotation Trends, Lost Quotations, Customer Acquisition and Loyalty, Sales Person-wise Transaction Summary, Territory-wise Sales, Lead Details, Lead Conversion Time, Lead Owner Efficiency, Sales Pipeline Analytics, Opportunity Summary by Sales Stage, Lost Opportunity, First Response Time for Opportunity, Campaign Efficiency.

`frappe.desk.query_report.run` is the perm-aware entry point.

### 2.5 AI/ML in ERPNext v16

Only `Sales Forecast` (manufacturing) and the `Exponential Smoothing Forecasting` report. **No lead scoring, no churn model, no LLM integration anywhere in erpnext or frappe core.** Sections 7 and 31 of your brief are 100% net-new.

### 2.6 Permissions today

- ERPNext `hooks.py` registers **no** `permission_query_conditions` for sales DocTypes — only `has_permission: erpnext.check_app_permission`.
- Territory / Sales Person / Company scoping is done entirely through **User Permission** records.
- Consequence: if a user's ERPNext permissions are loose, the agent will be loose. **Tool-level scoping is therefore mandatory, not optional.**

## 3. Frappe v16 primitives confirmed available

| Primitive | Detail |
|---|---|
| `frappe.enqueue` | `queue` short/default(300 s)/long(1500 s), `job_id`, `deduplicate=True`, `enqueue_after_commit`, `on_success`/`on_failure`, `at_front`. `MAX_QUEUED_JOBS=500` |
| `RQ Job` doctype | statuses QUEUED/STARTED/FINISHED/FAILED/STOPPED/SCHEDULED; results TTL 10 min |
| `frappe.publish_realtime` | `event, message, room, user, doctype, docname, task_id, after_commit` |
| `frappe.utils.safe_exec` | RestrictedPython; `restrict_commit_rollback`; requires `server_script_enabled` (already `1` in this bench) |
| Workflow / Workflow State / Workflow Action | full approval state machine, role-gated transitions |
| Assignment Rule, Notification, Auto Repeat, ToDo | reuse for routing + alerting |
| `extend_doctype_class` (v16) | mixin-style extension, preferred over `override_doctype_class` |
| Built-in LLM support | **none** |

---

## Part II — PHASE 2: GAP ANALYSIS (corrected against source)

| Capability | ERPNext 16 | Flow (as-built) | Sales AI Agent needs | Verdict |
|---|---|---|---|---|
| Sales domain data | ✅ full | — | ✅ | **reuse ERPNext** |
| Document transitions (lead→opp→quote→SO) | ✅ `get_mapped_doc` helpers | — | ✅ | **reuse ERPNext** |
| Pricing / tax / FX | ✅ | — | ✅ | **reuse ERPNext** |
| Permissions engine | ✅ roles + User Permission | ✅ respected | ✅ + tool scoping | **reuse + extend** |
| Approval state machine | ✅ Workflow | ⚠️ static bool | ✅ dynamic policy | **build** |
| LLM abstraction | ❌ | ✅ litellm | ✅ | **reuse Flow** |
| Tool contract + arg validation | ❌ | ✅ pydantic + JSON Schema | ✅ | **reuse Flow** |
| Agent loop / tool calling | ❌ | ✅ | ✅ | **reuse Flow** |
| HITL pause/resume | ⚠️ Workflow only | ✅ Question/Approve/Deny/redirect | ✅ | **reuse Flow** |
| Conversation persistence | ❌ | ✅ Session/Run/Message | ✅ | **reuse Flow** |
| Streaming UI | ❌ | ✅ SSE + Vue panel | ✅ | **reuse Flow** |
| Triggers (doc event + cron) | ⚠️ Notification/Server Script | ✅ | ✅ + dedup/debounce | **reuse + extend** |
| RAG / knowledge | ❌ | ✅ LanceDB | ⚠️ optional | **reuse Flow** |
| Agent memory | ❌ | ✅ | ⚠️ constrain | **reuse + constrain** |
| **Domain-scoped tool registry** | ❌ | ❌ (generic CRUD) | ✅ | **build — core value** |
| **Risk/amount/role approval policy** | ❌ | ❌ | ✅ | **build** |
| **Per-document audit of AI actions** | ⚠️ Version doctype | ⚠️ partial | ✅ | **build** |
| **Prompt-injection defence** | ❌ | ❌ | ✅ | **build** |
| **Long-running background runs** | ✅ RQ | ❌ | ✅ | **build on frappe** |
| **Cost / token budget** | ❌ | ❌ | ✅ | **build** |
| **Draft preview before write** | ❌ | ❌ | ✅ | **build** |
| **Sales intelligence (scoring/churn/forecast)** | ❌ | ❌ | ✅ | **build** |
| **Deterministic DAG playbooks** | ⚠️ Workflow ≠ DAG | ❌ | ⚠️ phase 6 | **build (defer)** |
| **Visual builder canvas** | ❌ | ❌ | ⚠️ phase 6 | **build (defer, biggest cost)** |
| **Eval harness / accuracy metrics** | ❌ | ❌ | ✅ | **build** |

Terminology your brief asked me to separate:

- **Flow Automation** — deterministic: trigger → fixed steps. *ERPNext has this (Notification, Assignment Rule, Server Script, Workflow). Flow the app does not.*
- **AI Agent** — LLM plans, selects tools, iterates, asks. *This is what Flow actually is.*
- **AI-powered Flow** — deterministic skeleton with LLM nodes at specific points. *Neither has it. This is the hybrid in your section 11 and it is the Phase 6 target.*

---

## Part III — KEY DECISIONS REQUIRING YOUR SIGN-OFF

### D1 — Depend on Flow, or stand alone?  *(REVISED — licensing)*

**Flow is AGPL-3.0-or-later.** Verified:
- `apps/flow/pyproject.toml`: `license = "AGPL-3.0-or-later"`
- `apps/flow/license.txt`: full GNU AGPL v3 text
- `apps/flow/README.md:7,129`: AGPL v3 badge + "License: GNU AGPLv3"

Ignore the `# License: MIT. See LICENSE` headers at the top of Flow's `.py` files — those are
leftover boilerplate emitted by `bench new-app` (our own `sales_ai` files carry the same stale
header). They are not a dual license. The authoritative license is AGPL-3.0.

License landscape in this bench:

| App | License | Obligation triggers on |
|---|---|---|
| frappe | MIT | nothing |
| erpnext | GPL-3.0 | **distribution** |
| **flow** | **AGPL-3.0-or-later** | **distribution AND network use (§13)** |
| sales_ai | MIT (as scaffolded) | — |

The delta that matters: `sales_ai` already links to ERPNext (GPL-3.0), so it can never be
truly proprietary *if distributed*. But GPL-3 says nothing about hosting. **AGPL §13 does**: if
users interact with the software over a network, they can demand the Corresponding Source of
the whole combined work — including `sales_ai`. For a company doing client work and
white-labelling (`medgrid_whitelabel`, `whitelabel`, `admionix_nexus` are in this bench),
that is a material commercial constraint.

Forking Flow's Vue frontend has the same consequence — it is AGPL too.

**Revised recommendation: STAND ALONE. Do not import `flow.*`.**

Use Flow as an *architectural reference only* (ideas and architecture are not copyrightable;
its specific code is). Build the LLM layer directly on permissively licensed libraries:

| Need | Standalone choice | License |
|---|---|---|
| Provider abstraction | `litellm` directly | MIT |
| Tool schema + arg validation | `pydantic` directly | MIT |
| Agent loop | own, ~150 lines | ours |
| Session / Run persistence | own DocTypes (needed anyway) | ours |
| SSE streaming | own, ~60 lines | ours |
| Chat panel | own Vue 3 + `frappe-ui` | MIT |

Revised cost estimate: roughly **800–1200 lines** of net-new engine code, not the 1500–2000 I
first quoted — because the registry, guard pipeline, policy engine, drafts, audit and
intelligence layers were always going to be ours regardless. The genuinely reusable Flow parts
(loop, tool contract, SSE) are the small ones. The real added cost is the chat panel UI.

This also removes risk **R1** (Flow is v0.0.1 on an unstable `develop` branch) entirely, and
lets `sales_ai` keep its MIT header or move to any license you choose.

**If you would rather depend on Flow anyway**, that is a legitimate choice — but then
`sales_ai` must be relicensed **AGPL-3.0-or-later**, and any client you host it for can
request the source. Decide this before any code is written; it is expensive to reverse.

*I am not a lawyer — please confirm with counsel before committing to a licensing position.*

**No longer a blocker:** Flow does not need to be installed on `ai.local`.

### D2 — Visual flow builder: build now, or later?

**Recommendation: LATER (Phase 6), and narrower than the brief.**

Reasoning: a general-purpose visual DAG builder (canvas, ports, edge routing, config panels, validation, versioning, a DAG execution engine, a step debugger) is realistically the largest single component in this entire project — larger than the agent, tools, policy engine and intelligence layer combined. It delivers no value until there is a proven tool library to wire together. Building it first means designing node schemas for tools that do not exist yet.

**Proposed narrowing:** instead of a general builder, ship **Sales AI Playbook** — a *linear-with-branches* recipe DSL (JSON) with a form-based step editor first, and a read-only rendered graph for observability. Upgrade to a full drag-and-drop canvas (`@vue-flow/core` + `dagre`) only after the playbook DSL has stabilised in real use.

### D3 — Tool surface

**Recommendation: closed world. Sales agents get ONLY `sales_ai` registry tools.**

Explicitly **never** attached: Flow's `execute`, `create`, `update`, `delete`, `run_action`, `read`, `find_doctypes`, `describe`. Enforced by a validation hook on the agent profile, not by convention.

Every write goes through an ERPNext controller or a `get_mapped_doc` helper. No `frappe.db.set_value`, no `frappe.db.sql`, no `ignore_permissions` anywhere in the tool layer.

### D4 — Approval model

**Recommendation: replace Flow's static `requires_confirmation` boolean with a server-side policy engine.**

Flow's model cannot express "auto-approve quotations under ₹50,000 for a Sales Manager but always ask for Sales Orders". We wrap each registry tool so that at invoke time the policy is evaluated against `(tool, risk_level, resolved argument values, acting user's roles, company, amount)`. The wrapper returns Flow's `Question` when approval is required — so we reuse Flow's pause machinery while owning the decision.

Critically: **the wrapper re-evaluates policy at approval time too**, so an approved call cannot execute with different arguments than were shown.

### D5 — UI surface

**Recommendation: own desk panel (forked from Flow's frontend), + Frappe desk forms for everything else.**

- Sales AI chat panel: separate Vite bundle → `sales_ai/public/sales_panel/`, mounted at `#sales-ai-root`, keyboard `Ctrl+Shift+A`. Forked because we need Sales-specific rendering (quotation preview cards, pipeline chips, doc-context binding) that would otherwise pollute Flow.
- **Adds what Flow lacks: desk page/route context.** The panel reads `cur_frm.doctype/docname` and the current route, and passes it as a structured `page_context` so "create a follow-up for this" works.
- Execution monitor, policies, settings, playbooks, insights, evals: **plain Frappe list/form views + one Workspace**. Zero custom UI cost.

### D6 — Prompt injection

**Recommendation: treat all ERPNext record content as untrusted data.**

Flow has no defence at all. Sales records contain free text (`Lead.notes`, `Opportunity.custom fields`, email bodies in `Communication`) that an outsider can write into. Mitigations designed in §6 below.

---

## Part IV — PHASE 3: ARCHITECTURE PROPOSAL

## 1. System architecture

```
 Desk user / Chat panel / Trigger / Scheduler
                    │
        ┌───────────▼────────────┐
        │  api/  (whitelisted)   │  start_run · resume_run · stop_run · preview_draft
        └───────────┬────────────┘
                    │
        ┌───────────▼────────────────────────────────────┐
        │  orchestrator/                                  │
        │   ├─ session      conversation + page_context   │
        │   ├─ profile      agent config → runtime Agent  │
        │   └─ scope        company/territory/salesperson │
        └───────────┬────────────────────────────────────┘
                    │
        ┌───────────▼───────────┐        ┌──────────────────────┐
        │  llm/adapter.py       │◄──────►│  flow.lib (Agent,    │
        │  (ONLY flow import)   │        │  Tool, Model, Q)     │
        └───────────┬───────────┘        └──────────────────────┘
                    │ tool call
        ┌───────────▼───────────────────────────────────────────┐
        │  tools/registry.py   — closed-world, typed, versioned  │
        └───────────┬───────────────────────────────────────────┘
                    │
        ┌───────────▼───────────┐
        │  guard/ (per call)    │ 1 schema+pydantic  2 scope resolve
        │                       │ 3 permission check 4 policy/risk
        │                       │ 5 draft/preview    6 approval
        │                       │ 7 execute          8 audit
        └───────────┬───────────┘
                    │
        ┌───────────▼───────────────────────────────────────────┐
        │  ERPNext controllers · get_mapped_doc · get_item_details│
        │  query_report.run · frappe ORM (permission-respecting)  │
        └───────────┬───────────────────────────────────────────┘
                    │
                 ERPNext
```

## 2. Component / folder architecture

Adapted from your section 23, corrected for what Flow actually provides.

```
apps/sales_ai/sales_ai/
├── hooks.py
├── llm/
│   └── adapter.py            ← the ONLY module importing flow.*
├── orchestrator/
│   ├── session.py            start/resume/stop, page_context injection
│   ├── profile.py            Sales AI Agent Profile → runtime Agent
│   ├── prompt.py             system prompt assembly (role, scope, guardrails)
│   └── scope.py              company / territory / sales person resolution
├── tools/
│   ├── registry.py           @sales_tool decorator + registry + categories
│   ├── schemas.py            shared pydantic arg models (DateRange, PartyRef, ...)
│   ├── customer.py  lead.py  opportunity.py  quotation.py  sales_order.py
│   ├── item.py      followup.py  analytics.py  communication.py
├── guard/
│   ├── policy.py             risk + amount + role evaluation
│   ├── permissions.py        has_permission + scope enforcement
│   ├── approval.py           Question building, re-validation on approve
│   ├── sanitize.py           untrusted-content fencing, output redaction
│   └── budget.py             token/cost/rate limits
├── drafts/
│   └── builder.py            build + preview a doc without inserting
├── execution/
│   ├── jobs.py               enqueue long runs, resume across jobs
│   ├── progress.py           publish_realtime
│   └── audit.py              Sales AI Action Log writer
├── playbook/                 ← PHASE 6
│   ├── engine.py  context.py  nodes/  validate.py
├── intelligence/             ← PHASE 7
│   ├── lead_scoring.py  churn.py  forecasting.py  recommendations.py
├── evals/
│   ├── runner.py  metrics.py  cases/
├── api/
│   └── agent.py              whitelisted endpoints
├── sales_ai/doctype/...
└── public/sales_panel/       ← built Vue bundle
frontend/                     ← Vue 3 + Vite source
docs/
```

## 3. Tool registry design

```python
# conceptual — NOT to be implemented until approved
@sales_tool(
    name="create_quotation",
    category="quotation",
    risk="high",                 # none | low | medium | high | critical
    writes="Quotation",          # doctype touched, drives audit + policy
    requires_draft=True,         # must produce a preview before approval
    scope=("company", "territory"),
    version=1,
)
def create_quotation(args: CreateQuotationArgs) -> ToolResult: ...
```

Registry metadata per your section 10: `name, description, category, input_schema, output_schema, permissions, risk_level, requires_approval, writes, scope, version, handler`.

Design rules:
- **One pydantic model per tool** for arguments — gives JSON Schema for the LLM *and* runtime validation for free (Flow's `build_schema` + `validate_call` already do this; we just declare models explicitly for the complex ones).
- **Structured errors**, never exceptions to the LLM:
  `{"success": false, "error_code": "CUSTOMER_NOT_FOUND", "message": "...", "recoverable": true, "suggestions": [...]}` — matching your section 16, where `suggestions` drives the disambiguation dialogue.
- **Read tools never write. Write tools never read broadly.**
- **Output shaping**: every tool returns a compact, token-bounded projection, not raw docs. A `Sales Order` doc is ~120 fields; the tool returns ~12. This is both a cost control and an injection-surface control.

Initial registry (Phase 4–5), ~34 tools:

| Category | Read (Phase 4) | Write (Phase 5, policy-gated) |
|---|---|---|
| customer | `search_customers`, `get_customer`, `get_customer_sales_history`, `get_customer_outstanding`, `get_customer_activity` | `create_customer`, `update_customer` |
| lead | `search_leads`, `get_lead` | `create_lead`, `update_lead`, `assign_lead`, `convert_lead_to_opportunity` |
| opportunity | `search_opportunities`, `get_opportunity` | `create_opportunity`, `update_opportunity`, `convert_opportunity_to_quotation` |
| quotation | `search_quotations`, `get_quotation`, `get_pending_quotations`, `get_expiring_quotations` | `draft_quotation`, `create_quotation`, `update_quotation`, `submit_quotation` |
| sales order | `search_sales_orders`, `get_sales_order`, `get_pending_sales_orders` | `create_sales_order_from_quotation` |
| item | `search_items`, `get_item_price` (wraps `get_item_details`) | — |
| followup | `list_my_followups` | `create_todo`, `create_task`, `schedule_followup`, `log_communication` |
| analytics | `sales_summary`, `sales_by_dimension`, `top_customers`, `quotation_conversion`, `pipeline_summary`, `run_sales_report` | — |

Note `draft_quotation` (build + price + preview, no insert) is separate from `create_quotation` (insert). This is what makes your section 13 preview card possible and keeps the risky step atomic.

## 4. Guard pipeline (the security core)

Every tool invocation, in order — all server-side, none skippable by the LLM:

| # | Stage | Fails with |
|---|---|---|
| 1 | Tool exists in registry (closed world) | `UNKNOWN_TOOL` |
| 2 | Arguments validated against pydantic schema | `INVALID_ARGUMENTS` (+ field errors, recoverable) |
| 3 | Scope resolution — company/territory/sales person injected from the **user's** permissions, **never from LLM arguments** | `OUT_OF_SCOPE` |
| 4 | `frappe.has_permission(doctype, ptype, doc)` for every doc touched | `PERMISSION_DENIED` |
| 5 | Budget check (tokens, calls/run, writes/run, cost/day) | `BUDGET_EXCEEDED` |
| 6 | Policy evaluation → `allow` \| `require_approval` \| `deny` | `POLICY_DENIED` |
| 7 | If draft-required: build preview, return it, **do not write** | — |
| 8 | Approval (Flow `Question`) — on approve, **re-run stages 2–6** against the exact stored arguments | `ARGUMENTS_CHANGED` |
| 9 | Execute via ERPNext controller | structured error |
| 10 | Audit → `Sales AI Action Log` (+ `Comment` on the affected document) | — |

Stage 3 is the one that matters most: **the LLM never supplies the security context.** If it passes `company="Other Co"` and the user has no permission there, stage 3/4 rejects it. Company/territory defaults are derived server-side.

## 5. Approval policy model

`Sales AI Action Policy` rows, evaluated most-specific-first:

| Field | Example |
|---|---|
| `tool` | `create_sales_order` |
| `mode` | `Always Require Approval` \| `Require Approval Above Amount` \| `Never Require Approval` \| `Deny` |
| `amount_field` / `threshold` / `currency` | `grand_total` / 50000 / INR |
| `applies_to_roles` | Sales Manager |
| `company`, `territory` | optional narrowing |
| `autonomous_override` | what happens in trigger/scheduled runs with no human present |
| `priority` | int |

Defaults shipped (fail-safe): every `risk >= high` tool is `Always Require Approval`; every write tool is at minimum `Require Approval Above Amount` with threshold 0 (= always) until an admin relaxes it.

**Autonomous runs:** Flow's `auto_approve` checkbox is too blunt. We replace it with per-policy `autonomous_override` ∈ `{Deny, Require Async Approval, Allow}`. `Require Async Approval` creates a ToDo + Notification for the approver and parks the run as `Waiting Approval` (see §7) rather than silently proceeding.

## 6. Prompt-injection & AI-safety design

| Threat | Mitigation |
|---|---|
| Injected instructions inside record text | All record-derived content wrapped in `<untrusted_data source="Lead/CRM-LEAD-0001">…</untrusted_data>` fences; system prompt states data inside fences is never instructions. Control characters and fence-lookalikes stripped. |
| Tool fabrication | Closed registry; unknown name → structured error, never dispatch (Flow already behaves this way). |
| Parameter injection | pydantic schemas; enums for status/doctype; `Literal` types; no free-form `filters` dict on write tools; no raw SQL/filter passthrough to the LLM. |
| Privilege escalation via arguments | Stage 3 — security context is server-derived, never LLM-supplied. |
| Hallucinated records | Write tools validate every link field exists before insert; the system prompt forbids stating any figure not returned by a tool; the UI renders tool-sourced figures with a provenance chip. |
| Exfiltration via email/communication tools | `log_communication` and any send-email tool are `risk=critical`, always require approval, recipients restricted to contacts already linked to the party. |
| Runaway loops / cost | `max_iterations`, per-run write cap, per-user daily token + cost budget, `frappe.enqueue` dedup on triggers. |
| Trigger storms | `job_id` + `deduplicate=True` keyed on `(trigger, doctype, docname)`, plus a configurable debounce window on `on_update`. |

## 7. Execution model

Run statuses (superset of Flow's four — your section 14):

```
Queued → Running → { Completed | Failed | Cancelled }
           │
           ├─→ Waiting Approval  (sync: user is present, SSE paused)
           └─→ Waiting Async Approval (autonomous: parked, ToDo raised, resumable later)
```

Two execution modes:

| Mode | Used by | Transport | Where |
|---|---|---|---|
| **Interactive** | chat panel | SSE stream (reuse Flow's) | inside the HTTP request |
| **Background** | triggers, schedules, bulk, anything over ~60 s | `frappe.enqueue(queue="long", job_id=..., deduplicate=True)` + `frappe.publish_realtime` progress | RQ worker |

Background runs checkpoint the transcript to `Sales AI Run` after **every** iteration (Flow only persists at the end), so a worker crash or the 1500 s timeout can resume rather than lose the run. This is what makes your section 15 ("analyse 10,000 customers") work: the run is chunked, each chunk is a job, progress is published, and the final summary notifies the manager.

## 8. Memory architecture

| Layer | Storage | Lifetime | Constraint |
|---|---|---|---|
| Conversation | `Flow Session Message` (reused) | session | token-window trimmed with a rolling summary |
| Execution | `Sales AI Run` (+ step rows) | run | checkpointed per iteration |
| Page context | request-scoped | single turn | `{doctype, docname, route, filters}` from the panel |
| Business memory | `Sales AI Insight` (**typed**, not free text) | durable | **Explicitly bounded:** only `lead_score`, `churn_risk`, `next_best_action`, `forecast`, each with `subject_doctype/subject_name`, numeric value, model version, computed_on, and an explanation. |

Deliberate divergence from Flow: Flow's `update_memory` lets the LLM write arbitrary free-text facts into a shared store — a persistent injection vector and an uncontrolled storage area (your own section 19 warns against this). **The Sales agent will not get a free-text memory-write tool.** Business memory is written only by the deterministic `intelligence/` layer.

## 9. DocType design

| DocType | Type | Purpose |
|---|---|---|
| `Sales AI Settings` | Single | default model/agent, autonomy level, budgets, debounce, feature flags |
| `Sales AI Agent Profile` | Master | instructions, model, enabled tool categories, max_iterations, scope defaults; **validates that no generic Flow builtin is attached** |
| `Sales AI Tool` | Master (synced) | registry mirror: name, category, risk, writes, description, enabled, version |
| `Sales AI Action Policy` | Master | approval rules (§5) |
| `Sales AI Run` | Transaction | run status incl. approval states, checkpointed transcript ref, iterations, usage, cost, error, page_context, links to `Flow Run` |
| `Sales AI Run Step` | Child | per-iteration/per-tool: tool, args, result summary, duration, status, policy decision — **this is the visual-debugging data source (§21)** |
| `Sales AI Action Log` | Transaction | immutable audit: user, run, tool, doctype, docname, before/after digest, approved_by, approved_on |
| `Sales AI Draft` | Transaction | staged document JSON + rendered preview + expiry; consumed on approval |
| `Sales AI Insight` | Transaction | typed business memory (§8) |
| `Sales AI Eval Case` / `Sales AI Eval Run` | Master / Transaction | §34 accuracy harness |
| `Sales AI Playbook` / `… Node`(child) / `… Edge`(child) / `… Playbook Run` / `… Playbook Step`(child) | Phase 6 | deterministic DAG |

Reused from Flow (not duplicated): `Flow Model`, `Flow Provider`, `Flow Session`, `Flow Session Message`, `Flow Run`, `Flow Trigger`, `Flow Knowledge *`.

## 10. API design

All `@frappe.whitelist()`, all permission-checked, no `allow_guest`.

| Method | Purpose |
|---|---|
| `sales_ai.api.agent.start_run` | `(input, session?, profile?, page_context?, stream=True)` |
| `sales_ai.api.agent.resume_run` | `(run, answers, stream=True)` — re-validates before executing |
| `sales_ai.api.agent.stop_run` | `(run)` |
| `sales_ai.api.agent.get_draft` | `(draft)` — preview payload for the approval card |
| `sales_ai.api.agent.list_pending_approvals` | async-approval inbox |
| `sales_ai.api.agent.approve_async` | `(run, answers)` for parked autonomous runs |
| `sales_ai.api.agent.get_run_trace` | `(run)` — steps for the debug view |
| `sales_ai.api.agent.submit_feedback` | rating + comment |

## 11. UI architecture

| Surface | Tech | Effort |
|---|---|---|
| Sales AI chat panel | Vue 3 + Vite, forked from Flow's frontend, scoped to `#sales-ai-root`, `app_include_js` | medium |
| Draft/approval card (quotation preview, §13) | new component | medium |
| Run trace / visual debug (§21) | Frappe desk form on `Sales AI Run` + child `Run Step` grid | **low** |
| Execution monitor (§20) | Frappe list view + dashboard chart | **low** |
| Policies / profiles / settings | Frappe desk forms | **low** |
| Approval inbox | Frappe list view on parked runs + ToDo | **low** |
| Playbook editor | Phase 6 — form-based first, canvas later | high (deferred) |

## 12. Testing strategy

| Layer | What |
|---|---|
| Unit | each tool: happy path, not-found, permission denied, invalid args, scope violation; policy resolution matrix; draft builder; sanitizer |
| Integration | tool → ERPNext controller → DocType, on a seeded fixture company (customers, items, price list, quotations, orders) |
| Security | acts-as tests for Sales User vs Sales Manager vs unrelated user; cross-company access attempt; cross-territory; approval bypass attempt; **re-validation on approve with tampered arguments** |
| Injection | corpus of adversarial strings planted in `Lead.notes` / `Communication.content`, asserting no tool call results |
| Adapter | contract tests pinning the Flow API surface we depend on — these are the early-warning system for Flow `develop` churn |
| Eval (§34) | golden dataset, metrics below |

Eval metrics, with proposed go-live gates:

| Metric | Gate |
|---|---|
| Intent accuracy | ≥ 95 % |
| Tool selection accuracy | ≥ 95 % |
| Parameter accuracy (exact match on required args) | ≥ 90 % |
| Execution success rate | ≥ 98 % |
| Hallucinated-fact rate | **0 %** on the factual-claims subset |
| Unauthorized action rate | **0 %** — hard gate, no exceptions |
| Injection success rate | **0 %** — hard gate |

## 13. Roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 Foundations** | install flow on ai.local; `sales_ai` skeleton; `llm/adapter.py`; Settings + Agent Profile doctypes; Workspace; LLM provider configured | a "hello" run completes end-to-end with zero tools |
| **4 MVP (read-only)** | registry + guard stages 1–5 + audit; ~14 read tools (customer, quotation, sales order, basic analytics); chat panel with page context; Run + Run Step + trace view | "Show my pending quotations", "What did ABC buy last year?", "Top 10 customers this quarter" — correct and permission-scoped |
| **5 Write actions** | draft builder; policy engine; approval card; guard stages 6–10; ~12 write tools; Action Log | §13 quotation creation with preview + approve/deny works; tampered-approval test fails closed |
| **5b Autonomy infra** | background runs, checkpointing, progress, triggers with dedup/debounce, async approval inbox, budgets | §12 "find quotations needing follow-up and create tasks" runs as a scheduled background job |
| **6 Playbooks** | Playbook DSL + deterministic engine + node types (trigger/condition/loop/tool/AI/approval/output) + form editor + read-only graph render | §35 workflow 4 (quotation → wait 3 days → check → AI recommend → approve → follow-up) runs as a playbook |
| **6b Visual canvas** | drag-and-drop builder | only if playbook DSL has proven stable |
| **7 Intelligence** | lead scoring, churn, opportunity scoring, forecast, segmentation, recommendations → `Sales AI Insight` | scores produced nightly, explainable, surfaced in tools |
| **8 Controlled autonomy** | autonomy levels, org-wide policy defaults, manager dashboards, full eval harness at the gates above | eval gates met; sign-off |

## 14. Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Flow is v0.0.1 on `develop`; API churn breaks us | High | adapter module + pinned commit + adapter contract tests in CI |
| R2 | Prompt injection via customer-supplied text causes a real ERPNext write | **Critical** | fencing + approval on all writes + injection test suite as a hard gate |
| R3 | Approval fatigue → admins set everything to auto-approve | High | risk-tiered defaults, amount thresholds, async approval inbox, weekly digest of auto-approved actions |
| R4 | Scope creep from the visual builder consuming the whole project | High | D2 — defer; ship value in phases 4–5 first |
| R5 | LLM cost runaway on scheduled/bulk runs | Medium | per-user and per-org token+cost budgets, cheap model for classification, output projection to cut tokens |
| R6 | Loose ERPNext permissions make the agent over-powerful | High | tool-level scope enforcement independent of role perms; pre-flight permission audit report |
| R7 | Hallucinated figures presented as ERPNext fact | High | provenance chips in UI, system prompt ban, eval gate at 0 % |
| R8 | Trigger storms from `on_update` on hot doctypes | Medium | dedup `job_id` + debounce window + per-trigger rate cap |
| R9 | `Flow Run`/`Sales AI Run` table growth | Low | log-clearing policy like Flow's 90-day session purge |
| R10 | Python 3.14 + young deps (litellm pinned <1.83.8) | Medium | pin and test; the tzdata issue already hit this bench |
| R11 | Multi-company / multi-currency correctness in analytics | Medium | always go through ERPNext reports and `get_exchange_rate`; never hand-roll aggregation |

---

## Part V — OPEN QUESTIONS FOR YOU

1. **D1** — depend on Flow (recommended) or stand alone?
2. **D2** — accept deferring the visual builder to Phase 6b?
3. **LLM provider** — which model/provider for `ai.local`? (Anthropic Claude recommended for tool-calling quality; litellm makes this swappable.) Do you have an API key to configure, or should I design for a local Ollama model in dev?
4. **App naming** — the app is `sales_ai`; your folder sketch said `sales_ai_agent`. Confirm `sales_ai` is fine.
5. **Scope of "sales"** — CRM (Lead/Opportunity) *and* Selling (Quotation/SO) *and* receivables (outstanding/collections)? Or Selling only for v1?
6. **Autonomy appetite** — for v1, should autonomous (trigger/scheduled) runs be allowed to write at all, or read-and-recommend only?
7. **Multi-company** — is `ai.local` single-company or multi-company? Affects scope resolution design significantly.
8. **Languages** — English only, or does the chat need Hindi/other Indian language input?

---

**STOP. Awaiting `APPROVED — START IMPLEMENTATION`.**
