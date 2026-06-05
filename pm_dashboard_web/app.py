"""PM Dashboard — Streamlit Web App."""
import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
from helpers import (parse_date, fmt_date, fy_label, date_to_fy,
                     calc_payment_due, calc_renewal_date, calc_tool_costs)
from data import (load_data, save_data, blank_project, blank_line,
                  compute_master_totals, project_contracted_total,
                  wo_project_total, flat_invoice_rows, get_all_notifications,
                  get_fy_options, project_in_fy)

st.set_page_config(page_title="PM Dashboard", page_icon="📁", layout="wide")

# ─── Session state init ──────────────────────────────────────────────────
if "data" not in st.session_state:
    st.session_state.data = load_data()


def D():
    return st.session_state.data


def save():
    save_data(D())


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
    ("⏱️", "Hours"),
    ("🛠️", "Tools"),
    ("🆚", "Results"),
]

if "page" not in st.session_state:
    st.session_state.page = "Overview"

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
    st.markdown("""
    <div style="display: flex; align-items: center; gap: 8px; padding: 4px;">
        <div style="width: 28px; height: 28px; border-radius: 50%;
                    background: rgba(52,211,153,0.15); display: flex;
                    align-items: center; justify-content: center;
                    font-size: 11px; font-weight: 500; color: #34d399 !important;">PM</div>
        <span style="font-size: 12px; color: #475569 !important;">Project Manager</span>
    </div>
    """, unsafe_allow_html=True)

page = st.session_state.page


# ═════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ═════════════════════════════════════════════════════════════════════════
if page == "Overview":
    st.title("📊 Overview")
    totals = compute_master_totals(D())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Projects", totals["active_projects"])
    c2.metric("Student Credits", totals["total_credits"])
    c3.metric("Total Budget", f"${totals['total_budget']:,.2f}")
    c4.metric("Personnel Cost", f"${totals['total_stu_personnel']:,.2f}")

    col_left, col_right = st.columns([2, 3])

    with col_left:
        st.subheader("Hours Utilization")
        items = []
        for name, proj in sorted(D()["project_records"].items()):
            budget = float(proj.get("hours_budget", 0))
            if budget <= 0:
                continue
            used = sum(float(e.get("hours", 0)) for e in proj.get("hours_log", []))
            pct = used / budget * 100
            items.append({"Project": name, "Used": used, "Budget": budget, "% Used": pct})
        if items:
            for item in items:
                pct = item["% Used"]
                color = "🔴" if pct > 100 else ("🟡" if pct > 80 else "🟢")
                st.markdown(f"**{item['Project']}** {color}")
                st.progress(min(pct / 100, 1.0))
                st.caption(f"{item['Used']:.0f} / {item['Budget']:.0f} hrs ({pct:.0f}%)")
        else:
            st.info("No projects with hours budgets set.")

    with col_right:
        st.subheader("⚠️ Alerts")
        notifs = get_all_notifications(D())
        if notifs:
            df = pd.DataFrame(notifs, columns=["!", "Category", "Project", "Details"])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.success("No alerts — all clear!")


# ═════════════════════════════════════════════════════════════════════════
# PROJECT VIEW
# ═════════════════════════════════════════════════════════════════════════
elif page == "Project View":
    st.title("📁 Project View")
    pr = D()["project_records"]
    projects = projects_sorted()

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        active = st.selectbox("Active Project", projects)
    with col2:
        if st.button("➕ Add Project"):
            st.session_state.show_add_project = True
    with col3:
        if st.button("🗑️ Remove Project", type="secondary"):
            if len(projects) > 1:
                st.session_state.confirm_del_proj = True
            else:
                st.error("Must keep at least one project.")

    # Confirm-gate for project deletion (prevents one-click data loss)
    if st.session_state.get("confirm_del_proj"):
        st.warning(f"Delete **{active}** and its student credits? This can't be undone.")
        dc1, dc2, _ = st.columns([1, 1, 4])
        if dc1.button("Yes, delete", type="primary"):
            del pr[active]
            D()["student_credits"] = [s for s in D()["student_credits"] if s["project"] != active]
            st.session_state.confirm_del_proj = False
            save()
            st.toast(f"Removed {active}")
            st.rerun()
        if dc2.button("Cancel"):
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
            if st.button("Create Project"):
                if new_code and new_name:
                    if new_name not in pr:
                        p = blank_project(new_code, new_name, new_nb)
                        p["start_date"] = str(new_start) if new_start else ""
                        p["end_date"] = str(new_end) if new_end else ""
                        pr[new_name] = p
                        save()
                        st.session_state.show_add_project = False
                        st.toast(f"Created {new_name}")
                        st.rerun()
                    else:
                        st.error(f"'{new_name}' already exists.")
                else:
                    st.error("Both code and name are required.")

    if active not in pr:
        st.stop()

    proj = pr[active]

    # Dates
    st.subheader("Project Dates")
    dc1, dc2, dc3, dc4 = st.columns([2, 2, 2, 1])
    sd = dc1.date_input("Start Date", value=parse_date(proj.get("start_date", "")),
                        key="proj_start", format="YYYY-MM-DD")
    ed = dc2.date_input("End Date", value=parse_date(proj.get("end_date", "")),
                        key="proj_end", format="YYYY-MM-DD")
    ext = dc3.date_input("Extension", value=parse_date(proj.get("extension_date", "")),
                         key="proj_ext", format="YYYY-MM-DD")
    no_budget = dc4.checkbox("No Budget", value=proj.get("has_budget", False), key="nb_chk")

    if st.button("💾 Save Dates"):
        proj["start_date"] = str(sd) if sd else ""
        proj["end_date"] = str(ed) if ed else ""
        proj["extension_date"] = str(ext) if ext else ""
        proj["has_budget"] = no_budget
        save()
        st.toast("Dates saved")

    # ── Line items (editable grid) ─────────────────────────────────────
    st.subheader("Line Items")
    st.caption("Edit any cell directly. Use the **＋** at the bottom of the grid to add a "
               "line, or tick a row's checkbox and press your keyboard Delete to remove it — "
               "then click Save Line Items.")

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
    } for row in proj["lines"]])

    money = st.column_config.NumberColumn(min_value=0.0, step=1.0, format="$%.2f")
    hours = st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.1f")
    edited = st.data_editor(
        edit_df, use_container_width=True, hide_index=True,
        num_rows="dynamic", key=f"lines_editor_{active}",
        column_config={
            "Line Item": st.column_config.TextColumn(required=True, width="medium"),
            "Students": st.column_config.NumberColumn(min_value=0, step=1),
            "Stu Rate": money, "Stu Hours": hours,
            "PI Rate": money, "PI Hours": hours,
            "Actual Travel": money, "Cont. Personnel": money, "Cont. PI": money,
            "Cont. Indirect": money, "Cont. Fringe": money, "Cont. Travel": money,
        },
    )

    if st.button("💾 Save Line Items"):
        new_lines = [[
            (r["Line Item"] or "Untitled"),
            num(r["Students"], int), num(r["Stu Rate"]), num(r["Stu Hours"]),
            num(r["PI Rate"]), num(r["PI Hours"]),
            0.0, 0.0, num(r["Actual Travel"]),
            num(r["Cont. Personnel"]), num(r["Cont. PI"]),
            num(r["Cont. Indirect"]), num(r["Cont. Fringe"]), num(r["Cont. Travel"]),
        ] for _, r in edited.iterrows()]
        proj["lines"] = new_lines or [blank_line()]
        save()
        st.toast("Line items saved")
        st.rerun()

    # Read-only computed recap (reflects the last saved state)
    recap = []
    for row in proj["lines"]:
        s, sr, sh = int(line_value(row, 1)), line_value(row, 2), line_value(row, 3)
        pr2, ph = line_value(row, 4), line_value(row, 5)
        cont = (line_value(row, 9) + line_value(row, 10) + line_value(row, 11)
                + line_value(row, 12) + contracted_travel_cost(row))
        recap.append({
            "Line Item": row[0],
            "Stu Cost": f"${s * sr * sh:,.2f}",
            "PI Cost": f"${pr2 * ph:,.2f}",
            "Contracted Total": f"${cont:,.2f}",
        })
    if recap:
        st.caption("Computed costs (from last save)")
        st.dataframe(pd.DataFrame(recap), use_container_width=True, hide_index=True)

    # Notes
    st.subheader("Notes")
    notes = st.text_area("Project notes", value=proj.get("notes", ""), key="proj_notes")
    if st.button("💾 Save Notes"):
        proj["notes"] = notes
        save()
        st.toast("Notes saved")

    # Tool assignments
    st.subheader("Assigned Tools")
    tool_names = [t.get("name", "") for t in D()["tools"] if t.get("name")]
    assigned = proj.get("assigned_tools", [])
    tc1, tc2 = st.columns([3, 1])
    with tc1:
        sel_tool = st.selectbox("Assign tool", [""] + [t for t in tool_names if t not in assigned])
    with tc2:
        if st.button("Assign") and sel_tool:
            assigned.append(sel_tool)
            proj["assigned_tools"] = assigned
            save()
            st.rerun()
    if assigned:
        tool_data = []
        total_m = total_a = 0.0
        for tn in assigned:
            t = next((t for t in D()["tools"] if t.get("name") == tn), None)
            if t:
                mc, ac = calc_tool_costs(t)
                total_m += mc; total_a += ac
                tool_data.append({"Tool": tn, "Vendor": t.get("vendor", ""),
                                  "Monthly": f"${mc:,.2f}", "Annual": f"${ac:,.2f}"})
            else:
                tool_data.append({"Tool": tn, "Vendor": "—", "Monthly": "—", "Annual": "—"})
        st.dataframe(pd.DataFrame(tool_data), use_container_width=True, hide_index=True)
        st.markdown(f"**Total: ${total_m:,.2f}/mo · ${total_a:,.2f}/yr**")
        rem_tool = st.selectbox("Remove tool", assigned, key="rem_tool")
        if st.button("Remove Tool"):
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
    for name, proj in filtered_projects(search, fy):
        is_red = proj.get("has_budget", False)
        budget = stu = pi = 0.0
        for row in proj["lines"]:
            # Contracted budget = contracted personnel + contracted PI + contracted indirect + contracted fringe + contracted travel
            budget += line_value(row, 9) + line_value(row, 10) + line_value(row, 11) + line_value(row, 12) + contracted_travel_cost(row)
            stu += actual_personnel_cost(row)
            pi += actual_pi_cost(row)
        credits = sum(s["credits"] for s in D()["student_credits"] if s["project"] == name)
        ext = proj.get("extension_date", "")
        ed = proj.get("end_date", "")
        end_disp = fmt_date(ext if ext else ed) + (" ★" if ext else "")
        rows.append({
            "Code": proj["code"], "Project": name,
            "Start": fmt_date(proj.get("start_date", "")),
            "End": end_disp,
            "Budget": f"${budget:,.2f}" if not is_red else "—",
            "Personnel": f"${stu:,.2f}", "PI Cost": f"${pi:,.2f}",
            "Credits": credits,
        })
        if not is_red:
            gt_b += budget
        gt_s += stu; gt_p += pi; gt_c += credits

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.markdown(f"**Grand Total — Budget: ${gt_b:,.2f} | Personnel: ${gt_s:,.2f} | "
                    f"PI: ${gt_p:,.2f} | Credits: {gt_c}**")

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
    for name, proj in filtered_projects(search, fy):
        is_red = proj.get("has_budget", False)

        # Budget / contracted categories
        cont_personnel = cont_pi = cont_indirect = cont_fringe = cont_travel = 0.0

        # Actual / running cost categories
        pers = pi = trv = tools_actual = 0.0

        for row in proj["lines"]:
            # Contracted budget = contracted personnel + contracted PI + contracted indirect + contracted fringe + contracted travel
            cont_personnel += line_value(row, 9)
            cont_pi += line_value(row, 10)
            cont_indirect += line_value(row, 11)
            cont_fringe += line_value(row, 12)
            cont_travel += contracted_travel_cost(row)

            # Actual cost = personnel + PI + actual travel + tools.
            # Indirect and fringe are NOT included in actual/running cost.
            pers += actual_personnel_cost(row)
            pi += actual_pi_cost(row)
            trv += line_value(row, 8)

        for tn in proj.get("assigned_tools", []):
            t = next((x for x in D()["tools"] if x.get("name") == tn), None)
            if t:
                _, annual_cost = calc_tool_costs(t)
                tools_actual += annual_cost

        budget = cont_personnel + cont_pi + cont_indirect + cont_fringe + cont_travel
        actuals = pers + pi + trv + tools_actual
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
        rows.append({
            "Code": proj["code"], "Project": name,
            "Start": fmt_date(proj.get("start_date", "")),
            "End": fmt_date(ext if ext else ed) + (" ★" if ext else ""),
            "Budget": bstr,
            "Actuals": f"${actuals:,.2f}",
            "Variance": var_s, "% Used": pct_s,
            "_budget_personnel": cont_personnel,
            "_budget_pi": cont_pi,
            "_budget_indirect": cont_indirect,
            "_budget_fringe": cont_fringe,
            "_budget_travel": cont_travel,
            "_actual_personnel": pers,
            "_actual_pi": pi,
            "_actual_travel": trv,
            "_actual_tools": tools_actual,
        })

    if rows:
        display_cols = ["Code", "Project", "Start", "End", "Budget", "Actuals", "Variance", "% Used"]
        display_df = pd.DataFrame([{k: r[k] for k in display_cols} for r in rows])
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.subheader("Budget Breakdown")
        for r in rows:
            with st.expander(f"{r['Project']} — {r['Budget']}"):
                bdf = pd.DataFrame([
                    {"Category": "Contracted Personnel", "Amount": f"${r['_budget_personnel']:,.2f}"},
                    {"Category": "Contracted PI", "Amount": f"${r['_budget_pi']:,.2f}"},
                    {"Category": "Contracted Indirect", "Amount": f"${r['_budget_indirect']:,.2f}"},
                    {"Category": "Contracted Fringe", "Amount": f"${r['_budget_fringe']:,.2f}"},
                    {"Category": "Contracted Travel", "Amount": f"${r['_budget_travel']:,.2f}"},
                ])
                adf = pd.DataFrame([
                    {"Category": "Actual Personnel", "Amount": f"${r['_actual_personnel']:,.2f}"},
                    {"Category": "Actual PI", "Amount": f"${r['_actual_pi']:,.2f}"},
                    {"Category": "Actual Travel", "Amount": f"${r['_actual_travel']:,.2f}"},
                    {"Category": "Actual Tools", "Amount": f"${r['_actual_tools']:,.2f}"},
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
    credits = D()["student_credits"]

    with st.form("add_credit"):
        st.subheader("Assign Credits")
        cc = st.columns(4)
        name = cc[0].text_input("Student Name")
        sid = cc[1].text_input("Student ID")
        creds = cc[2].number_input("Credits", min_value=1, value=3)
        proj = cc[3].selectbox("Project", projects_sorted())
        if st.form_submit_button("Assign"):
            if name and sid:
                if any(s["student_id"] == sid for s in credits):
                    st.error(f"Student ID '{sid}' already registered.")
                else:
                    credits.append({"name": name, "student_id": sid,
                                    "credits": creds, "project": proj})
                    save()
                    st.rerun()
            else:
                st.error("Name and ID are required.")

    if credits:
        df = pd.DataFrame(credits)
        df.columns = ["Name", "Student ID", "Credits", "Project"]
        event = st.dataframe(
            df, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key="credits_table",
        )
        sel = event.selection.rows
        if sel:
            i = sel[0]
            st.caption(f"Selected: **{credits[i]['name']}** ({credits[i]['student_id']})")
            if st.button("🗑️ Remove Student"):
                rem_id = credits[i]["student_id"]
                D()["student_credits"] = [s for s in credits if s["student_id"] != rem_id]
                save()
                st.rerun()
        else:
            st.caption("Click a row above to remove a student.")


# ═════════════════════════════════════════════════════════════════════════
# STUDENT WORKERS
# ═════════════════════════════════════════════════════════════════════════
elif page == "Student Workers":
    st.title("👥 Student Workers")
    D().setdefault("student_workers", {})
    sw = D()["student_workers"]

    fy = st.selectbox("Fiscal Year for this table", fy_choices(), key="sw_fy")

    st.subheader("Load from Excel")
    up = st.file_uploader(
        f"Drag an Excel file here to load the {fy} student worker list",
        type=["xlsx", "xls"], key="sw_upload",
    )
    if up is not None:
        try:
            df_new = read_excel(up)
            st.caption(f"Preview — {len(df_new)} rows, {len(df_new.columns)} columns")
            st.dataframe(df_new, use_container_width=True, hide_index=True)
            if st.button(f"💾 Save to {fy}", type="primary"):
                sw[fy] = df_to_store(df_new)
                save()
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
            num_rows="dynamic", key=f"sw_editor_{fy}",
        )
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("💾 Save Edits"):
            sw[fy] = df_to_store(edited)
            save()
            st.toast("Saved")
            st.rerun()
        if b2.button("🗑️ Clear Table"):
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

    fc1, fc2, fc3 = st.columns([1, 1, 1])
    show_hist = fc1.checkbox("Show historical", key="inv_hist")
    proj_filter = fc2.selectbox("Filter project", ["All"] + projects_sorted(), key="inv_proj_f")
    inv_type = fc3.radio("Type", ["Invoice", "Work Order"], horizontal=True)

    # Build the current flat list before the form so the same input area can edit existing rows.
    flat = flat_invoice_rows(D(), show_hist=show_hist, proj_filter=proj_filter)

    action_mode = st.radio(
        "Action",
        ["Add New", "Edit Existing"],
        horizontal=True,
        key="invoice_action_mode",
        disabled=not bool(flat),
    )
    if not flat:
        action_mode = "Add New"

    edit_row = edit_rec = edit_inst = None
    form_type = inv_type
    if action_mode == "Edit Existing" and flat:
        edit_pick = st.selectbox(
            "Record to edit",
            range(len(flat)),
            format_func=lambda i: f"{flat[i]['type']} #{flat[i]['number']} — "
                                  f"{flat[i]['project']} — ${flat[i]['inst_amount']:,.2f}",
            key="invoice_edit_row_picker",
        )
        edit_row = flat[edit_pick]
        edit_rec = edit_row["_rec"]
        edit_inst = edit_row["_inst"]
        form_type = edit_row["type"]
        st.caption(f"Editing: {form_type} #{edit_row['number']} — {edit_row['project']}")

    with st.form("add_edit_inv"):
        st.subheader(f"{'Edit' if action_mode == 'Edit Existing' else 'Add'} {form_type}")
        ic = st.columns(3)

        default_number = edit_rec.get("number", "") if edit_rec else ""
        default_project = edit_rec.get("project", "") if edit_rec else ""
        project_options = projects_sorted()
        project_index = project_options.index(default_project) if default_project in project_options else 0

        if action_mode == "Edit Existing" and edit_inst is not None:
            default_desc = edit_inst.get("period", edit_row.get("description", ""))
        elif action_mode == "Edit Existing" and edit_rec is not None:
            default_desc = edit_rec.get("description", "")
        else:
            default_desc = ""

        num_field = ic[0].text_input("Number", value=default_number, key="invwo_number")
        proj = ic[1].selectbox("Project", project_options, index=project_index, key="invwo_project")
        desc_label = "Period / Label" if form_type == "Work Order" else "Description"
        desc = ic[2].text_input(desc_label, value=default_desc, key="invwo_description")

        if form_type == "Invoice":
            default_amt = float(edit_rec.get("amount", 0)) if edit_rec else 0.0
            default_net = edit_rec.get("net_terms", "Net 30") if edit_rec else "Net 30"
            default_due = parse_date(edit_rec.get("due_date", "")) if edit_rec else None
            default_hrs = float(edit_rec.get("hours_deducted") or 0) if edit_rec else 0.0
            default_sent = bool(edit_rec.get("sent", False)) if edit_rec else False
            default_paid = bool(edit_rec.get("paid", False)) if edit_rec else False

            net_options = ["Net 30", "Net 60", "Net 90", "Net 120"]
            net_index = net_options.index(default_net) if default_net in net_options else 0

            ic2 = st.columns(4)
            amt = ic2[0].number_input("Amount ($)", value=default_amt, step=1.0, format="%.2f", key="invoice_amount")
            net = ic2[1].selectbox("Net Terms", net_options, index=net_index, key="invoice_net_terms")
            due = ic2[2].date_input("Invoice Date", value=default_due, key="inv_due")
            hrs_ded = ic2[3].number_input("Hours deducted", value=default_hrs, step=0.5, format="%.1f", key="invoice_hours_deducted")
            ic3 = st.columns(2)
            sent = ic3[0].checkbox("Sent", value=default_sent, key="inv_sent")
            paid = ic3[1].checkbox("Paid", value=default_paid, key="inv_paid")

            submit_invoice = st.form_submit_button(
                "💾 Save Invoice Changes" if action_mode == "Edit Existing" else "💾 Save Invoice"
            )
            if submit_invoice:
                if not num_field:
                    st.error("Number is required.")
                elif action_mode == "Edit Existing" and edit_rec is not None:
                    edit_rec["number"] = num_field
                    edit_rec["project"] = proj
                    edit_rec["description"] = desc
                    edit_rec["amount"] = round(amt, 2)
                    edit_rec["net_terms"] = net
                    edit_rec["due_date"] = str(due) if due else ""
                    edit_rec["hours_deducted"] = hrs_ded if hrs_ded else ""
                    edit_rec["sent"] = sent
                    edit_rec["paid"] = paid
                    save()
                    st.success("Invoice updated!")
                    st.rerun()
                else:
                    D()["invoices"].append({
                        "type": "Invoice", "number": num_field, "project": proj,
                        "description": desc, "amount": round(amt, 2),
                        "net_terms": net, "due_date": str(due) if due else "",
                        "hours_deducted": hrs_ded if hrs_ded else "",
                        "sent": sent, "paid": paid,
                    })
                    save()
                    st.rerun()

        else:
            if action_mode == "Edit Existing" and edit_inst is not None:
                default_amt = float(edit_inst.get("amount", 0))
                default_net = edit_inst.get("net_terms", "Net 30")
                default_due = parse_date(edit_inst.get("due_date", ""))
                default_sent = bool(edit_inst.get("sent", False))
                default_paid = bool(edit_inst.get("paid", False))

                net_options = ["Net 30", "Net 60", "Net 90", "Net 120"]
                net_index = net_options.index(default_net) if default_net in net_options else 0

                ic2 = st.columns(4)
                inst_amt = ic2[0].number_input("Amount ($)", value=default_amt, step=1.0, format="%.2f", key="wo_edit_amount")
                inst_net = ic2[1].selectbox("Net Terms", net_options, index=net_index, key="wo_edit_net_terms")
                inst_due = ic2[2].date_input("Invoice Date", value=default_due, key="wo_edit_invoice_date")
                ic3 = st.columns(2)
                inst_sent = ic3[0].checkbox("Sent", value=default_sent, key="wo_sent")
                inst_paid = ic3[1].checkbox("Paid", value=default_paid, key="wo_paid")

                save_wo_edit = st.form_submit_button("💾 Save Work Order Changes")
                if save_wo_edit:
                    if not num_field:
                        st.error("Number is required.")
                    else:
                        edit_rec["number"] = num_field
                        edit_rec["project"] = proj
                        edit_rec["description"] = edit_rec.get("description", "")
                        edit_inst["period"] = desc
                        edit_inst["amount"] = round(inst_amt, 2)
                        edit_inst["net_terms"] = inst_net
                        edit_inst["due_date"] = str(inst_due) if inst_due else ""
                        edit_inst["sent"] = inst_sent
                        edit_inst["paid"] = inst_paid
                        save()
                        st.success("Work order installment updated!")
                        st.rerun()
            else:
                st.markdown("**Add installments below, then click Save Work Order.**")
                if "wo_installments" not in st.session_state:
                    st.session_state.wo_installments = []

                # Use the main Work Order "Period / Label" box above as the installment label.
                # This avoids duplicate Period widgets and makes Add Installment behave reliably.
                ic2 = st.columns(3)
                inst_amt = ic2[0].number_input("Amount ($)", value=0.0, step=1.0, format="%.2f", key="wo_new_amount")
                inst_net = ic2[1].selectbox("Net Terms", ["Net 30", "Net 60", "Net 90", "Net 120"], key="wo_new_net_terms")
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
                            "net_terms": inst_net,
                            "due_date": str(inst_due) if inst_due else "",
                            "sent": False, "paid": False,
                        })
                        st.success("Installment added. Add another installment or save the work order.")
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
                            st.warning(f"⚠️ WO total ${wo_t:,.2f} ≠ Contracted ${contracted:,.2f}")
                        st.rerun()
                    else:
                        st.error("Add at least one installment.")
                elif save_wo and not num_field:
                    st.error("Number is required.")

    # Show staged installments
    if form_type == "Work Order" and action_mode == "Add New" and st.session_state.get("wo_installments"):
        st.markdown("**Staged installments:**")
        inst_df = pd.DataFrame(st.session_state.wo_installments)
        st.dataframe(inst_df, use_container_width=True, hide_index=True)
        total = sum(i["amount"] for i in st.session_state.wo_installments)
        st.markdown(f"**Total: ${total:,.2f}**")
        if st.button("Clear installments"):
            st.session_state.wo_installments = []
            st.rerun()

    # Display table
    st.subheader("All Records")
    flat = flat_invoice_rows(D(), show_hist=show_hist, proj_filter=proj_filter)
    if flat:
        display_rows = []
        for r in flat:
            display_rows.append({
                "Type": r["type"], "Number": r["number"],
                "Project": r["project"], "Description": r["description"],
                "Inst. Amount": f"${r['inst_amount']:,.2f}",
                "WO Total": f"${r['wo_total']:,.2f}" if r["type"] == "Work Order" else "",
                "Terms": r["net_terms"],
                "Invoice Date": fmt_date(r["due_date"]),
                "Payment Due": fmt_date(r["payment_due"]),
                "Hours Deducted": r.get("hours_deducted", "") if r["type"] == "Invoice" else "",
                "Sent": "☑" if r["sent"] else "☐",
                "Paid": "☑" if r["paid"] else "☐",
            })
        event = st.dataframe(
            pd.DataFrame(display_rows), use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key="invoice_table",
        )

        # Quick actions on the selected row (click a row above)
        st.subheader("Quick Actions")
        sel = event.selection.rows
        if sel:
            r = flat[sel[0]]
            st.caption(f"Selected: {r['type']} #{r['number']} — {r['project']} — ${r['inst_amount']:,.2f}")
            qc1, qc2, qc3 = st.columns([1, 1, 1])
            tg_field = qc1.selectbox("Toggle field", ["sent", "paid"], key="inv_toggle_field")
            if qc2.button("Toggle ☐ ↔ ☑"):
                if r["_inst"] is not None:
                    r["_inst"][tg_field] = not r["_inst"][tg_field]
                else:
                    r["_rec"][tg_field] = not r["_rec"][tg_field]
                save()
                st.rerun()
            if qc3.button("🗑️ Delete Selected"):
                rec = r["_rec"]
                if r["_inst"] is not None:
                    # WO installment — remove just this installment
                    rec.get("installments", []).remove(r["_inst"])
                    # If no installments left, remove the whole WO
                    if not rec.get("installments"):
                        D()["invoices"].remove(rec)
                else:
                    # Invoice — remove the whole record
                    D()["invoices"].remove(rec)
                save()
                st.rerun()
        else:
            st.caption("Click a row above to toggle Sent/Paid or delete it.")

        # CSV export
        csv = pd.DataFrame(display_rows).to_csv(index=False)
        st.download_button("⬇️ Export CSV", csv, "invoices_wo.csv", "text/csv")


# ═════════════════════════════════════════════════════════════════════════
# HOURS
# ═════════════════════════════════════════════════════════════════════════
elif page == "Hours":
    st.title("⏱️ Hours Tracker")
    pr = D()["project_records"]
    projects = projects_sorted()

    hc1, hc2 = st.columns([2, 1])
    active = hc1.selectbox("Project", projects, key="hrs_proj")
    proj = pr[active]

    budget = float(proj.get("hours_budget", 0))
    log = proj.get("hours_log", [])
    used = sum(float(e.get("hours", 0)) for e in log)
    remaining = budget - used
    pct = (used / budget * 100) if budget > 0 else 0

    with hc2:
        new_budget = st.number_input("Annual Hours Budget", value=budget,
                                     min_value=0.0, step=1.0, format="%.1f")
        if st.button("Set Budget"):
            proj["hours_budget"] = new_budget
            save()
            st.rerun()

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Budget", f"{budget:.0f} hrs")
    mc2.metric("Used", f"{used:.0f} hrs")
    mc3.metric("Remaining", f"{remaining:.0f} hrs")
    mc4.metric("% Consumed", f"{pct:.1f}%")

    col_l, col_r = st.columns([1, 2])

    with col_l:
        with st.form("deduct_hrs"):
            st.subheader("Deduct Hours")
            hrs = st.number_input("Hours", min_value=0.1, value=1.0, step=0.5, format="%.1f")
            desc = st.text_input("Description")
            dt = st.date_input("Date", value=date.today())
            if st.form_submit_button("Deduct"):
                proj.setdefault("hours_log", []).append({
                    "date": str(dt), "hours": round(hrs, 2), "description": desc,
                })
                save()
                st.rerun()

    with col_r:
        st.subheader("Deduction Log")
        if log:
            running = budget
            log_data = []
            for e in log:
                h = float(e.get("hours", 0))
                running -= h
                log_data.append({
                    "Date": fmt_date(e.get("date", "")),
                    "Hours": f"{h:.1f}",
                    "Description": e.get("description", ""),
                    "Remaining": f"{running:.1f}",
                })
            event = st.dataframe(
                pd.DataFrame(log_data), use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="hours_log_table",
            )
            sel = event.selection.rows
            if sel:
                if st.button("🗑️ Delete Selected Entry"):
                    log.pop(sel[0])
                    save()
                    st.rerun()
            else:
                st.caption("Click an entry above to delete it.")
        else:
            st.info("No deductions yet.")


# ═════════════════════════════════════════════════════════════════════════
# TOOLS
# ═════════════════════════════════════════════════════════════════════════
elif page == "Tools":
    st.title("🛠️ Tools & Subscriptions")
    tools = D()["tools"]

    # KPIs
    total_m = total_a = 0.0
    for t in tools:
        mc, ac = calc_tool_costs(t)
        total_m += mc; total_a += ac
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Tools", len(tools))
    mc2.metric("Monthly Total", f"${total_m:,.2f}")
    mc3.metric("Annual Total", f"${total_a:,.2f}")

    # Add / Edit tool using the same input form style
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

    cycle_options = ["Monthly", "Annual", "2-Year", "One-time"]
    current_cycle = selected_tool.get("billing_cycle", "Monthly")
    cycle_index = cycle_options.index(current_cycle) if current_cycle in cycle_options else 0

    with st.form("tool_form"):
        tc = st.columns(3)
        t_name = tc[0].text_input("Tool Name", value=selected_tool.get("name", ""))
        t_vendor = tc[1].text_input("Vendor", value=selected_tool.get("vendor", ""))
        t_cost = tc[2].number_input("Cost ($)",
                                    value=float(selected_tool.get("cost", 0.0)),
                                    min_value=0.0, step=1.0, format="%.2f")
        tc2 = st.columns(4)
        t_cycle = tc2[0].selectbox("Billing Cycle", cycle_options, index=cycle_index)
        t_start = tc2[1].date_input("Start Date",
                                    value=parse_date(selected_tool.get("start_date", "")),
                                    key=f"tool_start_{tool_mode}_{edit_tool_idx}")
        t_renew = tc2[2].checkbox("Auto-renew", value=selected_tool.get("auto_renew", True))
        t_paid = tc2[3].checkbox("Paid", value=selected_tool.get("paid", False))
        t_notes = st.text_input("Notes", value=selected_tool.get("notes", ""))

        submit_label = "💾 Save New Tool" if tool_mode == "Add New" else "💾 Update Tool"
        if st.form_submit_button(submit_label):
            if t_name:
                tool_payload = {
                    "name": t_name, "vendor": t_vendor,
                    "cost": round(t_cost, 2),
                    "billing_cycle": t_cycle,
                    "start_date": str(t_start) if t_start else "",
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
                st.rerun()
            else:
                st.error("Tool name is required.")

    if tools:
        today = date.today()
        tool_data = []
        for t in tools:
            mc, ac = calc_tool_costs(t)
            renewal = calc_renewal_date(t)
            tool_data.append({
                "Tool": t.get("name", ""),
                "Vendor": t.get("vendor", ""),
                "Cost": f"${t.get('cost', 0):,.2f}",
                "Cycle": t.get("billing_cycle", ""),
                "Monthly": f"${mc:,.2f}",
                "Annual": f"${ac:,.2f}",
                "Start": fmt_date(t.get("start_date", "")),
                "Next Renewal": fmt_date(renewal) if renewal else "—",
                "Auto": "Yes" if t.get("auto_renew") else "No",
                "Paid": "☑" if t.get("paid") else "☐",
                "Notes": t.get("notes", ""),
            })
        event = st.dataframe(
            pd.DataFrame(tool_data), use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row", key="tools_table",
        )

        # Toggle paid / Delete on the selected row
        sel = event.selection.rows
        if sel:
            i = sel[0]
            st.caption(f"Selected: **{tools[i].get('name', '')}**")
            b1, b2 = st.columns(2)
            if b1.button("Toggle Paid ☐ ↔ ☑"):
                tools[i]["paid"] = not tools[i].get("paid", False)
                save()
                st.rerun()
            if b2.button("🗑️ Delete Tool"):
                tools.pop(i)
                save()
                st.rerun()
        else:
            st.caption("Click a row above to toggle Paid or delete a tool.")


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

    def metrics_for(fy):
        proj_count = 0
        budget = 0.0
        credits = 0
        for name, proj in filtered_projects("", fy):
            proj_count += 1
            # has_budget == True means a "No Budget" project, so it's excluded from budget totals
            if not proj.get("has_budget", False):
                for row in proj["lines"]:
                    budget += (line_value(row, 9) + line_value(row, 10)
                               + line_value(row, 11) + line_value(row, 12)
                               + contracted_travel_cost(row))
            credits += sum(s["credits"] for s in D()["student_credits"]
                           if s["project"] == name)
        sw_rec = D().get("student_workers", {}).get(fy)
        sw_count = len(sw_rec.get("data", [])) if sw_rec else 0
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