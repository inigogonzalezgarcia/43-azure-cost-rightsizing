"""Fictional demo: Litware Group, a made-up insurance group with five Azure subscriptions, one per cost centre (claims, policy administration,
data and analytics, digital channels and the shared IT platform), reviewed on Wednesday 4 November 2026 for October 2026, in euros. Every
subscription, resource, price, person and number is made up; the prices are round fictional figures, not Azure's price list. The problems are
placed on purpose among the everyday ones."""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

ORG = "Litware Group"
AS_OF = datetime(2026, 11, 4, 8, 0)
TODAY = AS_OF.date()
SEED = 43
MONTH = "2026-10"
START, END = date(2026, 10, 1), date(2026, 11, 3)          # cost rows: October and November to date
WINDOW = (date(2026, 10, 5), date(2026, 11, 3))             # the 30 days of metrics before the export
REGION, LOCATION = "West Europe", "westeurope"

SETTINGS = {
    "organisation": ORG,
    "as_of": "2026-11-04 08:00",
    "month": MONTH,
    "currency": "EUR",
    "rs_cpu_p95": 40,
    "rs_mem_used": 0.4,
    "rs_min_saving": 20,
    "idle_cpu_p95": 5,
    "office_hours_per_week": 60,
    "always_on_share": 0.9,
    "nonprod_envs": ["dev", "test", "uat", "sandbox"],
    "unattached_days": 30,
    "util_warning": 0.8,
    "util_critical": 0.6,
    "expiry_days": 90,
    "decision_days": 60,
    "coverage_target": 0.6,
    "steady_share": 0.95,
    "commitment_discount": 0.3,
    "gap_warning": 0.05,
    "gap_critical": 0.1,
    "budget_critical": 1.1,
    "unit_cost_warning": 0.1,
    "tag_keys": {"cost_centre": ["costcentre", "costcenter"], "owner": ["owner"], "env": ["env", "environment"], "schedule": ["schedule"]},
    "owners": {"rightsizing": "Cloud platform team (Hugo Ferrer)", "waste": "Cloud platform team (Hugo Ferrer)",
               "commitments": "FinOps lead (Elena Márquez)", "showback": "FinOps lead (Elena Márquez)"},
    "cost_centres": {"CC-1100": "Claims", "CC-1200": "Policy administration", "CC-1300": "Data and analytics", "CC-1400": "Digital channels",
                     "CC-1900": "IT shared platform"},
}

# cost centre, subscription, owner of the budget, budget against October spend
SUBS = [
    ("CC-1100", "sub-litware-claims-prod", "Claims IT (Marta Solís)", 1.06),
    ("CC-1200", "sub-litware-policy-prod", "Policy IT (Andrés Vidal)", 1.08),
    ("CC-1300", "sub-litware-data-prod", "Data office (Lucía Herrera)", 1 / 1.14),
    ("CC-1400", "sub-litware-digital-prod", "Digital (Pablo Gil)", 1.04),
    ("CC-1900", "sub-litware-platform", "IT platform (Hugo Ferrer)", 1.1),
]
# fictional euros per vCPU-hour: compute by family, then the Windows Server and SQL Server Standard licence parts
RATE = {"D": 0.048, "E": 0.066, "F": 0.045, "B": 0.042}
WIN, SQL = 0.046, 0.10
GB_PER_VCPU = {"D": 4, "E": 8, "F": 2, "B": 4}
DISK_MONTH = {("Premium_LRS", 128): 19.7, ("StandardSSD_LRS", 128): 9.6, ("Premium_LRS", 1024): 135.0, ("Premium_LRS", 512): 70.0,
              ("Standard_LRS", 1024): 41.0, ("StandardSSD_LRS", 256): 19.2}
IP_MONTH = 3.3
RI_FACTOR = {"P1Y": 0.62, "P3Y": 0.45}
SP_FACTOR = 0.75

# VM groups: cc, resource group, name prefix, count, size, os ('linux', 'windows', 'sql'), env, profile
#   profile: steady (busy, all month), over (oversized), idle, always (non-prod 24/7, no schedule), sched (office hours), dealloc,
#            stopped (stopped from the guest OS on 12 Oct, still allocated), half (deallocated on 20 Oct)
GROUPS = [
    ("CC-1100", "rg-clm-app-prod", "vm-clm-app", 10, "Standard_D4s_v5", "linux", "prod", "steady"),
    ("CC-1100", "rg-clm-web-prod", "vm-clm-web", 6, "Standard_D8s_v5", "windows", "prod", "steady"),
    ("CC-1100", "rg-clm-sql-prod", "vm-clm-sql", 4, "Standard_E8s_v5", "sql", "prod", "steady"),
    ("CC-1100", "rg-clm-sql-prod", "vm-clm-sqlrpt", 2, "Standard_E16s_v5", "sql", "prod", "over"),
    ("CC-1100", "rg-clm-batch-prod", "vm-clm-batch", 4, "Standard_D4s_v5", "linux", "prod", "steady"),
    ("CC-1100", "rg-clm-rpt", "vm-clm-rpt", 1, "Standard_D8s_v5", "windows", "prod", "stopped"),
    ("CC-1100", "rg-clm-dev", "vm-clm-dev", 10, "Standard_D2s_v5", "windows", "dev", "mixed:4,4,2"),
    ("CC-1200", "rg-pol-batch-prod", "vm-pol-batch", 6, "Standard_F8s_v2", "linux", "prod", "steady"),
    ("CC-1200", "rg-pol-app-prod", "vm-pol-app", 8, "Standard_D4s_v5", "windows", "prod", "steady"),
    ("CC-1200", "rg-pol-sql-prod", "vm-pol-sql", 2, "Standard_E8s_v5", "sql", "prod", "steady"),
    ("CC-1200", "rg-pol-sql-prod", "vm-pol-sqlhist", 1, "Standard_E16s_v5", "sql", "prod", "over"),
    ("", "rg-pol-dr", "vm-pol-dr", 3, "Standard_D2s_v5", "windows", "", "steady"),
    ("CC-1200", "rg-pol-dev", "vm-pol-dev", 8, "Standard_D4s_v5", "linux", "test", "mixed:3,3,1,1"),
    ("CC-1300", "rg-data-proc-prod", "vm-data-proc", 6, "Standard_D16s_v5", "linux", "prod", "steady"),
    ("CC-1300", "rg-data-sql-prod", "vm-data-sql", 2, "Standard_E8s_v5", "sql", "prod", "steady"),
    ("CC-1300", "rg-data-etl", "vm-data-etl", 4, "Standard_D8s_v5", "linux", "prod", "steady"),
    ("CC-1300", "rg-data-etl", "vm-data-etlold", 1, "Standard_D8s_v5", "linux", "prod", "idle"),
    ("CC-1300", "rg-data-dev", "vm-data-dev", 8, "Standard_D4s_v5", "linux", "dev", "mixed:4,2,2"),
    ("CC-1400", "rg-dig-web-prod", "vm-dig-web", 10, "Standard_D8s_v5", "linux", "prod", "steady"),
    ("CC-1400", "rg-dig-api-prod", "vm-dig-api", 8, "Standard_D4s_v5", "linux", "prod", "steady"),
    ("", "rg-dig-cms-legacy", "vm-dig-cms", 4, "Standard_D4s_v5", "windows", "prod", "steady"),
    ("CC-1400", "rg-dig-dev", "vm-dig-dev", 10, "Standard_B2ms", "linux", "dev", "mixed:3,4,3"),
    ("CC-1900", "rg-plat-identity", "vm-plat-dc", 4, "Standard_D2s_v5", "windows", "prod", "steady"),
    ("CC-1900", "rg-plat-mgmt", "vm-plat-jump", 3, "Standard_B2ms", "windows", "prod", "steady"),
    ("CC-1900", "rg-plat-monitor", "vm-plat-mon", 2, "Standard_D4s_v5", "linux", "prod", "steady"),
    ("CC-1900", "rg-plat-sandbox", "vm-plat-sbx", 6, "Standard_B2ms", "linux", "sandbox", "mixed:0,5,1"),
]
OVERSIZED = {"vm-clm-web-02", "vm-clm-web-05", "vm-dig-web-03", "vm-dig-web-08", "vm-data-etl-04"}
IDLE = {"vm-plat-jump-03"}
STOPPED = {"vm-dig-cms-02", "vm-pol-dev-08"}
UNTAGGED_OWNER = {"vm-clm-dev-09", "vm-dig-dev-10"}
OWNERS = {"CC-1100": "marta.solis", "CC-1200": "andres.vidal", "CC-1300": "lucia.herrera", "CC-1400": "pablo.gil", "CC-1900": "hugo.ferrer"}
DOMAIN = "litware.example"

# commitments: id key, name, type, sku, term, quantity or hourly commitment, expiry, renew, members (VM name prefixes it covers)
COMMITMENTS = [
    ("ri-f8", "RI-F8sv2-PolicyBatch", "Reservation", "Standard_F8s_v2", "P1Y", 10, date(2026, 12, 15), True, ("vm-pol-batch",)),
    ("ri-e8", "RI-E8sv5-SQL", "Reservation", "Standard_E8s_v5", "P1Y", 6, date(2027, 1, 20), False, ("vm-clm-sql", "vm-pol-sql")),
    ("ri-d4", "RI-D4sv5-ClaimsApp", "Reservation", "Standard_D4s_v5", "P1Y", 10, date(2027, 6, 30), True, ("vm-clm-app",)),
    ("sp-01", "SP-Compute-Shared", "Savings Plan", "Compute_Savings_Plan", "P3Y", 5.45, date(2029, 3, 31), False,
     ("vm-data-proc", "vm-data-sql", "vm-dig-api")),
]

# other resources: cc ('' untagged), subscription cc, resource group, name, ARM type, ResourceType, ServiceName, ServiceCategory, euros a month
PAAS = [
    ("CC-1100", "CC-1100", "rg-clm-portal-prod", "asp-clm-portal", "microsoft.web/serverfarms", "App Service plan", "Azure App Service", "Web", 1150),
    ("CC-1100", "CC-1100", "rg-clm-data-prod", "sqldb-clm-docs", "microsoft.sql/servers/databases", "SQL database", "Azure SQL Database",
     "Databases", 1900),
    ("", "CC-1100", "rg-clm-data-prod", "stclmdocsprod", "microsoft.storage/storageaccounts", "Storage account", "Storage Accounts", "Storage",
     2600),
    ("CC-1100", "CC-1100", "rg-clm-portal-prod", "kv-clm-prod", "microsoft.keyvault/vaults", "Key vault", "Key Vault", "Security", 14),
    ("CC-1200", "CC-1200", "rg-pol-app-prod", "asp-pol-broker", "microsoft.web/serverfarms", "App Service plan", "Azure App Service", "Web", 920),
    ("CC-1200", "CC-1200", "rg-pol-sql-prod", "sqldb-pol-rating", "microsoft.sql/servers/databases", "SQL database", "Azure SQL Database",
     "Databases", 2400),
    ("CC-1200", "CC-1200", "rg-pol-app-prod", "stpoldocs", "microsoft.storage/storageaccounts", "Storage account", "Storage Accounts", "Storage",
     1100),
    ("", "CC-1200", "rg-pol-dr", "rsv-pol-dr", "microsoft.recoveryservices/vaults", "Recovery Services vault", "Azure Site Recovery",
     "Management and Governance", 1450),
    ("CC-1300", "CC-1300", "rg-data-platform-prod", "dbw-litware-analytics", "microsoft.databricks/workspaces", "Databricks workspace",
     "Azure Databricks", "Analytics", 15800),
    ("CC-1300", "CC-1300", "rg-data-platform-prod", "stlitwaredatalake", "microsoft.storage/storageaccounts", "Storage account", "Storage Accounts",
     "Storage", 3900),
    ("CC-1300", "CC-1300", "rg-data-platform-prod", "adf-litware", "microsoft.datafactory/factories", "Data factory", "Azure Data Factory",
     "Integration", 1600),
    ("", "CC-1300", "rg-halcyon-migration", "sthalcyonstage", "microsoft.storage/storageaccounts", "Storage account", "Storage Accounts",
     "Storage", 480),
    ("CC-1400", "CC-1400", "rg-dig-edge-prod", "afd-litware", "microsoft.cdn/profiles", "Front Door profile", "Azure Front Door", "Networking",
     1350),
    ("CC-1400", "CC-1400", "rg-dig-edge-prod", "agw-dig-prod", "microsoft.network/applicationgateways", "Application gateway",
     "Application Gateway", "Networking", 820),
    ("CC-1400", "CC-1400", "rg-dig-api-prod", "cosmos-dig-sessions", "microsoft.documentdb/databaseaccounts", "Azure Cosmos DB account",
     "Azure Cosmos DB", "Databases", 2300),
    ("CC-1400", "CC-1400", "rg-dig-api-prod", "apim-dig", "microsoft.apimanagement/service", "API Management service", "API Management",
     "Integration", 2450),
    ("CC-1900", "CC-1900", "rg-plat-hub", "afw-hub", "microsoft.network/azurefirewalls", "Firewall", "Azure Firewall", "Security", 1280),
    ("CC-1900", "CC-1900", "rg-plat-hub", "vpng-hub", "microsoft.network/virtualnetworkgateways", "Virtual network gateway", "VPN Gateway",
     "Networking", 320),
    ("CC-1900", "CC-1900", "rg-plat-monitor", "law-litware-platform", "microsoft.operationalinsights/workspaces", "Log Analytics workspace",
     "Log Analytics", "Management and Governance", 3600),
    ("CC-1900", "CC-1900", "rg-plat-hub", "bas-hub", "microsoft.network/bastionhosts", "Bastion", "Azure Bastion", "Networking", 300),
    ("", "CC-1900", "rg-shared-integration", "sb-litware-integration", "microsoft.servicebus/namespaces", "Service Bus namespace", "Service Bus",
     "Integration", 650),
    ("", "CC-1900", "", "Microsoft Defender for Cloud", "microsoft.security/pricings", "Defender plan", "Microsoft Defender for Cloud", "Security",
     2100),
    ("CC-1900", "CC-1900", "rg-plat-backup", "rsv-plat-backup", "microsoft.recoveryservices/vaults", "Recovery Services vault", "Azure Backup",
     "Storage", 900),
]
# business unit metric: cc, unit, October units, change of units since September, change of cost since September (for September's cost)
UNITS = [("CC-1100", "claim processed", 41800, 0.01, 0.03), ("CC-1200", "policy in force", 286400, 0.004, -0.02),
         ("CC-1400", "monthly active user", 118300, 0.12, -0.03)]


def _uuid(rnd: random.Random) -> str:
    h = f"{rnd.getrandbits(128):032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _days(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


def _iso(d: date) -> str:
    return f"{d:%Y-%m-%d}T00:00:00Z"


def _tags(x: dict) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":")) if x else ""


def _f(x: float, k: int = 4) -> str:
    return f"{x:.{k}f}"


def _g(x: float, k: int = 4) -> str:
    """Fixed decimals without trailing zeros, to keep the daily export small."""
    t = f"{x:.{k}f}".rstrip("0").rstrip(".")
    return t if t not in ("", "-0") else "0"


def size_info(size: str) -> tuple[str, int]:
    core = size.split("_")[1]
    fam = "".join(ch for ch in core if ch.isupper())
    n = int("".join(ch for ch in core if ch.isdigit()))
    return fam, n


def estate(rnd: random.Random) -> dict:
    ids = random.Random(SEED + 100)
    subs = {cc: {"cc": cc, "name": n, "id": _uuid(ids), "owner": o, "factor": f} for cc, n, o, f in SUBS}
    vms = []
    for cc, rg, prefix, count, size, os_, env, profile in GROUPS:
        sub = subs[cc or ("CC-1200" if "pol" in rg else "CC-1400")]
        mix = []
        if profile.startswith("mixed:"):
            n = [int(x) for x in profile.split(":")[1].split(",")] + [0, 0, 0]
            mix = ["always"] * n[0] + ["sched"] * n[1] + ["dealloc"] * n[2] + ["stopped"] * n[3]
        for k in range(1, count + 1):
            name = f"{prefix}-{k:02d}"
            prof = mix[k - 1] if mix else profile
            if name in OVERSIZED:
                prof = "over"
            if name in IDLE:
                prof = "idle"
            if name in STOPPED:
                prof = "stopped"
            fam, n = size_info(size)
            tags = {}
            if cc:
                tags = {"costcentre": cc, "env": env, "owner": f"{OWNERS[cc]}@{DOMAIN}"}
                if prof == "sched":
                    tags["schedule"] = "office-hours"
                if name in UNTAGGED_OWNER:
                    del tags["owner"]
            rid = (f"/subscriptions/{sub['id']}/resourcegroups/{rg}/providers/microsoft.compute/virtualmachines/{name}")
            vms.append({"name": name, "id": rid, "sub": sub, "rg": rg, "size": size, "fam": fam, "vcpus": n, "mem": n * GB_PER_VCPU[fam] if fam != "B" else 8,
                        "os": os_, "env": env, "prof": prof, "tags": tags, "cc": cc})
    return {"subs": subs, "vms": vms}


def hours(vm: dict, day: date) -> float:
    """Running hours of the VM that day; 'stopped' VMs keep their compute allocated (and billed) after the guest shut down on 12 October."""
    p = vm["prof"]
    if p == "dealloc":
        return 0
    if p == "sched":
        return 12 if day.weekday() < 5 else 0
    if vm["name"] == "vm-pol-batch-06" and day > date(2026, 10, 19):
        return 0
    if vm["name"] == "vm-clm-sql-04" and date(2026, 10, 20) <= day <= date(2026, 10, 26):
        return 0
    return 24


def billed_hours(vm: dict, day: date) -> float:
    return hours(vm, day)


def run_hours(vm: dict, day: date) -> float:
    """Hours with metric data (the guest is up)."""
    if vm["prof"] == "stopped" and day >= date(2026, 10, 12):
        return 0
    return hours(vm, day)


def commitment_of(vm: dict) -> tuple | None:
    for c in COMMITMENTS:
        if any(vm["name"].startswith(p + "-") for p in c[8]):
            return c
    return None


def costs(rnd: random.Random, E: dict) -> tuple[list, dict]:
    subs, vms = E["subs"], E["vms"]
    ids = random.Random(SEED + 200)
    cmt = {}
    for key, name, typ, sku, term, qty, exp, renew, _ in COMMITMENTS:
        order, cid = _uuid(ids), _uuid(ids)
        if typ == "Reservation":
            rid = f"/providers/microsoft.capacity/reservationorders/{order}/reservations/{cid}"
        else:
            rid = f"/providers/microsoft.billingbenefits/savingsplanorders/{order}/savingsplans/{cid}"
        cmt[key] = {"key": key, "id": rid, "name": name, "type": typ, "sku": sku, "term": term, "qty": qty, "expiry": exp, "renew": renew,
                    "used": {}, "unit_rate": None}
    rows = []
    platform = subs["CC-1900"]

    def row(day, sub, rg, rid, rname, arm, rtype, service, scat, pricing, qty, unit, lst, eff, billed, tags, cdid="", cdtype="", cdstatus="",
            category="Usage"):
        per = date(day.year, day.month, 1)
        rows.append([_iso(per), _iso(day), category, rid, rname, arm, service, scat, REGION, sub["name"] if sub else "", rg, pricing, cdid, cdtype,
                     cdstatus, _g(qty, 3), unit, _g(lst), _g(eff), _g(billed), "EUR", _tags(tags)])

    for vm in vms:
        fam, n = vm["fam"], vm["vcpus"]
        comp = RATE[fam] * n
        lic = n * (WIN if vm["os"] in ("windows", "sql") else 0) + n * (SQL if vm["os"] == "sql" else 0)
        c = commitment_of(vm)
        for day in _days(START, END):
            h = billed_hours(vm, day)
            if not h:
                continue
            args = (day, vm["sub"], vm["rg"], vm["id"], vm["name"], "microsoft.compute/virtualmachines", "Virtual machine", "Virtual Machines",
                    "Compute")
            if c:
                k = cmt[c[0]]
                factor = RI_FACTOR[c[4]] if c[2] == "Reservation" else SP_FACTOR
                row(*args, "Committed", h, "Hours", comp * h, comp * h * factor, 0, vm["tags"], k["id"], c[2], "Used")
                if c[2] == "Reservation":
                    k["used"][day] = k["used"].get(day, 0) + h * comp / (RATE[fam] * size_info(c[3])[1])
                else:
                    k["used"][day] = k["used"].get(day, 0) + comp * h * factor
                if lic:
                    row(*args, "Standard", h, "Hours", lic * h, lic * h, lic * h, vm["tags"])
            else:
                total = (comp + lic) * h
                row(*args, "Standard", h, "Hours", total, total, total, vm["tags"])
    # unused commitment and the monthly purchase rows
    for key, name, typ, sku, term, qty, exp, renew, _ in COMMITMENTS:
        k = cmt[key]
        for day in _days(START, END):
            if typ == "Reservation":
                fam, n = size_info(sku)
                unit_eff = RATE[fam] * n * RI_FACTOR[term]
                unused_units_h = qty * 24 - k["used"].get(day, 0)
                unused = max(0.0, unused_units_h) * unit_eff
                fee = qty * 24 * unit_eff
            else:
                fee = qty * 24
                unused = max(0.0, fee - k["used"].get(day, 0))
            arm = "microsoft.capacity/reservationorders/reservations" if typ == "Reservation" else "microsoft.billingbenefits/savingsplanorders/savingsplans"
            rtype = "Reservation" if typ == "Reservation" else "Savings plan"
            if unused > 0.005:
                row(day, platform, "", k["id"], name, arm, rtype, "Virtual Machines", "Compute", "Committed", round(unused / (fee / 24), 3), "Hours", 0,
                    unused, 0, {}, k["id"], typ, "Unused")
            if day.day == 1:
                month_fee = fee * (31 if day.month == 10 else 30)
                row(day, platform, "", k["id"], name, arm, rtype, "Virtual Machines", "Compute", "Committed", 1, "Months", 0, 0, month_fee, {}, k["id"],
                    typ, "", "Purchase")
    # managed disks and public IPs
    for dk in E["disks"]:
        price = DISK_MONTH[(dk["sku"], dk["size"])]
        for day in _days(START, END):
            if dk["created"] and day < dk["created"]:
                continue
            per_day = price * 12 / 365
            row(day, dk["sub"], dk["rg"], dk["id"], dk["name"], "microsoft.compute/disks", "Disk", "Storage", "Storage", "Standard", 1 / 30.4, "Months",
                per_day, per_day, per_day, dk["tags"])
    for ip in E["ips"]:
        for day in _days(START, END):
            per_day = IP_MONTH * 12 / 365
            row(day, ip["sub"], ip["rg"], ip["id"], ip["name"], "microsoft.network/publicipaddresses", "Public IP address", "Virtual Network",
                "Networking", "Standard", 24, "Hours", per_day, per_day, per_day, ip["tags"])
    # platform services
    for cc, scc, rg, name, arm, rtype, service, scat, monthly in PAAS:
        sub = subs[scc]
        rid = (f"/subscriptions/{sub['id']}/resourcegroups/{rg}/providers/{arm}/{name}" if rg
               else f"/subscriptions/{sub['id']}/providers/{arm}/virtualmachines")
        tags = {"costcentre": cc, "env": "prod", "owner": f"{OWNERS[cc]}@{DOMAIN}"} if cc else {}
        for day in _days(START, END):
            wd = 1.08 if day.weekday() < 5 else 0.8
            grow = 1.0
            if name == "dbw-litware-analytics":
                grow = 0.9 + 0.25 * (day - START).days / 33          # the analytics workspace grows through the month
            x = monthly / 30.4 * wd * grow * (1 + rnd.uniform(-0.04, 0.04))
            row(day, sub, rg, rid, name, arm, rtype, service, scat, "Standard", 1, "Units", x, x, x, tags)
    rows.sort(key=lambda r: (r[1], r[3], r[11], r[2]))
    return rows, cmt


def disks_ips(rnd: random.Random, E: dict) -> None:
    subs = E["subs"]
    disks, ips = [], []
    for vm in E["vms"]:
        sku = "Premium_LRS" if vm["env"] in ("prod", "") else "StandardSSD_LRS"
        state = "Reserved" if vm["prof"] == "dealloc" or vm["name"] == "vm-pol-batch-06" else "Attached"
        base = vm["id"].split("/providers/")[0]
        for k, (suffix, size) in enumerate([("OsDisk", 128)] + ([("data1", 1024), ("log1", 512)] if vm["os"] == "sql" else [])):
            dsku = "Premium_LRS" if k else sku
            name = f"{vm['name']}_{suffix}"
            disks.append({"name": name, "id": f"{base}/providers/microsoft.compute/disks/{name}", "sub": vm["sub"], "rg": vm["rg"], "sku": dsku,
                          "size": size, "state": state, "managed_by": vm["id"], "owner_t": datetime(2026, 9, 1 + rnd.randint(0, 20), 3, 10),
                          "created": None, "tags": vm["tags"]})
    data = subs["CC-1300"]
    base = f"/subscriptions/{data['id']}/resourcegroups/rg-halcyon-migration/providers"
    for k, (sku, size) in enumerate([("Premium_LRS", 1024), ("Premium_LRS", 1024), ("Premium_LRS", 512), ("Premium_LRS", 512), ("Premium_LRS", 512),
                                     ("Standard_LRS", 1024), ("Standard_LRS", 1024), ("StandardSSD_LRS", 256), ("StandardSSD_LRS", 256)], start=1):
        name = f"disk-halcyon-mig{k:02d}"
        disks.append({"name": name, "id": f"{base}/microsoft.compute/disks/{name}", "sub": data, "rg": "rg-halcyon-migration", "sku": sku, "size": size,
                      "state": "Unattached", "managed_by": "", "owner_t": datetime(2026, 8, 27, 18, 40 + k), "created": None, "tags": {}})
    clm = subs["CC-1100"]
    t = {"costcentre": "CC-1100", "env": "dev", "owner": f"{OWNERS['CC-1100']}@{DOMAIN}"}
    for name, when, size in (("vm-clm-devold-01_OsDisk", datetime(2026, 7, 14, 9, 5), 128), ("vm-clm-dev-11_OsDisk", datetime(2026, 10, 29, 16, 20), 128),
                             ("vm-clm-dev-12_OsDisk", datetime(2026, 10, 29, 16, 22), 128)):
        disks.append({"name": name, "id": f"/subscriptions/{clm['id']}/resourcegroups/rg-clm-dev/providers/microsoft.compute/disks/{name}", "sub": clm,
                      "rg": "rg-clm-dev", "sku": "StandardSSD_LRS", "size": size, "state": "Unattached", "managed_by": "", "owner_t": when,
                      "created": None, "tags": t})
    for dk in disks:
        dk["created_dt"] = datetime(2025, 3, 1) + timedelta(days=rnd.randint(0, 300))
    n = 1
    plat = subs["CC-1900"]
    for sub, rg, name, cfg, tags in (
            (plat, "rg-plat-hub", "pip-afw-hub", "azureFirewallIpConfiguration", "CC-1900"),
            (plat, "rg-plat-hub", "pip-vpng-hub", "vnetGatewayConfig", "CC-1900"),
            (plat, "rg-plat-hub", "pip-bas-hub", "IpConf", "CC-1900"),
            (subs["CC-1400"], "rg-dig-edge-prod", "pip-agw-dig-prod", "appGatewayFrontendIP", "CC-1400"),
            (subs["CC-1100"], "rg-clm-web-prod", "pip-clm-lb-prod", "LoadBalancerFrontEnd", "CC-1100"),
            (data, "rg-halcyon-migration", "pip-halcyon-mig01", "", ""),
            (data, "rg-halcyon-migration", "pip-halcyon-mig02", "", ""),
            (data, "rg-halcyon-migration", "pip-halcyon-sftp", "", ""),
            (subs["CC-1400"], "rg-dig-cms-legacy", "pip-dig-cms-old", "", ""),
            (subs["CC-1100"], "rg-clm-dev", "pip-clm-test-lb", "", "CC-1100")):
        rid = f"/subscriptions/{sub['id']}/resourcegroups/{rg}/providers/microsoft.network/publicipaddresses/{name}"
        tg = {"costcentre": tags, "env": "prod", "owner": f"{OWNERS[tags]}@{DOMAIN}"} if tags else {}
        cfg_id = f"/subscriptions/{sub['id']}/resourcegroups/{rg}/providers/microsoft.network/x/{name}/ipconfigurations/{cfg}" if cfg else ""
        ips.append({"name": name, "id": rid, "sub": sub, "rg": rg, "sku": "Standard", "alloc": "Static", "address": f"203.0.113.{10 + n}", "cfg": cfg_id,
                    "tags": tg})
        n += 1
    E["disks"], E["ips"] = disks, ips


def metrics(rnd: random.Random, E: dict) -> list:
    out = []
    win_h = ((WINDOW[1] - WINDOW[0]).days + 1) * 24
    for vm in E["vms"]:
        h = sum(run_hours(vm, d) for d in _days(*WINDOW))
        if h == 0:
            out.append([vm["id"], vm["name"], win_h, 0, "", "", ""])
            continue
        p = vm["prof"]
        if p == "over":
            p95, used = rnd.uniform(14, 30), rnd.uniform(0.2, 0.34)
        elif p == "idle":
            p95, used = rnd.uniform(1.2, 3.5), rnd.uniform(0.12, 0.2)
        else:
            p95 = rnd.uniform(46, 88)
            used = rnd.uniform(0.45, 0.82)
        avg = p95 * rnd.uniform(0.35, 0.6)
        out.append([vm["id"], vm["name"], win_h, int(h), _f(avg, 1), _f(p95, 1), _f(vm["mem"] * (1 - used), 2)])
    return out


def write(folder: Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(SEED)
    E = estate(rnd)
    disks_ips(rnd, E)
    met = metrics(rnd, E)
    cost_rows, cmt = costs(rnd, E)
    vm_rows = []
    for vm in E["vms"]:
        power = {"dealloc": "VM deallocated", "sched": "VM deallocated", "stopped": "VM stopped"}.get(vm["prof"], "VM running")
        if vm["name"] in ("vm-pol-batch-06",):
            power = "VM deallocated"
        os_type = "Windows" if vm["os"] in ("windows", "sql") else "Linux"
        vm_rows.append([vm["sub"]["id"], vm["sub"]["name"], vm["rg"], vm["name"], vm["id"], LOCATION, vm["size"], vm["vcpus"], vm["mem"], power, os_type,
                        _tags(vm["tags"])])
    disk_rows = [[dk["sub"]["id"], dk["sub"]["name"], dk["rg"], dk["name"], dk["id"], dk["sku"], dk["size"], dk["state"], dk["managed_by"],
                  f"{dk['owner_t']:%Y-%m-%dT%H:%M:%SZ}", f"{dk['created_dt']:%Y-%m-%dT%H:%M:%SZ}", _tags(dk["tags"])] for dk in E["disks"]]
    ip_rows = [[ip["sub"]["id"], ip["sub"]["name"], ip["rg"], ip["name"], ip["id"], ip["sku"], ip["alloc"], ip["address"], ip["cfg"], "", _tags(ip["tags"])]
               for ip in E["ips"]]
    com_rows = []
    for key, name, typ, sku, term, qty, exp, renew, _ in COMMITMENTS:
        k = cmt[key]

        def util(a: date, b: date) -> float:
            used = sum(v for d, v in k["used"].items() if a <= d <= b)
            if typ == "Reservation":
                return used / (qty * 24 * ((b - a).days + 1)) * 100
            return used / (qty * 24 * ((b - a).days + 1)) * 100
        com_rows.append([k["id"], name, typ, sku, "VirtualMachines" if typ == "Reservation" else "", "Shared", "", term,
                         qty if typ == "Reservation" else "", "" if typ == "Reservation" else qty, _f(util(END, END), 1),
                         _f(util(END - timedelta(days=6), END), 1), _f(util(*WINDOW), 1), f"{exp:%Y-%m-%d}", "true" if renew else "false"])
    # budgets from October's spend per cost centre, units from the business
    spend = {cc: 0.0 for cc, *_ in SUBS}
    for r in cost_rows:
        if r[0].startswith("2026-10") and r[21]:
            cc = json.loads(r[21]).get("costcentre")
            if cc in spend:
                spend[cc] += float(r[18])
    budget_rows = []
    for cc, _, owner, f in SUBS:
        b = round(spend[cc] * f / 500) * 500
        for m in ("2026-10", "2026-11"):
            budget_rows.append([cc, SETTINGS["cost_centres"][cc], m, b, owner])
    unit_rows = []
    for cc, unit, units, du, dc in UNITS:
        sep_units = round(units * (1 + du))
        sep_cost = spend[cc] * (1 + dc)
        aug_units = round(sep_units * 1.01)
        unit_rows.append(["2026-08", cc, unit, aug_units, _f(sep_cost * 0.99, 2)])
        unit_rows.append(["2026-09", cc, unit, sep_units, _f(sep_cost, 2)])
        unit_rows.append(["2026-10", cc, unit, units, ""])
    files = {
        "costs.csv": (["BillingPeriodStart", "ChargePeriodStart", "ChargeCategory", "ResourceId", "ResourceName", "x_ResourceType", "ServiceName",
                       "ServiceCategory", "RegionName", "SubAccountName", "x_ResourceGroupName", "PricingCategory", "CommitmentDiscountId",
                       "CommitmentDiscountType", "CommitmentDiscountStatus", "ConsumedQuantity", "ConsumedUnit", "ListCost", "EffectiveCost",
                       "BilledCost", "BillingCurrency", "Tags"], cost_rows),
        "vms.csv": (["SubscriptionId", "Subscription", "ResourceGroupName", "Name", "Id", "Location", "VmSize", "vCPUs", "MemoryGB", "PowerState", "OsType",
                     "Tags"], vm_rows),
        "vm_metrics.csv": (["ResourceId", "Name", "window_hours", "running_hours", "cpu_avg_pct", "cpu_p95_pct", "available_memory_p5_gb"], met),
        "disks.csv": (["SubscriptionId", "Subscription", "ResourceGroupName", "Name", "Id", "Sku", "DiskSizeGB", "DiskState", "ManagedBy",
                       "LastOwnershipUpdateTime", "TimeCreated", "Tags"], disk_rows),
        "public_ips.csv": (["SubscriptionId", "Subscription", "ResourceGroupName", "Name", "Id", "Sku", "PublicIpAllocationMethod", "IpAddress",
                            "IpConfigurationId", "NatGatewayId", "Tags"], ip_rows),
        "commitments.csv": (["id", "displayName", "type", "sku", "reservedResourceType", "appliedScopeType", "appliedScopeDisplayName", "term", "quantity",
                             "commitmentAmount", "utilization1Day", "utilization7Days", "utilization30Days", "expiryDate", "renew"], com_rows),
        "budgets.csv": (["cost_centre", "name", "month", "budget", "owner"], budget_rows),
        "units.csv": (["month", "cost_centre", "unit", "units", "effective_cost"], unit_rows),
    }
    for name, (header, rows) in files.items():
        with open(folder / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
    (folder / "settings.json").write_text(json.dumps(SETTINGS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder
