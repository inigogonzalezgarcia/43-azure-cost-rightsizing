"""Outputs: report.html (verdict, tiles, showback by cost centre against budget, the savings identified, right-sizing, waste, commitments, unit
cost and all findings), savings-pack.md (the pack for the monthly FinOps review), and the CSVs: rightsizing, waste, commitments, showback,
actions and findings."""
from __future__ import annotations

import csv
import html
from collections import defaultdict
from pathlib import Path

from .checks import (AREAS, CODES, CRITICAL, INFO, ORDER, UNALLOCATED, VERDICT, WARNING, Finding, _n, area_status, money, month_label, overall,
                     pct, summary)
from .model import FILES, Data

e = html.escape
CODE_LABEL = {"RS-01": "Resize one size down", "RS-02": "Idle VMs", "WST-01": "Stopped, not deallocated", "WST-02": "Non-production always on",
              "WST-03": "Unattached disks", "WST-04": "Unassociated public IPs", "COM-03": "Commit steady VMs"}


# ---------------------------------------------------------------- csv

def _w(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _f(x, k=2) -> str:
    return "" if x is None else f"{x:.{k}f}"


def write_findings(findings: list[Finding], path: Path) -> None:
    _w(path, ["severity", "area", "code", "subject", "detail", "next_step", "owner", "monthly_saving", "items"],
       [[f.severity, f.area, f.code, f.subject, f.detail, f.fix, f.owner, _f(f.saving), "; ".join(f.items)] for f in findings])


def write_actions(findings: list[Finding], path: Path) -> None:
    _w(path, ["ref", "action", "next_step", "owner", "severity", "monthly_saving", "due", "decision", "done"],
       [[f.code, f.subject, f.fix, f.owner, f.severity, _f(f.saving), "", "", ""] for f in findings if f.severity != INFO])


def vm_recommendation(row: dict, rv: dict) -> tuple[str, str]:
    codes = [i.code for i in row["issues"]]
    rs = {r["vm"].id: r for r in rv["rightsizing"]}
    steady = {r["vm"].id for r in rv["coverage"].get("steady", [])}
    vm = row["vm"]
    if "WST-01" in codes:
        return "deallocate", ""
    if "RS-02" in codes:
        return "idle: deallocate or delete", ""
    if "RS-01" in codes:
        return "resize", rs[vm.id]["target"]
    if "WST-02" in codes:
        return "schedule", ""
    if vm.id in steady:
        return "commit", ""
    if not vm.running:
        return "not running", ""
    if row["metric"] is None or row["metric"].cpu_p95 is None:
        return "no metrics", ""
    return "keep", ""


def write_rightsizing(d: Data, rv: dict, path: Path) -> None:
    rows = []
    for r in rv["vms"]:
        vm, m = r["vm"], r["metric"]
        rec, target = vm_recommendation(r, rv)
        saving = sum(i.saving for i in r["issues"])
        rows.append([vm.subscription, vm.rg, vm.name, vm.size, target, rec, vm.vcpus if vm.vcpus is not None else "", _f(vm.memory_gb, 0), vm.os,
                     vm.power, r["env"], r["cc"], r["owner"], _f(m.cpu_avg if m else None, 1), _f(m.cpu_p95 if m else None, 1), _f(r["mem_used"], 3),
                     _f(r["on_share"], 3), _f(r["cost"]), _f(r["run_rate"]), _f(saving), _f(r["committed_share"], 3),
                     "; ".join(dict.fromkeys(i.code for i in r["issues"])), vm.id])
    _w(path, ["subscription", "resource_group", "vm", "size", "target_size", "recommendation", "vcpus", "memory_gb", "os", "power_state", "env",
              "cost_centre", "owner", "cpu_avg_30d", "cpu_p95_30d", "memory_used_peak", "running_share_30d", "cost_month", "run_rate_month",
              "saving_month", "committed_share", "codes", "resource_id"], rows)


def write_waste(d: Data, rv: dict, path: Path) -> None:
    rows = sorted(rv["waste"], key=lambda w: (w["code"], -w["saving"], w["name"]))
    _w(path, ["code", "kind", "subscription", "resource_group", "name", "detail", "since", "cost_centre", "owner", "cost_month", "saving_month", "action",
              "resource_id"],
       [[w["code"], w["kind"], w["subscription"], w["rg"], w["name"], w["detail"], w["since"], w["cc"], w["owner"], _f(w["cost"]), _f(w["saving"]),
         w["action"], w["id"]] for w in rows])


def write_commitments(d: Data, rv: dict, path: Path) -> None:
    rows = []
    for r in rv["commitments"]:
        c = r["c"]
        rows.append([c.name, c.kind, c.sku, c.term, c.scope, "" if c.quantity is None else f"{c.quantity:g}", "" if c.commitment is None else
                     f"{c.commitment:g}", _f(c.util_1d, 3), _f(c.util_7d, 3), _f(r["util"], 3), _f(r["used"]), _f(r["unused"]), _f(r["savings"]),
                     r["resources"], c.expiry.isoformat() if c.expiry else "", "" if r["days"] is None else r["days"], "on" if c.renew else "off",
                     r["decision"], "; ".join(dict.fromkeys(i.code for i in r["issues"])), c.id])
    _w(path, ["name", "type", "sku", "term", "scope", "quantity", "hourly_commitment", "utilisation_1d", "utilisation_7d", "utilisation_30d",
              "used_cost_month", "unused_cost_month", "savings_vs_list_month", "resources_covered", "expiry", "days_to_expiry", "auto_renew",
              "decision", "codes", "id"], rows)


def cc_savings(rv: dict) -> dict:
    out = defaultdict(float)
    for i in rv["issues"]:
        if i.saving and i.code != "COM-03":
            out[i.cost_centre or UNALLOCATED] += i.saving
    return out


def write_showback(d: Data, rv: dict, path: Path) -> None:
    units = {u["cc"]: u for u in rv["units"]}
    sv = cc_savings(rv)
    rows = []
    for r in rv["showback"]:
        u = units.get(r["cc"], {})
        rows.append([r["cc"], r["name"], r["owner"], _f(r["spend"]), _f(r["share"], 4), _f(r["budget"]), _f(r["ratio"], 3), _f(r["mtd"]),
                     _f(r["forecast"]), _f(r["budget_next"]), _f(r["fc_ratio"], 3), u.get("unit", ""), _f(u.get("units"), 0), _f(u.get("unit_cost"), 4),
                     _f(u.get("prev_unit_cost"), 4), _f(u.get("change"), 3), _f(sv.get(r["cc"], 0))])
    _w(path, ["cost_centre", "name", "owner", "effective_cost", "share", "budget", "budget_used", "month_to_date_current", "forecast_current",
              "budget_current", "forecast_vs_budget", "unit", "units", "unit_cost", "unit_cost_previous", "unit_cost_change", "identified_saving_month"],
       rows)


# ---------------------------------------------------------------- the pack

TODO = "**[TO COMPLETE]**"


def _md(x) -> str:
    return str(x).replace("|", "/")


def savings_rows(sm: dict) -> list[tuple]:
    return [(c, CODE_LABEL[c], sm["by_code"].get(c, 0.0)) for c in CODE_LABEL if sm["by_code"].get(c)]


def pack_md(d: Data, rv: dict, findings: list[Finding], title: str) -> str:
    s, sm, st = d.settings, summary(d, rv), area_status(findings)
    cur = sm["currency"]
    M = lambda x, k=0: money(x, cur, k)  # noqa: E731
    mk, ml = d.month, month_label(d.month)
    t = rv["totals"]
    out = [f"# {title}", "",
           f"> Generated by azcostreview on {d.as_of:%a %d %b %Y} from the exports listed under *Scope and evidence*. Read-only: nothing was "
           f"changed in Azure. Every saving is an estimate per month; sections marked {TODO} are for the meeting.", "",
           f"**Overall: {overall(findings).upper()}**", "", "| Area | Status | To act on |", "|---|---|---|"]
    for a in AREAS:
        n = sum(f.area == a and f.severity != INFO for f in findings)
        out.append(f"| {a} | {'ON TRACK' if st[a] == 'ok' else VERDICT[st[a]].upper()} | {n} |")
    out += ["", "## Headlines", "",
            f"- {ml}: **{M(sm['spend'])}** effective (amortised) cost, {M(sm['billed'])} billed, {M(sm['list'])} at list price.",
            f"- Commitments saved {M(sm['gross_savings'])} against list price, less {M(sm['unused'])} of unused commitment: **{M(sm['net_savings'])} "
            f"net**. VM commitment coverage {pct(sm['coverage'])}.",
            f"- Savings identified: **{M(sm['identified'])} a month** ({pct(sm['identified'] / sm['spend'] if sm['spend'] else None)} of the month): "
            f"right-sizing {M(sm['rightsizing'])}, waste {M(sm['waste'])}, commitments for steady VMs {M(sm['commitment_saving'])}.",
            f"- {sm['resize']} VMs to resize, {sm['idle']} idle, {sm['stopped']} stopped but not deallocated, {sm['always_on']} non-production VMs on "
            f"around the clock, {sm['disks']} unattached disks, {sm['ips']} unassociated public IPs.",
            f"- {_n(sm['low_util'], 'commitment')} under {pct(s.util_warning)} utilisation; {sm['expiring']} expiring in the next {s.expiry_days} days.",
            f"- Showback: {pct(sm['gap'], 1)} of the month ({M(sm['gap_amount'])}) has no cost centre; {_n(sm['over_budget'], 'cost centre')} over budget, "
            f"{sm['forecast_over']} forecast over budget in {month_label(d.current)}.",
            "", "## Scope and evidence", "", f"- Organisation: {s.organisation}", f"- Month reviewed: {ml} (BillingPeriodStart {mk}-01)",
            f"- Reference date: {d.as_of:%a %d %b %Y %H:%M}; forecast for {month_label(d.current)} from the days before it",
            f"- Currency: {cur}; cost rows in the month: {t['rows']:,}", "", "| Source | File | Records |", "|---|---|---|"]
    out += [f"| {FILES.get(k, k)} | `{k}` | {v:,} |" for k, v in d.sources.items()]
    # 1 spend
    out += ["", "## 1. Where the money went", "", "| Service | Effective cost | Share |", "|---|---|---|"]
    for k, v in t["by_service"].most_common(8):
        out.append(f"| {_md(k)} | {M(v)} | {pct(v / t['effective'] if t['effective'] else None, 1)} |")
    out += ["", "| Subscription | Effective cost |", "|---|---|"]
    out += [f"| {_md(k)} | {M(v)} |" for k, v in sorted(t["by_subscription"].items(), key=lambda x: -x[1])]
    # 2 savings
    out += ["", "## 2. Savings identified", "", "| What | Code | Monthly saving (estimate) |", "|---|---|---|"]
    out += [f"| {lbl} | {c} | {M(v)} |" for c, lbl, v in savings_rows(sm)]
    out += [f"| **Total** | | **{M(sm['identified'])}** |", "", "### Resize and idle", "",
            "| VM | Size | Proposal | CPU p95 | Peak memory | Saving | Owner | Decision |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rv["rightsizing"], key=lambda x: -x["saving"]):
        prop = f"-> {r['target']}" if r["target"] else "idle"
        out.append(f"| {r['vm'].name} ({r['vm'].subscription}) | {r['vm'].size} | {prop}{' (committed)' if r['covered'] else ''} | {r['cpu_p95']:.0f}% | "
                   f"{pct(r['mem_used'])} | {M(r['saving'])} | {r['owner'] or '–'} | {TODO} |")
    # 3 waste
    out += ["", "## 3. Waste", "", "| Kind | Resource | Detail | Saving | Owner |", "|---|---|---|---|---|"]
    for w in sorted(rv["waste"], key=lambda w: (w["code"], -w["saving"], w["name"])):
        out.append(f"| {w['kind']} | {w['name']} ({w['rg']}) | {_md(w['detail'])} | {M(w['saving'])} | {w['owner'] or '(no owner tag)'} |")
    # 4 commitments
    out += ["", "## 4. Commitments", "", "| Commitment | Type | Utilisation 30 d | Unused this month | Saved vs list | Expires | Auto-renew | Decision |",
            "|---|---|---|---|---|---|---|---|"]
    for r in rv["commitments"]:
        c = r["c"]
        exp = f"{c.expiry:%d %b %Y} ({r['days']} d)" if c.expiry else "–"
        dec = f"{r['decision']} {TODO}" if r["decision"] and any(i.severity != INFO for i in r["issues"]) else (r["decision"] or "–")
        out.append(f"| {c.name} ({c.sku}{f' x{c.quantity:g}' if c.quantity else ''}) | {c.kind} {c.term} | {pct(r['util'], 1)} | {M(r['unused'])} | "
                   f"{M(r['savings'])} | {exp} | {'on' if c.renew else 'off'} | {_md(dec)} |")
    cov = rv["coverage"]
    out += ["", f"VM coverage: {pct(cov['coverage'])} of the VM list cost is covered by a reservation or a savings plan (target "
            f"{pct(s.coverage_target)}). {len(cov['steady'])} production VMs ran at least {pct(s.steady_share)} of the hours on pay-as-you-go, "
            f"{M(cov['on_demand'])} a month: " + (", ".join(f"{k} x{v}" for k, v in cov["sizes"].most_common()) or "none") + "."]
    # 5 showback
    out += ["", "## 5. Showback and budgets", "",
            f"| Cost centre | Owner | {ml} | Share | Budget | Used | {month_label(d.current)} forecast | Budget | |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rv["showback"]:
        flag = "over" if (r["ratio"] or 0) > 1 else ("forecast over" if (r["fc_ratio"] or 0) > 1 else "")
        out.append(f"| {_md((r['cc'] + ' ' + r['name']).strip())} | {_md(r['owner']) or '–'} | {M(r['spend'])} | {pct(r['share'], 1)} | {M(r['budget'])} | "
                   f"{pct(r['ratio'])} | {M(r['forecast'])} | {M(r['budget_next'])} | {flag} |")
    if rv["gap"]["where"]:
        out += ["", "Unallocated cost, largest first:", ""]
        out += [f"- {_md(sub)} / {_md(rg)}: {M(v)}" for (sub, rg), v in rv["gap"]["where"][:8]]
    # 6 unit cost
    out += ["", "## 6. Unit cost", "", "| Cost centre | Unit | Units | Cost per unit | Previous month | Change |", "|---|---|---|---|---|---|"]
    for u in rv["units"]:
        out.append(f"| {u['cc']} {_md(u['name'])} | {u['unit']} | {u['units']:,.0f} | {M(u['unit_cost'], 3)} | {M(u['prev_unit_cost'], 3)} | "
                   f"{pct(u['change'], 1) if u['change'] is not None else '–'} |")
    if not rv["units"]:
        out.append("| – | No units.csv | | | | |")
    out += ["", "## Findings", ""]
    for f in findings:
        if f.severity == INFO:
            continue
        out += [f"### {f.code} · {f.subject} ({f.severity})", "", _md(f.detail), "", f"Next step: {f.fix} Owner: {f.owner}.", ""]
    out += ["## Actions", "", "| Ref | Action | Owner | Saving | Due | Decision |", "|---|---|---|---|---|---|"]
    out += [f"| {f.code} | {_md(f.subject)} | {f.owner} | {M(f.saving) if f.saving else '–'} | {TODO if f.severity == CRITICAL else ''} | |"
            for f in findings if f.severity != INFO]
    out += ["", "## Notes", "",
            "- Effective cost is the amortised cost (FOCUS `EffectiveCost`): reservation and savings plan purchases are spread over the usage they "
            "cover, and the unused part shows as rows with `CommitmentDiscountStatus` Unused. Billed cost (`BilledCost`) is what the invoice shows.",
            f"- Monthly savings are estimates at the run rate of the last seven days of {ml}. Resizing to half the vCPUs in the same family and "
            "generation is taken as half the compute cost; an office-hours schedule as "
            f"{s.office_hours_per_week:g} of 168 hours; a commitment as {pct(s.commitment_discount)} off the VM cost (an upper estimate: Windows and "
            "SQL Server licences are not discounted). Check them against Azure Advisor and the price sheet before deciding.",
            f"- A VM is a resize candidate when its CPU p95 over 30 days is under {s.rs_cpu_p95:g}% and its peak memory use (from the 5th percentile "
            f"of Available Memory Bytes) under {pct(s.rs_mem_used)}, so both stay under twice that after the resize.",
            "- A resize of a VM covered by a reservation saves money only once the freed capacity covers other VMs or the reservation is exchanged.",
            f"- Decisions of the meeting: {TODO}", "",
            "## Sign-off", "", f"- Prepared by (FinOps): {TODO}", f"- Reviewed with (cost centre owners, cloud platform): {TODO}", f"- Date: {TODO}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- html

CSS = """
:root{--bg:#f6f7f8;--card:#fff;--ink:#1b2128;--muted:#66717c;--line:#e2e5e9;--ok:#1e7a4c;--warn:#a46200;--bad:#b42318;--info:#0b6bcb;--bar:#3d6fb6;--bar2:#c9d6ea}
@media (prefers-color-scheme:dark){:root{--bg:#13171b;--card:#1b2127;--ink:#e7eaee;--muted:#9aa5b0;--line:#2b333b;--ok:#4cc38a;--warn:#f0a83a;--bad:#f2685c;--info:#5aa9f0;--bar:#6f9be0;--bar2:#2e3d55}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:28px 18px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 12px}h3{font-size:15px;margin:18px 0 8px}
.muted{color:var(--muted)}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.tile b{display:block;font-size:24px}
.overall{display:inline-block;margin-top:14px;padding:6px 14px;border-radius:99px;font-weight:700;border:2px solid currentColor}
.areas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.area{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--ok);border-radius:10px;padding:10px 14px}
.area.critical{border-left-color:var(--bad)}.area.warning{border-left-color:var(--warn)}
.area .lbl{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:4px}.area .cnt{display:block;font-size:13px;color:var(--muted);margin-top:4px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}.wrap{overflow-x:auto}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-block;white-space:nowrap;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid currentColor}
.critical{color:var(--bad)}.warning{color:var(--warn)}.info{color:var(--info)}.ok{color:var(--ok)}
.area.critical,.area.warning,.area.ok{color:inherit}
.pill.on-track{color:var(--ok)}.pill.attention{color:var(--warn)}.pill.action-needed{color:var(--bad)}
td.subj{min-width:190px}td.nw{white-space:nowrap}td.fix{font-size:13px;color:var(--muted);min-width:200px;max-width:380px}td.sm{font-size:13px}
.brk{overflow-wrap:anywhere;word-break:break-word}
.bar{position:relative;height:14px;min-width:140px;background:var(--bar2);border-radius:3px}.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--bar);border-radius:3px}
.bar i.over{background:var(--bad)}.bar s{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink)}
footer{margin-top:40px;font-size:13px}
"""


_COVERED = "<br><span class='muted'>covered by a commitment</span>"


def _pill(cls: str, text: str) -> str:
    return f"<span class='pill {cls}'>{e(text)}</span>"


def _vpill(v: str) -> str:
    return _pill(v.replace(" ", "-"), v.upper())


def _table(head: list[str], rows: list[str]) -> str:
    return "<div class='wrap'><table><tr>" + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr>" + "".join(rows) + "</table></div>"


def _sect(h: list, n: int, title: str, area: str | None, st: dict, note: str) -> None:
    pill = _vpill("on track" if st[area] == "ok" else VERDICT[st[area]]) if area else ""
    h.append(f"<h2>{n}. {e(title)} {pill}</h2><p class='muted'>{e(note)}</p>")


def _area_table(findings: list[Finding], prefix: str) -> str:
    xs = [f for f in findings if f.code.startswith(prefix)]
    if not xs:
        return "<p class='muted'>Nothing to report.</p>"
    return _table(["Severity", "Finding", "Detail", "Next step"],
                  [f"<tr><td>{_pill(f.severity, f.severity.upper())}</td><td class='subj'><b>{e(f.code)}</b> {e(f.subject)}</td>"
                   f"<td class='sm brk'>{e(f.detail)}</td><td class='fix'>{e(f.fix)}</td></tr>" for f in xs])


def _bar(value: float, scale: float, mark: float | None = None, over: bool = False) -> str:
    w = 0 if not scale else min(100.0, value / scale * 100)
    m = "" if mark is None or not scale else f"<s style='left:{min(100.0, mark / scale * 100):.1f}%'></s>"
    return f"<div class='bar'><i class='{'over' if over else ''}' style='width:{w:.1f}%'></i>{m}</div>"


def render_html(d: Data, rv: dict, findings: list[Finding], title: str) -> str:
    s, sm, st = d.settings, summary(d, rv), area_status(findings)
    cur = sm["currency"]
    M = lambda x, k=0: money(x, cur, k)  # noqa: E731
    ov, ml = overall(findings), month_label(d.month)
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>",
         f"<h1>{e(title)}</h1><p class='muted'>{e(ml)} · {e(s.organisation)} · as of {d.as_of:%a %d %b %Y} · amounts in {e(cur)}, savings per month "
         "(estimates)</p>",
         f"<span class='overall {'critical' if ov == 'action needed' else 'warning' if ov == 'attention' else 'ok'}'>Overall: {e(ov.upper())}</span>"
         "<div class='tiles'>"]
    tiles = [("Spend", M(sm["spend"]), f"effective · billed {M(sm['billed'])}"),
             ("Commitment savings", M(sm["net_savings"]), f"net of {M(sm['unused'])} unused"),
             ("Savings identified", M(sm["identified"]), f"a month · {pct(sm['identified'] / sm['spend'] if sm['spend'] else None)} of spend"),
             ("VM coverage", pct(sm["coverage"]), f"target {pct(s.coverage_target)}"),
             ("Resize or idle", str(sm["resize"] + sm["idle"]), f"VMs · {M(sm['rightsizing'])} a month"),
             ("Waste", M(sm["waste"]), f"{sm['stopped']} stopped VMs, {sm['always_on']} always-on, {sm['disks']} disks, {sm['ips']} IPs"),
             ("Commitments", str(sm["commitments"]), f"{sm['low_util']} under {pct(s.util_warning)} · {sm['expiring']} expiring"),
             ("Unallocated", pct(sm["gap"], 1), f"{M(sm['gap_amount'])} with no cost centre"),
             ("Budgets", f"{sm['over_budget']} over", f"of {sm['cost_centres']} cost centres · {sm['forecast_over']} forecast over")]
    h += [f"<div class='tile'><span class='muted'>{e(x)}</span><b>{e(y)}</b><span class='muted'>{e(z)}</span></div>" for x, y, z in tiles]
    h.append("</div><h2>Status by area</h2><div class='areas'>")
    for ar in AREAS:
        n = sum(f.area == ar and f.severity != INFO for f in findings)
        h.append(f"<div class='area {st[ar]}'><span class='lbl'>{e(ar)}</span>{_pill(st[ar].replace('ok', 'on-track'), 'ON TRACK' if st[ar] == 'ok' else VERDICT[st[ar]].upper())}"
                 f"<span class='cnt'>{n} to act on</span></div>")
    h.append("</div>")
    crit = [f for f in findings if f.severity == CRITICAL]
    if crit:
        h.append("<h2>Fix first</h2>")
        h.append(_table(["Subject", "What", "Next step", "Owner"],
                        [f"<tr><td class='subj'><b>{e(f.subject)}</b><br><span class='muted'>{e(f.area)} · {e(f.code)}</span></td>"
                         f"<td class='sm brk'>{e(f.detail)}</td><td class='fix'>{e(f.fix)}</td><td class='nw'>{e(f.owner)}</td></tr>" for f in crit]))
    # 1 showback
    _sect(h, 1, "Showback by cost centre", "Showback", st,
          f"Effective cost of {ml} by the cost centre tag, against the budget (the mark), and the {month_label(d.current)} forecast. showback.csv "
          "has every number.")
    scale = max([r["spend"] for r in rv["showback"]] + [r["budget"] or 0 for r in rv["showback"]] + [1])
    sv = cc_savings(rv)
    h.append(_table(["Cost centre", "Spend", "Against budget", "Budget", "Used", "Forecast", "Identified saving"],
                    [f"<tr><td class='sm'><b>{e(r['cc'])}</b> {e(r['name'])}<br><span class='muted'>{e(r['owner'])}</span></td>"
                     f"<td class='num'>{M(r['spend'])}<br><span class='muted'>{pct(r['share'], 1)}</span></td>"
                     f"<td>{_bar(r['spend'], scale, r['budget'], (r['ratio'] or 0) > 1)}</td><td class='num'>{M(r['budget'])}</td>"
                     f"<td class='num {'critical' if (r['ratio'] or 0) > s.budget_critical else 'warning' if (r['ratio'] or 0) > 1 else ''}'>"
                     f"{pct(r['ratio'])}</td><td class='num {'warning' if (r['fc_ratio'] or 0) > 1 else ''}'>{M(r['forecast'])}<br>"
                     f"<span class='muted'>{pct(r['fc_ratio'])}</span></td><td class='num'>{M(sv.get(r['cc'], 0))}</td></tr>" for r in rv["showback"]]))
    h.append("<p></p>" + _area_table(findings, "SHB"))
    if rv["units"]:
        h.append("<h3>Unit cost</h3>")
        h.append(_table(["Cost centre", "Unit", "Units", "Cost per unit", "Previous month", "Change"],
                        [f"<tr><td class='sm'><b>{e(u['cc'])}</b> {e(u['name'])}</td><td>{e(u['unit'])}</td><td class='num'>{u['units']:,.0f}</td>"
                         f"<td class='num'>{M(u['unit_cost'], 3)}</td><td class='num'>{M(u['prev_unit_cost'], 3)}</td><td class='num "
                         f"{'warning' if (u['change'] or 0) > s.unit_cost_warning else ''}'>{pct(u['change'], 1)}</td></tr>" for u in rv["units"]]))
    # 2 savings
    h.append(f"<h2>2. Savings identified</h2><p class='muted'>Estimates per month at the run rate of the last seven days of {e(ml)}; how each one "
             "is worked out is in the notes of savings-pack.md.</p>")
    rows = savings_rows(sm)
    top = max([v for _, _, v in rows] + [1])
    h.append(_table(["What", "Code", "Monthly saving", ""],
                    [f"<tr><td>{e(lbl)}</td><td class='nw'>{e(c)}</td><td class='num'>{M(v)}</td><td>{_bar(v, top)}</td></tr>" for c, lbl, v in rows]
                    + [f"<tr><td><b>Total</b></td><td></td><td class='num'><b>{M(sm['identified'])}</b></td><td></td></tr>"]))
    # 3 right-sizing
    _sect(h, 3, "Right-sizing", "Right-sizing", st,
          f"Running VMs with 30 days of Azure Monitor metrics: CPU p95 under {s.rs_cpu_p95:g}% and peak memory use under {pct(s.rs_mem_used)} go one "
          f"size down in the same family; CPU p95 under {s.idle_cpu_p95:g}% is idle. rightsizing.csv has every VM with its recommendation.")
    h.append(_table(["VM", "Size", "Proposal", "CPU p95", "Peak memory", "After", "Saving", "Owner"],
                    [f"<tr><td class='sm'><b>{e(r['vm'].name)}</b><br><span class='muted'>{e(r['vm'].subscription)} · {e(r['env'])}</span></td>"
                     f"<td class='nw sm'>{e(r['vm'].size)}</td><td class='sm'>{e(r['target'] or 'idle: deallocate or delete')}"
                     f"{_COVERED if r['covered'] else ''}</td><td class='num'>{r['cpu_p95']:.0f}%</td>"
                     f"<td class='num'>{pct(r['mem_used'])}</td><td class='num sm'>"
                     f"{_after(r)}"
                     f"</td><td class='num'>{M(r['saving'])}</td><td class='sm brk'>{e(r['owner'] or '–')}</td></tr>"
                     for r in sorted(rv["rightsizing"], key=lambda x: -x["saving"])]) if rv["rightsizing"] else "<p class='muted'>None.</p>")
    # 4 waste
    _sect(h, 4, "Waste", "Waste", st, f"Paid for and not used: VMs stopped but still allocated, non-production VMs on around the clock with no schedule "
          f"tag, managed disks unattached for {s.unattached_days} days or more and public IPs associated with nothing. waste.csv has every item.")
    h.append(_table(["Kind", "Resource", "Detail", "Saving", "Owner"],
                    [f"<tr><td class='nw sm'>{e(w['kind'])}</td><td class='sm'><b>{e(w['name'])}</b><br><span class='muted'>{e(w['subscription'])} · "
                     f"{e(w['rg'])}</span></td><td class='sm'>{e(w['detail'])}</td><td class='num'>{M(w['saving'])}</td>"
                     f"<td class='sm brk'>{e(w['owner'] or '(no owner tag)')}</td></tr>"
                     for w in sorted(rv["waste"], key=lambda w: (w["code"], -w["saving"], w["name"]))]) if rv["waste"] else "<p class='muted'>None.</p>")
    # 5 commitments
    cov = rv["coverage"]
    _sect(h, 5, "Commitments", "Commitments", st,
          f"Reservations and savings plans: utilisation over 30 days, the unused cost and the savings against list price in {ml}, expiry and "
          f"auto-renew. VM coverage {pct(cov['coverage'])} against a target of {pct(s.coverage_target)}.")
    h.append(_table(["Commitment", "Utilisation 30 d", "Unused", "Saved vs list", "Expires", "Auto-renew", "Decision"],
                    [f"<tr><td class='sm'><b>{e(r['c'].name)}</b><br><span class='muted'>{e(r['c'].kind)} · {e(r['c'].sku)} · {e(r['c'].term)} · "
                     f"{e(r['c'].scope)}</span></td><td class='num {_util_cls(r['util'], s)}'>{pct(r['util'], 1)}</td><td class='num'>{M(r['unused'])}</td>"
                     f"<td class='num'>{M(r['savings'])}</td><td class='nw sm'>{r['c'].expiry.isoformat() if r['c'].expiry else '–'}"
                     f"{_in_days(r['days'])}</td>"
                     f"<td>{'on' if r['c'].renew else 'off'}</td><td class='sm'>{e(r['decision'] or '–')}</td></tr>" for r in rv["commitments"]]))
    h.append("<p></p>" + _area_table(findings, "COM"))
    h.append("<h2>All findings</h2>")
    h.append(_table(["Severity", "Area", "Subject", "Detail", "Next step", "Owner"],
                    [f"<tr><td>{_pill(f.severity, f.severity.upper())}</td><td class='nw'>{e(f.area)}</td><td>{e(f.subject)}</td>"
                     f"<td class='sm brk'>{e(f.detail)}</td><td class='fix'>{e(f.fix)}</td><td class='nw'>{e(f.owner)}</td></tr>" for f in findings]))
    h.append("<footer class='muted'>Generated by azcostreview. Fictional demo data unless you ran it on your own exports. The pack for the monthly "
             "review is savings-pack.md.</footer></main></body></html>")
    return "".join(h)


def _after(r: dict) -> str:
    """CPU p95 and peak memory use projected on the smaller size."""
    if r["cpu_after"] is None:
        return "–"
    return f"{r['cpu_after']:.0f}% / {pct(r['mem_after'])}"


def _in_days(days) -> str:
    return "" if days is None else f"<br><span class='muted'>in {days} days</span>"


def _util_cls(u, s) -> str:
    if u is None:
        return ""
    return "critical" if u < s.util_critical else "warning" if u < s.util_warning else ""


__all__ = ["render_html", "pack_md", "WARNING", "CODES", "ORDER"]
