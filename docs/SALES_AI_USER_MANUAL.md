# Sales AI App — Simple User Manual

**For:** Anyone who will use the Sales AI App (sales people, managers, admins).
**Goal:** After reading this, you can log in, find records, create records, and use the AI helper on your own.

> Words we use here:
>
> - **Record** means one saved form, like one quotation or one order.
> - **Draft** means saved but not final yet. You can still change it.
> - **Submitted** means final. It is locked.
> - **AI helper** means the chat box that answers you and does work for you.

**Screenshot Required: [Login page]**
**Screenshot Required: [Main screen with the Sales AI button highlighted]**

---

# 1. What Is the Sales AI App

Sales AI is a helper inside your ERPNext system. It helps sales people with daily work.

It can:

- Find your customers, leads, quotations, and orders when you ask in normal words.
- Create new leads, customers, quotations, and reminders for you.
- Show your sales numbers on a dashboard.
- Ask your approval before it saves anything important.
- Keep a full list of everything it did, so managers can check it later.

One important rule: the AI helper can only do what **you** are allowed to do. It can never see another person's private records, and it can never do something you have no permission for.

---

# 2. Logging In

Follow these steps:

1. Open your company website link in the browser.
2. Type your user name and password.
3. Click **Login**.
4. You will see the main screen with your apps.
5. Click the **Sales AI** app to open it.

What you should see: the Sales AI home page with sections like Sales Performance, This Week, Review, and Manage.

If the page asks you to log in again, your session ended. Just log in again.

---

# 3. The Main Screens

There are only 3 places you need to know.

## 3.1 The Sales AI Home Page

This is the first page when you open the Sales AI app.

- **Sales Performance:** a shortcut to your sales numbers page.
- **This Week:** small cards showing how many AI chats ran, how many are waiting for approval, how many changes were made, and how many failed.
- **Review:** links to check the AI's work (runs and history).
- **Manage:** settings and setup. Only admins use this.

## 3.2 The Sales Dashboard Page

This page shows your sales numbers: total sales, open deals, won and lost deals, and best-selling items.

To use it:

1. Open Sales AI home page.
2. Click **Sales Dashboard**.
3. At the top, select your **Company**, **From Date**, and **To Date**.
4. Read the number cards on the screen.

Note: a normal sales person sees only **their own** numbers here. A manager sees the team's numbers.

**Screenshot Required: [Sales Dashboard page with Company and date filters]**

## 3.3 The AI Helper Chat Box

This is the chat panel you use to talk to the AI.

To open it:

1. Look for the **Ask AI** or **Sales AI** button on the screen. It is visible on every page.
2. Click it. A chat panel opens on the right side.
3. Type your question in the box at the bottom.
4. Press Enter or click Send.
5. Wait for the answer. It appears line by line.

**Screenshot Required: [Chat panel open with a sample question and answer]**

---

# 4. Who Can Do What

There are 3 kinds of users. Here is what each can do, in simple words.

## 4.1 Sales Person (normal user)

- Sees only **their own** records. Cannot see another sales person's records.
- Can create leads, customers, opportunities, quotations, and sales orders.
- Can submit small quotations and orders (below 5,000 in company money).
- Cannot submit big quotations or orders (5,000 or more). A manager must do that.
- Cannot cancel any record through the AI.
- Can use the AI helper for search, creation, follow-ups, and reports about their own work.

## 4.2 Sales Manager

- Sees their **whole team's** records.
- Can do everything a sales person can do.
- Can also submit and cancel quotations, orders, and invoices.
- Can approve big orders (5,000 or more).
- Can see extra reports, like unsupervised AI actions.

## 4.3 Admin (System Manager)

- Sees **everything**.
- Sets up the app: turns it on, adds AI profiles, sets rules, adds users and roles.
- Checks the full history of what the AI did.
- Fixes problems with permissions and settings.

Example: Ravi creates order SO-0001.

- Ravi can see and edit SO-0001.
- Anita (another sales person) cannot see SO-0001 at all. If she asks the AI for it, the AI says it is not available to her.
- The manager and the admin can both see SO-0001.

---

# 5. Everyday Tasks (Step by Step)

## 5.1 Find a Record With the AI

Purpose: quickly find your quotation, order, or customer without opening many pages.

Steps:

1. Open the AI chat box.
2. Type, for example: "Show my open quotations".
3. Read the list the AI shows.
4. To open one record, click its name or ask: "Open SAL-QTN-2026-00123".

What you should see: a short list with names, customers, dates, and amounts.

If the AI says a record is "not available to you", it means the record does not exist or it belongs to someone else. Check the spelling, or ask the owner or your manager.

## 5.2 Create a New Lead With the AI

Purpose: save a new potential customer.

Steps:

1. Open the AI chat box.
2. Type, for example: "Create a lead. First name Priya, company ABC Medical Store, email priya@abc.example, phone 98765 43210".
3. The AI will show you what it understood.
4. If something is missing, it will ask you. Answer the question.
5. When the approval box appears, check the details.
6. Click **Approve**.

What you should see: the AI tells you the new lead number, like CRM-LEAD-2026-0010.

Fields you may need to give:

- First name: required. Example: Priya.
- Company name: the shop or office name. Example: ABC Medical Store.
- Email: helps avoid making the same lead twice. Example: priya@abc.example.
- Phone: the contact number.
- City and area: where the customer is, like Delhi / North.

## 5.3 Create a Quotation With the AI

Purpose: make a price offer for a customer.

Steps:

1. Open the AI chat box.
2. Type, for example: "Create a quotation for ABC Medical Store for 10 LAPTOP-001 at 50,000 each".
3. The AI finds the customer and the item, and works out the price and tax.
4. An approval box appears showing the items, quantity, rate, tax, and total.
5. Check the total carefully.
6. Click **Approve**.

What you should see: the AI tells you the new quotation number, like SAL-QTN-2026-00123, and the total amount.

What happens next: the quotation is saved as a **Draft**. It is not final yet. You or your manager must submit it (see next section).

Things you need to give:

- Customer name: must already exist in the system. Example: ABC Medical Store.
- Item name: must already exist. Example: LAPTOP-001.
- Quantity: how many pieces. Example: 10.
- Rate: price per piece (optional — the AI can pick the normal price itself). Example: 50000.

## 5.4 Submit a Quotation or Order

Purpose: make a draft final so work can continue.

Steps (on screen):

1. Open the record (for example, your quotation).
2. Check all details and the total.
3. Click **Save** if you made changes.
4. Click **Submit**.
5. Confirm when asked.

Simple money rule:

- If the total is **below 5,000**, you can submit it yourself.
- If the total is **5,000 or more**, you cannot submit it. Leave it as Draft and ask your Sales Manager to check and submit it. The manager's submit counts as the approval.

The same rule works whether you click Submit on screen or ask the AI to submit.

## 5.5 Convert a Quotation to a Sales Order

Purpose: after the customer accepts your quotation, turn it into a confirmed order.

Steps:

1. Open the **submitted** quotation. (It must be submitted first. You cannot convert a draft.)
2. Click the **Make** button on the form.
3. Select **Sales Order**.
4. Check the items and dates on the new order form.
5. Click **Save**. This creates a Draft Sales Order.
6. Click **Submit** (same 5,000 rule as above).

Or with the AI: type "Convert SAL-QTN-2026-00123 to a sales order", check the approval box, and click **Approve**.

**Screenshot Required: [Submitted quotation showing the Make button]**

## 5.6 Add a Reminder (Follow-up)

Purpose: remind yourself to call or visit a customer.

Steps:

1. Open the AI chat box.
2. Type, for example: "Remind me to call ABC Medical Store tomorrow at 11am".
3. Check the approval box.
4. Click **Approve**.

What you should see: the AI confirms the reminder is saved. You will find it in your ToDo list.

To see your reminders, ask: "Show my follow-ups".

## 5.7 Check Your Numbers

Purpose: see how your sales are doing.

Steps:

1. Open the AI chat box.
2. Type, for example: "What is my open pipeline this month?" or "Top customers this quarter".
3. Read the answer with the numbers.

Or open the **Sales Dashboard** page and read the cards (see section 3.2).

---

# 6. The Approval Box (Very Important)

Whenever the AI wants to save, change, or submit something, it first shows you an **approval box**.

- It tells you in simple words what it wants to do.
- It shows the amounts, so you approve a figure, not a guess.
- It has two buttons: **Approve** and **Deny**.

What to do:

1. Read what it wants to do.
2. Check the customer name, items, and total.
3. If correct, click **Approve**.
4. If wrong, click **Deny** and tell the AI what to fix.

Note: approving does not skip permissions. If you are not allowed to do that action, it will still be blocked, and you will see the reason.

**Screenshot Required: [Approval box showing items, total, Approve and Deny buttons]**

---

# 7. What You Can and Cannot See

- You can always see records **you** created.
- You cannot see records created by **another sales person**. They will not appear in your lists, searches, or AI answers. This is normal and keeps everyone's work private.
- Your manager can see your records and help you with them.
- If you need another person's record, ask them or your manager. Do not try to guess the record number — the AI will refuse, and the refusal is recorded.

---

# 8. Things the AI Will Never Do

Even if you ask, the AI will always refuse these. This is by design, to keep data safe:

- Delete any record. (It will cancel instead, which keeps history.)
- Show you another person's private records.
- Send an email to an address that is not already saved on that record.
- Change the items on a submitted (final) record. A person must cancel and remake it.
- Run computer code or create new record types.
- Guess a customer name, item name, or amount. If it is unsure, it asks you.

---

# 9. If Something Goes Wrong

Find your problem below. Read why it happened and what to do.

- **Problem:** The chat says "Sales AI is turned off."
  **Why:** An admin switched the app off.
  **Do this:** Ask your admin to turn on Enable Sales AI in settings.

- **Problem:** The chat says "You do not have a role that permits access."
  **Why:** Your user has no sales role.
  **Do this:** Ask your admin to give you the Sales User role.

- **Problem:** The AI says a record is "not available to you."
  **Why:** The record does not exist, or it belongs to someone else.
  **Do this:** Check the spelling. If it is a colleague's record, ask them or your manager.

- **Problem:** You cannot submit. It talks about a 5,000 limit.
  **Why:** The total is 5,000 or more, so only a manager can submit.
  **Do this:** Keep it as Draft and ask your Sales Manager to review and submit.

- **Problem:** The Cancel button is missing.
  **Why:** Normal sales users cannot cancel. Only managers can.
  **Do this:** Ask your manager.

- **Problem:** A required field error appears.
  **Why:** Some needed information is missing, like customer name or item.
  **Do this:** Give the missing information. The AI will never guess it for you.

- **Problem:** The approval box disappeared after you closed the chat.
  **Why:** The work is paused, waiting for your answer.
  **Do this:** Open the chat again and open your recent chat. The approval box will be there.

- **Problem:** The AI says it cannot price the items.
  **Why:** The item, customer, or price list has a problem.
  **Do this:** Check the item and customer names. Ask your admin if the price list is missing.

---

# 10. Example Questions You Can Copy

Replace the names and numbers with your own, then send:

- Create a quotation for ABC Medical Store for 10 LAPTOP-001 at 50000 each.
- Show my open quotations.
- Show my open opportunities closing this month.
- Convert lead CRM-LEAD-2026-0010 to an opportunity titled 50 laptops for ABC.
- Update opportunity CRM-OPP-2026-0004 probability to 70.
- Submit quotation SAL-QTN-2026-00123.
- Remind me to call ABC Medical Store tomorrow at 11am.
- Show my follow-ups.
- What did ABC Medical Store buy last year?
- Which quotations expire this week?

---

# 11. For Admins and Managers (Short Guide)

## 11.1 Turn the App On

1. Log in as Administrator or System Manager.
2. Open Sales AI home page, then go to **Sales AI Settings** (under Manage, then Configuration).
3. Tick **Enable Sales AI**.
4. Select the **Default Agent Profile** (normally called Sales Advisor).
5. Choose **Autonomy**:
   - Read Only: the AI only answers, never saves anything.
   - Ask Before Every Change: the AI always asks first (recommended).
   - Act On Low Risk: the AI may do small safe things alone, but always asks for important things.
6. Click **Save**.

There are two extra switches. Keep them off unless you need them:

- **Enable Triggers:** lets the AI start work by itself (for example, on a schedule). Turn on only when your automatic rules are tested.
- **Enable For Customers:** lets website customers use a separate helper. Needs its own customer profile.

## 11.2 Give Users Access

1. Open the **User** record in ERPNext.
2. Add the role: **Sales User** for sales people, **Sales Manager** for managers.
3. To limit a sales person to their own customers, add a **User Permission** record. Example: user priya@example.com, allow Customer, value ABC Medical Store.
4. Tell the user to log out and log in again.

Remember: the chat helper only opens for office users (System Users) with a sales or accounts role. Other users are blocked automatically.

## 11.3 Approve Big Orders

1. Open the draft quotation or order the sales person prepared.
2. Check customer, items, and total.
3. Click **Submit**. Your submit counts as the approval for totals of 5,000 or more.

## 11.4 Check What the AI Did

1. Open Sales AI home page.
2. Go to **Review**, then **Sales AI Action Log**.
3. You will see who asked, what the AI did, which record changed, and whether it was allowed or refused.
4. For a quick audit, open the **Unsupervised Actions** report. It lists everything the AI did without a human approval. Review it regularly.

## 11.5 Set Rules for the AI (Optional)

1. Open **Sales AI Action Policy** (under Manage, then Governance).
2. Click **Add**.
3. Select the **Tool** (the action you want to control, for example create quotation).
4. Select the **Mode**: Allow (do it alone), Require Approval (always ask), or Deny (never do it).
5. Optionally set a **Role** (rule applies only to that role) and **Priority**.
6. Click **Save**.

If no rule exists for an action, the app is careful by default: every important change asks for approval.

---

# 12. The Full Journey in 10 Steps (Summary)

1. Log in.
2. Open the Sales AI app or click Ask AI on any page.
3. Find your customer or lead (or create a new one).
4. Draft a quotation with the AI and approve it.
5. Submit it if below 5,000, or ask your manager if 5,000 or more.
6. Convert the accepted quotation to a sales order.
7. Submit the sales order (same money rule).
8. The store/accounts team makes the delivery and the bill from your order.
9. Add reminders so no customer is forgotten.
10. Check your dashboard numbers at the end of the week.

---

*End of manual. Written in simple language from the actual Sales AI App implementation. Screenshot spots are marked where photos of your own system should be added before sharing with customers.*
