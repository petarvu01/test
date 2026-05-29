"""Data management — load, save, compute totals, notifications."""
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from helpers import (parse_date, fmt_date, fy_label, date_to_fy,
                     fy_range, calc_payment_due, calc_renewal_date, calc_tool_costs)

DATA_FILE = Path(__file__).parent / "dashboard_progress.json"
MAX_BACKUPS = 10

# ─── Default project template ────────────────────────────────────────────
INITIAL_PROJECTS = [
    ("I0850",   "ABBOTT",               False),
    ("I2620C",  "BIOMARIN",             False),
    ("I2620K",  "MOSAIC",               False),
    ("I2620G",  "LUXOTTICA",            False),
    ("I2620B",  "SKB",                  False),
    ("I2620D",  "CLEANSPARK",           False),
    ("I2620P",  "LOGISTIC+",            False),
    ("I2620N",  "PROACTIVE WW",         False),
    ("I2620J",  "SOURCED INTELLIGENCE", False),
    ("I2620O",  "CLPS",                 False),
    ("I2520Q",  "NUTRIEN",              False),
    ("I2620H",  "FRDA",                 False),
    ("I2620R",  "E3RF",                 False),
    ("I1018",   "MU-SEO",              False),
    ("I1270",   "NSIC/NASIC",           False),
    ("I2620I",  "Enbridge",             False),
    ("I0684",   "ECGRA",               False),
    ("1",       "PSYOPS",               True),
    ("NETCORE", "CYBER",                True),
    ("BI2",     "BI2",                  True),
    ("SAINT",   "SAINT",                True),
    ("WATCH",   "WATCH",                True),
    ("GEN AI",  "QUANTUM FORGE",        True),
    ("NGA",     "TEARLINE",             True),
    ("HIDTA",   "HIDTA",                True),
]


def blank_line(name="Phase 1 - Setup"):
    return [name, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def blank_project(code, name, has_budget=False):
    return {
        "code": code, "has_budget": has_budget,
        "start_date": "", "end_date": "", "extension_date": "",
        "notes": "", "hours_budget": 0.0, "hours_log": [],
        "assigned_tools": [],
        "lines": [blank_line("Phase 1 - Setup"), blank_line("Phase 2 - Core Dev")],
    }


def validate_line(line):
    if not isinstance(line, list):
        return blank_line()
    while len(line) < 13:
        line.append(0.0)
    return line[:13]


def default_data():
    pr = {}
    for code, name, hb in INITIAL_PROJECTS:
        pr[name] = blank_project(code, name, hb)
    return {
        "project_records": pr,
        "student_credits": [
            {"name": "Alice Cooper",   "student_id": "S10102", "credits": 4, "project": "ABBOTT"},
            {"name": "Bob Dylan",      "student_id": "S10488", "credits": 3, "project": "BIOMARIN"},
            {"name": "Charlie Parker", "student_id": "S10925", "credits": 3, "project": "ABBOTT"},
            {"name": "David Bowie",    "student_id": "S10712", "credits": 4, "project": "MOSAIC"},
        ],
        "invoices": [],
        "tools": [],
    }


def load_data() -> dict:
    if not DATA_FILE.exists():
        return default_data()
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        pr = data.get("project_records", {})
        for proj in pr.values():
            for key, default in [("notes",""), ("start_date",""), ("end_date",""),
                                  ("extension_date",""), ("hours_budget",0.0),
                                  ("hours_log",[]), ("assigned_tools",[])]:
                proj.setdefault(key, default)
            proj["lines"] = [validate_line(l) for l in proj.get("lines", [])]
            if not proj["lines"]:
                proj["lines"] = [blank_line()]
        data["project_records"] = pr
        data.setdefault("student_credits", [])
        data.setdefault("invoices", [])
        data.setdefault("tools", [])
        return data
    except Exception:
        return default_data()


def save_data(data: dict):
    try:
        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        _auto_backup()
    except OSError:
        pass


def _auto_backup():
    if not DATA_FILE.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"dashboard_backup_{ts}.json"
    for folder in [DATA_FILE.parent / "backups",
                   Path.home() / "Documents" / "PM_Dashboard_Backups"]:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA_FILE, folder / name)
            backups = sorted(folder.glob("dashboard_backup_*.json"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[MAX_BACKUPS:]:
                old.unlink()
        except Exception:
            pass


# ─── Computed values ─────────────────────────────────────────────────────
def compute_master_totals(data: dict) -> dict:
    pr = data["project_records"]
    total_budget = total_stu = total_pi = 0.0
    for proj in pr.values():
        is_red = proj.get("has_budget", False)
        for row in proj["lines"]:
            total_stu += int(row[1]) * float(row[2]) * float(row[3])
            total_pi  += float(row[4]) * float(row[5])
            if not is_red:
                total_budget += float(row[9]) + float(row[10]) + float(row[11]) + float(row[12])
    return {
        "active_projects":     len(pr),
        "total_budget":        total_budget,
        "total_stu_personnel": total_stu,
        "total_pi_cost":       total_pi,
        "total_credits":       sum(s["credits"] for s in data["student_credits"]),
    }


def project_contracted_total(proj: dict) -> float:
    return sum(float(r[9]) + float(r[10]) + float(r[11]) + float(r[12])
               for r in proj.get("lines", []))


def wo_project_total(data: dict, proj_name: str) -> float:
    total = 0.0
    for rec in data["invoices"]:
        if rec.get("type") == "Work Order" and rec.get("project") == proj_name:
            for inst in rec.get("installments", []):
                total += float(inst.get("amount", 0))
    return total


def flat_invoice_rows(data: dict, show_hist=True, proj_filter="All") -> list:
    rows = []
    for rec in data["invoices"]:
        proj = rec.get("project", "")
        if proj_filter != "All" and proj != proj_filter:
            continue
        if rec.get("type") == "Work Order":
            wo_total = round(sum(float(i.get("amount", 0))
                                 for i in rec.get("installments", [])), 2)
            for inst in rec.get("installments", []):
                if not show_hist and (inst.get("sent") or inst.get("paid")):
                    continue
                due = inst.get("due_date", "")
                net = inst.get("net_terms", "")
                rows.append({
                    "type": "Work Order", "number": rec.get("number", ""),
                    "project": proj, "description": inst.get("period", ""),
                    "inst_amount": float(inst.get("amount", 0)),
                    "wo_total": wo_total,
                    "net_terms": net, "due_date": due,
                    "payment_due": calc_payment_due(due, net),
                    "sent": inst.get("sent", False),
                    "paid": inst.get("paid", False),
                    "hours_deducted": "",
                    "_rec": rec, "_inst": inst,
                })
        else:
            if not show_hist and (rec.get("sent") or rec.get("paid")):
                continue
            amt = float(rec.get("amount", 0))
            due = rec.get("due_date", "")
            net = rec.get("net_terms", "")
            rows.append({
                "type": "Invoice", "number": rec.get("number", ""),
                "project": proj, "description": rec.get("description", ""),
                "inst_amount": amt, "wo_total": amt,
                "net_terms": net, "due_date": due,
                "payment_due": calc_payment_due(due, net),
                "sent": rec.get("sent", False),
                "paid": rec.get("paid", False),
                "hours_deducted": rec.get("hours_deducted", ""),
                "_rec": rec, "_inst": None,
            })
    return rows


def get_all_notifications(data: dict) -> list:
    today = date.today()
    notifs = []
    # 1. Payments
    for row in flat_invoice_rows(data):
        if row["sent"] or row["paid"]:
            continue
        d = parse_date(row["payment_due"]) or parse_date(row["due_date"])
        if not d:
            continue
        days = (d - today).days
        if days < 0:
            notifs.append(("🔴", "Overdue Payment", row["project"],
                           f"{row['type']} #{row['number']} — ${row['inst_amount']:,.2f} "
                           f"was due {abs(days)} day{'s' if abs(days)!=1 else ''} ago"))
        elif days <= 7:
            label = "TODAY" if days == 0 else ("TOMORROW" if days == 1 else f"in {days} days")
            notifs.append(("🟡", "Payment Due", row["project"],
                           f"{row['type']} #{row['number']} — ${row['inst_amount']:,.2f} due {label}"))
    # 2. Hours
    for name, proj in data["project_records"].items():
        budget = float(proj.get("hours_budget", 0))
        if budget <= 0:
            continue
        used = sum(float(e.get("hours", 0)) for e in proj.get("hours_log", []))
        pct = used / budget * 100
        remaining = budget - used
        if remaining < 0:
            notifs.append(("🔴", "Hours Exceeded", name,
                           f"Over by {abs(remaining):.1f} hrs — {used:.1f}/{budget:.1f} ({pct:.0f}%)"))
        elif pct >= 80:
            notifs.append(("🟡", "Hours At Risk", name,
                           f"{remaining:.1f} hrs left — {used:.1f}/{budget:.1f} ({pct:.0f}%)"))
    # 3. WO mismatches
    for name, proj in data["project_records"].items():
        contracted = project_contracted_total(proj)
        wo_t = wo_project_total(data, name)
        if abs(wo_t - contracted) > 0.01 and (wo_t > 0 or contracted > 0):
            notifs.append(("🔵", "WO Mismatch", name,
                           f"WO ${wo_t:,.2f} ≠ Contracted ${contracted:,.2f} (diff ${abs(wo_t-contracted):,.2f})"))
    # 4. Tool renewals
    for t in data["tools"]:
        if t.get("paid") or t.get("billing_cycle") == "One-time":
            continue
        r = calc_renewal_date(t)
        rd = parse_date(r)
        if not rd:
            continue
        days = (rd - today).days
        cost = float(t.get("cost", 0))
        if days < 0:
            notifs.append(("🔴", "Tool Overdue", t.get("name", ""),
                           f"{t.get('billing_cycle','')} ${cost:,.2f} was due {abs(days)} days ago"))
        elif days <= 7:
            label = "TODAY" if days == 0 else ("TOMORROW" if days == 1 else f"in {days} days")
            notifs.append(("🟡", "Tool Renewal", t.get("name", ""),
                           f"{t.get('billing_cycle','')} ${cost:,.2f} due {label}"))
    # Sort: red, yellow, blue
    order = {"🔴": 0, "🟡": 1, "🔵": 2}
    notifs.sort(key=lambda n: order.get(n[0], 9))
    return notifs


def get_fy_options(data: dict) -> list:
    fys = set()
    for proj in data["project_records"].values():
        ext = proj.get("extension_date", "")
        end = proj.get("end_date", "")
        effective_end = ext if ext else end
        for ds in (proj.get("start_date", ""), effective_end):
            d = parse_date(ds)
            if d:
                fys.add(date_to_fy(d))
    return ["All"] + [fy_label(y) for y in sorted(fys)]


def project_in_fy(proj: dict, label: str) -> bool:
    if label == "All":
        return True
    fy_s, fy_e = fy_range(label)
    if not fy_s:
        return True
    p_start = parse_date(proj.get("start_date", ""))
    ext = proj.get("extension_date", "")
    end = proj.get("end_date", "")
    p_end = parse_date(ext if ext else end)
    if not p_start and not p_end:
        return False
    if p_start and p_end:
        return p_start <= fy_e and p_end >= fy_s
    if p_start:
        return fy_s <= p_start <= fy_e
    if p_end:
        return fy_s <= p_end <= fy_e
    return False
