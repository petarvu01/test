"""PM Dashboard — Streamlit Web App."""
import streamlit as st
import pandas as pd
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


def actual_personnel_cost(row):
    return int(line_value(row, 1)) * line_value(row, 2) * line_value(row, 3)


def actual_pi_cost(row):
    return line_value(row, 4) * line_value(row, 5)


def contracted_travel_cost(row):
    # New field is row[13]. For older saved rows, copy old travel value as a safe migration.
    return line_value(row, 13, line_value(row, 8))


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
    ("📄", "Invoices / WO"),
    ("⏱️", "Hours"),
    ("🛠️", "Tools"),
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
                del pr[active]
                D()["student_credits"] = [s for s in D()["student_credits"] if s["project"] != active]
                save()
                st.rerun()
            else:
                st.error("Must keep at least one project.")

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
        st.success("Dates saved!")

    # Line items table
    st.subheader("Line Items")

    table_data = []
    for row in proj["lines"]:
        s, sr, sh = int(line_value(row, 1)), line_value(row, 2), line_value(row, 3)
        pr2, ph = line_value(row, 4), line_value(row, 5)
        table_data.append({
            "Line Item": row[0], "Students": s,
            "Stu Rate": sr, "Stu Hours": sh,
            "Stu Cost": round(s * sr * sh, 2),
            "PI Rate": pr2, "PI Hours": ph,
            "PI Cost": round(pr2 * ph, 2),
            "Actual Travel": line_value(row, 8),
            "Cont. Personnel": line_value(row, 9),
            "Cont. PI": line_value(row, 10),
            "Cont. Indirect": line_value(row, 11),
            "Cont. Fringe": line_value(row, 12),
            "Cont. Travel": contracted_travel_cost(row),
        })

    df = pd.DataFrame(table_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Edit line item
    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("➕ Add Line Item"):
            n = len(proj["lines"]) + 1
            proj["lines"].append(blank_line(f"Phase {n} - New Item"))
            save()
            st.rerun()
    with bc2:
        del_idx = st.number_input("Line # to delete", min_value=1,
                                   max_value=max(len(proj["lines"]), 1),
                                   value=1, key="del_line")
        if st.button("🗑️ Delete Line Item"):
            if len(proj["lines"]) > 1:
                proj["lines"].pop(del_idx - 1)
                save()
                st.rerun()
            else:
                st.error("Must keep at least one line item.")

    st.subheader("Edit Line Item")
    edit_idx = st.selectbox("Select line to edit",
                             range(len(proj["lines"])),
                             format_func=lambda i: proj["lines"][i][0])
    row = proj["lines"][edit_idx]

    with st.form("edit_line"):
        fc = st.columns(4)
        new_name = fc[0].text_input("Name", value=row[0])
        new_stu = fc[1].number_input("Students", value=int(line_value(row, 1)), min_value=0)
        new_sr = fc[2].number_input("Stu Rate", value=line_value(row, 2), min_value=0.0, step=1.0, format="%.2f")
        new_sh = fc[3].number_input("Stu Hours", value=line_value(row, 3), min_value=0.0, step=0.5, format="%.1f")
        fc2 = st.columns(4)
        new_pr = fc2[0].number_input("PI Rate", value=line_value(row, 4), min_value=0.0, step=1.0, format="%.2f")
        new_ph = fc2[1].number_input("PI Hours", value=line_value(row, 5), min_value=0.0, step=0.5, format="%.1f")
        new_trv = fc2[2].number_input("Actual Travel", value=line_value(row, 8), min_value=0.0, step=1.0, format="%.2f")
        new_cp = fc2[3].number_input("Cont. Personnel", value=line_value(row, 9), min_value=0.0, step=1.0, format="%.2f")
        fc3 = st.columns(4)
        new_cpi = fc3[0].number_input("Cont. PI", value=line_value(row, 10), min_value=0.0, step=1.0, format="%.2f")
        new_ci = fc3[1].number_input("Cont. Indirect", value=line_value(row, 11), min_value=0.0, step=1.0, format="%.2f")
        new_cf = fc3[2].number_input("Cont. Fringe", value=line_value(row, 12), min_value=0.0, step=1.0, format="%.2f")
        new_ct = fc3[3].number_input("Cont. Travel", value=contracted_travel_cost(row), min_value=0.0, step=1.0, format="%.2f")

        if st.form_submit_button("💾 Save Changes"):
            row[:] = [
                new_name, new_stu, new_sr, new_sh, new_pr, new_ph,
                0.0, 0.0, new_trv, new_cp, new_cpi, new_ci, new_cf, new_ct
            ]
            save()
            st.success(f"Updated '{new_name}'!")
            st.rerun()

    # Notes
    st.subheader("Notes")
    notes = st.text_area("Project notes", value=proj.get("notes", ""), key="proj_notes")
    if st.button("💾 Save Notes"):
        proj["notes"] = notes
        save()
        st.success("Notes saved!")

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
    for name in sorted(pr.keys()):
        proj = pr[name]
        if search and search.lower() not in name.lower() and search.lower() not in proj["code"].lower():
            continue
        if not project_in_fy(proj, fy):
            continue
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
    if rows:
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
    for name in sorted(pr.keys()):
        proj = pr[name]
        if search and search.lower() not in name.lower() and search.lower() not in proj["code"].lower():
            continue
        if not project_in_fy(proj, fy):
            continue
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
        st.dataframe(df, use_container_width=True, hide_index=True)

        del_id = st.selectbox("Remove student by ID",
                               [s["student_id"] for s in credits])
        if st.button("🗑️ Remove"):
            D()["student_credits"] = [s for s in credits if s["student_id"] != del_id]
            save()
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════
# INVOICES / WO
# ═════════════════════════════════════════════════════════════════════════
elif page == "Invoices / WO":
    st.title("📄 Invoices & Work Orders")

    fc1, fc2, fc3 = st.columns([1, 1, 1])
    show_hist = fc1.checkbox("Show historical", key="inv_hist")
    proj_filter = fc2.selectbox("Filter project", ["All"] + projects_sorted(), key="inv_proj_f")
    inv_type = fc3.radio("Type", ["Invoice", "Work Order"], horizontal=True)

    with st.form("add_inv"):
        st.subheader(f"Add {inv_type}")
        ic = st.columns(3)
        num = ic[0].text_input("Number")
        proj = ic[1].selectbox("Project", projects_sorted(), key="inv_proj")
        desc = ic[2].text_input("Description")

        if inv_type == "Invoice":
            ic2 = st.columns(4)
            amt = ic2[0].number_input("Amount ($)", value=0.0, step=1.0, format="%.2f")
            net = ic2[1].selectbox("Net Terms", ["Net 30", "Net 60", "Net 90", "Net 120"])
            due = ic2[2].date_input("Due Date", value=None, key="inv_due")
            hrs_ded = ic2[3].number_input("Hours deducted", value=0.0, step=0.5, format="%.1f")
            ic3 = st.columns(2)
            sent = ic3[0].checkbox("Sent", key="inv_sent")
            paid = ic3[1].checkbox("Paid", key="inv_paid")

            if st.form_submit_button("Save Invoice"):
                if num:
                    D()["invoices"].append({
                        "type": "Invoice", "number": num, "project": proj,
                        "description": desc, "amount": round(amt, 2),
                        "net_terms": net, "due_date": str(due) if due else "",
                        "hours_deducted": hrs_ded if hrs_ded else "",
                        "sent": sent, "paid": paid,
                    })
                    save()
                    # Check WO vs contracted
                    st.rerun()
                else:
                    st.error("Number is required.")
        else:
            st.markdown("**Add installments below, then click Save Work Order.**")
            if "wo_installments" not in st.session_state:
                st.session_state.wo_installments = []
            ic2 = st.columns(4)
            period = ic2[0].text_input("Period / Label")
            inst_amt = ic2[1].number_input("Amount ($)", value=0.0, step=1.0, format="%.2f", key="inst_amt")
            inst_net = ic2[2].selectbox("Net Terms", ["Net 30", "Net 60", "Net 90", "Net 120"], key="inst_net")
            inst_due = ic2[3].date_input("Due Date", value=None, key="inst_due")
            add_inst = st.form_submit_button("➕ Add Installment")
            save_wo = st.form_submit_button("💾 Save Work Order")

        if inv_type == "Work Order":
            if add_inst and period:
                st.session_state.wo_installments.append({
                    "period": period, "amount": round(inst_amt, 2),
                    "net_terms": inst_net,
                    "due_date": str(inst_due) if inst_due else "",
                    "sent": False, "paid": False,
                })
                st.rerun()
            if save_wo and num:
                if st.session_state.wo_installments:
                    D()["invoices"].append({
                        "type": "Work Order", "number": num, "project": proj,
                        "description": desc,
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

    # Show staged installments
    if inv_type == "Work Order" and st.session_state.get("wo_installments"):
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
                "Sent": "☑" if r["sent"] else "☐",
                "Paid": "☑" if r["paid"] else "☐",
            })
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

        # Toggle sent/paid and Delete
        st.subheader("Quick Actions")
        tg_c = st.columns(4)
        tg_idx = tg_c[0].number_input("Row # (1-based)", min_value=1,
                                        max_value=len(flat), value=1)
        tg_field = tg_c[1].selectbox("Toggle", ["sent", "paid"])
        if tg_c[2].button("Toggle ☐ ↔ ☑"):
            r = flat[tg_idx - 1]
            if r["_inst"] is not None:
                r["_inst"][tg_field] = not r["_inst"][tg_field]
            else:
                r["_rec"][tg_field] = not r["_rec"][tg_field]
            save()
            st.rerun()
        if tg_c[3].button("🗑️ Delete Row"):
            r = flat[tg_idx - 1]
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
            st.dataframe(pd.DataFrame(log_data), use_container_width=True, hide_index=True)

            del_idx = st.number_input("Entry # to delete (1-based)",
                                       min_value=1, max_value=len(log), value=1)
            if st.button("🗑️ Delete Entry"):
                log.pop(del_idx - 1)
                save()
                st.rerun()
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

    with st.form("add_tool"):
        st.subheader("Add / Edit Tool")
        tc = st.columns(3)
        t_name = tc[0].text_input("Tool Name")
        t_vendor = tc[1].text_input("Vendor")
        t_cost = tc[2].number_input("Cost ($)", value=0.0, step=1.0, format="%.2f")
        tc2 = st.columns(4)
        t_cycle = tc2[0].selectbox("Billing Cycle",
                                    ["Monthly", "Annual", "2-Year", "One-time"])
        t_start = tc2[1].date_input("Start Date", value=None, key="tool_start")
        t_renew = tc2[2].checkbox("Auto-renew", value=True)
        t_paid = tc2[3].checkbox("Paid")
        t_notes = st.text_input("Notes")

        if st.form_submit_button("Save Tool"):
            if t_name:
                tools.append({
                    "name": t_name, "vendor": t_vendor,
                    "cost": round(t_cost, 2),
                    "billing_cycle": t_cycle,
                    "start_date": str(t_start) if t_start else "",
                    "auto_renew": t_renew, "paid": t_paid,
                    "notes": t_notes,
                })
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
        st.dataframe(pd.DataFrame(tool_data), use_container_width=True, hide_index=True)

        # Toggle paid / Delete
        tc1, tc2, tc3 = st.columns(3)
        t_idx = tc1.number_input("Tool # (1-based)", min_value=1,
                                  max_value=len(tools), value=1)
        if tc2.button("Toggle Paid ☐ ↔ ☑"):
            tools[t_idx - 1]["paid"] = not tools[t_idx - 1].get("paid", False)
            save()
            st.rerun()
        if tc3.button("🗑️ Delete Tool"):
            tools.pop(t_idx - 1)
            save()
            st.rerun()
