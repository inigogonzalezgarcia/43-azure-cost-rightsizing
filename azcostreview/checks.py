"""The checks of the monthly Azure cost review. Four areas: right-sizing (VMs whose CPU p95 and peak memory fit in one size down within the
same family, and idle VMs), waste (VMs stopped but not deallocated, still billed for compute; non-production VMs running around the clock
without a schedule tag; managed disks unattached for weeks; public IPs associated with nothing), commitments (reservations and savings plans
under their utilisation target, expiring soon with a renew or exchange decision to take, and steady pay-as-you-go VMs a commitment would cover)
and showback (cost with no cost centre tag, cost centres over budget or forecast to end the month over it, unit cost going up, VMs missing the
tags showback needs). Every saving is an estimate per month, at the run rate of the last seven days of the month reviewed, and says how it
was worked out."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .model import Data, _norm, month_days, prev_month, size_down, size_parts

CRITICAL, WARNING, INFO = "critical", "warning", "info"
ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}
AREAS = ("Right-sizing", "Waste", "Commitments", "Showback")
PREFIX = {"RS": "Right-sizing", "WST": "Waste", "COM": "Commitments", "SHB": "Showback"}
VERDICT = {CRITICAL: "action needed", WARNING: "attention", None: "on track"}
SAVING_CODES = ("RS-01", "RS-02", "WST-01", "WST-02", "WST-03", "WST-04", "COM-03")
UNALLOCATED = "(unallocated)"


@dataclass
class Finding:
    severity: str
    area: str
    code: str
    subject: str
    detail: str
    fix: str
    owner: str = ""
    items: list[str] = field(default_factory=list)
    saving: float = 0


@dataclass
class Issue:
    severity: str
    code: str
    text: str
    resource: str = ""
    saving: float = 0
    owner: str = ""
    cost_centre: str = ""

    @property
    def area(self) -> str:
        return PREFIX[self.code.split("-")[0]]


def _n(k: int, word: str, plural: str | None = None) -> str:
    return f"{k} {word if k == 1 else (plural or word + 's')}"


def _top(items: list[str], k: int = 6, sep: str = ", ") -> str:
    return sep.join(items[:k]) + (f" and {len(items) - k} more" if len(items) > k else "")


def pct(x: float | None, k: int = 0) -> str:
    return "–" if x is None else f"{x:.{k}%}"


def money(x: float | None, cur: str = "EUR", k: int = 0) -> str:
    if x is None:
        return "–"
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(cur.upper())
    v = f"{abs(x):,.{k}f}"
    sign = "-" if x < 0 else ""
    return f"{sign}{sym}{v}" if sym else f"{sign}{v} {cur}"


def month_label(key: str) -> str:
    y, m = (int(x) for x in key.split("-"))
    return date(y, m, 1).strftime("%B %Y")


# ---------------------------------------------------------------- costs

def cost_centre(d: Data, tags: dict) -> str:
    return d.settings.tag(tags, "cost_centre")


def resource_costs(d: Data) -> dict:
    """Per resource for the month reviewed: effective, list and billed cost, the committed share, the last seven days and the run rate."""
    mk = d.month
    days = month_days(mk)
    y, m = (int(x) for x in mk.split("-"))
    last7 = date(y, m, days) - timedelta(days=6)
    out = {}
    for c in d.costs:
        if c.period != mk or not c.resource_id or c.unused:
            continue
        r = out.get(c.resource_id)
        if r is None:
            r = out[c.resource_id] = {"effective": 0.0, "list": 0.0, "billed": 0.0, "committed": 0.0, "last7": 0.0, "name": c.resource_name,
                                      "sub": c.sub_name, "rg": c.rg, "type": c.arm_type, "service": c.service, "tags": c.tags,
                                      "hours": defaultdict(float), "committed_hours": defaultdict(float)}
        r["effective"] += c.effective
        r["list"] += c.list_cost
        r["billed"] += c.billed
        key = c.day or date(y, m, 1)
        # a VM covered by a commitment still has pay-as-you-go rows for its Windows or SQL Server licence: the hours tell how much is covered
        r["hours"][key] = max(r["hours"][key], c.qty)
        if c.committed:
            r["committed"] += c.effective
            r["committed_hours"][key] += c.qty
        if c.day and c.day >= last7:
            r["last7"] += c.effective
        if c.tags and not r["tags"]:
            r["tags"] = c.tags
    for r in out.values():
        r["run_rate"] = r["last7"] / 7 * days
        h = sum(r["hours"].values())
        r["committed_share"] = min(1.0, sum(r["committed_hours"].values()) / h) if h else (1.0 if r["committed"] else 0.0)
    return out


def totals(d: Data) -> dict:
    mk = d.month
    rows = [c for c in d.costs if c.period == mk]
    used = [c for c in rows if c.committed and not c.unused and c.category.lower() == "usage"]
    gross = sum(c.list_cost - c.effective for c in used)
    unused = sum(c.effective for c in rows if c.unused)
    vm = [c for c in rows if c.is_vm and c.category.lower() == "usage" and not c.unused]
    vm_list = sum(c.list_cost for c in vm)
    vm_committed = sum(c.list_cost for c in vm if c.committed)
    cur = Counter(c.currency for c in rows if c.currency).most_common(1)
    return {"effective": sum(c.effective for c in rows), "billed": sum(c.billed for c in rows), "list": sum(c.list_cost for c in rows),
            "rows": len(rows), "gross_savings": gross, "unused": unused, "net_savings": gross - unused,
            "vm_effective": sum(c.effective for c in vm), "coverage": vm_committed / vm_list if vm_list else None,
            "purchases": sum(c.billed for c in rows if c.category.lower() == "purchase"), "currency": cur[0][0] if cur else d.settings.currency,
            "by_service": Counter({k: v for k, v in _sum_by(rows, lambda c: c.service or "(none)").items()}),
            "by_subscription": Counter({k: v for k, v in _sum_by(rows, lambda c: c.sub_name or "(none)").items()})}


def _sum_by(rows, key) -> dict:
    out = defaultdict(float)
    for c in rows:
        out[key(c)] += c.effective
    return out


# ---------------------------------------------------------------- review

def review(d: Data) -> dict:
    s = d.settings
    rc = resource_costs(d)
    tot = totals(d)
    rv = {"costs": rc, "totals": tot, "issues": [], "rightsizing": [], "waste": [], "vms": [], "commitments": [], "showback": [], "units": [],
          "coverage": {}}
    cur = tot["currency"]
    M = lambda x: money(x, cur)  # noqa: E731
    flagged = set()

    # VMs: right-sizing and the VM side of waste
    for vm in sorted(d.vms, key=lambda v: (v.subscription, v.rg, v.name)):
        m = d.metrics.get(vm.id)
        cost = rc.get(vm.id, {})
        run = cost.get("run_rate", 0.0)
        tags = vm.tags or cost.get("tags", {})
        cc, owner, env, sched = cost_centre(d, tags), s.tag(tags, "owner"), s.tag(tags, "env").lower(), s.tag(tags, "schedule")
        on_share = (m.hours / m.window) if m and m.hours is not None and m.window else None
        mem_used = (1 - m.mem_p5_gb / vm.memory_gb) if m and m.mem_p5_gb is not None and vm.memory_gb else None
        row = {"vm": vm, "metric": m, "cost": cost.get("effective", 0.0), "run_rate": run, "committed_share": cost.get("committed_share", 0.0),
               "cc": cc, "owner": owner, "env": env, "schedule": sched, "on_share": on_share, "mem_used": mem_used, "issues": []}
        rv["vms"].append(row)
        label = f"{vm.name} ({vm.size}, {vm.subscription})"
        covered = row["committed_share"] >= 0.5
        if vm.stopped_billed:
            i = Issue(CRITICAL, "WST-01", f"{label}: '{vm.power}', not deallocated, still billed for compute ({M(run)} a month)", vm.name, run, owner, cc)
            row["issues"].append(i)
            rv["waste"].append({"kind": "VM stopped, not deallocated", "name": vm.name, "id": vm.id, "subscription": vm.subscription, "rg": vm.rg,
                                "detail": f"{vm.size}, power state '{vm.power}'", "since": "", "cost": row["cost"], "saving": run, "cc": cc,
                                "owner": owner, "action": "Deallocate it (Stop from the portal or Stop-AzVM), or delete it if nobody claims it",
                                "code": "WST-01"})
            flagged.add(vm.id)
        if vm.running and m and m.cpu_p95 is not None:
            target = size_down(vm.size)
            if m.cpu_p95 < s.idle_cpu_p95:
                i = Issue(WARNING, "RS-02", f"{label}: idle, CPU p95 {m.cpu_p95:.1f}% over 30 days, {M(run)} a month", vm.name, run, owner, cc)
                row["issues"].append(i)
                rv["rightsizing"].append(_rs_row(vm, row, "idle", "", m, mem_used, run, covered,
                                                 "Confirm with the owner; deallocate it, schedule it or delete it"))
                flagged.add(vm.id)
            elif (m.cpu_p95 < s.rs_cpu_p95 and mem_used is not None and mem_used < s.rs_mem_used and target):
                ratio = int(size_parts(target)["vcpu"]) / int(size_parts(vm.size)["vcpu"])
                saving = run * (1 - ratio)
                if saving >= s.rs_min_saving:
                    note = "; covered by a commitment, so the saving shows once the freed capacity covers other VMs or is exchanged" if covered else ""
                    i = Issue(WARNING, "RS-01", f"{label}: CPU p95 {m.cpu_p95:.0f}%, peak memory used {mem_used:.0%} -> {target}, about {M(saving)} "
                              f"a month{note}", vm.name, saving, owner, cc)
                    row["issues"].append(i)
                    rv["rightsizing"].append(_rs_row(vm, row, "one size down", target, m, mem_used, saving, covered,
                                                     "Resize in the next change window after the owner agrees; check again after 2 weeks"))
                    flagged.add(vm.id)
        if (vm.running and env in s.nonprod_envs and not sched and on_share is not None and on_share >= s.always_on_share
                and vm.id not in flagged):
            off = 1 - s.office_hours_per_week / 168
            saving = run * off
            i = Issue(WARNING, "WST-02", f"{label}: {env} VM on {on_share:.0%} of the hours with no schedule tag; office hours only would save about "
                      f"{M(saving)} a month", vm.name, saving, owner, cc)
            row["issues"].append(i)
            rv["waste"].append({"kind": "Non-production VM always on", "name": vm.name, "id": vm.id, "subscription": vm.subscription, "rg": vm.rg,
                                "detail": f"{vm.size}, {env}, running {on_share:.0%} of the last 30 days, no schedule tag", "since": "",
                                "cost": row["cost"], "saving": saving, "cc": cc, "owner": owner,
                                "action": f"Start/stop schedule for office hours ({s.office_hours_per_week:g} h a week) and tag it", "code": "WST-02"})
            flagged.add(vm.id)
        rv["issues"] += row["issues"]

    # disks and public IPs
    for dk in sorted(d.disks, key=lambda x: (x.subscription, x.rg, x.name)):
        if not dk.unattached:
            continue
        since = dk.last_owner or dk.created
        age = (d.today - since.date()).days if since else None
        if age is None or age < s.unattached_days:
            continue
        cost = rc.get(dk.id, {})
        run = cost.get("run_rate", 0.0)
        tags = dk.tags or cost.get("tags", {})
        cc, owner = cost_centre(d, tags), s.tag(tags, "owner")
        rv["issues"].append(Issue(WARNING, "WST-03", f"{dk.name} ({dk.sku} {dk.size_gb or 0:g} GB, {dk.rg}): unattached for {age} days, "
                                  f"{M(run)} a month", dk.name, run, owner, cc))
        rv["waste"].append({"kind": "Unattached disk", "name": dk.name, "id": dk.id, "subscription": dk.subscription, "rg": dk.rg,
                            "detail": f"{dk.sku}, {dk.size_gb or 0:g} GB, unattached {age} days", "since": f"{since:%Y-%m-%d}", "cost": cost.get("effective", 0.0),
                            "saving": run, "cc": cc, "owner": owner, "action": "Snapshot if the owner wants to keep the data, then delete the disk",
                            "code": "WST-03"})
    for ip in sorted(d.ips, key=lambda x: (x.subscription, x.rg, x.name)):
        if not ip.unassociated:
            continue
        cost = rc.get(ip.id, {})
        run = cost.get("run_rate", 0.0)
        tags = ip.tags or cost.get("tags", {})
        cc, owner = cost_centre(d, tags), s.tag(tags, "owner")
        rv["issues"].append(Issue(WARNING, "WST-04", f"{ip.name} ({ip.sku} {ip.allocation}, {ip.rg}): associated with nothing, {M(run)} a month",
                                  ip.name, run, owner, cc))
        rv["waste"].append({"kind": "Unassociated public IP", "name": ip.name, "id": ip.id, "subscription": ip.subscription, "rg": ip.rg,
                            "detail": f"{ip.sku}, {ip.allocation}, {ip.address or 'no address'}", "since": "", "cost": cost.get("effective", 0.0),
                            "saving": run, "cc": cc, "owner": owner, "action": "Check no DNS record or allow-list points at it, then delete it",
                            "code": "WST-04"})

    check_commitments(d, rv, flagged)
    check_showback(d, rv)
    rv["issues"].sort(key=lambda i: (ORDER[i.severity], i.code, -i.saving, i.text))
    return rv


def _rs_row(vm, row, kind, target, m, mem_used, saving, covered, action) -> dict:
    p = size_parts(vm.size)
    t = size_parts(target) if target else None
    ratio = int(t["vcpu"]) / int(p["vcpu"]) if p and t else None
    return {"vm": vm, "kind": kind, "target": target, "cpu_p95": m.cpu_p95, "cpu_avg": m.cpu_avg, "mem_used": mem_used,
            "cpu_after": m.cpu_p95 / ratio if ratio else None, "mem_after": mem_used / ratio if ratio and mem_used is not None else None,
            "cost": row["cost"], "run_rate": row["run_rate"], "saving": saving, "covered": covered, "cc": row["cc"], "owner": row["owner"],
            "env": row["env"], "action": action}


# ---------------------------------------------------------------- commitments

def check_commitments(d: Data, rv: dict, flagged: set) -> None:
    s, mk = d.settings, d.month
    cur = rv["totals"]["currency"]
    M = lambda x: money(x, cur)  # noqa: E731
    per = defaultdict(lambda: {"used": 0.0, "unused": 0.0, "savings": 0.0, "resources": set()})
    for c in d.costs:
        if c.period != mk or not c.cd_id:
            continue
        p = per[c.cd_id]
        if c.unused:
            p["unused"] += c.effective
        elif c.category.lower() == "usage":
            p["used"] += c.effective
            p["savings"] += c.list_cost - c.effective
            if c.resource_id:
                p["resources"].add(c.resource_id)
    for cm in sorted(d.commitments, key=lambda x: (x.expiry or date.max, x.name)):
        p = per.get(cm.id, {"used": 0.0, "unused": 0.0, "savings": 0.0, "resources": set()})
        util = cm.util_30d
        if util is None and p["used"] + p["unused"]:
            util = p["used"] / (p["used"] + p["unused"])
        days = (cm.expiry - d.today).days if cm.expiry else None
        row = {"c": cm, "util": util, "used": p["used"], "unused": p["unused"], "savings": p["savings"], "resources": len(p["resources"]),
               "days": days, "decision": "", "issues": []}
        rv["commitments"].append(row)
        what = f"{cm.name} ({cm.kind}, {cm.sku or cm.resource_type}{f' x{cm.quantity:g}' if cm.quantity else ''}, {cm.term}, {cm.scope})"
        low = util is not None and util < s.util_warning
        if low:
            sev = CRITICAL if util < s.util_critical else WARNING
            row["issues"].append(Issue(sev, "COM-01", f"{what}: {pct(util)} used over 30 days; {M(p['unused'])} of it unused in {month_label(mk)}",
                                       cm.name, 0, s.owner("commitments")))
        if days is not None and 0 <= days <= s.expiry_days:
            if cm.renew and low:
                sev, dec = CRITICAL, "Turn auto-renew off; exchange or let it expire, and size any new one on what is used"
                text = f"{what}: expires {cm.expiry:%d %b %Y} (in {days} days) and auto-renews at {pct(util)} utilisation"
            elif low:
                sev, dec = (WARNING if days <= s.decision_days else INFO), "Exchange for the sizes in use, or let it expire"
                text = f"{what}: expires {cm.expiry:%d %b %Y} (in {days} days) at {pct(util)} utilisation"
            else:
                sev = WARNING if days <= s.decision_days and not cm.renew else INFO
                dec = "Auto-renew is on: confirm the size still fits" if cm.renew else "Renew: it is well used"
                text = f"{what}: expires {cm.expiry:%d %b %Y} (in {days} days), {pct(util)} used, auto-renew {'on' if cm.renew else 'off'}"
            row["decision"] = dec
            row["issues"].append(Issue(sev, "COM-02", text + f"; {dec.lower()}", cm.name, 0, s.owner("commitments")))
        rv["issues"] += row["issues"]
    # coverage: steady pay-as-you-go production VMs a commitment would cover
    steady = []
    for row in rv["vms"]:
        vm, on = row["vm"], row["on_share"]
        if (vm.running and on is not None and on >= s.steady_share and row["env"] not in s.nonprod_envs and row["committed_share"] < 0.5
                and vm.id not in flagged and row["run_rate"] > 0):
            steady.append(row)
    pot = sum(r["run_rate"] for r in steady)
    saving = pot * s.commitment_discount
    sizes = Counter(r["vm"].size for r in steady)
    cov = rv["totals"]["coverage"]
    rv["coverage"] = {"coverage": cov, "steady": steady, "on_demand": pot, "saving": saving, "sizes": sizes}
    if steady and saving >= s.rs_min_saving and (cov is None or cov < s.coverage_target):
        top = ", ".join(f"{k} x{v}" for k, v in sizes.most_common(4))
        rv["issues"].append(Issue(WARNING, "COM-03", f"VM commitment coverage {pct(cov)} (target {pct(s.coverage_target)}): {len(steady)} production VMs "
                                  f"run all month on pay-as-you-go, {M(pot)} a month ({top}); a reservation or savings plan at about "
                                  f"{pct(s.commitment_discount)} off would save about {M(saving)} a month", "", saving, s.owner("commitments")))


# ---------------------------------------------------------------- showback

def check_showback(d: Data, rv: dict) -> None:
    s, mk, cur_m = d.settings, d.month, d.current
    cur = rv["totals"]["currency"]
    M = lambda x: money(x, cur)  # noqa: E731
    spend, mtd = defaultdict(float), defaultdict(float)
    untagged = defaultdict(float)
    first = date(*(int(x) for x in cur_m.split("-")), 1)
    for c in d.costs:
        cc = cost_centre(d, c.tags) or UNALLOCATED
        if c.period == mk:
            spend[cc] += c.effective
            if cc == UNALLOCATED:
                untagged[(c.sub_name or "(no subscription)", c.rg or ("unused commitment" if c.unused else "(no resource group)"))] += c.effective
        elif c.period == cur_m and c.day and c.day < d.today:
            mtd[cc] += c.effective
    elapsed = (d.today - first).days
    total = sum(spend.values())
    budgets = {(b.cost_centre, b.month): b for b in d.budgets}
    names = {b.cost_centre: b.name for b in d.budgets if b.name}
    names.update(s.cost_centres)
    owners = {b.cost_centre: b.owner for b in d.budgets if b.owner}
    ccs = sorted(set(spend) | set(mtd) | {b.cost_centre for b in d.budgets}, key=lambda x: (x == UNALLOCATED, x))
    for cc in ccs:
        b, bn = budgets.get((cc, mk)), budgets.get((cc, cur_m))
        fc = mtd[cc] / elapsed * month_days(cur_m) if elapsed > 0 else None
        row = {"cc": cc, "name": names.get(cc, ""), "owner": owners.get(cc, ""), "spend": spend[cc], "share": spend[cc] / total if total else 0,
               "budget": b.budget if b else None, "ratio": spend[cc] / b.budget if b and b.budget else None, "mtd": mtd[cc], "forecast": fc,
               "budget_next": bn.budget if bn else None, "fc_ratio": fc / bn.budget if fc is not None and bn and bn.budget else None, "issues": []}
        rv["showback"].append(row)
        label = f"{cc} {row['name']}".strip()
        if row["ratio"] is not None and row["ratio"] > 1:
            sev = CRITICAL if row["ratio"] > s.budget_critical else WARNING
            row["issues"].append(Issue(sev, "SHB-02", f"{label}: {M(row['spend'])} in {month_label(mk)} against a budget of {M(row['budget'])} "
                                       f"({pct(row['ratio'])})", cc, 0, row["owner"] or s.owner("showback"), cc))
        if row["fc_ratio"] is not None and row["fc_ratio"] > 1:
            row["issues"].append(Issue(WARNING, "SHB-03", f"{label}: {month_label(cur_m)} forecast {M(fc)} ({M(mtd[cc])} in {elapsed} days) against a "
                                       f"budget of {M(row['budget_next'])} ({pct(row['fc_ratio'])})", cc, 0, row["owner"] or s.owner("showback"), cc))
        rv["issues"] += row["issues"]
    gap = spend.get(UNALLOCATED, 0) / total if total else 0
    rv["gap"] = {"share": gap, "amount": spend.get(UNALLOCATED, 0), "where": sorted(untagged.items(), key=lambda x: -x[1])}
    if gap >= s.gap_warning:
        where = "; ".join(f"{sub} / {rg} {M(v)}" for (sub, rg), v in rv["gap"]["where"][:4])
        rv["issues"].append(Issue(CRITICAL if gap >= s.gap_critical else WARNING, "SHB-01",
                                  f"{pct(gap, 1)} of {month_label(mk)} ({M(spend[UNALLOCATED])}) has no cost centre tag: {where}", "", 0,
                                  s.owner("showback")))
    # unit cost
    pm = prev_month(mk)
    by = {(u.cost_centre, u.month): u for u in d.units}
    for cc in sorted({u.cost_centre for u in d.units}):
        now, before = by.get((cc, mk)), by.get((cc, pm))
        if not now or not now.units:
            continue
        cost_now = now.effective if now.effective is not None else spend.get(cc, 0)
        uc = cost_now / now.units
        ub = before.effective / before.units if before and before.units and before.effective is not None else None
        ch = (uc - ub) / ub if ub else None
        row = {"cc": cc, "name": names.get(cc, ""), "unit": now.unit, "units": now.units, "cost": cost_now, "unit_cost": uc, "prev_unit_cost": ub,
               "change": ch, "prev_units": before.units if before else None}
        rv["units"].append(row)
        if ch is not None and ch > s.unit_cost_warning:
            rv["issues"].append(Issue(WARNING, "SHB-04", f"{cc} {row['name']}: cost per {now.unit} {money(uc, cur, 3)} in {month_label(mk)}, up "
                                      f"{pct(ch)} from {money(ub, cur, 3)}", cc, 0, owners.get(cc) or s.owner("showback"), cc))
    # tags showback needs
    miss = []
    for row in rv["vms"]:
        vm = row["vm"]
        lack = [k for k, v in (("cost centre", row["cc"]), ("owner", row["owner"]), ("env", row["env"])) if not v]
        if lack:
            miss.append(f"{vm.name} ({', '.join(lack)})")
    if miss:
        rv["issues"].append(Issue(WARNING, "SHB-05", f"{_n(len(miss), 'VM')} missing tags: {_top(miss, 8)}", "", 0, s.owner("showback")))


# ---------------------------------------------------------------- findings

CODES = {
    "RS-01": ("rightsizing", "VMs to resize one size down", "Agree each resize with the owner, do it in a change window and check the metrics two weeks "
              "later. Same family and generation, half the vCPUs: about half the compute cost.", "VM"),
    "RS-02": ("rightsizing", "idle VMs", "Ask the owner: deallocate, put on a schedule or delete. An idle VM costs the same as a busy one.", "VM"),
    "WST-01": ("waste", "VMs stopped but not deallocated", "Deallocate them: a VM stopped from inside the guest keeps its compute reserved and billed.",
               "VM"),
    "WST-02": ("waste", "non-production VMs on around the clock", "Put them on an office-hours start/stop schedule and tag them with it.", "VM"),
    "WST-03": ("waste", "managed disks unattached for weeks", "Snapshot if the data is needed, then delete. Unattached disks are billed in full.", "disk"),
    "WST-04": ("waste", "public IPs associated with nothing", "Check DNS and allow-lists, then delete them.", "public IP"),
    "COM-01": ("commitments", "commitments under their utilisation target",
               "Widen the scope to shared, turn instance size flexibility on, or exchange the unused part for the sizes in use.", "commitment"),
    "COM-02": ("commitments", "commitments expiring soon", "Decide before expiry: renew what is used, exchange or let expire what is not.",
               "commitment"),
    "COM-03": ("commitments", "steady pay-as-you-go VMs a commitment would cover",
               "Size a reservation or a savings plan on the steady VMs after the resizes, from the Azure Advisor or Cost Management recommendations.",
               "group"),
    "SHB-01": ("showback", "cost with no cost centre tag", "Tag the resource groups shown (or set a tag inheritance policy) and agree who owns the rest.",
               "gap"),
    "SHB-02": ("showback", "cost centres over budget", "Review the overspend with the cost centre owner: one-off, growth or waste.", "cost centre"),
    "SHB-03": ("showback", "cost centres forecast over budget this month", "Warn the owner now; it is cheaper to act mid-month than to explain it after.",
               "cost centre"),
    "SHB-04": ("showback", "unit cost going up", "Ask whether the volume dropped or the cost grew; the unit cost is the number the business reads.",
               "cost centre"),
    "SHB-05": ("showback", "VMs missing showback tags", "Add the missing costcentre, owner and env tags; an Azure Policy can require them.", "group"),
}


def run_all(d: Data, rv: dict | None = None) -> list[Finding]:
    rv = rv if rv is not None else review(d)
    s = d.settings
    cur = rv["totals"]["currency"]
    hits = defaultdict(list)
    for i in rv["issues"]:
        hits[i.code].append(i)
    out: list[Finding] = []
    for code, (owner, subject, fix, word) in CODES.items():
        for sev in (CRITICAL, WARNING, INFO):
            xs = [i for i in hits.get(code, []) if i.severity == sev]
            if not xs:
                continue
            saving = sum(i.saving for i in xs)
            head = subject if word == "gap" or word == "group" else f"{_n(len(xs), word)}: {subject}"
            if saving:
                head += f" (about {money(saving, cur)} a month)"
            head = head[0].upper() + head[1:]
            if sev == INFO:
                head = f"For information ({code}): {head}"
            out.append(Finding(sev, PREFIX[code.split("-")[0]], code, head, _top([i.text for i in xs], 8, "; "),
                               fix if sev != INFO else "No action now; on the agenda of the next monthly review.", s.owner(owner),
                               [i.resource for i in xs if i.resource], saving))
    sm = summary(d, rv)
    out.append(Finding(INFO, "Showback", "MET-01", f"{month_label(d.month)}: {money(sm['spend'], cur)} effective cost, {money(sm['identified'], cur)} a "
                       f"month of savings identified", f"Billed {money(sm['billed'], cur)}; commitments saved {money(sm['net_savings'], cur)} net of "
                       f"{money(sm['unused'], cur)} unused; VM coverage {pct(sm['coverage'])}; {pct(sm['gap'], 1)} unallocated",
                       "Review at the monthly FinOps meeting.", s.owner("showback")))
    out.sort(key=lambda f: (ORDER[f.severity], AREAS.index(f.area), f.code))
    return out


def summary(d: Data, rv: dict) -> dict:
    t = rv["totals"]
    by_code = defaultdict(float)
    for i in rv["issues"]:
        if i.code in SAVING_CODES:
            by_code[i.code] += i.saving
    exp = [r for r in rv["commitments"] if r["days"] is not None and 0 <= r["days"] <= d.settings.expiry_days]
    return dict(
        month=d.month, spend=t["effective"], billed=t["billed"], list=t["list"], gross_savings=t["gross_savings"], unused=t["unused"],
        net_savings=t["net_savings"], coverage=t["coverage"], currency=t["currency"],
        identified=sum(by_code.values()), rightsizing=by_code["RS-01"] + by_code["RS-02"],
        waste=sum(by_code[c] for c in ("WST-01", "WST-02", "WST-03", "WST-04")), commitment_saving=by_code["COM-03"], by_code=dict(by_code),
        vms=len(d.vms), running=sum(v.running for v in d.vms), resize=sum(1 for r in rv["rightsizing"] if r["kind"] != "idle"),
        idle=sum(1 for r in rv["rightsizing"] if r["kind"] == "idle"), stopped=sum(1 for i in rv["issues"] if i.code == "WST-01"),
        always_on=sum(1 for i in rv["issues"] if i.code == "WST-02"), disks=sum(1 for i in rv["issues"] if i.code == "WST-03"),
        ips=sum(1 for i in rv["issues"] if i.code == "WST-04"), commitments=len(rv["commitments"]), expiring=len(exp),
        low_util=sum(1 for r in rv["commitments"] if r["util"] is not None and r["util"] < d.settings.util_warning),
        gap=rv["gap"]["share"], gap_amount=rv["gap"]["amount"],
        over_budget=sum(1 for r in rv["showback"] if r["ratio"] is not None and r["ratio"] > 1),
        forecast_over=sum(1 for r in rv["showback"] if r["fc_ratio"] is not None and r["fc_ratio"] > 1),
        cost_centres=sum(1 for r in rv["showback"] if r["cc"] != UNALLOCATED),
    )


def area_status(findings: list[Finding]) -> dict[str, str]:
    st = {}
    for a in AREAS:
        sev = [f.severity for f in findings if f.area == a and f.severity != INFO]
        st[a] = CRITICAL if CRITICAL in sev else WARNING if sev else "ok"
    return st


def overall(findings: list[Finding]) -> str:
    st = set(area_status(findings).values())
    return VERDICT[CRITICAL] if CRITICAL in st else VERDICT[WARNING] if WARNING in st else VERDICT[None]


__all__ = ["AREAS", "CRITICAL", "WARNING", "INFO", "Finding", "Issue", "review", "run_all", "summary", "area_status", "overall"]
