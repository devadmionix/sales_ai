"""Exhaustive cross-user RBAC test across all doctypes and operations.

Run with: bench --site ai.local execute sales_ai.tests.rbac_live.run
"""
import frappe
from sales_ai.guard import read_list, read_document, may_change, GuardError
from sales_ai.guard.analytics import aggregate

KHUSHI = "khushi.admionix@gmail.com"
TESTER = "salestest@example.com"
MANAGER = "salesmgr@example.com"


def run():
    results = []

    def test(desc, user, fn):
        frappe.set_user(user)
        try:
            ok, msg = fn()
            results.append(("PASS" if ok else "FAIL", desc, msg))
        except Exception as e:
            results.append(("FAIL", desc, str(e)[:100]))

    # === PART 1: LIST — each Sales User sees ONLY their own records ===
    for dt in ("Lead", "Quotation", "Opportunity", "Sales Order"):
        def check_list_khushi(dt=dt):
            r = read_list(dt)
            for rec in r["records"]:
                o = frappe.db.get_value(dt, rec["name"], "owner")
                if o != KHUSHI:
                    return False, f"LEAK! sees {rec['name']} owned by {o}"
            return True, f"sees {r['count']} own record(s)"
        test(f"Khushi lists {dt} — only own", KHUSHI, check_list_khushi)

        def check_list_tester(dt=dt):
            r = read_list(dt)
            for rec in r["records"]:
                o = frappe.db.get_value(dt, rec["name"], "owner")
                if o != TESTER:
                    return False, f"LEAK! sees {rec['name']} owned by {o}"
            return True, f"sees {r['count']} own record(s)"
        test(f"Tester lists {dt} — only own", TESTER, check_list_tester)

    # === PART 2: READ — each Sales User cannot read the other's ===
    frappe.set_user("Administrator")
    pairs = []
    for dt in ("Lead", "Quotation", "Opportunity", "Sales Order"):
        k = frappe.db.get_value(dt, {"owner": KHUSHI}, "name")
        t = frappe.db.get_value(dt, {"owner": TESTER}, "name")
        if k and t:
            pairs.append((dt, k, t))

    for dt, krec, trec in pairs:
        def try_kt(dt=dt, name=trec):
            try:
                read_document(dt, name)
                return False, f"LEAK! read {name}"
            except GuardError:
                return True, "blocked"
        test(f"Khushi BLOCKED from Tester's {dt} ({trec})", KHUSHI, try_kt)

        def try_tk(dt=dt, name=krec):
            try:
                read_document(dt, name)
                return False, f"LEAK! read {name}"
            except GuardError:
                return True, "blocked"
        test(f"Tester BLOCKED from Khushi's {dt} ({krec})", TESTER, try_tk)

        def own_k(dt=dt, name=krec):
            read_document(dt, name)
            return True, f"reads {name}"
        test(f"Khushi CAN read own {dt} ({krec})", KHUSHI, own_k)

        def own_t(dt=dt, name=trec):
            read_document(dt, name)
            return True, f"reads {name}"
        test(f"Tester CAN read own {dt} ({trec})", TESTER, own_t)

    # === PART 3: UPDATE — each Sales User cannot update the other's ===
    for dt, krec, trec in pairs:
        def upd_kt(dt=dt, name=trec):
            try:
                may_change(dt, name, "write", "Update")
                return False, f"LEAK! updated {name}"
            except GuardError:
                return True, "blocked"
        test(f"Khushi BLOCKED from updating Tester's {dt}", KHUSHI, upd_kt)

        def upd_tk(dt=dt, name=krec):
            try:
                may_change(dt, name, "write", "Update")
                return False, f"LEAK! updated {name}"
            except GuardError:
                return True, "blocked"
        test(f"Tester BLOCKED from updating Khushi's {dt}", TESTER, upd_tk)

    # === PART 4: Admin-owned records invisible to Sales Users ===
    frappe.set_user("Administrator")
    for dt in ("Lead", "Quotation"):
        arec = frappe.db.get_value(dt, {"owner": "Administrator"}, "name")
        if arec:
            def adm_k(dt=dt, name=arec):
                try:
                    read_document(dt, name)
                    return False, f"LEAK! read Admin's {name}"
                except GuardError:
                    return True, "blocked"
            test(f"Khushi BLOCKED from Admin's {dt} ({arec})", KHUSHI, adm_k)

            def adm_t(dt=dt, name=arec):
                try:
                    read_document(dt, name)
                    return False, f"LEAK! read Admin's {name}"
                except GuardError:
                    return True, "blocked"
            test(f"Tester BLOCKED from Admin's {dt} ({arec})", TESTER, adm_t)

    # === PART 5: Manager CAN see everyone's records ===
    for dt, krec, trec in pairs:
        def mgr_k(dt=dt, name=krec):
            read_document(dt, name)
            return True, f"reads {name}"
        test(f"Manager CAN read Khushi's {dt}", MANAGER, mgr_k)

        def mgr_t(dt=dt, name=trec):
            read_document(dt, name)
            return True, f"reads {name}"
        test(f"Manager CAN read Tester's {dt}", MANAGER, mgr_t)

    # === PART 6: Analytics scoped per user ===
    for dt_agg in ("Lead", "Quotation"):
        def agg_k(dt=dt_agg):
            r = aggregate(dt, measures=["count"])
            c = r["rows"][0]["count"]
            return c <= 3, f"count={c}"
        test(f"Analytics: Khushi {dt_agg} count (own only)", KHUSHI, agg_k)

        def agg_m(dt=dt_agg):
            r = aggregate(dt, measures=["count"])
            c = r["rows"][0]["count"]
            return c >= 4, f"count={c}"
        test(f"Analytics: Manager {dt_agg} count (all)", MANAGER, agg_m)

    # === RESULTS ===
    frappe.set_user("Administrator")
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")

    print()
    print("=" * 72)
    print("  COMPREHENSIVE CROSS-USER RBAC TEST — ai.local")
    print("=" * 72)
    for status, desc, msg in results:
        print(f"  {status}  {desc} — {msg}")
    print()
    print(f"  TOTAL: {passed} passed, {failed} failed out of {len(results)}")
    print("=" * 72)
