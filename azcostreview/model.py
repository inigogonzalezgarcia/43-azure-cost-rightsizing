"""Loading: the Azure Cost Management FOCUS 1.0 cost and usage export, the virtual machines (Get-AzVM -Status with the size's vCPUs and
memory from Get-AzVMSize), 30 days of Azure Monitor metrics per VM ("Percentage CPU" and "Available Memory Bytes"), the managed disks
(Get-AzDisk), the public IP addresses (Get-AzPublicIpAddress), the reservations and savings plans with their utilisation, the budgets per cost
centre and, optionally, a business unit metric per cost centre; with forgiving column names, into plain dataclasses."""
from __future__ import annotations

import calendar
import csv
import gzip
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

COST_ALIASES = {
    "period": ["BillingPeriodStart", "billing period start"],
    "day": ["ChargePeriodStart", "charge period start", "date", "usage date"],
    "category": ["ChargeCategory", "charge category"],
    "resource_id": ["ResourceId", "resource id", "instance id"],
    "resource_name": ["ResourceName", "resource name"],
    "arm_type": ["x_ResourceType", "resource type arm"],
    "resource_type": ["ResourceType", "resource type"],
    "service": ["ServiceName", "service name", "meter category"],
    "service_category": ["ServiceCategory", "service category"],
    "region": ["RegionName", "region name", "region", "resource location"],
    "sub_id": ["SubAccountId", "sub account id", "subscription id"],
    "sub_name": ["SubAccountName", "sub account name", "subscription name", "subscription"],
    "rg": ["x_ResourceGroupName", "resource group name", "resource group"],
    "pricing": ["PricingCategory", "pricing category", "pricing model"],
    "cd_id": ["CommitmentDiscountId", "commitment discount id", "benefit id", "reservation id"],
    "cd_type": ["CommitmentDiscountType", "commitment discount type"],
    "cd_status": ["CommitmentDiscountStatus", "commitment discount status"],
    "qty": ["ConsumedQuantity", "consumed quantity", "quantity"],
    "unit": ["ConsumedUnit", "consumed unit", "unit of measure"],
    "list_cost": ["ListCost", "list cost"],
    "effective": ["EffectiveCost", "effective cost", "amortized cost", "cost in billing currency"],
    "billed": ["BilledCost", "billed cost"],
    "currency": ["BillingCurrency", "billing currency", "currency"],
    "tags": ["Tags", "tag"],
}
VM_ALIASES = {
    "id": ["Id", "resource id", "resourceid", "vm id"],
    "name": ["Name", "vm name", "computer name"],
    "sub_id": ["SubscriptionId", "subscription id"],
    "subscription": ["Subscription", "subscription name"],
    "rg": ["ResourceGroupName", "resource group", "resourcegroup"],
    "location": ["Location", "region"],
    "size": ["VmSize", "vm size", "size", "hardwareprofile vmsize"],
    "vcpus": ["vCPUs", "vcpus", "numberofcores", "cores"],
    "memory_gb": ["MemoryGB", "memory gb", "memory"],
    "memory_mb": ["MemoryInMB", "memory in mb", "memory mb"],
    "power": ["PowerState", "power state", "status"],
    "os": ["OsType", "os type", "os", "operating system"],
    "tags": ["Tags", "tag"],
}
METRIC_ALIASES = {
    "id": ["ResourceId", "resource id", "id", "vm id"],
    "cpu_avg": ["cpu_avg_pct", "cpu avg", "percentage cpu avg", "cpu average"],
    "cpu_p95": ["cpu_p95_pct", "cpu p95", "percentage cpu p95"],
    "mem_p5": ["available_memory_p5_gb", "available memory p5 gb", "available memory p5"],
    "hours": ["running_hours", "running hours", "hours with data"],
    "window": ["window_hours", "window hours"],
}
DISK_ALIASES = {
    "id": ["Id", "resource id"],
    "name": ["Name", "disk name"],
    "sub_id": ["SubscriptionId", "subscription id"],
    "subscription": ["Subscription", "subscription name"],
    "rg": ["ResourceGroupName", "resource group"],
    "sku": ["Sku", "sku name", "disk sku"],
    "size_gb": ["DiskSizeGB", "disk size gb", "size gb", "size"],
    "state": ["DiskState", "disk state", "state"],
    "managed_by": ["ManagedBy", "managed by", "attached to"],
    "last_owner": ["LastOwnershipUpdateTime", "last ownership update time", "detached"],
    "created": ["TimeCreated", "time created", "created"],
    "tags": ["Tags", "tag"],
}
IP_ALIASES = {
    "id": ["Id", "resource id"],
    "name": ["Name", "public ip name"],
    "sub_id": ["SubscriptionId", "subscription id"],
    "subscription": ["Subscription", "subscription name"],
    "rg": ["ResourceGroupName", "resource group"],
    "sku": ["Sku", "sku name"],
    "allocation": ["PublicIpAllocationMethod", "allocation method", "allocation"],
    "address": ["IpAddress", "ip address"],
    "ip_config": ["IpConfigurationId", "ip configuration id", "ipconfiguration", "associated to"],
    "nat_gateway": ["NatGatewayId", "nat gateway id", "natgateway"],
    "tags": ["Tags", "tag"],
}
COMMIT_ALIASES = {
    "id": ["id", "commitment id", "reservation id", "benefit id"],
    "name": ["displayName", "display name", "name"],
    "type": ["type", "commitment type", "benefit type"],
    "sku": ["sku", "sku name", "reserved sku"],
    "resource_type": ["reservedResourceType", "reserved resource type", "product"],
    "scope": ["appliedScopeType", "applied scope type", "scope"],
    "scope_name": ["appliedScopeDisplayName", "applied scope", "scope name"],
    "term": ["term"],
    "quantity": ["quantity"],
    "commitment": ["commitmentAmount", "commitment amount", "hourly commitment"],
    "util_1d": ["utilization1Day", "utilisation 1 day", "utilization 1d"],
    "util_7d": ["utilization7Days", "utilisation 7 days", "utilization 7d"],
    "util_30d": ["utilization30Days", "utilisation 30 days", "utilization 30d", "utilization", "utilisation"],
    "expiry": ["expiryDate", "expiry date", "expires", "expiryDateTime"],
    "renew": ["renew", "auto renew", "userFriendlyRenewState"],
}
BUDGET_ALIASES = {
    "cost_centre": ["cost_centre", "cost center", "costcentre", "costcenter", "cost centre"],
    "name": ["name", "cost centre name", "description"],
    "month": ["month", "period"],
    "budget": ["budget", "amount", "budget eur"],
    "owner": ["owner", "budget owner"],
}
UNIT_ALIASES = {
    "month": ["month", "period"],
    "cost_centre": ["cost_centre", "cost center", "costcentre", "costcenter", "cost centre"],
    "unit": ["unit", "metric", "unit name"],
    "units": ["units", "value", "count"],
    "effective": ["effective_cost", "effective cost", "cost"],
}

YES = {"yes", "y", "true", "1", "on", "si"}
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%m/%d/%Y")
DEFAULT_OWNERS = {"rightsizing": "Cloud platform team", "waste": "Cloud platform team", "commitments": "FinOps lead", "showback": "FinOps lead"}
DEFAULT_TAGS = {"cost_centre": ["costcentre", "costcenter", "cost-centre", "cost-center", "cc"], "owner": ["owner", "technicalowner", "contact"],
                "env": ["env", "environment"], "schedule": ["schedule", "autoshutdown", "shutdown", "start-stop"]}
VM_TYPE = "microsoft.compute/virtualmachines"
DISK_TYPE = "microsoft.compute/disks"
IP_TYPE = "microsoft.network/publicipaddresses"


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(ch for ch in s if not unicodedata.combining(ch))).strip()


def parse_dt(v) -> datetime | None:
    s = str(v or "").strip()
    if not s or _norm(s) in {"n a", "na", "none", "null", "never"}:
        return None
    s = re.sub(r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})$", "", s)
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def parse_d(v) -> date | None:
    x = parse_dt(v)
    return x.date() if x else None


def yes(v) -> bool:
    return _norm(v) in YES


def num(v) -> float | None:
    s = str(v or "").strip().replace("%", "").replace("€", "").replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    s = s.replace(",", "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def share(v) -> float | None:
    """'55', '55 %' or '0.55' -> 0.55."""
    x = num(v)
    if x is None:
        return None
    return x / 100 if x > 1 else x


def month_key(v) -> str:
    """'2026-10', '2026-10-01T00:00:00Z' or '10/2026' -> '2026-10'."""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})[/-](\d{4})$", s)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    d = parse_d(s)
    return f"{d:%Y-%m}" if d else ""


def month_days(key: str) -> int:
    y, m = (int(x) for x in key.split("-"))
    return calendar.monthrange(y, m)[1]


def prev_month(key: str) -> str:
    y, m = (int(x) for x in key.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def parse_tags(v) -> dict:
    """FOCUS Tags is a JSON object; older exports drop the braces ('"env": "prod","owner": "x"'); Get-AzVM tags exported as key=value;key=value."""
    s = str(v or "").strip()
    if not s:
        return {}
    for text in (s, "{" + s + "}"):
        try:
            x = json.loads(text)
            if isinstance(x, dict):
                return {str(k).strip().lower(): str(val).strip() for k, val in x.items()}
        except ValueError:
            pass
    out = {}
    for part in re.split(r"[;|]", s):
        if "=" in part:
            k, _, val = part.partition("=")
            out[k.strip().lower()] = val.strip()
    return out


def _open(path: Path):
    """Plain or gzip-compressed CSV (Cost Management exports can be compressed with gzip)."""
    if str(path).endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig", newline="")
    return open(path, newline="", encoding="utf-8-sig")


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with _open(path) as fh:
        header = fh.readline()
        fh.seek(0)
        delim = max(",;\t", key=header.count)
        reader = csv.DictReader(fh, delimiter=delim)
        return list(reader.fieldnames or []), list(reader)


def _map(fields: list[str], aliases: dict[str, list[str]]) -> dict[str, str]:
    heads = {_norm(h): h for h in fields}
    cols = {}
    for k, names in aliases.items():
        for n in names + [k.replace("_", " ")]:
            if _norm(n) in heads and heads[_norm(n)] not in cols.values():
                cols[k] = heads[_norm(n)]
                break
    return cols


def _rows(path: Path, aliases: dict[str, list[str]]) -> list[dict]:
    fields, raw = _read(path)
    cols = _map(fields, aliases)
    return [{k: (r.get(c) or "").strip() for k, c in cols.items()} for r in raw]


SIZE_RE = re.compile(r"^(?P<pre>(?:Standard|Basic)_)(?P<family>[A-Z]+)(?P<vcpu>\d+)(?P<constrained>-\d+)?(?P<feat>[a-z]*)(?P<ver>_v\d+)?$")


def size_parts(size: str) -> dict | None:
    """'Standard_D8s_v5' -> family D, 8 vCPUs, features 's', version '_v5'. None for sizes it cannot read."""
    m = SIZE_RE.match(str(size or "").strip())
    return m.groupdict() if m else None


def size_down(size: str, steps: int = 1) -> str | None:
    """One size down in the same family and version: half the vCPUs ('Standard_E16s_v5' -> 'Standard_E8s_v5'). None for constrained-core
    sizes, for 2 vCPUs or fewer and for sizes it cannot read."""
    p = size_parts(size)
    if not p or p["constrained"]:
        return None
    n = int(p["vcpu"])
    for _ in range(steps):
        if n <= 2 or n % 2:
            return None
        n //= 2
    return f"{p['pre']}{p['family']}{n}{p['feat']}{p['ver'] or ''}"


# ---------------------------------------------------------------- records

@dataclass
class Cost:
    period: str
    day: date | None
    category: str = "Usage"
    resource_id: str = ""
    resource_name: str = ""
    arm_type: str = ""
    resource_type: str = ""
    service: str = ""
    service_category: str = ""
    region: str = ""
    sub_id: str = ""
    sub_name: str = ""
    rg: str = ""
    pricing: str = ""
    cd_id: str = ""
    cd_type: str = ""
    cd_status: str = ""
    qty: float = 0
    unit: str = ""
    list_cost: float = 0
    effective: float = 0
    billed: float = 0
    currency: str = ""
    tags: dict = field(default_factory=dict)

    @property
    def is_vm(self) -> bool:
        return self.arm_type == VM_TYPE or (not self.arm_type and _norm(self.service) == "virtual machines")

    @property
    def committed(self) -> bool:
        return _norm(self.pricing) == "committed"

    @property
    def unused(self) -> bool:
        return _norm(self.cd_status) == "unused"


@dataclass
class VM:
    id: str
    name: str = ""
    sub_id: str = ""
    subscription: str = ""
    rg: str = ""
    location: str = ""
    size: str = ""
    vcpus: int | None = None
    memory_gb: float | None = None
    power: str = ""
    os: str = ""
    tags: dict = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return "running" in _norm(self.power)

    @property
    def stopped_billed(self) -> bool:
        """'VM stopped' keeps the compute allocated and billed; only 'VM deallocated' stops the compute charge."""
        n = _norm(self.power)
        return "stopped" in n and "deallocat" not in n


@dataclass
class Metric:
    id: str
    cpu_avg: float | None = None
    cpu_p95: float | None = None
    mem_p5_gb: float | None = None
    hours: float | None = None
    window: float | None = None


@dataclass
class Disk:
    id: str
    name: str = ""
    sub_id: str = ""
    subscription: str = ""
    rg: str = ""
    sku: str = ""
    size_gb: float | None = None
    state: str = ""
    managed_by: str = ""
    last_owner: datetime | None = None
    created: datetime | None = None
    tags: dict = field(default_factory=dict)

    @property
    def unattached(self) -> bool:
        return _norm(self.state) == "unattached" or (not self.state and not self.managed_by)


@dataclass
class PublicIP:
    id: str
    name: str = ""
    sub_id: str = ""
    subscription: str = ""
    rg: str = ""
    sku: str = ""
    allocation: str = ""
    address: str = ""
    ip_config: str = ""
    nat_gateway: str = ""
    tags: dict = field(default_factory=dict)

    @property
    def unassociated(self) -> bool:
        return not self.ip_config and not self.nat_gateway


@dataclass
class Commitment:
    id: str
    name: str = ""
    type: str = ""
    sku: str = ""
    resource_type: str = ""
    scope: str = ""
    scope_name: str = ""
    term: str = ""
    quantity: float | None = None
    commitment: float | None = None
    util_1d: float | None = None
    util_7d: float | None = None
    util_30d: float | None = None
    expiry: date | None = None
    renew: bool = False

    @property
    def kind(self) -> str:
        n = _norm(self.type + " " + self.id)
        return "Savings Plan" if "saving" in n or "billingbenefits" in n else "Reservation"


@dataclass
class Budget:
    cost_centre: str
    name: str = ""
    month: str = ""
    budget: float = 0
    owner: str = ""


@dataclass
class Unit:
    month: str
    cost_centre: str
    unit: str = ""
    units: float | None = None
    effective: float | None = None


@dataclass
class Settings:
    organisation: str = "Azure cost review"
    as_of: datetime | None = None
    month: str = ""
    currency: str = "EUR"
    rs_cpu_p95: float = 40
    rs_mem_used: float = 0.4
    rs_min_saving: float = 20
    idle_cpu_p95: float = 5
    office_hours_per_week: float = 60
    always_on_share: float = 0.9
    nonprod_envs: list = field(default_factory=lambda: ["dev", "test", "uat", "sandbox"])
    unattached_days: int = 30
    util_warning: float = 0.8
    util_critical: float = 0.6
    expiry_days: int = 90
    decision_days: int = 60
    coverage_target: float = 0.6
    steady_share: float = 0.95
    commitment_discount: float = 0.3
    gap_warning: float = 0.05
    gap_critical: float = 0.1
    budget_critical: float = 1.1
    unit_cost_warning: float = 0.1
    tag_keys: dict = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_TAGS.items()})
    owners: dict = field(default_factory=lambda: dict(DEFAULT_OWNERS))
    cost_centres: dict = field(default_factory=dict)

    def owner(self, area: str) -> str:
        return self.owners.get(area) or DEFAULT_OWNERS.get(area, "FinOps lead")

    def tag(self, tags: dict, what: str) -> str:
        for k in self.tag_keys.get(what, DEFAULT_TAGS.get(what, [])):
            v = tags.get(k.lower())
            if v:
                return v
        return ""


def settings_from_dict(raw: dict) -> Settings:
    s = Settings()
    for k, v in raw.items():
        if not hasattr(s, k) or v is None:
            continue
        cur = getattr(s, k)
        if k == "as_of":
            s.as_of = parse_dt(v)
        elif k == "month":
            s.month = month_key(v)
        elif isinstance(cur, dict):
            cur.update({str(a): b for a, b in v.items()})
        elif isinstance(cur, list):
            setattr(s, k, [str(x).lower() for x in v])
        elif isinstance(cur, bool):
            setattr(s, k, bool(v))
        elif isinstance(cur, int):
            setattr(s, k, int(num(v) or 0))
        elif isinstance(cur, float):
            setattr(s, k, float(num(v) or 0))
        else:
            setattr(s, k, v)
    return s


@dataclass
class Data:
    settings: Settings
    as_of: datetime
    costs: list[Cost] = field(default_factory=list)
    vms: list[VM] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)          # lower-case resource id -> Metric
    disks: list[Disk] = field(default_factory=list)
    ips: list[PublicIP] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    budgets: list[Budget] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    sources: dict = field(default_factory=dict)

    @property
    def today(self) -> date:
        return self.as_of.date()

    @property
    def month(self) -> str:
        """The month under review: settings.month, or the last full month before the reference date."""
        return self.settings.month or prev_month(f"{self.today:%Y-%m}")

    @property
    def current(self) -> str:
        """The month of the reference date, for the month-end forecast."""
        return f"{self.today:%Y-%m}"


# ---------------------------------------------------------------- loaders

def _id(v) -> str:
    return str(v or "").strip().lower()


def load_costs(path: Path) -> list[Cost]:
    out = []
    for r in _rows(path, COST_ALIASES):
        day = parse_d(r.get("day"))
        per = month_key(r.get("period")) or (f"{day:%Y-%m}" if day else "")
        if not per:
            continue
        out.append(Cost(per, day, r.get("category") or "Usage", _id(r.get("resource_id")), r.get("resource_name", ""), _id(r.get("arm_type")),
                        r.get("resource_type", ""), r.get("service", ""), r.get("service_category", ""), r.get("region", ""), _id(r.get("sub_id")),
                        r.get("sub_name", ""), r.get("rg", "").lower(), r.get("pricing", ""), _id(r.get("cd_id")), r.get("cd_type", ""),
                        r.get("cd_status", ""), num(r.get("qty")) or 0, r.get("unit", ""), num(r.get("list_cost")) or 0, num(r.get("effective")) or 0,
                        num(r.get("billed")) or 0, r.get("currency", ""), parse_tags(r.get("tags"))))
    return out


def load_vms(path: Path) -> list[VM]:
    out = []
    for r in _rows(path, VM_ALIASES):
        if not r.get("id") and not r.get("name"):
            continue
        mem = num(r.get("memory_gb"))
        if mem is None and num(r.get("memory_mb")) is not None:
            mem = num(r.get("memory_mb")) / 1024
        v = num(r.get("vcpus"))
        if v is None and size_parts(r.get("size", "")):
            v = int(size_parts(r["size"])["vcpu"])
        out.append(VM(_id(r.get("id")) or r.get("name", "").lower(), r.get("name", ""), _id(r.get("sub_id")), r.get("subscription", ""),
                      r.get("rg", "").lower(), r.get("location", ""), r.get("size", ""), int(v) if v is not None else None, mem, r.get("power", ""),
                      r.get("os", ""), parse_tags(r.get("tags"))))
    return out


def load_metrics(path: Path) -> dict:
    out = {}
    for r in _rows(path, METRIC_ALIASES):
        if r.get("id"):
            out[_id(r["id"])] = Metric(_id(r["id"]), num(r.get("cpu_avg")), num(r.get("cpu_p95")), num(r.get("mem_p5")), num(r.get("hours")),
                                       num(r.get("window")))
    return out


def load_disks(path: Path) -> list[Disk]:
    return [Disk(_id(r.get("id")) or r.get("name", "").lower(), r.get("name", ""), _id(r.get("sub_id")), r.get("subscription", ""),
                 r.get("rg", "").lower(), r.get("sku", ""), num(r.get("size_gb")), r.get("state", ""), _id(r.get("managed_by")),
                 parse_dt(r.get("last_owner")), parse_dt(r.get("created")), parse_tags(r.get("tags")))
            for r in _rows(path, DISK_ALIASES) if r.get("id") or r.get("name")]


def load_ips(path: Path) -> list[PublicIP]:
    return [PublicIP(_id(r.get("id")) or r.get("name", "").lower(), r.get("name", ""), _id(r.get("sub_id")), r.get("subscription", ""),
                     r.get("rg", "").lower(), r.get("sku", ""), r.get("allocation", ""), r.get("address", ""), _id(r.get("ip_config")),
                     _id(r.get("nat_gateway")), parse_tags(r.get("tags")))
            for r in _rows(path, IP_ALIASES) if r.get("id") or r.get("name")]


def load_commitments(path: Path) -> list[Commitment]:
    out = []
    for r in _rows(path, COMMIT_ALIASES):
        if not r.get("id"):
            continue
        rn = _norm(r.get("renew"))
        out.append(Commitment(_id(r["id"]), r.get("name", ""), r.get("type", ""), r.get("sku", ""), r.get("resource_type", ""), r.get("scope", ""),
                              r.get("scope_name", ""), r.get("term", ""), num(r.get("quantity")), num(r.get("commitment")), share(r.get("util_1d")),
                              share(r.get("util_7d")), share(r.get("util_30d")), parse_d(r.get("expiry")), yes(rn) or rn == "on"))
    return out


def load_budgets(path: Path) -> list[Budget]:
    return [Budget(r["cost_centre"], r.get("name", ""), month_key(r.get("month")), num(r.get("budget")) or 0, r.get("owner", ""))
            for r in _rows(path, BUDGET_ALIASES) if r.get("cost_centre")]


def load_units(path: Path) -> list[Unit]:
    return [Unit(month_key(r.get("month")), r["cost_centre"], r.get("unit", ""), num(r.get("units")), num(r.get("effective")))
            for r in _rows(path, UNIT_ALIASES) if r.get("cost_centre") and month_key(r.get("month"))]


FILES = {
    "costs.csv": "Azure Cost Management FOCUS 1.0 cost and usage export",
    "vms.csv": "Virtual machines with size, vCPUs, memory, power state and tags (Get-AzVM -Status, Get-AzVMSize)",
    "vm_metrics.csv": "30 days of Azure Monitor metrics per VM: Percentage CPU and Available Memory Bytes (Get-AzMetric)",
    "disks.csv": "Managed disks with their state (Get-AzDisk)",
    "public_ips.csv": "Public IP addresses and what they are associated with (Get-AzPublicIpAddress)",
    "commitments.csv": "Reservations and savings plans with their utilisation (Capacity and BillingBenefits APIs)",
    "budgets.csv": "Budget per cost centre and month (finance)",
    "units.csv": "Business unit metric per cost centre and month, for the unit cost",
}
LOADERS = {"costs.csv": ("costs", load_costs), "vms.csv": ("vms", load_vms), "vm_metrics.csv": ("metrics", load_metrics),
           "disks.csv": ("disks", load_disks), "public_ips.csv": ("ips", load_ips), "commitments.csv": ("commitments", load_commitments),
           "budgets.csv": ("budgets", load_budgets), "units.csv": ("units", load_units)}


def _find(folder: Path, name: str) -> Path | None:
    for p in (folder / name, folder / (name + ".gz")):
        if p.exists():
            return p
    stem = name.rsplit(".", 1)[0]
    alts = sorted(x for pat in (f"{stem}*.csv", f"{stem}*.csv.gz") for x in folder.glob(pat)
                  if not (stem == "vms" and x.name.startswith("vm_metrics")))
    return alts[0] if alts else None


def load(folder: Path, as_of: datetime | None = None) -> Data:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"no folder {folder}")
    sp = folder / "settings.json"
    s = settings_from_dict(json.loads(sp.read_text(encoding="utf-8"))) if sp.exists() else Settings()
    when = as_of or s.as_of or datetime.now().replace(second=0, microsecond=0)
    paths = {n: _find(folder, n) for n in FILES}
    if not paths["costs.csv"]:
        raise FileNotFoundError(f"{folder}: need costs.csv ({FILES['costs.csv']})")
    d = Data(s, when)
    for n, (attr, fn) in LOADERS.items():
        if paths[n]:
            setattr(d, attr, fn(paths[n]))
    if not d.costs:
        raise ValueError(f"{paths['costs.csv']}: no cost rows (check the BillingPeriodStart or ChargePeriodStart column)")
    d.sources = {n: len(getattr(d, attr)) for n, (attr, _) in LOADERS.items() if paths[n]}
    return d


__all__ = ["Data", "Settings", "Cost", "VM", "Metric", "Disk", "PublicIP", "Commitment", "Budget", "Unit", "load", "size_down", "parse_tags"]
