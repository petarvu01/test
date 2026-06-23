"""Shared helper functions — identical to the desktop version."""
from datetime import date, datetime, timedelta
from calendar import monthrange


def parse_date(s: str):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def fmt_date(s: str) -> str:
    d = parse_date(s)
    return d.strftime("%b %d, %Y") if d else "—"


def fy_label(year: int) -> str:
    end2 = str(year + 1)[2:]
    return f"FY {year}-{end2} (FY{end2})"


def date_to_fy(d: date) -> int:
    return d.year if d.month >= 6 else d.year - 1


def fy_range(label: str):
    try:
        y = int(label.split()[1].split("-")[0])
        return date(y, 6, 1), date(y + 1, 5, 31)
    except Exception:
        return None, None


def calc_payment_due(date_str: str, net_terms: str) -> str:
    d = parse_date(date_str)
    if not d:
        return ""
    days_map = {"Net 30": 30, "Net 60": 60, "Net 90": 90, "Net 120": 120}
    days = days_map.get(net_terms, 0)
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def calc_renewal_date(tool: dict) -> str:
    d = parse_date(tool.get("start_date", ""))
    if not d:
        return ""
    cycle = tool.get("billing_cycle", "Monthly")
    if cycle == "One-time":
        return ""
    step = {"Monthly": 1, "Annual": 12, "2-Year": 24}.get(cycle, 1)
    today = date.today()
    renew = d
    while renew <= today:
        m = renew.month + step
        y = renew.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        max_day = monthrange(y, m)[1]
        renew = date(y, m, min(d.day, max_day))
    return renew.strftime("%Y-%m-%d")


def calc_tool_costs(tool: dict) -> tuple:
    """Return (monthly_cost, annual_cost) under the two-cycle model:
    Monthly tools recur monthly (annual = cost*12); One-time tools are paid
    once a year (no monthly component, annual = cost)."""
    cost = float(tool.get("cost", 0))
    cycle = tool.get("billing_cycle", "Monthly")
    if cycle == "Monthly":
        return cost, cost * 12
    # One-time / once-a-year
    return 0.0, cost