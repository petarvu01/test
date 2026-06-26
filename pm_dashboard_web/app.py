"""PM Dashboard — Streamlit Web App."""
import streamlit as st
import pandas as pd
import numpy as np
import json
import base64
from pathlib import Path
from datetime import date
from helpers import (parse_date, fmt_date, fy_label, date_to_fy,
                     calc_payment_due, calc_renewal_date, calc_tool_costs)
from data import (load_data, save_data, blank_project, blank_line,
                  compute_master_totals, project_contracted_total,
                  wo_project_total, flat_invoice_rows, get_all_notifications,
                  get_fy_options, project_in_fy, tool_in_fy, project_hours_summary,
                  credits_for_fy, project_credits, set_credits_for_fy,
                  count_tool_users, tool_share_costs, gist_configured,
                  fy_funding_rows, fy_carry_in_for, fy_budget_available,
                  fy_hours_available, fy_total_budget_added, fy_actual_spend,
                  fy_hours_spend, fy_lines, flatten_lines, project_primary_fy,
                  project_note_for_fy, set_project_note_for_fy,
                  fy_contracted_budget, fy_contracted_hours, fy_hours_summary,
                  tool_users, tool_split_for, tool_split_amounts,
                  tool_has_custom_split, tool_split_status,
                  compute_kpis, KPI_OPTIONS, KPI_LABELS, DEFAULT_KPIS, kpi_fy_options)

st.set_page_config(page_title="PM Dashboard", page_icon="📁", layout="wide")

# ─── Session state init ──────────────────────────────────────────────────
if "data" not in st.session_state:
    st.session_state.data = load_data()


def D():
    return st.session_state.data


# ─── Accounts / roles ────────────────────────────────────────────────────
# Two accounts: a privileged editor and a read-only viewer. Passwords are read
# from Streamlit secrets ([auth] editor_password / viewer_password) when set,
# otherwise these defaults apply — change them in secrets for real security.
ACCOUNTS = {"Privileged (full access)": "editor", "View only": "viewer"}


def _account_pw(secret_key: str, default: str) -> str:
    try:
        return str(st.secrets["auth"][secret_key])
    except Exception:
        return default


_PASSWORDS = {
    "editor": _account_pw("editor_password", "pm-admin"),
    "viewer": _account_pw("viewer_password", "pm-view"),
}


def can_edit() -> bool:
    """True only for the privileged account — gates every write."""
    return st.session_state.get("role") == "editor"


def save():
    # Hard backstop: a view-only account can never persist changes, even if a
    # control were ever shown to it by mistake.
    if not can_edit():
        st.toast("👁️ View-only account — changes are not saved.", icon="👁️")
        return
    ok = save_data(D())
    if ok is False:
        st.toast("⚠️ Cloud save failed — saved locally only. "
                 "Check your connection or Gist token, then re-save.", icon="⚠️")


def projects_sorted():
    return sorted(D()["project_records"].keys())


def line_value(row, idx, default=0.0):
    """Safely read a numeric line-item value, including older saved rows."""
    try:
        return float(row[idx])
    except Exception:
        return default


def num(v, cast=float):
    """Coerce a (possibly NaN/blank) data_editor cell to a number."""
    return cast(v) if pd.notna(v) else cast(0)


def actual_personnel_cost(row):
    return int(line_value(row, 1)) * line_value(row, 2) * line_value(row, 3)


def actual_pi_cost(row):
    return line_value(row, 4) * line_value(row, 5)


def contracted_travel_cost(row):
    # New field is row[13]. For older saved rows, copy old travel value as a safe migration.
    return line_value(row, 13, line_value(row, 8))


def filtered_projects(search, fy):
    """Yield (name, proj) for projects matching the search text and fiscal year.
    Shared by Master View and Actual vs Budget so the two can't drift apart."""
    s = (search or "").lower()
    for name in sorted(D()["project_records"].keys()):
        proj = D()["project_records"][name]
        if s and s not in name.lower() and s not in proj["code"].lower():
            continue
        if not project_in_fy(proj, fy):
            continue
        yield name, proj


def fy_choices():
    """Concrete FY labels (no 'All') from project dates + any saved student-worker
    tables + the current fiscal year, so a year is always available to pick."""
    years = set()
    for proj in D()["project_records"].values():
        ext = proj.get("extension_date", "")
        eff_end = ext if ext else proj.get("end_date", "")
        for ds in (proj.get("start_date", ""), eff_end):
            d = parse_date(ds)
            if d:
                years.add(date_to_fy(d))
    for label in D().get("student_workers", {}):
        try:
            years.add(int(label.split()[1].split("-")[0]))
        except Exception:
            pass
    years.add(date_to_fy(date.today()))
    return [fy_label(y) for y in sorted(years)]


def _fy_year_of(label):
    """Parse the fiscal-year start-year integer out of any FY label."""
    try:
        return int(str(label).split()[1].split("-")[0])
    except Exception:
        return None


def sw_count_for_fy(fy):
    """Count of student-worker rows saved for a fiscal year, matched by YEAR so
    it works regardless of label format ('FY 2025-26' vs 'FY 2025-26 (FY26)').
    This is the roster row count (header is stored separately, not counted)."""
    sw = D().get("student_workers", {})
    if fy == "All":
        return sum(len((r or {}).get("data", [])) for r in sw.values())
    target = _fy_year_of(fy)
    for key, rec in sw.items():
        if _fy_year_of(key) == target:
            return len((rec or {}).get("data", []))
    return 0


def read_excel(file):
    """Read an uploaded Excel file into a DataFrame, with date columns as strings."""
    df = pd.read_excel(file)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


def df_to_store(df):
    """Convert a DataFrame to a JSON-safe {columns, data} dict for saving."""
    clean = df.astype(object).where(pd.notna(df), None)
    data = []
    for _, row in clean.iterrows():
        out = []
        for v in row:
            if isinstance(v, np.integer):
                out.append(int(v))
            elif isinstance(v, np.floating):
                out.append(float(v))
            elif v is None or isinstance(v, (str, int, float, bool)):
                out.append(v)
            else:
                out.append(str(v))
        data.append(out)
    return {"columns": [str(c) for c in df.columns], "data": data}


def store_to_df(store):
    if not store:
        return pd.DataFrame()
    return pd.DataFrame(store.get("data", []), columns=store.get("columns", []))


def parse_credit_rows(df):
    """Map a credits DataFrame (Name, Student ID, Credits, Project) to records.
    Column matching is case-insensitive. Returns (records, error_message)."""
    cols = {str(c).strip().lower(): c for c in df.columns}

    def find(*needles):
        for n in needles:
            for low, orig in cols.items():
                if n in low:
                    return orig
        return None

    c_name = find("name")
    c_id = find("student id", "id")
    c_cred = find("credit")
    c_proj = find("project")
    missing = [lbl for lbl, c in
               [("Credits", c_cred), ("Project", c_proj)] if c is None]
    if missing:
        return [], (f"Missing required column(s): {', '.join(missing)}. "
                    "The file needs columns: Name, Student ID, Credits, Project.")
    records = []
    for _, row in df.iterrows():
        proj = "" if c_proj is None or pd.isna(row[c_proj]) else str(row[c_proj]).strip()
        if not proj:
            continue  # skip rows with no project
        try:
            creds = float(row[c_cred]) if pd.notna(row[c_cred]) else 0.0
        except (TypeError, ValueError):
            creds = 0.0
        records.append({
            "name": "" if c_name is None or pd.isna(row[c_name]) else str(row[c_name]).strip(),
            "student_id": "" if c_id is None or pd.isna(row[c_id]) else str(row[c_id]).strip(),
            "credits": creds,
            "project": proj,
        })
    return records, None


# ─── Custom CSS ──────────────────────────────────────────────────────────
st.markdown("""<style>
    /* ── Sidebar ────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        background: #0f172a;
    }
    [data-testid="stSidebar"] * {
        color: #94a3b8 !important;
    }

    /* Nav button styling */
    [data-testid="stSidebar"] .stButton > button {
        background: transparent;
        border: none;
        color: #94a3b8 !important;
        width: 100%;
        text-align: left;
        padding: 10px 16px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 500;
        margin: 2px 0;
        transition: all 0.15s;
        border-left: 3px solid transparent;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: #1e293b;
        color: #e2e8f0 !important;
        border-left-color: #334155;
    }
    [data-testid="stSidebar"] .stButton > button:active,
    [data-testid="stSidebar"] .stButton > button:focus {
        background: rgba(52, 211, 153, 0.1);
        color: #34d399 !important;
        border-left-color: #34d399;
    }

    /* KPI cards */
    .stMetric {
        background: white;
        padding: 12px 16px;
        border-radius: 8px;
        border: 1px solid #e2e8f0;
    }
    div[data-testid="stMetricValue"] { font-size: 28px; }

    /* Hide default radio buttons */
    [data-testid="stSidebar"] .stRadio { display: none; }
</style>""", unsafe_allow_html=True)

# ─── Sidebar navigation ─────────────────────────────────────────────────
NAV_ITEMS = [
    ("📊", "Overview"),
    ("📁", "Project View"),
    ("📋", "Master View"),
    ("📈", "Actual vs Budget"),
    ("🎓", "Credits"),
    ("👥", "Student Workers"),
    ("📄", "Invoices / WO"),
    ("🛠️", "Tools"),
    ("🆚", "Results"),
]

if "page" not in st.session_state:
    st.session_state.page = "Overview"


# ─── Login gate (centered, shown before the app shell) ───────────────────
def _login_bg():
    """Find the login background image next to this script (PNG or JPG) and
    return (base64_data, mime_type), or (None, None) if none is found."""
    names = ("CIRATLogo.png", "CIRATLogo.jpg", "CIRATLogo.jpeg",
             "login-bg.png", "login-bg.jpg", "login_bg.png", "login_bg.jpg")
    for name in names:
        p = Path(__file__).parent / name
        if p.exists():
            try:
                data = base64.b64encode(p.read_bytes()).decode("ascii")
                mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                return data, mime
            except Exception:
                return None, None
    return None, None


def _inject_login_style():
    """Full-screen background image for the login page + a translucent,
    still-legible (frosted-glass) login card. Login screen only.

    Uses background-size: contain so the WHOLE image shows the same way on
    any monitor resolution (no resolution-dependent cropping). To change the
    picture, replace the image file in this folder. PNG or JPG both work; the
    name can be CIRATLogo / login-bg / login_bg with a .png or .jpg ending."""
    b64, mime = _login_bg()
    st.markdown(f"""
    <style>
      [data-testid="stAppViewContainer"] {{
          background-color: #0f172a;
          {f'background-image: url("data:{mime};base64,{b64}");' if b64 else
            'background-image: linear-gradient(135deg, #0f172a, #1e293b);'}
          background-size: auto 100%;        /* full height shown; width scales */
          background-position: center;
          background-repeat: no-repeat;
          background-attachment: fixed;
      }}
      [data-testid="stHeader"] {{ background: rgba(0,0,0,0); }}
      /* The login form becomes a translucent frosted card */
      [data-testid="stForm"] {{
          background: rgba(15, 23, 42, 0.45);
          backdrop-filter: blur(10px);
          -webkit-backdrop-filter: blur(10px);
          border: 1px solid rgba(255,255,255,0.18);
          border-radius: 16px;
          padding: 22px 22px 8px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.45);
      }}
      [data-testid="stForm"] label,
      [data-testid="stForm"] p {{ color: #e2e8f0 !important; }}
    </style>
    """, unsafe_allow_html=True)


def _login_gate():
    """Block the whole app behind a centered sign-in screen until a valid
    account + password is entered. Returns once signed in."""
    if st.session_state.get("role"):
        return
    _inject_login_style()
    # Center a compact login card; nothing else renders until signed in.
    st.markdown("<div style='height: 8vh;'></div>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        with st.form("login_form"):
            acct = st.selectbox("Account", list(ACCOUNTS.keys()), key="login_account")
            pw = st.text_input("Password", type="password", key="login_pw")
            ok = st.form_submit_button("Sign in", use_container_width=True,
                                       type="primary")
        if ok:
            want = ACCOUNTS[acct]
            if pw == _PASSWORDS[want]:
                st.session_state["role"] = want
                st.session_state.pop("login_pw", None)
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


_login_gate()

with st.sidebar:
    # Logo
    st.markdown("""
    <div style="padding: 8px 0 16px;">
        <div style="font-size: 14px; font-weight: 600; color: #34d399 !important;
                    letter-spacing: 0.03em;">PM DASHBOARD</div>
        <div style="font-size: 11px; color: #475569 !important; margin-top: 2px;">
            Project Intelligence</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<hr style="border: none; border-top: 1px solid #1e293b; margin: 0 0 12px;">',
                unsafe_allow_html=True)

    st.markdown('<div style="font-size: 10px; font-weight: 500; color: #334155 !important; '
                'letter-spacing: 0.08em; padding: 0 4px; margin-bottom: 6px;">MAIN</div>',
                unsafe_allow_html=True)

    for icon, label in NAV_ITEMS:
        is_active = st.session_state.page == label
        # Active state styling via markdown + button combo
        if is_active:
            st.markdown(
                f'<div style="background: rgba(52,211,153,0.1); border-radius: 8px; '
                f'border-left: 3px solid #34d399; padding: 10px 16px; margin: 2px 0; '
                f'font-size: 14px; font-weight: 500; color: #34d399 !important; '
                f'cursor: default;">{icon}  {label}</div>',
                unsafe_allow_html=True)
        else:
            if st.button(f"{icon}  {label}", key=f"nav_{label}",
                         use_container_width=True):
                st.session_state.page = label
                st.rerun()

    # Footer
    st.markdown('<hr style="border: none; border-top: 1px solid #1e293b; '
                'margin: 16px 0 12px;">', unsafe_allow_html=True)

    # ── Signed-in account + sign out (login happens on the centered gate) ──
    role = st.session_state.get("role")
    badge = "🔑 Privileged" if role == "editor" else "👁️ View only"
    st.markdown(f"""
    <div style="display: flex; align-items: center; gap: 8px; padding: 4px;">
        <div style="width: 28px; height: 28px; border-radius: 50%;
                    background: rgba(52,211,153,0.15); display: flex;
                    align-items: center; justify-content: center;
                    font-size: 11px; font-weight: 500; color: #34d399 !important;">PM</div>
        <span style="font-size: 12px; color: #475569 !important;">{badge}</span>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Sign out", use_container_width=True, key="sign_out"):
        st.session_state.pop("role", None)
        st.session_state.pop("login_pw", None)
        st.rerun()

    # Storage status + manual backup
    if gist_configured():
        st.caption("💾 Cloud storage: on (Gist)")
    else:
        st.caption("⚠️ Local only — data resets on redeploy")
    st.download_button(
        "⬇️ Download backup (JSON)",
        json.dumps(st.session_state.data, indent=2),
        "dashboard_progress.json", "application/json",
        use_container_width=True, key="sidebar_backup",
    )

page = st.session_state.page


# ═════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ═════════════════════════════════════════════════════════════════════════
if page == "Overview":
    st.title("📊 Overview")

    # ── Configurable KPI board (shared — saved in the data file) ─────────
    selected_kpis = [k for k in D().get("overview_kpis", DEFAULT_KPIS) if k in KPI_LABELS]
    if not selected_kpis:
        selected_kpis = list(DEFAULT_KPIS)

    # Fiscal-year filter (a per-session view setting — not saved/shared).
    fy_opts = kpi_fy_options(D())
    fcol, _ = st.columns([1, 3])
    overview_fy = fcol.selectbox(
        "Fiscal year", fy_opts, key="overview_fy",
        help="Scope every KPI below to one fiscal year — including the Tools KPIs, "
             "which are scoped by each tool's active date range. 'All' shows "
             "whole-dataset totals across every fiscal year.",
    )
    if overview_fy != "All":
        st.caption(f"Showing KPIs for **{overview_fy}**. Active Projects and Hours "
                   "Utilization cover projects running in this fiscal year; Tools "
                   "cover subscriptions active in it.")
    kpi_values = compute_kpis(D(), fy=overview_fy)

    for start in range(0, len(selected_kpis), 4):
        chunk = selected_kpis[start:start + 4]
        cols = st.columns(4)
        for col, key in zip(cols, chunk):
            col.metric(KPI_LABELS[key], kpi_values.get(key, "—"))

    with st.expander("⚙️ Customize KPIs"):
        st.caption("Pick which metrics show on the Overview. The selection is saved "
                   "with the shared data, so everyone sees the same board.")
        all_labels = [label for _, label in KPI_OPTIONS]
        label_to_key = {label: key for key, label in KPI_OPTIONS}
        current_labels = [KPI_LABELS[k] for k in selected_kpis]
        picked = st.multiselect("Visible KPIs (shown in this order)", all_labels,
                                default=current_labels, key="kpi_picker")
        kc1, kc2 = st.columns([1, 1])
        if can_edit() and kc1.button("💾 Save KPIs"):
            if picked:
                D()["overview_kpis"] = [label_to_key[l] for l in picked]
                save()
                st.toast("KPI board saved")
                st.rerun()
            else:
                st.error("Pick at least one KPI.")
        if can_edit() and kc2.button("↩️ Reset to default"):
            D()["overview_kpis"] = list(DEFAULT_KPIS)
            save()
            st.rerun()

    col_left, col_right = st.columns([2, 3])

    with col_left:
        if overview_fy == "All":
            st.subheader("Hours Utilization")
            ov_year = None
        else:
            st.subheader(f"Hours Utilization — {overview_fy}")
            try:
                ov_year = int(overview_fy.split()[1].split("-")[0])
            except (IndexError, ValueError):
                ov_year = None
        items = []
        for name, proj in sorted(D()["project_records"].items()):
            if ov_year is None:
                budget, used = project_hours_summary(proj)
            else:
                # Only projects actually running in this FY (by their dates),
                # then this FY's available (carry-in + contracted) vs spent.
                if not project_in_fy(proj, overview_fy):
                    continue
                budget, used, _ = fy_hours_summary(proj, ov_year)
            if budget <= 0:
                continue
            pct = used / budget * 100
            items.append({"Project": name, "Used": used, "Budget": budget, "% Used": pct})
        if items:
            for item in items:
                pct = item["% Used"]
                color = "🔴" if pct > 100 else ("🟡" if pct > 80 else "🟢")
                st.markdown(f"**{item['Project']}** {color}")
                st.progress(min(pct / 100, 1.0))
                st.caption(f"{item['Used']:.0f} / {item['Budget']:.0f} hrs ({pct:.0f}%)")
        elif overview_fy == "All":
            st.info("No projects with contracted hours set.")
        else:
            st.info(f"No projects with contracted hours in {overview_fy}.")

    with col_right:
        notifs = get_all_notifications(D())

        # A stable signature per alert lets us remember which ones were cleared.
        def _alert_sig(note):
            return "‖".join(str(x) for x in note)

        live_sigs = {_alert_sig(n) for n in notifs}
        dismissed = D().get("dismissed_alerts", [])
        # Auto-forget dismissals whose underlying reason no longer exists, so a
        # resolved alert is removed (and re-alerts cleanly if it ever recurs).
        pruned = [s for s in dismissed if s in live_sigs]
        if pruned != dismissed:
            D()["dismissed_alerts"] = pruned
            dismissed = pruned
            save()

        visible = [n for n in notifs if _alert_sig(n) not in dismissed]
        n_alerts = len(visible)
        title = f"⚠️ Alerts ({n_alerts})" if n_alerts else "✅ Alerts (0)"
        # An expander is a dropdown that opens client-side — no rerun, so the
        # page keeps its scroll position when the user opens it.
        with st.expander(title, expanded=st.session_state.get("alerts_open", False)):
            if visible:
                st.caption("Press Clear to dismiss an alert. It returns "
                           "automatically if the condition changes or recurs.")
                for note in visible:
                    icon, cat, proj, details = note
                    a1, a2 = st.columns([0.86, 0.14])
                    a1.markdown(f"{icon} **{cat}** — {proj}  \n{details}")
                    if can_edit() and a2.button("Clear", key=f"alert_clr_{_alert_sig(note)}"):
                        D().setdefault("dismissed_alerts", []).append(_alert_sig(note))
                        st.session_state["alerts_open"] = True
                        save()
                        st.rerun()
            else:
                st.success("No alerts — all clear!")


# ═════════════════════════════════════════════════════════════════════════
# PROJECT VIEW
# ═════════════════════════════════════════════════════════════════════════
elif page == "Project View":
    st.title("📁 Project View")
    pr = D()["project_records"]
    projects = projects_sorted()

    # If a project was just created, land on it (must be set BEFORE the
    # selectbox is built so the widget picks it up).
    _new = st.session_state.pop("_new_project_name", None)
    if _new and _new in projects:
        st.session_state["proj_select"] = _new
    # Drop a stale selection (e.g. after a delete) so the box doesn't error.
    if st.session_state.get("proj_select") not in projects:
        st.session_state.pop("proj_select", None)

    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    with col1:
        active = st.selectbox("Active Project", projects, key="proj_select")
    with col2:
        if can_edit() and st.button("➕ Add Project"):
            st.session_state.show_add_project = True
    with col3:
        if can_edit() and st.button("✏️ Edit Project"):
            st.session_state.show_edit_project = True
    with col4:
        if can_edit() and st.button("🗑️ Remove Project", type="secondary"):
            if len(projects) > 1:
                st.session_state.confirm_del_proj = True
            else:
                st.error("Must keep at least one project.")

    # Confirm-gate for project deletion (prevents one-click data loss)
    if st.session_state.get("confirm_del_proj"):
        st.warning(f"Delete **{active}** and its student credits? This can't be undone.")
        dc1, dc2, _ = st.columns([1, 1, 4])
        if can_edit() and dc1.button("Yes, delete", type="primary"):
            del pr[active]
            scbf = D().setdefault("student_credits_by_fy", {})
            for k in list(scbf.keys()):
                scbf[k] = [s for s in scbf[k] if s.get("project") != active]
            D()["student_credits"] = [s for recs in scbf.values() for s in recs]
            st.session_state.confirm_del_proj = False
            save()
            st.toast(f"Removed {active}")
            st.rerun()
        if can_edit() and dc2.button("Cancel"):
            st.session_state.confirm_del_proj = False
            st.rerun()

    # Add project dialog
    if st.session_state.get("show_add_project"):
        with st.expander("New Project", expanded=True):
            nc, nn = st.columns(2)
            new_code = nc.text_input("Project Code")
            new_name = nn.text_input("Project Name")
            ns, ne = st.columns(2)
            new_start = ns.date_input("Start Date", value=None, key="new_start")
            new_end = ne.date_input("End Date", value=None, key="new_end")
            new_nb = st.checkbox("No Budget")
            if can_edit() and st.button("Create Project"):
                if new_code and new_name:
                    if new_name not in pr:
                        p = blank_project(new_code, new_name, new_nb)
                        p["start_date"] = str(new_start) if new_start else ""
                        p["end_date"] = str(new_end) if new_end else ""
                        pr[new_name] = p
                        save()
                        st.session_state.show_add_project = False
                        st.session_state["_new_project_name"] = new_name
                        st.toast(f"Created {new_name}")
                        st.rerun()
                    else:
                        st.error(f"'{new_name}' already exists.")
                else:
                    st.error("Both code and name are required.")

    # Edit project dialog (rename + change code)
    if st.session_state.get("show_edit_project") and active in pr:
        with st.expander(f"✏️ Edit '{active}'", expanded=True):
            ec, en = st.columns(2)
            edit_code = ec.text_input("Project Code",
                                      value=pr[active].get("code", ""),
                                      key=f"edit_code_{active}")
            edit_name = en.text_input("Project Name", value=active,
                                      key=f"edit_name_{active}")
            eb1, eb2, _ = st.columns([1, 1, 3])
            if can_edit() and eb1.button("💾 Save Changes", type="primary", key=f"edit_save_{active}"):
                new_code = edit_code.strip()
                new_name = edit_name.strip()
                if not new_code or not new_name:
                    st.error("Both code and name are required.")
                elif new_name != active and new_name in pr:
                    st.error(f"'{new_name}' already exists.")
                else:
                    pr[active]["code"] = new_code
                    if new_name != active:
                        # Move the record, then repoint every reference that
                        # keys off the project name.
                        pr[new_name] = pr.pop(active)
                        for rec in D().get("invoices", []):
                            if rec.get("project") == active:
                                rec["project"] = new_name
                        scbf = D().setdefault("student_credits_by_fy", {})
                        for recs in scbf.values():
                            for s in recs:
                                if s.get("project") == active:
                                    s["project"] = new_name
                        D()["student_credits"] = [
                            s for recs in scbf.values() for s in recs]
                        for t in D().get("tools", []):
                            sa = t.get("split_amounts")
                            if isinstance(sa, dict) and active in sa:
                                sa[new_name] = sa.pop(active)
                        # Land on the renamed project after rerun.
                        st.session_state["_new_project_name"] = new_name
                        st.session_state.pop(f"view_fy_{active}", None)
                    st.session_state.show_edit_project = False
                    save()
                    st.toast(f"Saved {new_name}")
                    st.rerun()
            if can_edit() and eb2.button("Cancel", key=f"edit_cancel_{active}"):
                st.session_state.show_edit_project = False
                st.rerun()

    if active not in pr:
        st.stop()

    proj = pr[active]

    # ── Dates ────────────────────────────────────────────────────────
    st.subheader("Project Dates")
    dc1, dc2, dc3, dc4 = st.columns([2, 2, 2, 1])
    sd = dc1.date_input("Start Date", value=parse_date(proj.get("start_date", "")),
                        key=f"proj_start_{active}", format="YYYY-MM-DD")
    ed = dc2.date_input("End Date", value=parse_date(proj.get("end_date", "")),
                        key=f"proj_end_{active}", format="YYYY-MM-DD")
    ext = dc3.date_input("Extension", value=parse_date(proj.get("extension_date", "")),
                         key=f"proj_ext_{active}", format="YYYY-MM-DD")
    no_budget = dc4.checkbox("No Budget", value=proj.get("has_budget", False),
                             key=f"nb_chk_{active}")
    btn_c1, btn_c2, _ = st.columns([1, 1, 3])
    if can_edit() and btn_c1.button("💾 Save Dates", key=f"save_dates_{active}"):
        proj["start_date"] = str(sd) if sd else ""
        proj["end_date"] = str(ed) if ed else ""
        proj["extension_date"] = str(ext) if ext else ""
        proj["has_budget"] = no_budget
        st.session_state.pop(f"view_fy_{active}", None)
        save()
        st.toast("Dates saved")
        st.rerun()
    if proj.get("extension_date"):
        if can_edit() and btn_c2.button("🗑️ Clear Extension", key=f"clear_ext_{active}"):
            proj["extension_date"] = ""
            # reset the date widget so it shows empty again
            st.session_state.pop(f"proj_ext_{active}", None)
            st.session_state.pop(f"view_fy_{active}", None)
            save()
            st.toast("Extension date removed")
            st.rerun()

    # ── Status (auto-Finished when past-dated; Renewed when extended) ─
    status_options = ["Active", "On Pause", "Finished"]
    badge = {"Active": "🟢", "On Pause": "🟡", "Finished": "⚪"}
    stored_status = proj.get("status", "Active")
    if stored_status not in status_options:
        stored_status = "Active"
    saved_end = parse_date(proj.get("end_date", ""))
    saved_ext = parse_date(proj.get("extension_date", ""))
    latest_date = saved_ext or saved_end
    is_renewed = bool(saved_ext)
    auto_finished = bool(latest_date and latest_date < date.today())
    effective_status = "Finished" if auto_finished else stored_status

    stc1, stc2 = st.columns([2, 3])
    with stc1:
        new_status = st.selectbox(
            "Project Status", status_options,
            index=status_options.index(stored_status),
            key=f"proj_status_{active}", disabled=auto_finished,
            help="A project whose end/extension date is in the past is "
                 "automatically marked Finished.",
        )
    if not auto_finished and new_status != stored_status:
        proj["status"] = new_status
        save()
        st.toast(f"Status set to {new_status}")
    badge_line = f"### {badge.get(effective_status, '')} {effective_status}"
    if is_renewed:
        badge_line += "  ·  🔄 Renewed"
    stc2.markdown(badge_line)
    if auto_finished:
        stc2.caption("Auto-set to Finished — the date has passed.")

    # ── Fiscal-year filter (scopes budget, line items & hours below) ──
    st.subheader("Fiscal Year")
    vfy_opts = [o for o in get_fy_options(D()) if o != "All"]
    for k in (list(proj.get("lines_by_fy", {}).keys())
              + list(proj.get("contracted_hours_by_fy", {}).keys())):
        vfy_opts.append(fy_label(int(k)))
    vfy_this = fy_label(date_to_fy(date.today()))
    vfy_opts.append(vfy_this)
    vfy_opts.append(fy_label(project_primary_fy(proj)))
    # Default: the project's START fiscal year — unless an extension date falls
    # in a later FY, in which case open on the extension's FY.
    start_d = parse_date(proj.get("start_date", ""))
    start_fy = date_to_fy(start_d) if start_d else project_primary_fy(proj)
    ext_d = parse_date(proj.get("extension_date", ""))
    default_year = start_fy
    if ext_d and date_to_fy(ext_d) > start_fy:
        default_year = date_to_fy(ext_d)
    # make sure the chosen year is selectable
    if fy_label(default_year) not in vfy_opts:
        vfy_opts.append(fy_label(default_year))
    vfy_opts = sorted(set(vfy_opts), key=lambda lab: int(lab.split()[1].split("-")[0]))
    default_lbl = fy_label(default_year)
    vfy_idx = vfy_opts.index(default_lbl) if default_lbl in vfy_opts else 0
    view_fy_label = st.selectbox(
        "View / edit fiscal year", vfy_opts, index=vfy_idx, key=f"view_fy_{active}",
        help="Budget, line items and hours below are for this fiscal year. "
             "Unspent budget ($) and hours carry into the next FY after May 31.",
    )
    view_year = int(view_fy_label.split()[1].split("-")[0])
    view_key = str(view_year)

    # Per-FY budget summary (derived; carries over automatically at FY end).
    # Carried-in is masked for fiscal years the project isn't running in, so an
    # ended project doesn't appear to have a balance in a future FY.
    project_running = project_in_fy(proj, view_fy_label)
    if not proj.get("has_budget", False):
        cin_d = fy_carry_in_for(proj, view_year)[0] if project_running else 0.0
        contr_d = fy_contracted_budget(proj, view_year)
        spent_d = fy_actual_spend(proj, view_year)
        avail_d = cin_d + contr_d
        bm1, bm2, bm3, bm4 = st.columns(4)
        bm1.metric("Carried in $", f"${cin_d:,.0f}")
        bm2.metric("Contracted $ (budget)", f"${contr_d:,.0f}")
        bm3.metric("Spent $ (actual)", f"${spent_d:,.0f}")
        bm4.metric("Remaining $", f"${avail_d - spent_d:,.0f}")
        if project_running:
            st.caption(
                f"Available this FY = carried-in + contracted = ${avail_d:,.0f}. "
                f"Leftover (${avail_d - spent_d:,.0f}) carries into "
                f"{fy_label(view_year + 1)} automatically after May 31."
            )
        else:
            st.caption(
                f"ℹ️ This project isn't running in {view_fy_label}, so any carried-over "
                "balance is hidden here. Carry-over shows only in fiscal years the project "
                "is active. Add an extension date that reaches into this FY to carry the "
                "balance forward."
            )


    # ── Line items (editable grid) ─────────────────────────────────────
    st.subheader(f"Line Items — {view_fy_label}")

    # Line items belong to the fiscal year chosen in the Fiscal Year filter above.
    lbf = proj.setdefault("lines_by_fy", {})
    li_sel = view_fy_label
    li_year = view_year
    li_key = view_key
    fy_rows_for_edit = lbf.get(li_key, [])
    # Masked hours view: carried-in hours are hidden when the project isn't
    # running in this FY (consistent with the budget summary above).
    _h_cin = fy_carry_in_for(proj, view_year)[1] if project_running else 0.0
    fy_h_avail = _h_cin + fy_contracted_hours(proj, view_year)
    fy_h_spent = fy_hours_spend(proj, view_year)
    fy_h_remaining = fy_h_avail - fy_h_spent
    tracks_hours = fy_h_avail > 0

    grid_caption = (f"Editing **{li_sel}** costs (change the Fiscal Year filter above to edit "
                    "another year). Enter contracted amounts (the budget) and actual costs. "
                    "Use the **＋** at the bottom to add a line, or tick a row and press "
                    "Delete to remove it — then click Save Line Items.")
    if tracks_hours:
        grid_caption += (" Hours deducted from the Contracted Hours budget are computed "
                         "automatically: **(Students × Stu Hours) + PI Hours** per line.")
    st.caption(grid_caption)

    edit_df = pd.DataFrame([{
        "Line Item": row[0],
        "Students": int(line_value(row, 1)),
        "Stu Rate": line_value(row, 2),
        "Stu Hours": line_value(row, 3),
        "PI Rate": line_value(row, 4),
        "PI Hours": line_value(row, 5),
        "Actual Travel": line_value(row, 8),
        "Cont. Personnel": line_value(row, 9),
        "Cont. PI": line_value(row, 10),
        "Cont. Indirect": line_value(row, 11),
        "Cont. Fringe": line_value(row, 12),
        "Cont. Travel": contracted_travel_cost(row),
    } for row in (fy_rows_for_edit or [blank_line()])])

    money = st.column_config.NumberColumn(min_value=0.0, step=1.0, format="$%.2f")
    hours = st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.1f")
    edited = st.data_editor(
        edit_df, use_container_width=True, hide_index=True,
        num_rows="dynamic", key=f"lines_editor_{active}_{li_key}",
        disabled=not can_edit(),
        column_config={
            "Line Item": st.column_config.TextColumn(required=True, width="medium"),
            "Students": st.column_config.NumberColumn(min_value=0, step=1),
            "Stu Rate": money, "Stu Hours": hours,
            "PI Rate": money, "PI Hours": hours,
            "Actual Travel": money, "Cont. Personnel": money, "Cont. PI": money,
            "Cont. Indirect": money, "Cont. Fringe": money, "Cont. Travel": money,
        },
    )

    if can_edit() and st.button("💾 Save Line Items", key=f"save_lines_{active}_{li_key}"):
        new_lines = [[
            (r["Line Item"] or "Untitled"),
            num(r["Students"], int), num(r["Stu Rate"]), num(r["Stu Hours"]),
            num(r["PI Rate"]), num(r["PI Hours"]),
            0.0, 0.0, num(r["Actual Travel"]),
            num(r["Cont. Personnel"]), num(r["Cont. PI"]),
            num(r["Cont. Indirect"]), num(r["Cont. Fringe"]), num(r["Cont. Travel"]),
        ] for _, r in edited.iterrows()]
        lbf[li_key] = new_lines or [blank_line()]
        proj["lines"] = flatten_lines(proj) or [blank_line()]
        save()
        st.toast(f"{li_sel} line items saved")
        st.rerun()

    # Read-only computed recap (reflects the last saved state of THIS FY)
    recap = []
    for row in lbf.get(li_key, []):
        s, sr, sh = int(line_value(row, 1)), line_value(row, 2), line_value(row, 3)
        pr2, ph = line_value(row, 4), line_value(row, 5)
        cont = (line_value(row, 9) + line_value(row, 10) + line_value(row, 11)
                + line_value(row, 12) + contracted_travel_cost(row))
        item = {
            "Line Item": row[0],
            "Stu Cost": f"${s * sr * sh:,.2f}",
            "PI Cost": f"${pr2 * ph:,.2f}",
            "Contracted Total": f"${cont:,.2f}",
        }
        if tracks_hours:
            student_hrs_total = s * sh
            line_deducted = student_hrs_total + ph
            item["Stu Hrs (× students)"] = f"{student_hrs_total:.1f}"
            item["PI Hrs"] = f"{ph:.1f}"
            item["Hours Deducted"] = f"{line_deducted:.1f}"
        recap.append(item)
    if recap:
        if tracks_hours:
            st.caption("Computed values (from last save) — Hours Deducted per line "
                       "= (Students × Stu Hours) + PI Hours")
        else:
            st.caption("Computed costs (from last save)")
        st.dataframe(pd.DataFrame(recap), use_container_width=True, hide_index=True)

    # ── Contracted Hours for this FY (% used / hours left) ────────────
    st.subheader(f"Contracted Hours — {view_fy_label}")
    chf = proj.setdefault("contracted_hours_by_fy", {})
    cur_ch = float(chf.get(view_key, 0) or 0)
    cin_h = fy_carry_in_for(proj, view_year)[1] if project_running else 0.0
    hc1, hc2 = st.columns([2, 2])
    with hc1:
        new_ch = st.number_input(
            f"Annual contracted hours for {view_fy_label}",
            value=cur_ch, min_value=0.0, step=1.0, format="%.1f",
            key=f"cont_hours_{active}_{view_key}",
            help="Hours allocated for this fiscal year. Unspent hours carry "
                 "into the next FY automatically after May 31.",
        )
    with hc2:
        if cin_h:
            st.caption(f"+ {cin_h:,.1f} hrs carried in from the prior FY.")
    if can_edit() and st.button("💾 Save Contracted Hours", key=f"save_ch_{active}_{view_key}"):
        if new_ch > 0:
            chf[view_key] = float(new_ch)
        else:
            chf.pop(view_key, None)
        save()
        st.toast("Contracted Hours saved")
        st.rerun()

    avail_h, spent_h, remaining_h = fy_h_avail, fy_h_spent, fy_h_remaining
    if avail_h > 0:
        pct = (spent_h / avail_h * 100) if avail_h > 0 else 0
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Available", f"{avail_h:.0f} hrs")
        h2.metric("Deducted", f"{spent_h:.0f} hrs")
        h3.metric("Remaining", f"{remaining_h:.0f} hrs")
        h4.metric("% Used", f"{pct:.1f}%")
        st.caption("Hours Deducted = (Students × Stu Hours) + PI Hours, summed "
                   f"over {view_fy_label} line items. Available = carried-in + "
                   "contracted for this FY.")
    else:
        st.caption("No contracted hours set for this fiscal year — hours tracking "
                   "is off. Enter a value above to turn it on.")

    # Notes — kept separately for each fiscal year (uses the Fiscal Year filter above)
    st.subheader("Notes")
    st.caption(f"Notes for **{active}** — {view_fy_label}. Each fiscal year keeps "
               "its own notes; change the Fiscal Year filter above to write notes "
               "for another year.")
    cur_note = project_note_for_fy(proj, view_year)
    notes = st.text_area("Project notes", value=cur_note,
                         key=f"proj_notes_{active}_{view_key}")
    # Auto-save to THIS fiscal year so switching project or FY never loses notes.
    if notes != cur_note:
        set_project_note_for_fy(proj, view_year, notes)
        save()
    if can_edit() and st.button("💾 Save Notes", key=f"save_notes_{active}_{view_key}"):
        set_project_note_for_fy(proj, view_year, notes)
        save()
        st.toast(f"Notes saved for {view_fy_label}")

    # Tool assignments
    st.subheader("Assigned Tools")
    tool_names = [t.get("name", "") for t in D()["tools"] if t.get("name")]
    assigned = proj.get("assigned_tools", [])
    tc1, tc2 = st.columns([3, 1])
    with tc1:
        sel_tool = st.selectbox("Assign tool", [""] + [t for t in tool_names if t not in assigned])
    with tc2:
        if can_edit() and st.button("Assign") and sel_tool:
            assigned.append(sel_tool)
            proj["assigned_tools"] = assigned
            save()
            st.rerun()
    if assigned:
        tool_data = []
        total_m_share = total_a_share = 0.0
        for tn in assigned:
            t = next((t for t in D()["tools"] if t.get("name") == tn), None)
            if t:
                full_mc, full_ac = calc_tool_costs(t)
                share_mc, share_ac = tool_share_costs(D(), t, active)
                n_users = count_tool_users(D(), tn)
                cycle = t.get("billing_cycle", "Monthly")
                split_label = (f"{n_users} project{'s' if n_users != 1 else ''}"
                               + (" (custom $)" if tool_has_custom_split(D(), t) else ""))
                total_m_share += share_mc; total_a_share += share_ac
                tool_data.append({
                    "Tool": tn, "Vendor": t.get("vendor", ""),
                    "Full Annual": f"${full_ac:,.2f}",
                    "Split among": split_label,
                    f"Share / {cycle.lower()}": f"${tool_split_for(D(), t, active):,.2f}",
                    "Your share / mo": f"${share_mc:,.2f}",
                    "Your share / yr": f"${share_ac:,.2f}",
                })
            else:
                tool_data.append({"Tool": tn, "Vendor": "—",
                                  "Full Annual": "—", "Split among": "—",
                                  "Your share / mo": "—", "Your share / yr": "—"})
        st.dataframe(pd.DataFrame(tool_data), use_container_width=True, hide_index=True)
        st.markdown(f"**This project's share: ${total_m_share:,.2f}/mo · "
                    f"${total_a_share:,.2f}/yr**")
        st.caption("Shared tools split equally by default; enter custom $ amounts below "
                   "or on the Tools tab.")

        # ── Inline $ split editor ──────────────────────────────────────
        shared_tools = [tn for tn in assigned if count_tool_users(D(), tn) >= 2]
        if shared_tools:
            with st.expander("✏️ Edit cost split ($)"):
                pick = st.selectbox("Tool", shared_tools, key=f"pv_split_tool_{active}")
                t_sel = next((x for x in D()["tools"] if x.get("name") == pick), None)
                if t_sel:
                    users = tool_users(D(), pick)
                    cycle = t_sel.get("billing_cycle", "Monthly")
                    try:
                        cost = float(t_sel.get("cost", 0))
                    except (TypeError, ValueError):
                        cost = 0.0
                    st.caption(f"{cycle} cost: **${cost:,.2f}** — enter each project's "
                               f"dollar amount per {cycle.lower()} period.")
                    sa = t_sel.get("split_amounts") or {}
                    equal_amt = round(cost / len(users), 2) if users else 0.0
                    amt_inputs = {}
                    cols = st.columns(min(len(users), 4))
                    for j, p in enumerate(users):
                        default_amt = float(sa.get(p, equal_amt) or 0.0)
                        with cols[j % len(cols)]:
                            amt_inputs[p] = st.number_input(
                                f"{p} ($)", min_value=0.0, value=default_amt,
                                step=5.0, format="%.2f",
                                key=f"pv_split_{active}_{pick}_{p}",
                            )
                    entered = sum(amt_inputs.values())
                    diff = entered - cost
                    if abs(diff) > 0.01:
                        word = "unassigned" if diff < 0 else "over-assigned"
                        st.warning(f"Entered ${entered:,.2f} of ${cost:,.2f} — "
                                   f"${abs(diff):,.2f} {word}.")
                    else:
                        st.caption(f"✓ Matches the {cycle.lower()} cost (${cost:,.2f})")
                    pc1, pc2 = st.columns(2)
                    if can_edit() and pc1.button("💾 Save Split", key=f"pv_save_split_{active}_{pick}"):
                        t_sel["split_amounts"] = {p: float(v) for p, v in amt_inputs.items()}
                        save()
                        st.toast("Split saved")
                        st.rerun()
                    if can_edit() and pc2.button("↩️ Reset to equal split", key=f"pv_reset_split_{active}_{pick}"):
                        t_sel["split_amounts"] = {}
                        save()
                        st.rerun()

        rem_tool = st.selectbox("Remove tool", assigned, key="rem_tool")
        if can_edit() and st.button("Remove Tool"):
            assigned.remove(rem_tool)
            save()
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════
# MASTER VIEW
# ═════════════════════════════════════════════════════════════════════════
elif page == "Master View":
    st.title("📋 Master Summary")
    pr = D()["project_records"]

    fc1, fc2 = st.columns([2, 1])
    search = fc1.text_input("🔍 Search", key="master_search")
    fy = fc2.selectbox("Fiscal Year", get_fy_options(D()), key="master_fy")

    rows = []
    gt_b = gt_s = gt_p = 0.0; gt_c = 0
    gt_ch = 0.0
    # Selected fiscal year (None = "All")
    sel_year = None
    if fy != "All":
        try:
            sel_year = int(fy.split()[1].split("-")[0])
        except Exception:
            sel_year = None
    st.caption(
        f"Budget shown is the **{fy}** budget = carried-in + contracted line items."
        if sel_year is not None else
        "Budget shown is total contracted budget across all fiscal years. "
        "Pick a fiscal year to see that year's budget and spend."
    )
    for name, proj in filtered_projects(search, fy):
        is_red = proj.get("has_budget", False)
        if sel_year is not None:
            budget = fy_budget_available(proj, sel_year)
            scope_lines = fy_lines(proj, sel_year)
        else:
            budget = fy_total_budget_added(proj)
            scope_lines = proj["lines"]
        stu = sum(actual_personnel_cost(row) for row in scope_lines)
        pi = sum(actual_pi_cost(row) for row in scope_lines)
        ch_budget, _ = project_hours_summary(proj)
        credits = project_credits(D(), fy, name)
        ext = proj.get("extension_date", "")
        ed = proj.get("end_date", "")
        end_disp = fmt_date(ext if ext else ed) + (" ★" if ext else "")
        rows.append({
            "Code": proj["code"], "Project": name,
            "Start": fmt_date(proj.get("start_date", "")),
            "End": end_disp,
            "Budget": f"${budget:,.2f}" if not is_red else "—",
            "Cont. Hours": f"{ch_budget:.0f}" if ch_budget > 0 else "—",
            "Personnel": f"${stu:,.2f}", "PI Cost": f"${pi:,.2f}",
            "Credits": credits,
        })
        if not is_red:
            gt_b += budget
        gt_ch += ch_budget
        gt_s += stu; gt_p += pi; gt_c += credits

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        gt_line = (f"**Grand Total — Budget: ${gt_b:,.2f} | "
                   f"Personnel: ${gt_s:,.2f} | PI: ${gt_p:,.2f} | Credits: {gt_c}**")
        if gt_ch > 0:
            gt_line += f"  \n**Cont. Hours total: {gt_ch:.0f} hrs**"
        st.markdown(gt_line)

        # CSV download
        csv = pd.DataFrame(rows).to_csv(index=False)
        st.download_button("⬇️ Download CSV", csv, "master_summary.csv", "text/csv")


# ═════════════════════════════════════════════════════════════════════════
# ACTUAL vs BUDGET
# ═════════════════════════════════════════════════════════════════════════
elif page == "Actual vs Budget":
    st.title("📊 Actual vs Budget")
    pr = D()["project_records"]

    fc1, fc2 = st.columns([2, 1])
    search = fc1.text_input("🔍 Search", key="avb_search")
    fy = fc2.selectbox("Fiscal Year", get_fy_options(D()), key="avb_fy")

    rows = []
    sel_year = None
    if fy != "All":
        try:
            sel_year = int(fy.split()[1].split("-")[0])
        except Exception:
            sel_year = None
    st.caption(
        f"Budget = the **{fy}** budget (carried-in + contracted). Actuals = that year's "
        "line-item spend (personnel + PI + actual travel). Tool costs are tracked "
        "separately in Tools and aren't part of this per-FY comparison."
        if sel_year is not None else
        "Showing all fiscal years combined: Budget = total contracted, Actuals = "
        "all line-item spend. Pick a fiscal year to compare a single year."
    )
    for name, proj in filtered_projects(search, fy):
        is_red = proj.get("has_budget", False)

        if sel_year is not None:
            budget = fy_budget_available(proj, sel_year)
            carried_in_d = fy_carry_in_for(proj, sel_year)[0]
            added_d = fy_contracted_budget(proj, sel_year)
            scope_lines = fy_lines(proj, sel_year)
        else:
            budget = fy_total_budget_added(proj)
            carried_in_d = 0.0
            added_d = budget
            scope_lines = proj["lines"]

        pers = pi = trv = 0.0
        for row in scope_lines:
            pers += actual_personnel_cost(row)
            pi += actual_pi_cost(row)
            trv += line_value(row, 8)

        actuals = pers + pi + trv
        if is_red:
            bstr, var_s, pct_s = "—", f"(${actuals:,.2f})", "—"
        else:
            pct = (actuals / budget * 100) if budget > 0 else 0
            var = budget - actuals
            bstr = f"${budget:,.2f}"
            var_s = f"(${abs(var):,.2f})" if var < 0 else f"${var:,.2f}"
            pct_s = f"{pct:.1f}%"
        ext = proj.get("extension_date", "")
        ed = proj.get("end_date", "")
        ch_budget, _ = project_hours_summary(proj)
        rows.append({
            "Code": proj["code"], "Project": name,
            "Start": fmt_date(proj.get("start_date", "")),
            "End": fmt_date(ext if ext else ed) + (" ★" if ext else ""),
            "Budget": bstr,
            "Cont. Hours": f"{ch_budget:.0f}" if ch_budget > 0 else "—",
            "Actuals": f"${actuals:,.2f}",
            "Variance": var_s, "% Used": pct_s,
            "_budget_carried": carried_in_d,
            "_budget_added": added_d,
            "_actual_personnel": pers,
            "_actual_pi": pi,
            "_actual_travel": trv,
        })

    if rows:
        display_cols = ["Code", "Project", "Start", "End", "Budget", "Cont. Hours",
                        "Actuals", "Variance", "% Used"]
        display_df = pd.DataFrame([{k: r[k] for k in display_cols} for r in rows])
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.subheader("Budget Breakdown")
        for r in rows:
            with st.expander(f"{r['Project']} — {r['Budget']}"):
                bdf = pd.DataFrame([
                    {"Category": "Carried in (from prior FY)", "Amount": f"${r['_budget_carried']:,.2f}"},
                    {"Category": "Contracted this FY", "Amount": f"${r['_budget_added']:,.2f}"},
                ])
                adf = pd.DataFrame([
                    {"Category": "Actual Personnel", "Amount": f"${r['_actual_personnel']:,.2f}"},
                    {"Category": "Actual PI", "Amount": f"${r['_actual_pi']:,.2f}"},
                    {"Category": "Actual Travel", "Amount": f"${r['_actual_travel']:,.2f}"},
                ])
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Budget components**")
                    st.dataframe(bdf, use_container_width=True, hide_index=True)
                with c2:
                    st.markdown("**Actual components**")
                    st.dataframe(adf, use_container_width=True, hide_index=True)

        csv_df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
        csv = csv_df.to_csv(index=False)
        st.download_button("⬇️ Download CSV", csv, "actual_vs_budget.csv", "text/csv")


# ═════════════════════════════════════════════════════════════════════════
# CREDITS
# ═════════════════════════════════════════════════════════════════════════
elif page == "Credits":
    st.title("🎓 Student Credits")
    D().setdefault("student_credits_by_fy", {})
    fy = st.selectbox("Fiscal Year", fy_choices(), key="cred_fy")

    if can_edit():
        st.subheader("Load from Excel")
        st.caption("Required columns: **Name, Student ID, Credits, Project** "
                   "(one row per student). Uploading replaces this fiscal year's credits.")
    nonce = st.session_state.get(f"cred_nonce_{fy}", 0)
    up = st.file_uploader(
        f"Drag an Excel (.xlsx) file here to load {fy} credits",
        type=["xlsx"], key=f"cred_upload_{fy}_{nonce}",
    ) if can_edit() else None
    if up is not None:
        try:
            df_new = read_excel(up)
            records, err = parse_credit_rows(df_new)
            if err:
                st.error(err)
            else:
                st.caption(f"Preview — {len(records)} students with a project")
                st.dataframe(df_new, use_container_width=True, hide_index=True)
                if can_edit() and st.button(f"💾 Save to {fy}", type="primary", key=f"cred_save_{fy}"):
                    set_credits_for_fy(D(), fy, records)
                    save()
                    st.session_state[f"cred_nonce_{fy}"] = nonce + 1
                    st.toast(f"Saved {len(records)} credit rows to {fy}")
                    st.rerun()
        except Exception as e:
            st.error(f"Couldn't read that file: {e}. Save it as .xlsx with "
                     "columns Name, Student ID, Credits, Project.")

    # ── Add / edit manually (like the Student Workers grid) ──
    st.subheader(f"Add / edit students — {fy}")
    st.caption("Type directly into the grid, or use the ＋ / row checkboxes to add and "
               "remove rows, then Save. Rows left without a project are skipped on save.")
    cur = credits_for_fy(D(), fy)
    if cur:
        seed = pd.DataFrame(cur)[["name", "student_id", "credits", "project"]]
        seed.columns = ["Name", "Student ID", "Credits", "Project"]
    else:
        seed = pd.DataFrame(
            [{"Name": "", "Student ID": "", "Credits": 0.0, "Project": ""}]
        )
    # Generation key so the grid remounts with the freshly saved rows after save.
    gen = st.session_state.get(f"cred_editor_gen_{fy}", 0)
    edited = st.data_editor(
        seed, use_container_width=True, hide_index=True, num_rows="dynamic",
        key=f"cred_editor_{fy}_{gen}", disabled=not can_edit(),
        column_config={
            "Name": st.column_config.TextColumn("Name"),
            "Student ID": st.column_config.TextColumn("Student ID"),
            "Credits": st.column_config.NumberColumn(
                "Credits", min_value=0.0, step=1.0, format="%g"),
            "Project": st.column_config.TextColumn("Project"),
        },
    )
    if can_edit() and st.button(f"💾 Save students to {fy}", type="primary", key=f"cred_manual_save_{fy}"):
        records, err = parse_credit_rows(edited)
        if err:
            st.error(err)
        else:
            set_credits_for_fy(D(), fy, records)
            save()
            st.session_state[f"cred_editor_gen_{fy}"] = gen + 1
            st.toast(f"Saved {len(records)} credit rows to {fy}")
            st.rerun()

    # Per-project totals + metrics from the saved data
    cur = credits_for_fy(D(), fy)
    if cur:
        totals = {}
        for r in cur:
            totals[r["project"]] = totals.get(r["project"], 0) + float(r.get("credits", 0) or 0)
        st.subheader("Totals per project")
        tdf = pd.DataFrame(
            [{"Project": p, "Total Credits": c} for p, c in sorted(totals.items())]
        )
        st.dataframe(tdf, use_container_width=True, hide_index=True)
        m1, m2 = st.columns(2)
        m1.metric("Students", len(cur))
        m2.metric(f"Total credits — {fy}", f"{sum(float(r.get('credits',0) or 0) for r in cur):g}")

        df = pd.DataFrame(cur)[["name", "student_id", "credits", "project"]]
        df.columns = ["Name", "Student ID", "Credits", "Project"]
        csv = df.to_csv(index=False)
        st.download_button("⬇️ Download CSV", csv,
                           f"credits_{fy.replace(' ', '_')}.csv", "text/csv")
    else:
        st.info(f"No credits saved for {fy} yet. Add students in the grid above "
                "or upload an .xlsx.")


# ═════════════════════════════════════════════════════════════════════════
# STUDENT WORKERS
# ═════════════════════════════════════════════════════════════════════════
elif page == "Student Workers":
    st.title("👥 Student Workers")
    D().setdefault("student_workers", {})
    sw = D()["student_workers"]

    fy = st.selectbox("Fiscal Year for this table", fy_choices(), key="sw_fy")

    if can_edit():
        st.subheader("Load from Excel")
    # The uploader key carries a per-FY nonce so we can fully reset it (clear the
    # preview) after a save. Changing FY also changes the key → fresh empty box.
    nonce = st.session_state.get(f"sw_nonce_{fy}", 0)
    up = st.file_uploader(
        f"Drag an Excel (.xlsx) file here to load the {fy} student worker list",
        type=["xlsx"], key=f"sw_upload_{fy}_{nonce}",
    ) if can_edit() else None
    if up is not None:
        try:
            df_new = read_excel(up)
            st.caption(f"Preview — {len(df_new)} rows, {len(df_new.columns)} columns")
            st.dataframe(df_new, use_container_width=True, hide_index=True)
            if can_edit() and st.button(f"💾 Save to {fy}", type="primary", key=f"sw_save_{fy}"):
                sw[fy] = df_to_store(df_new)
                save()
                # Bump the nonce so the uploader resets and the preview disappears.
                st.session_state[f"sw_nonce_{fy}"] = nonce + 1
                st.toast(f"Saved {len(df_new)} student workers to {fy}")
                st.rerun()
        except Exception as e:
            st.error(f"Couldn't read that file: {e}. Try re-saving it as .xlsx.")

    st.subheader(f"Saved table — {fy}")
    if sw.get(fy) and sw[fy].get("data"):
        cur = store_to_df(sw[fy])
        st.caption("Edit cells directly, or use the grid's ＋ / row checkboxes to add and "
                   "remove rows, then Save Edits.")
        edited = st.data_editor(
            cur, use_container_width=True, hide_index=True,
            num_rows="dynamic", key=f"sw_editor_{fy}", disabled=not can_edit(),
        )
        b1, b2, b3 = st.columns([1, 1, 2])
        if can_edit() and b1.button("💾 Save Edits"):
            sw[fy] = df_to_store(edited)
            save()
            st.toast("Saved")
            st.rerun()
        if can_edit() and b2.button("🗑️ Clear Table"):
            sw.pop(fy, None)
            save()
            st.rerun()
        st.markdown(f"**{len(cur)} student workers in {fy}**")
        csv = cur.to_csv(index=False)
        st.download_button("⬇️ Export CSV", csv,
                           f"student_workers_{fy.replace(' ', '_')}.csv", "text/csv")
    else:
        st.info(f"No student worker table saved for {fy} yet. "
                "Drag an Excel file above to add one.")


# ═════════════════════════════════════════════════════════════════════════
# INVOICES / WO
# ═════════════════════════════════════════════════════════════════════════
elif page == "Invoices / WO":
    st.title("📄 Invoices & Work Orders")

    # ── Filters: project + the Invoice/Work-Order toggle (no historical filter;
    #    the table always shows every record so nothing is hidden). ──
    fc1, fc2 = st.columns([1, 1])
    proj_filter = fc1.selectbox("Filter project", ["All"] + projects_sorted(), key="inv_proj_f")
    inv_type = fc2.radio("Type to add", ["Invoice", "Work Order"], horizontal=True, key="inv_add_type")
    form_type = inv_type

    NET_OPTIONS = ["Net 30", "Net 60", "Net 90", "Net 120"]

    def _date_to_str(d):
        """data_editor / date_input value → 'YYYY-MM-DD' string (or '')."""
        if d is None or (isinstance(d, float) and pd.isna(d)):
            return ""
        try:
            return pd.to_datetime(d).date().isoformat()
        except Exception:
            return str(d)[:10] if d else ""

    # ══════════════════════════════════════════════════════════════════
    # ADD NEW  (input boxes only — editing happens inline in the table below)
    # ══════════════════════════════════════════════════════════════════
    if can_edit():
        with st.form("add_inv"):
            st.subheader(f"➕ Add {form_type}")
            ic = st.columns(3)
            project_options = projects_sorted()
            num_field = ic[0].text_input("Number", value="", key="invwo_number")
            proj = ic[1].selectbox("Project", project_options, key="invwo_project")
            desc_label = "Period / Label" if form_type == "Work Order" else "Description"
            desc = ic[2].text_input(desc_label, value="", key="invwo_description")

            if form_type == "Invoice":
                ic2 = st.columns(4)
                amt = ic2[0].number_input("Amount ($)", value=0.0, step=1.0, format="%.2f", key="invoice_amount")
                net = ic2[1].selectbox("Net Terms", NET_OPTIONS, key="invoice_net_terms")
                due = ic2[2].date_input("Invoice Date", value=None, key="inv_due")
                hrs_ded = ic2[3].number_input("Hours deducted", value=0.0, step=0.5, format="%.1f", key="invoice_hours_deducted")
                ic3 = st.columns(2)
                sent = ic3[0].checkbox("Sent", value=False, key="inv_sent")
                paid = ic3[1].checkbox("Paid", value=False, key="inv_paid")

                if st.form_submit_button("💾 Save Invoice"):
                    if not num_field:
                        st.error("Number is required.")
                    else:
                        D()["invoices"].append({
                            "type": "Invoice", "number": num_field, "project": proj,
                            "description": desc, "amount": round(amt, 2),
                            "net_terms": net, "due_date": _date_to_str(due),
                            "hours_deducted": hrs_ded if hrs_ded else "",
                            "sent": sent, "paid": paid,
                        })
                        save()
                        st.toast("Invoice saved")
                        st.rerun()
            else:
                st.markdown("**Add installments below, then click Save Work Order.**")
                if "wo_installments" not in st.session_state:
                    st.session_state.wo_installments = []
                ic2 = st.columns(3)
                inst_amt = ic2[0].number_input("Amount ($)", value=0.0, step=1.0, format="%.2f", key="wo_new_amount")
                inst_net = ic2[1].selectbox("Net Terms", NET_OPTIONS, key="wo_new_net_terms")
                inst_due = ic2[2].date_input("Invoice Date", value=None, key="wo_new_invoice_date")
                add_inst = st.form_submit_button("➕ Add Installment")
                save_wo = st.form_submit_button("💾 Save Work Order")

                if add_inst:
                    if not desc:
                        st.error("Period / Label is required before adding an installment.")
                    elif inst_amt <= 0:
                        st.error("Installment amount must be greater than $0.")
                    else:
                        st.session_state.wo_installments.append({
                            "period": desc, "amount": round(inst_amt, 2),
                            "net_terms": inst_net, "due_date": _date_to_str(inst_due),
                            "sent": False, "paid": False,
                        })
                        st.toast("Installment added — add another or save the work order.")
                        st.rerun()

                if save_wo and num_field:
                    if st.session_state.wo_installments:
                        D()["invoices"].append({
                            "type": "Work Order", "number": num_field, "project": proj,
                            "description": num_field,
                            "installments": list(st.session_state.wo_installments),
                        })
                        wo_t = sum(i["amount"] for i in st.session_state.wo_installments)
                        contracted = project_contracted_total(D()["project_records"].get(proj, {}))
                        save()
                        st.session_state.wo_installments = []
                        if abs(wo_t - contracted) > 0.01 and (wo_t > 0 or contracted > 0):
                            st.toast(f"⚠️ WO total ${wo_t:,.2f} ≠ Contracted ${contracted:,.2f}")
                        else:
                            st.toast("Work order saved")
                        st.rerun()
                    else:
                        st.error("Add at least one installment.")
                elif save_wo and not num_field:
                    st.error("Number is required.")

    # Staged installments preview (for a new Work Order)
    if form_type == "Work Order" and st.session_state.get("wo_installments"):
        st.markdown("**Staged installments:**")
        st.dataframe(pd.DataFrame(st.session_state.wo_installments),
                     use_container_width=True, hide_index=True)
        total = sum(i["amount"] for i in st.session_state.wo_installments)
        st.markdown(f"**Total: ${total:,.2f}**")
        if can_edit() and st.button("Clear installments"):
            st.session_state.wo_installments = []
            st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # ALL RECORDS  (edit inline, then confirm before changes are written)
    # ══════════════════════════════════════════════════════════════════
    st.subheader("All Records")
    st.caption("Edit any cell directly in the table below. To remove a row, tick its "
               "**🗑️ Delete** box (first column), then click **Review changes** — nothing "
               "is saved until you confirm. A Work Order shows one row per installment; "
               "tick every installment row to delete the whole Work Order. "
               "**Payment Due** and **WO Total** are computed.")

    if "inv_editor_gen" not in st.session_state:
        st.session_state.inv_editor_gen = 0
    if "inv_pending" not in st.session_state:
        st.session_state.inv_pending = False

    flat = flat_invoice_rows(D(), proj_filter=proj_filter)
    if not flat:
        st.info("No records yet. Add an invoice or work order above.")
    else:
        edit_df = pd.DataFrame([{
            "Delete": False,
            "Type": r["type"],
            "Number": str(r["number"]),
            "Project": r["project"],
            "Description": r["description"],
            "Amount": float(r["inst_amount"]),
            "Net Terms": r["net_terms"] if r["net_terms"] in NET_OPTIONS else "Net 30",
            "Invoice Date": parse_date(r["due_date"]),
            "Hours Deducted": float(r["hours_deducted"]) if (r["type"] == "Invoice" and r.get("hours_deducted") not in ("", None)) else 0.0,
            "Sent": bool(r["sent"]),
            "Paid": bool(r["paid"]),
            "Payment Due": fmt_date(r["payment_due"]),
            "WO Total": f"${r['wo_total']:,.2f}" if r["type"] == "Work Order" else "",
        } for r in flat])

        money_col = st.column_config.NumberColumn(min_value=0.0, step=1.0, format="$%.2f")
        edited = st.data_editor(
            edit_df, use_container_width=True, hide_index=True, num_rows="fixed",
            key=f"inv_editor_{st.session_state.inv_editor_gen}",
            disabled=not can_edit(),
            column_config={
                "Delete": st.column_config.CheckboxColumn("🗑️ Delete", help="Tick to remove this row on save", width="small"),
                "Type": st.column_config.TextColumn(disabled=True, width="small"),
                "Number": st.column_config.TextColumn(required=True),
                "Project": st.column_config.SelectboxColumn(options=projects_sorted(), required=True),
                "Description": st.column_config.TextColumn(),
                "Amount": money_col,
                "Net Terms": st.column_config.SelectboxColumn(options=NET_OPTIONS),
                "Invoice Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
                "Hours Deducted": st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.1f"),
                "Sent": st.column_config.CheckboxColumn(),
                "Paid": st.column_config.CheckboxColumn(),
                "Payment Due": st.column_config.TextColumn(disabled=True),
                "WO Total": st.column_config.TextColumn(disabled=True),
            },
        )

        # ── Confirm-before-write gate ──
        if not st.session_state.inv_pending:
            if can_edit() and st.button("📝 Review changes"):
                st.session_state.inv_pending = True
                st.rerun()
        else:
            n_del = int(edited["Delete"].sum()) if "Delete" in edited else 0
            warn = "Are you sure you want to apply these changes?"
            if n_del:
                warn += f" This will also delete {n_del} row{'s' if n_del != 1 else ''}."
            st.warning(warn)
            cc1, cc2, _ = st.columns([1, 1, 3])
            if can_edit() and cc1.button("✅ Yes, apply changes"):
                # 1. Write edits back to every non-deleted row (object references
                #    in flat[i] keep us aligned to the underlying records).
                for i in range(len(flat)):
                    row = edited.iloc[i]
                    if bool(row.get("Delete")):
                        continue
                    rec = flat[i]["_rec"]
                    inst = flat[i]["_inst"]
                    rec["number"] = str(row["Number"]).strip()
                    rec["project"] = row["Project"]
                    if flat[i]["type"] == "Invoice":
                        rec["description"] = row["Description"]
                        rec["amount"] = round(num(row["Amount"]), 2)
                        rec["net_terms"] = row["Net Terms"]
                        rec["due_date"] = _date_to_str(row["Invoice Date"])
                        hd = num(row["Hours Deducted"])
                        rec["hours_deducted"] = hd if hd else ""
                        rec["sent"] = bool(row["Sent"])
                        rec["paid"] = bool(row["Paid"])
                    else:  # Work Order installment
                        inst["period"] = row["Description"]
                        inst["amount"] = round(num(row["Amount"]), 2)
                        inst["net_terms"] = row["Net Terms"]
                        inst["due_date"] = _date_to_str(row["Invoice Date"])
                        inst["sent"] = bool(row["Sent"])
                        inst["paid"] = bool(row["Paid"])
                # 2. Process deletions (installment → drop it; empty WO or invoice
                #    → drop the whole record).
                for i in range(len(flat)):
                    if not bool(edited.iloc[i].get("Delete")):
                        continue
                    rec = flat[i]["_rec"]
                    inst = flat[i]["_inst"]
                    if inst is not None:
                        if inst in rec.get("installments", []):
                            rec["installments"].remove(inst)
                        if not rec.get("installments") and rec in D()["invoices"]:
                            D()["invoices"].remove(rec)
                    elif rec in D()["invoices"]:
                        D()["invoices"].remove(rec)
                save()
                st.session_state.inv_pending = False
                st.session_state.inv_editor_gen += 1  # remount editor fresh
                st.toast("Changes saved")
                st.rerun()
            if can_edit() and cc2.button("✖ Cancel"):
                st.session_state.inv_pending = False
                st.session_state.inv_editor_gen += 1  # discard in-grid edits
                st.rerun()

        # CSV export (reflects what's currently saved)
        export_rows = [{
            "Type": r["type"], "Number": r["number"], "Project": r["project"],
            "Description": r["description"], "Inst. Amount": r["inst_amount"],
            "WO Total": r["wo_total"] if r["type"] == "Work Order" else "",
            "Terms": r["net_terms"], "Invoice Date": fmt_date(r["due_date"]),
            "Payment Due": fmt_date(r["payment_due"]),
            "Hours Deducted": r.get("hours_deducted", "") if r["type"] == "Invoice" else "",
            "Sent": "Yes" if r["sent"] else "No", "Paid": "Yes" if r["paid"] else "No",
        } for r in flat]
        csv = pd.DataFrame(export_rows).to_csv(index=False)
        st.download_button("⬇️ Export CSV", csv, "invoices_wo.csv", "text/csv")


# ═════════════════════════════════════════════════════════════════════════
# TOOLS
# ═════════════════════════════════════════════════════════════════════════
elif page == "Tools":
    st.title("🛠️ Tools & Subscriptions")
    tools = D()["tools"]

    # Fiscal-year filter (based on each tool's start/end dates)
    tfy_years = set()
    for t in tools:
        for ds in (t.get("start_date", ""), t.get("end_date", "")):
            d = parse_date(ds)
            if d:
                tfy_years.add(date_to_fy(d))
    tfy_years.add(date_to_fy(date.today()))
    # Individual fiscal years only (no "All") — default to the current FY.
    tool_fy_opts = [fy_label(y) for y in sorted(tfy_years)]
    cur_fy_lbl = fy_label(date_to_fy(date.today()))
    tool_fy_idx = (tool_fy_opts.index(cur_fy_lbl)
                   if cur_fy_lbl in tool_fy_opts else len(tool_fy_opts) - 1)
    # Clear any stored value left over from when "All" was an option.
    if st.session_state.get("tools_fy") not in tool_fy_opts:
        st.session_state.pop("tools_fy", None)
    tool_fy = st.selectbox("Fiscal Year", tool_fy_opts,
                           index=tool_fy_idx, key="tools_fy")

    # Tools active in the selected FY, keeping their original index for edit/delete.
    filtered = [(i, t) for i, t in enumerate(tools) if tool_in_fy(t, tool_fy)]

    # KPIs (scoped to the selected FY): split tools into monthly vs one-time.
    monthly_sum = onetime_sum = 0.0
    for _, t in filtered:
        cost = float(t.get("cost", 0) or 0)
        if t.get("billing_cycle") == "Monthly":
            monthly_sum += cost
        else:
            onetime_sum += cost
    total_cost = monthly_sum * 12 + onetime_sum
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Tools", len(filtered))
    mc2.metric("Total Tools Cost", f"${total_cost:,.2f}")
    mc3.metric("Monthly Cost", f"${monthly_sum:,.2f}")
    mc4.metric("One-time Cost", f"${onetime_sum:,.2f}")
    if tool_fy != "All":
        st.caption(f"Showing tools active in {tool_fy} "
                   "(a tool with no end date counts as ongoing from its start).")
    st.caption("Total = monthly tools × 12 + one-time tools × 1.")

    # Add / Edit tool using the same input form style
    if can_edit():
        st.subheader("Add / Edit Tool")

        tool_mode = st.radio("Tool Action", ["Add New", "Edit Existing"],
                             horizontal=True, key="tool_mode")

        edit_tool_idx = None
        selected_tool = {}
        if tool_mode == "Edit Existing":
            if tools:
                edit_tool_idx = st.selectbox(
                    "Select tool to edit",
                    range(len(tools)),
                    format_func=lambda i: tools[i].get("name", f"Tool {i + 1}"),
                    key="edit_tool_idx"
                )
                selected_tool = tools[edit_tool_idx]
            else:
                st.info("No tools available to edit yet.")

        cycle_options = ["Monthly", "One-time"]
        current_cycle = selected_tool.get("billing_cycle", "Monthly")
        cycle_index = cycle_options.index(current_cycle) if current_cycle in cycle_options else 0

        # Generation counter: bumped after a successful save so the fields remount
        # fresh (clears the Add New form; repopulates Edit from the selected tool).
        gen = st.session_state.setdefault("tool_form_gen", 0)
        kp = f"{tool_mode}_{edit_tool_idx}_{gen}"

        with st.form("tool_form"):
            tc = st.columns(3)
            t_name = tc[0].text_input("Tool Name", value=selected_tool.get("name", ""),
                                      key=f"tool_name_{kp}")
            t_vendor = tc[1].text_input("Vendor", value=selected_tool.get("vendor", ""),
                                        key=f"tool_vendor_{kp}")
            t_cost = tc[2].number_input("Cost ($)",
                                        value=float(selected_tool.get("cost", 0.0)),
                                        min_value=0.0, step=1.0, format="%.2f",
                                        key=f"tool_cost_{kp}")
            tc2 = st.columns(4)
            t_cycle = tc2[0].selectbox("Billing Cycle", cycle_options, index=cycle_index,
                                       key=f"tool_cycle_{kp}")
            t_start = tc2[1].date_input("Start Date",
                                        value=parse_date(selected_tool.get("start_date", "")),
                                        key=f"tool_start_{kp}")
            t_end = tc2[2].date_input("End Date (blank = ongoing)",
                                      value=parse_date(selected_tool.get("end_date", "")),
                                      key=f"tool_end_{kp}")
            t_renew = tc2[3].checkbox("Auto-renew", value=selected_tool.get("auto_renew", True),
                                      key=f"tool_renew_{kp}")
            tc3 = st.columns(4)
            t_paid = tc3[0].checkbox("Paid", value=selected_tool.get("paid", False),
                                     key=f"tool_paid_{kp}")
            t_notes = st.text_input("Notes", value=selected_tool.get("notes", ""),
                                    key=f"tool_notes_{kp}")

            submit_label = "💾 Save New Tool" if tool_mode == "Add New" else "💾 Update Tool"
            if st.form_submit_button(submit_label):
                if t_name:
                    tool_payload = {
                        "name": t_name, "vendor": t_vendor,
                        "cost": round(t_cost, 2),
                        "billing_cycle": t_cycle,
                        "start_date": str(t_start) if t_start else "",
                        "end_date": str(t_end) if t_end else "",
                        "auto_renew": t_renew, "paid": t_paid,
                        "notes": t_notes,
                    }

                    if tool_mode == "Edit Existing" and edit_tool_idx is not None:
                        old_name = tools[edit_tool_idx].get("name", "")
                        tools[edit_tool_idx] = tool_payload

                        # Keep project tool assignments synced if the tool name changes
                        if old_name and old_name != t_name:
                            for proj in D()["project_records"].values():
                                proj["assigned_tools"] = [
                                    t_name if assigned_tool == old_name else assigned_tool
                                    for assigned_tool in proj.get("assigned_tools", [])
                                ]
                    else:
                        tools.append(tool_payload)

                    save()
                    # Remount the form fields fresh (clears inputs after saving).
                    st.session_state["tool_form_gen"] = gen + 1
                    st.rerun()
                else:
                    st.error("Tool name is required.")

    if filtered:
        today = date.today()
        tool_data = []
        for _, t in filtered:
            mc, ac = calc_tool_costs(t)
            renewal = calc_renewal_date(t)
            n_users = count_tool_users(D(), t.get("name", ""))
            if n_users <= 0:
                per_proj = "—"
            elif tool_has_custom_split(D(), t):
                per_proj = "custom"
            else:
                per_proj = f"${ac / n_users:,.2f}"
            tool_data.append({
                "Tool": t.get("name", ""),
                "Vendor": t.get("vendor", ""),
                "Cost": f"${t.get('cost', 0):,.2f}",
                "Cycle": t.get("billing_cycle", ""),
                "Monthly": f"${mc:,.2f}" if t.get("billing_cycle") == "Monthly" else "—",
                "Annual": f"${ac:,.2f}",
                "Used by": f"{n_users}" if n_users else "—",
                "Per project / yr": per_proj,
                "Start": fmt_date(t.get("start_date", "")),
                "End": fmt_date(t.get("end_date", "")) if t.get("end_date") else "ongoing",
                "Next Renewal": fmt_date(renewal) if renewal else "—",
                "Auto": "Yes" if t.get("auto_renew") else "No",
                "Paid": "☑" if t.get("paid") else "☐",
                "Notes": t.get("notes", ""),
            })
        event = st.dataframe(
            pd.DataFrame(tool_data), use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key="tools_table",
            column_config={
                "Notes": st.column_config.TextColumn(
                    "Notes", width="large",
                    help="Drag the column border to widen, or scroll the table "
                         "sideways to read the full note.",
                ),
            },
        )

        # Toggle paid / Delete on the selected row (map back to the real index)
        sel = event.selection.rows
        if sel and sel[0] < len(filtered):
            i = filtered[sel[0]][0]
            st.caption(f"Selected: **{tools[i].get('name', '')}**")
            b1, b2 = st.columns(2)
            if can_edit() and b1.button("Toggle Paid ☐ ↔ ☑"):
                tools[i]["paid"] = not tools[i].get("paid", False)
                save()
                st.rerun()
            if can_edit() and b2.button("🗑️ Delete Tool"):
                st.session_state["confirm_del_tool"] = i
            # Reset a pending confirmation if a different row got selected.
            if (st.session_state.get("confirm_del_tool") is not None
                    and st.session_state["confirm_del_tool"] != i):
                st.session_state.pop("confirm_del_tool", None)
            # Two-step confirmation so a tool is never removed by a stray click.
            if st.session_state.get("confirm_del_tool") == i:
                st.warning(f"Remove **{tools[i].get('name', '')}**? "
                           "This can't be undone.")
                cd1, cd2 = st.columns(2)
                if can_edit() and cd1.button("✅ Yes, remove it", key=f"del_yes_{i}"):
                    tools.pop(i)
                    st.session_state.pop("confirm_del_tool", None)
                    save()
                    st.rerun()
                if can_edit() and cd2.button("↩️ Cancel", key=f"del_no_{i}"):
                    st.session_state.pop("confirm_del_tool", None)
                    st.rerun()

            # ── Cost split editor (only meaningful when 2+ projects share it) ──
            t_sel = tools[i]
            users = tool_users(D(), t_sel.get("name", ""))
            if len(users) >= 2:
                cycle = t_sel.get("billing_cycle", "Monthly")
                try:
                    cost = float(t_sel.get("cost", 0))
                except (TypeError, ValueError):
                    cost = 0.0
                _, full_annual = calc_tool_costs(t_sel)
                st.markdown("**Cost split across projects ($)**")
                st.caption(f"{cycle} cost **${cost:,.2f}** (${full_annual:,.2f}/yr). "
                           f"Enter each project's dollar amount per {cycle.lower()} "
                           "period — equal split applies until you save a custom one. "
                           "Projects left at $0 are charged nothing.")
                sa = t_sel.get("split_amounts") or {}
                equal_amt = round(cost / len(users), 2)
                amt_inputs = {}
                cols = st.columns(min(len(users), 4))
                for j, p in enumerate(users):
                    default_amt = float(sa.get(p, equal_amt) or 0.0)
                    with cols[j % len(cols)]:
                        amt_inputs[p] = st.number_input(
                            f"{p} ($)", min_value=0.0, value=default_amt,
                            step=5.0, format="%.2f",
                            key=f"alloc_{i}_{p}",
                        )
                entered = sum(amt_inputs.values())
                diff = entered - cost
                if abs(diff) > 0.01:
                    word = "unassigned" if diff < 0 else "over-assigned"
                    st.warning(f"Entered ${entered:,.2f} of ${cost:,.2f} — "
                               f"${abs(diff):,.2f} {word}. Amounts are kept exactly "
                               "as typed.")
                else:
                    st.caption(f"✓ Matches the {cycle.lower()} cost (${cost:,.2f})")
                sc1, sc2 = st.columns(2)
                if can_edit() and sc1.button("💾 Save Split", key=f"save_split_{i}"):
                    t_sel["split_amounts"] = {p: float(v) for p, v in amt_inputs.items()}
                    save()
                    st.toast("Custom $ split saved")
                    st.rerun()
                if can_edit() and sc2.button("↩️ Reset to equal split", key=f"reset_split_{i}"):
                    t_sel["split_amounts"] = {}
                    save()
                    st.rerun()
                # Preview of resulting annual shares (exactly as entered, annualized)
                from data import _cycle_amount_to_monthly_annual
                preview = []
                for p, v in amt_inputs.items():
                    pm, pa = _cycle_amount_to_monthly_annual(v, cycle)
                    preview.append({"Project": p,
                                    f"$ / {cycle.lower()}": f"${v:,.2f}",
                                    "$ / mo": f"${pm:,.2f}",
                                    "$ / yr": f"${pa:,.2f}"})
                st.dataframe(pd.DataFrame(preview), use_container_width=True,
                             hide_index=True)
            elif len(users) == 1:
                st.caption(f"Only **{users[0]}** uses this tool — it bears the full cost. "
                           "Assign the tool to more projects to split it.")
        else:
            st.caption("Click a row above to toggle Paid, delete, or set a cost split.")


# ═════════════════════════════════════════════════════════════════════════
# RESULTS — FY COMPARISON
# ═════════════════════════════════════════════════════════════════════════
elif page == "Results":
    st.title("🆚 Results — Fiscal Year Comparison")
    choices = fy_choices()

    c1, c2 = st.columns(2)
    fy_a = c1.selectbox("Compare from (older FY)", choices, index=0, key="res_fy_a")
    fy_b = c2.selectbox("…to (newer FY)", choices,
                        index=len(choices) - 1, key="res_fy_b")
    mode = st.radio("Comparison", ["All projects (total)", "Per project"],
                    horizontal=True, key="res_mode")

    fy_year_a = None if fy_a == "All" else int(fy_a.split()[1].split("-")[0])
    fy_year_b = None if fy_b == "All" else int(fy_b.split()[1].split("-")[0])

    def proj_budget(proj, fy_year):
        if proj.get("has_budget", False):
            return None
        return fy_budget_available(proj, fy_year) if fy_year is not None \
            else fy_total_budget_added(proj)

    def proj_actuals(proj, fy_year):
        scope = fy_lines(proj, fy_year) if fy_year is not None else proj["lines"]
        return sum(actual_personnel_cost(r) + actual_pi_cost(r) + line_value(r, 8)
                   for r in scope)

    def metrics_for(fy):
        proj_count = 0
        budget = 0.0
        credits = 0
        fy_year = None
        if fy != "All":
            try:
                fy_year = int(fy.split()[1].split("-")[0])
            except Exception:
                fy_year = None
        for name, proj in filtered_projects("", fy):
            proj_count += 1
            # has_budget == True means a "No Budget" project, so it's excluded from budget totals
            if not proj.get("has_budget", False):
                if fy_year is not None:
                    budget += fy_budget_available(proj, fy_year)
                else:
                    budget += fy_total_budget_added(proj)
            credits += project_credits(D(), fy, name)
        sw_count = sw_count_for_fy(fy)
        return {"Student Workers": sw_count, "Projects": proj_count,
                "Budget": budget, "Credits": credits}

    ma = metrics_for(fy_a)
    mb = metrics_for(fy_b)
    metric_order = ["Student Workers", "Projects", "Budget", "Credits"]

    def growth(a, b):
        return None if a == 0 else (b - a) / a * 100

    def fmt_val(metric, v):
        return f"${v:,.2f}" if metric == "Budget" else f"{int(v):,}"

    st.caption(f"Comparing **{fy_a} → {fy_b}** "
               f"({'same year' if fy_a == fy_b else 'year over year'})")

    if mode == "All projects (total)":
        # Headline cards (newer FY value, with growth % as the delta arrow)
        cols = st.columns(4)
        for col, metric in zip(cols, metric_order):
            a, b = ma[metric], mb[metric]
            g = growth(a, b)
            col.metric(metric, fmt_val(metric, b),
                       None if g is None else f"{g:+.1f}%")

        # Detailed table: both years, absolute change, and growth %
        rows = []
        for metric in metric_order:
            a, b = ma[metric], mb[metric]
            g = growth(a, b)
            change = b - a
            if metric == "Budget":
                sign = "+" if change >= 0 else "−"
                change_str = f"{sign}${abs(change):,.2f}"
            else:
                change_str = f"{int(change):+,}"
            rows.append({
                "Metric": metric,
                fy_a: fmt_val(metric, a),
                fy_b: fmt_val(metric, b),
                "Change": change_str,
                "Growth %": "—" if g is None else f"{g:+.1f}%",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("Growth % shows “—” when the older year is 0 (no baseline to divide by). "
                   "Budget and project count are based on projects active in each FY; "
                   "credits come from the Credits tab; student workers from the saved table for that FY.")

        csv = pd.DataFrame(rows).to_csv(index=False)
        st.download_button("⬇️ Download CSV", csv,
                           f"results_{fy_a.replace(' ', '_')}_vs_{fy_b.replace(' ', '_')}.csv",
                           "text/csv")

    else:  # ── Per project ──────────────────────────────────────────────
        names = sorted(
            n for n, p in D()["project_records"].items()
            if project_in_fy(p, fy_a) or project_in_fy(p, fy_b)
        )
        if not names:
            st.info("No projects fall within either of the selected fiscal years.")
        else:
            tot_ba = tot_bb = tot_aa = tot_ab = 0.0
            rows = []
            for name in names:
                p = D()["project_records"][name]
                in_a = project_in_fy(p, fy_a)
                in_b = project_in_fy(p, fy_b)
                ba = proj_budget(p, fy_year_a) if in_a else None
                bb = proj_budget(p, fy_year_b) if in_b else None
                aa = proj_actuals(p, fy_year_a) if in_a else None
                ab = proj_actuals(p, fy_year_b) if in_b else None
                if ba is not None:
                    tot_ba += ba
                if bb is not None:
                    tot_bb += bb
                if aa is not None:
                    tot_aa += aa
                if ab is not None:
                    tot_ab += ab
                bgr = growth(ba or 0, bb or 0) if (ba is not None and bb is not None) else None

                def money(v):
                    return "—" if v is None else f"${v:,.2f}"
                rows.append({
                    "Project": name,
                    f"Budget {fy_a}": money(ba),
                    f"Budget {fy_b}": money(bb),
                    "Budget Growth %": "—" if bgr is None else f"{bgr:+.1f}%",
                    f"Actuals {fy_a}": money(aa),
                    f"Actuals {fy_b}": money(ab),
                })
            # Totals row
            tgr = growth(tot_ba, tot_bb)
            rows.append({
                "Project": "— TOTAL —",
                f"Budget {fy_a}": f"${tot_ba:,.2f}",
                f"Budget {fy_b}": f"${tot_bb:,.2f}",
                "Budget Growth %": "—" if tgr is None else f"{tgr:+.1f}%",
                f"Actuals {fy_a}": f"${tot_aa:,.2f}",
                f"Actuals {fy_b}": f"${tot_ab:,.2f}",
            })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption("One row per project that falls within either fiscal year. A column "
                       "shows “—” when the project isn't active in that FY. Budget is that "
                       "FY's available budget (carried-in + contracted); Actuals is that "
                       "FY's line-item spend. “No Budget” projects show — for budget.")
            csv = df.to_csv(index=False)
            st.download_button("⬇️ Download CSV", csv,
                               f"results_per_project_{fy_a.replace(' ', '_')}_vs_{fy_b.replace(' ', '_')}.csv",
                               "text/csv")