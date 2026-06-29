"""Data management — load, save, compute totals, notifications."""
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path

from helpers import (parse_date, fmt_date, fy_label, date_to_fy,
                     fy_range, calc_payment_due, calc_renewal_date, calc_tool_costs)

DATA_FILE = Path(__file__).parent / "dashboard_progress.json"
GIST_FILENAME = "dashboard_progress.json"
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
    # Index map:
    # 0 name, 1 students, 2 stu rate, 3 stu hours, 4 PI rate, 5 PI hours,
    # 6 legacy actual indirect, 7 legacy actual fringe, 8 actual travel,
    # 9 contracted personnel, 10 contracted PI, 11 contracted indirect,
    # 12 contracted fringe, 13 contracted travel
    # Hours deducted is COMPUTED from (students × stu hours) + PI hours — no field.
    return [name, 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def blank_project(code, name, has_budget=False):
    return {
        "code": code, "has_budget": has_budget,
        "status": "Active",
        "start_date": "", "end_date": "", "extension_date": "",
        "notes": "", "notes_by_fy": {}, "hours_budget": 0.0, "hours_log": [],
        "contracted_hours": 0.0,
        "contracted_hours_by_fy": {},
        "fy_funding": {},
        "assigned_tools": [],
        "lines_by_fy": {},
        "lines": [blank_line("Phase 1 - Setup"), blank_line("Phase 2 - Core Dev")],
    }


def _coerce(v, cast=float):
    """Best-effort numeric coercion; never raises (0 on failure)."""
    try:
        return cast(v)
    except (TypeError, ValueError):
        try:
            return cast(float(v))
        except (TypeError, ValueError):
            return cast(0)


def validate_line(line):
    if not isinstance(line, list):
        return blank_line()
    # Migrate old 13-field rows by copying old travel into the new contracted travel slot.
    if len(line) == 13:
        try:
            line.append(float(line[8]))
        except Exception:
            line.append(0.0)
    while len(line) < 14:
        line.append(0.0)
    line = line[:14]
    # Coerce field types so downstream math never crashes on stray/blank text.
    line[0] = "" if line[0] is None else str(line[0])
    line[1] = _coerce(line[1], int)
    for i in (2, 3, 4, 5, 8, 9, 10, 11, 12, 13):
        line[i] = _coerce(line[i], float)
    # Legacy actual indirect/fringe are no longer used.
    line[6] = 0.0
    line[7] = 0.0
    return line


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


# ─── GitHub Gist storage (durable, survives Streamlit redeploys) ──────────
def _gist_config():
    """Return (token, gist_id) from Streamlit secrets or env vars; (None, None) if unset.
    Looks under [pm_dashboard] first, then [github], so either section name works."""
    token = gist_id = None
    try:
        import streamlit as st
        for section in ("pm_dashboard", "github"):
            if section in st.secrets:
                token = st.secrets[section].get("token")
                gist_id = st.secrets[section].get("gist_id")
                if token and gist_id:
                    break
    except Exception:
        pass
    token = token or os.environ.get("GITHUB_TOKEN")
    gist_id = gist_id or os.environ.get("GIST_ID")
    return token, gist_id


def gist_configured() -> bool:
    token, gist_id = _gist_config()
    return bool(token and gist_id)


def _load_from_gist():
    """Return parsed dict from the gist, or None if not configured / unreachable.
    An empty gist returns {} (distinct from None) so callers can seed it safely."""
    token, gist_id = _gist_config()
    if not token or not gist_id:
        return None
    try:
        import requests
        r = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        files = r.json().get("files", {})
        f = files.get(GIST_FILENAME)
        if not f:
            return {}
        content = f.get("content", "")
        if f.get("truncated") and f.get("raw_url"):
            content = requests.get(f["raw_url"], timeout=10).text
        content = (content or "").strip()
        if not content:
            return {}
        return json.loads(content)
    except Exception:
        return None


def _save_to_gist(data: dict) -> bool:
    token, gist_id = _gist_config()
    if not token or not gist_id:
        return False
    try:
        import requests
        payload = {"files": {GIST_FILENAME: {"content": json.dumps(data, indent=4)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}",
                     "Accept": "application/vnd.github+json"},
            json=payload, timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _read_local():
    """Raw read of the local cache file, or None."""
    if not DATA_FILE.exists():
        return None
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_local(data: dict):
    try:
        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError:
        pass


def _normalize(data: dict) -> dict:
    """Apply field defaults and per-line migrations to a loaded data dict."""
    # Re-key student-worker tables to the canonical FY label, so older keys
    # (e.g. "FY 2025-26") adopt the current label format automatically and
    # idempotently. The year is parsed from the 2nd token, which is unchanged.
    sw = data.get("student_workers")
    if isinstance(sw, dict) and sw:
        fixed = {}
        for label, rec in sw.items():
            try:
                y = int(str(label).split()[1].split("-")[0])
                fixed[fy_label(y)] = rec
            except Exception:
                fixed[label] = rec
        data["student_workers"] = fixed

    # Per-FY student credits. Migrate an old flat list into a fiscal-year bucket
    # (the current FY), then re-key everything to the canonical label and keep a
    # flattened "student_credits" view so whole-dataset totals keep working.
    scbf = data.get("student_credits_by_fy")
    if not isinstance(scbf, dict):
        flat = data.get("student_credits", []) or []
        scbf = {fy_label(date_to_fy(date.today())): flat} if flat else {}
    fixed_c = {}
    for label, recs in scbf.items():
        try:
            y = int(str(label).split()[1].split("-")[0])
            fixed_c[fy_label(y)] = recs or []
        except Exception:
            fixed_c[label] = recs or []
    data["student_credits_by_fy"] = fixed_c
    data["student_credits"] = [r for recs in fixed_c.values() for r in recs]

    # Tools: collapse the old cycles (Annual/2-Year/anything) into the
    # two-cycle model. Only "Monthly" stays monthly; everything else is one-time.
    for t in data.get("tools", []) or []:
        if t.get("billing_cycle") != "Monthly":
            t["billing_cycle"] = "One-time"

    pr = data.get("project_records", {})
    for proj in pr.values():
        for key, default in [("notes",""), ("notes_by_fy",{}),
                              ("start_date",""), ("end_date",""),
                              ("extension_date",""), ("hours_budget",0.0),
                              ("hours_log",[]), ("assigned_tools",[]),
                              ("status","Active"), ("fy_funding",{}),
                              ("lines_by_fy",{}), ("contracted_hours_by_fy",{})]:
            proj.setdefault(key, default)
        # Per-FY notes: keys are FY years as strings ({"2026": "..."}). The legacy
        # project-level "notes" string is preserved untouched and acts as the
        # fallback shown for any FY that has no note of its own (see
        # project_note_for_fy). Re-key any non-canonical year keys to plain "YYYY".
        nbf = proj.get("notes_by_fy")
        if isinstance(nbf, dict) and nbf:
            fixed_n = {}
            for k, v in nbf.items():
                try:
                    fixed_n[str(int(str(k).split()[1].split("-")[0]))] = v
                except Exception:
                    fixed_n[str(k)] = v
            proj["notes_by_fy"] = fixed_n
        # Migrate old Hours-tab project-level budget into the new contracted_hours field.
        if "contracted_hours" not in proj:
            proj["contracted_hours"] = float(proj.get("hours_budget", 0.0))
        # Per-FY line items: if not yet split by FY, move existing flat lines
        # under the project's primary fiscal year (one-time migration).
        if not proj.get("lines_by_fy"):
            existing = [validate_line(l) for l in proj.get("lines", [])]
            if not existing:
                existing = [blank_line()]
            proj["lines_by_fy"] = {str(project_primary_fy(proj)): existing}
        else:
            proj["lines_by_fy"] = {
                str(y): [validate_line(l) for l in (rows or [])]
                for y, rows in proj["lines_by_fy"].items()
            }
        # Keep the flat "lines" list as a synced view of every FY (so all the
        # whole-project totals that read proj["lines"] keep working unchanged).
        proj["lines"] = flatten_lines(proj) or [blank_line()]
        # Per-FY contracted hours: migrate from last round's fy_funding hours,
        # then from the legacy project-level contracted_hours, one time only.
        if not proj.get("contracted_hours_by_fy"):
            chf = {}
            for y, e in (proj.get("fy_funding") or {}).items():
                h = float((e or {}).get("hours_added") or 0)
                if h:
                    chf[str(y)] = h
            if not chf:
                legacy = float(proj.get("contracted_hours", 0.0) or 0)
                if legacy:
                    chf[str(project_primary_fy(proj))] = legacy
            proj["contracted_hours_by_fy"] = chf
    data["project_records"] = pr
    data.setdefault("student_credits", [])
    data.setdefault("invoices", [])
    data.setdefault("tools", [])
    for t in data["tools"]:
        if isinstance(t, dict):
            t.setdefault("split_amounts", {})
            # Migrate the earlier percent-based 'allocations' to dollar amounts
            # (percent of the billing-cycle cost), then drop the old field.
            old = t.pop("allocations", None)
            if old and not t["split_amounts"]:
                try:
                    valid = {p: float(v) for p, v in old.items() if float(v) > 0}
                    total = sum(valid.values())
                    cost = float(t.get("cost", 0))
                    if total > 0 and cost > 0:
                        t["split_amounts"] = {p: round(cost * v / total, 2)
                                              for p, v in valid.items()}
                except (TypeError, ValueError):
                    pass
    data.setdefault("overview_kpis", list(DEFAULT_KPIS))
    return data


def load_data() -> dict:
    # 1. Prefer the gist (the durable source of truth on Streamlit Cloud).
    gist_data = _load_from_gist()
    if gist_data is not None:
        if gist_data:  # gist has real content
            data = _normalize(gist_data)
            _write_local(data)      # refresh local cache
            return data
        # Gist is configured but EMPTY → seed it from local cache or defaults (once).
        seed = _normalize(_read_local() or default_data())
        _save_to_gist(seed)
        _write_local(seed)
        return seed
    # 2. Gist not configured or unreachable → use local cache, never overwrite the gist.
    local = _read_local()
    if local is not None:
        try:
            return _normalize(local)
        except Exception:
            return default_data()
    # 3. Nothing anywhere yet → defaults.
    return default_data()


def save_data(data: dict):
    """Persist data. Returns True if the gist write succeeded, False if it was
    attempted but failed, and None if the gist isn't configured (local-only)."""
    # Always write the local cache (fast, and a fallback if the gist is briefly down).
    _write_local(data)
    try:
        _auto_backup()
    except Exception:
        pass
    # Push to the durable gist store.
    if not gist_configured():
        return None
    return _save_to_gist(data)


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
            total_budget += fy_total_budget_added(proj)
    return {
        "active_projects":     len(pr),
        "total_budget":        total_budget,
        "total_stu_personnel": total_stu,
        "total_pi_cost":       total_pi,
        "total_credits":       sum(s["credits"] for s in data["student_credits"]),
    }


def project_contracted_total(proj: dict) -> float:
    return sum(float(r[9]) + float(r[10]) + float(r[11]) + float(r[12]) + float(r[13])
               for r in proj.get("lines", []))


def project_hours_summary(proj: dict):
    """Return (budget, deducted) hours for a project, whole-project totals.

    Budget   = sum of per-FY contracted hours (falls back to legacy
               project-level contracted_hours during migration).
    Deducted = sum over all lines of (Students × Stu Hours) + PI Hours.
    """
    chf = proj.get("contracted_hours_by_fy") or {}
    if chf:
        budget = sum(float(v or 0) for v in chf.values())
    else:
        budget = float(proj.get("contracted_hours", 0.0))
    deducted = 0.0
    for r in proj.get("lines", []):
        try:
            students = int(r[1]) if len(r) > 1 else 0
            stu_hrs  = float(r[3]) if len(r) > 3 else 0.0
            pi_hrs   = float(r[5]) if len(r) > 5 else 0.0
            deducted += students * stu_hrs + pi_hrs
        except (TypeError, ValueError):
            pass
    return budget, deducted


# ─── Per-FY line items + budget (carry-over) ─────────────────────────────
# Line items are stored per fiscal year:  proj["lines_by_fy"] = {"2026": [...]}
# proj["lines"] is kept as the flattened view of every FY's lines.
#
# BUDGET for a FY = the contracted columns of that FY's line items
#   (Cont. Personnel + PI + Indirect + Fringe + Travel).
# SPENT for a FY  = that FY's actual costs (Personnel + PI + actual Travel).
# Leftover (Available - Spent) carries into the next FY automatically once the
# FY has ended (after May 31).  Carry-in is DERIVED, never stored, so the whole
# ledger is always re-computable and never double-counts.
#
# Contracted HOURS are set per FY in proj["contracted_hours_by_fy"] = {"2026": 100}.

def _fy_ended(year: int) -> bool:
    """True once fiscal year (Jun 1 of `year` → May 31 of year+1) is over."""
    try:
        return date.today() > date(year + 1, 5, 31)
    except Exception:
        return False


def project_primary_fy(proj: dict) -> int:
    """The FY a project 'belongs' to by default (used to migrate old data)."""
    for ds in (proj.get("start_date", ""), proj.get("end_date", ""),
               proj.get("extension_date", "")):
        d = parse_date(ds)
        if d:
            return date_to_fy(d)
    return date_to_fy(date.today())


def project_note_for_fy(proj: dict, year) -> str:
    """Note text for one fiscal year. A FY that has its own saved note (even an
    empty string) uses it; otherwise we fall back to the legacy project-level
    'notes' so existing notes keep showing until a FY-specific note is saved."""
    nbf = proj.get("notes_by_fy") or {}
    key = str(year)
    if key in nbf:
        return nbf[key] or ""
    return proj.get("notes", "") or ""


def set_project_note_for_fy(proj: dict, year, text: str):
    """Save a note for one fiscal year. The value is stored even when blank, so
    clearing a FY's note overrides the legacy fallback rather than re-showing it."""
    nbf = proj.setdefault("notes_by_fy", {})
    nbf[str(year)] = text or ""


def lines_by_fy(proj: dict) -> dict:
    return proj.setdefault("lines_by_fy", {})


def flatten_lines(proj: dict) -> list:
    lbf = proj.get("lines_by_fy") or {}
    out = []
    for y in sorted(lbf, key=lambda k: int(k)):
        out.extend(lbf[y])
    return out


def fy_lines(proj: dict, year) -> list:
    return (proj.get("lines_by_fy") or {}).get(str(year), [])


def _contracted_travel(r) -> float:
    """Contracted travel = row[13], falling back to old row[8] if blank."""
    try:
        v = float(r[13])
    except Exception:
        v = 0.0
    if v == 0.0:
        try:
            v = float(r[8])
        except Exception:
            v = 0.0
    return v


def fy_contracted_budget(proj: dict, year) -> float:
    """Budget for one FY = contracted Personnel + PI + Indirect + Fringe + Travel."""
    total = 0.0
    for r in fy_lines(proj, year):
        r = validate_line(r)
        total += float(r[9]) + float(r[10]) + float(r[11]) + float(r[12]) + _contracted_travel(r)
    return total


def fy_actual_spend(proj: dict, year) -> float:
    """Actual spend for a FY: personnel + PI + actual travel from its lines."""
    total = 0.0
    for r in fy_lines(proj, year):
        r = validate_line(r)
        total += int(r[1]) * float(r[2]) * float(r[3])   # students × rate × hrs
        total += float(r[4]) * float(r[5])               # PI rate × PI hrs
        total += float(r[8])                             # actual travel
    return total


def fy_contracted_hours(proj: dict, year) -> float:
    return float((proj.get("contracted_hours_by_fy") or {}).get(str(year), 0) or 0)


def fy_hours_spend(proj: dict, year) -> float:
    """Hours deducted for a FY: (students × stu hrs) + PI hrs from its lines."""
    total = 0.0
    for r in fy_lines(proj, year):
        r = validate_line(r)
        total += int(r[1]) * float(r[3]) + float(r[5])
    return total


def _fy_year_set(proj: dict):
    years = set(int(k) for k in (proj.get("lines_by_fy") or {}))
    years |= set(int(k) for k in (proj.get("contracted_hours_by_fy") or {}))
    return years


def fy_funding_rows(proj: dict) -> list:
    """Derived per-FY rows, ascending by year.

    Row keys: year, carried_in, added (= contracted budget), available, spent,
    remaining, and the h_* hours equivalents.  Leftover (remaining) flows into
    the next FY's carried_in once the FY has ended.
    """
    rows = []
    prev_year = None
    prev_rem = 0.0
    prev_hrem = 0.0
    for y in sorted(_fy_year_set(proj)):
        added = fy_contracted_budget(proj, y)
        hadded = fy_contracted_hours(proj, y)
        # Running balance: leftover from the previous year always carries into
        # this one (so the next FY shows the carry-in immediately, not only
        # after the prior year ends on May 31).
        if prev_year is not None:
            cin, hcin = prev_rem, prev_hrem
        else:
            cin, hcin = 0.0, 0.0
        avail = cin + added
        havail = hcin + hadded
        spent = fy_actual_spend(proj, y)
        hspent = fy_hours_spend(proj, y)
        rem = avail - spent
        hrem = havail - hspent
        rows.append({
            "year": y, "carried_in": cin, "added": added, "available": avail,
            "spent": spent, "remaining": rem,
            "h_carried_in": hcin, "h_added": hadded, "h_available": havail,
            "h_spent": hspent, "h_remaining": hrem,
        })
        prev_year, prev_rem, prev_hrem = y, rem, hrem
    return rows


def fy_carry_in_for(proj: dict, year: int) -> tuple:
    """($ carry-in, hours carry-in) for a FY. If the year already exists in the
    ledger its computed carry-in is authoritative; otherwise carry the leftover
    of the most recent earlier year that has data (running balance)."""
    rows = fy_funding_rows(proj)
    for r in rows:
        if r["year"] == int(year):
            return r["carried_in"], r["h_carried_in"]
    prior = [r for r in rows if r["year"] < int(year)]
    if prior:
        last = prior[-1]
        return last["remaining"], last["h_remaining"]
    return 0.0, 0.0


def fy_budget_available(proj: dict, year) -> float:
    """Budget available for one FY = carried-in + contracted budget."""
    for r in fy_funding_rows(proj):
        if r["year"] == int(year):
            return r["available"]
    return fy_carry_in_for(proj, int(year))[0]


def fy_hours_available(proj: dict, year) -> float:
    for r in fy_funding_rows(proj):
        if r["year"] == int(year):
            return r["h_available"]
    return fy_carry_in_for(proj, int(year))[1]


def fy_hours_summary(proj: dict, year) -> tuple:
    """(available, spent, remaining) contracted hours for one FY."""
    cin_h = fy_carry_in_for(proj, int(year))[1]
    avail = cin_h + fy_contracted_hours(proj, year)
    spent = fy_hours_spend(proj, year)
    return avail, spent, avail - spent


def fy_total_budget_added(proj: dict) -> float:
    """Total contracted budget across all FYs (for the 'All' filter); equals
    the whole-project contracted total and does not double-count carry-over."""
    return sum(fy_contracted_budget(proj, y) for y in _fy_year_set(proj))


def count_tool_users(data: dict, tool_name: str) -> int:
    """Number of projects that have this tool in their assigned_tools list."""
    if not tool_name:
        return 0
    return sum(1 for proj in data.get("project_records", {}).values()
               if tool_name in proj.get("assigned_tools", []))


def tool_users(data: dict, tool_name: str) -> list:
    """Sorted list of project names that have this tool assigned."""
    if not tool_name:
        return []
    return sorted(name for name, proj in data.get("project_records", {}).items()
                  if tool_name in proj.get("assigned_tools", []))


def tool_split_amounts(data: dict, tool: dict) -> dict:
    """Custom dollar split over the tool's CURRENT users, in billing-cycle units
    ({project: dollars}). Entries for unassigned projects are ignored.
    Returns {} when no custom split is set (→ equal split applies)."""
    users = tool_users(data, tool.get("name", ""))
    sa = tool.get("split_amounts") or {}
    out = {}
    for p in users:
        try:
            v = float(sa.get(p, 0))
            if v > 0:
                out[p] = v
        except (TypeError, ValueError):
            pass
    return out


def tool_has_custom_split(data: dict, tool: dict) -> bool:
    """True if the tool has at least one positive custom dollar amount
    for a currently-assigned project."""
    return bool(tool_split_amounts(data, tool))


def tool_split_for(data: dict, tool: dict, project_name: str) -> float:
    """This project's dollar share of the tool, in billing-cycle units.

    Literal semantics: with a custom split, each project gets exactly the
    amount entered for it (projects without an entry get 0). Without a custom
    split, the tool's cost divides equally among its users."""
    users = tool_users(data, tool.get("name", ""))
    if project_name not in users:
        return 0.0
    custom = tool_split_amounts(data, tool)
    if custom:
        return custom.get(project_name, 0.0)
    try:
        cost = float(tool.get("cost", 0))
    except (TypeError, ValueError):
        cost = 0.0
    return cost / len(users)


def _cycle_amount_to_monthly_annual(amount: float, cycle: str):
    """Convert a billing-cycle dollar amount to (monthly, annual) — mirrors
    calc_tool_costs' conversion."""
    if cycle == "Monthly":
        return amount, amount * 12
    if cycle == "Annual":
        return round(amount / 12, 2), amount
    if cycle == "2-Year":
        return round(amount / 24, 2), round(amount / 2, 2)
    return 0.0, 0.0  # One-time / unknown


def tool_share_costs(data: dict, tool: dict, project_name: str = None):
    """Return (monthly_share, annual_share) of this tool's cost.

    With project_name: that project's share (literal custom $ if set, else equal).
    Without:           the equal per-project share.
    (0, 0) if no projects use the tool."""
    users = tool_users(data, tool.get("name", ""))
    if not users:
        return 0.0, 0.0
    cycle = tool.get("billing_cycle", "Monthly")
    if project_name is None:
        try:
            amt = float(tool.get("cost", 0)) / len(users)
        except (TypeError, ValueError):
            amt = 0.0
    else:
        amt = tool_split_for(data, tool, project_name)
    return _cycle_amount_to_monthly_annual(amt, cycle)


def tool_split_status(data: dict, tool: dict):
    """Compare the entered custom split against the tool's cost.
    Returns (entered_total, cost, diff) where diff = entered − cost,
    or None when no custom split is set."""
    custom = tool_split_amounts(data, tool)
    if not custom:
        return None
    try:
        cost = float(tool.get("cost", 0))
    except (TypeError, ValueError):
        cost = 0.0
    entered = sum(custom.values())
    return entered, cost, entered - cost


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
    # 2. Hours (budget = project-level contracted_hours; used = sum of line hours_deducted)
    for name, proj in data["project_records"].items():
        budget, used = project_hours_summary(proj)
        if budget <= 0:
            continue
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
    # 3b. Tool split mismatches (entered $ split ≠ tool cost)
    for t in data["tools"]:
        status = tool_split_status(data, t)
        if status:
            entered, cost, diff = status
            if abs(diff) > 0.01:
                word = "unassigned" if diff < 0 else "over-assigned"
                notifs.append(("🔵", "Tool Split Mismatch", t.get("name", ""),
                               f"Split ${entered:,.2f} ≠ cost ${cost:,.2f} "
                               f"(${abs(diff):,.2f} {word})"))
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


def tool_in_fy(tool: dict, label: str) -> bool:
    """Whether a tool/subscription is active in the given fiscal year.
    A tool with no end date is treated as ongoing from its start date onward;
    a tool with no start date shows in every FY."""
    if label == "All":
        return True
    fy_s, fy_e = fy_range(label)
    if not fy_s:
        return True
    t_start = parse_date(tool.get("start_date", ""))
    t_end = parse_date(tool.get("end_date", ""))
    if not t_start:
        return True
    if t_start > fy_e:
        return False
    if t_end is None:
        return True
    return t_end >= fy_s


def _fy_year_of(label) -> int:
    try:
        return int(str(label).split()[1].split("-")[0])
    except Exception:
        return None


def credits_for_fy(data: dict, label: str) -> list:
    """Credit records for a fiscal year (matched by year so label format doesn't
    matter). 'All' returns every FY's records combined."""
    scbf = data.get("student_credits_by_fy", {}) or {}
    if label == "All":
        return [r for recs in scbf.values() for r in recs]
    target = _fy_year_of(label)
    out = []
    for key, recs in scbf.items():
        if _fy_year_of(key) == target:
            out.extend(recs or [])
    return out


def project_credits(data: dict, label: str, project: str) -> float:
    return sum(float(r.get("credits", 0) or 0)
               for r in credits_for_fy(data, label) if r.get("project") == project)


def set_credits_for_fy(data: dict, label: str, records: list):
    """Replace one fiscal year's credit records (stored under the canonical
    label) and keep the flattened student_credits view in sync."""
    scbf = data.setdefault("student_credits_by_fy", {})
    y = _fy_year_of(label)
    key = fy_label(y) if y is not None else label
    for k in list(scbf.keys()):
        if _fy_year_of(k) == y:
            del scbf[k]
    scbf[key] = records
    data["student_credits"] = [r for recs in scbf.values() for r in recs]


# ─── Overview KPIs (predefined, user-selectable) ──────────────────────────
# Ordered (key, label) pairs — this is the menu users pick from.
KPI_OPTIONS = [
    ("active_projects",     "Active Projects"),
    ("total_credits",       "Student Credits"),
    ("total_budget",        "Total Budget"),
    ("total_stu_personnel", "Personnel Cost"),
    ("total_pi_cost",       "PI Cost"),
    ("total_actuals",       "Total Actuals"),
    ("total_cont_hours",    "Contracted Hours"),
    ("total_hours_deducted","Hours Deducted"),
    ("hours_remaining",     "Hours Remaining"),
    ("unpaid_count",        "Unpaid Invoices"),
    ("unpaid_amount",       "Unpaid Amount"),
    ("overdue_count",       "Overdue Payments"),
    ("tools_count",         "Tools"),
    ("tools_annual",        "Tool Cost / yr"),
    ("students_count",      "Student Workers (Credits tab)"),
]
DEFAULT_KPIS = ["active_projects", "total_credits", "total_budget", "total_stu_personnel"]
KPI_LABELS = dict(KPI_OPTIONS)


def kpi_fy_options(data: dict) -> list:
    """Fiscal years that have data anywhere (project dates, line items,
    contracted hours, credits, or invoices), for the Overview FY filter.
    Always includes 'All' first and the current FY so the filter is usable."""
    years = set()
    for proj in data.get("project_records", {}).values():
        for k in (proj.get("lines_by_fy") or {}):
            try:
                years.add(int(k))
            except (TypeError, ValueError):
                pass
        for k in (proj.get("contracted_hours_by_fy") or {}):
            try:
                years.add(int(k))
            except (TypeError, ValueError):
                pass
        ext = proj.get("extension_date", "")
        for ds in (proj.get("start_date", ""), ext or proj.get("end_date", "")):
            d = parse_date(ds)
            if d:
                years.add(date_to_fy(d))
    for k in (data.get("student_credits_by_fy") or {}):
        yy = _fy_year_of(k)
        if yy is not None:
            years.add(yy)
    for rec in data.get("invoices", []):
        if rec.get("type") == "Work Order":
            for inst in rec.get("installments", []):
                d = parse_date(inst.get("due_date", ""))
                if d:
                    years.add(date_to_fy(d))
        else:
            d = parse_date(rec.get("due_date", ""))
            if d:
                years.add(date_to_fy(d))
    years.add(date_to_fy(date.today()))
    return ["All"] + [fy_label(y) for y in sorted(years)]


def compute_kpis(data: dict, fy: str = "All") -> dict:
    """Compute every predefined KPI as formatted strings.

    fy='All' returns whole-dataset values (unchanged). For a specific FY label,
    every figure is scoped to that fiscal year using the same per-FY logic the
    rest of the app uses. Tool KPIs are scoped by each tool's active date range
    (tool_in_fy), matching the Tools tab's fiscal-year filter."""
    pr = data.get("project_records", {})
    scoped = fy != "All"
    y = _fy_year_of(fy) if scoped else None

    if scoped and y is not None:
        active_projects = sum(1 for p in pr.values() if project_in_fy(p, fy))
        total_credits = sum(float(r.get("credits", 0) or 0)
                            for r in credits_for_fy(data, fy))
        total_budget = sum(fy_contracted_budget(p, y) for p in pr.values()
                           if not p.get("has_budget", False))
        total_stu = total_pi = 0.0
        for p in pr.values():
            for r in fy_lines(p, y):
                r = validate_line(r)
                total_stu += int(r[1]) * float(r[2]) * float(r[3])
                total_pi  += float(r[4]) * float(r[5])
        actuals = sum(fy_actual_spend(p, y) for p in pr.values())
        cont_hours = hours_deducted = 0.0
        for p in pr.values():
            hb = fy_contracted_hours(p, y)
            cont_hours += hb
            if hb > 0:
                hours_deducted += fy_hours_spend(p, y)
        rows = []
        for r in flat_invoice_rows(data):
            d = parse_date(r["due_date"])
            if d and date_to_fy(d) == y:
                rows.append(r)
        students_count = len(credits_for_fy(data, fy))
    else:
        totals = compute_master_totals(data)
        active_projects = totals["active_projects"]
        total_credits = totals["total_credits"]
        total_budget = totals["total_budget"]
        total_stu = totals["total_stu_personnel"]
        total_pi = totals["total_pi_cost"]
        actuals = 0.0
        for name, proj in pr.items():
            for r in proj.get("lines", []):
                try:
                    actuals += int(r[1]) * float(r[2]) * float(r[3])   # student personnel
                    actuals += float(r[4]) * float(r[5])               # PI
                    actuals += float(r[8])                             # actual travel
                except (TypeError, ValueError, IndexError):
                    pass
            for tn in proj.get("assigned_tools", []):
                t = next((x for x in data.get("tools", []) if x.get("name") == tn), None)
                if t:
                    _, a = tool_share_costs(data, t, name)
                    actuals += a
        cont_hours = hours_deducted = 0.0
        for p in pr.values():
            b, ded = project_hours_summary(p)
            cont_hours += b
            if b > 0:
                hours_deducted += ded
        rows = flat_invoice_rows(data)
        students_count = len(data.get("student_credits", []))

    unpaid = [r for r in rows if not r["paid"]]
    unpaid_amount = sum(r["inst_amount"] for r in unpaid)
    today = date.today()
    overdue = 0
    for r in unpaid:
        d = parse_date(r["payment_due"]) or parse_date(r["due_date"])
        if d and d < today:
            overdue += 1

    tools_all = data.get("tools", [])
    if scoped and y is not None:
        tools = [t for t in tools_all if tool_in_fy(t, fy)]
    else:
        tools = tools_all
    tools_annual = 0.0
    for t in tools:
        _, a = calc_tool_costs(t)
        tools_annual += a

    return {
        "active_projects":      f"{active_projects}",
        "total_credits":        f"{total_credits:g}",
        "total_budget":         f"${total_budget:,.2f}",
        "total_stu_personnel":  f"${total_stu:,.2f}",
        "total_pi_cost":        f"${total_pi:,.2f}",
        "total_actuals":        f"${actuals:,.2f}",
        "total_cont_hours":     f"{cont_hours:,.0f} hrs",
        "total_hours_deducted": f"{hours_deducted:,.0f} hrs",
        "hours_remaining":      f"{cont_hours - hours_deducted:,.0f} hrs",
        "unpaid_count":         f"{len(unpaid)}",
        "unpaid_amount":        f"${unpaid_amount:,.2f}",
        "overdue_count":        f"{overdue}",
        "tools_count":          f"{len(tools)}",
        "tools_annual":         f"${tools_annual:,.2f}",
        "students_count":       f"{students_count}",
    }