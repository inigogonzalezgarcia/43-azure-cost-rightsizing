import csv
import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from azcostreview import cli
from azcostreview.checks import CRITICAL, INFO, ORDER, WARNING, area_status, overall, review, run_all, summary
from azcostreview.model import (Budget, Commitment, Cost, Data, Disk, Metric, PublicIP, Unit, VM, load, load_commitments, load_costs, load_disks,
                                load_ips, load_metrics, load_vms, month_key, parse_tags, settings_from_dict, size_down)

AS_OF = datetime(2026, 11, 4, 8, 0)          # Wednesday; the month reviewed is October 2026
SUB = "/subscriptions/00000000-0000-0000-0000-000000000001"
TAGS = {"costcentre": "CC-1", "env": "prod", "owner": "ana@contoso.example"}


def write(path: Path, header: list[str], rows: list[list], delimiter: str = ",") -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(header)
        w.writerows(rows)
    return path


def vm_id(name: str) -> str:
    return f"{SUB}/resourcegroups/rg-app/providers/microsoft.compute/virtualmachines/{name}"


def daily(rid, per_day, month="2026-10", days=31, arm="microsoft.compute/virtualmachines", tags=None, pricing="Standard", qty=24, **kw) -> list[Cost]:
    """One cost row a day for the month: per_day euros of effective and list cost."""
    y, m = (int(x) for x in month.split("-"))
    out = []
    lst = kw.pop("list_cost", None)
    for k in range(days):
        day = date(y, m, 1) + timedelta(days=k)
        out.append(Cost(month, day, "Usage", rid, rid.rsplit("/", 1)[-1], arm, "", "Virtual Machines", "Compute", "West Europe", SUB, "sub-app", "rg-app",
                        pricing, kw.get("cd_id", ""), kw.get("cd_type", ""), kw.get("cd_status", ""), qty, "Hours",
                        per_day if lst is None else lst, per_day, 0 if pricing == "Committed" else per_day, "EUR", dict(TAGS if tags is None else tags)))
    return out


def vm(name, size="Standard_D8s_v5", power="VM running", vcpus=8, mem=32, tags=None) -> VM:
    return VM(vm_id(name), name, SUB, "sub-app", "rg-app", "westeurope", size, vcpus, mem, power, "Linux", dict(TAGS if tags is None else tags))


def metric(name, p95=60, mem_free=16, hours=720) -> Metric:
    return Metric(vm_id(name), p95 / 2, p95, mem_free, hours, 720)


def data(costs=(), vms=(), metrics=(), disks=(), ips=(), commitments=(), budgets=(), units=(), **raw) -> Data:
    d = Data(settings_from_dict(raw), AS_OF, list(costs), list(vms), {m.id: m for m in metrics}, list(disks), list(ips), list(commitments),
             list(budgets), list(units))
    return d


def codes(findings, code):
    return [f for f in findings if f.code == code]


def issues(rv, code):
    return [i for i in rv["issues"] if i.code == code]


class TestLoading(unittest.TestCase):
    def test_focus_costs_tags_and_gzip(self):
        header = ["BillingPeriodStart", "ChargePeriodStart", "ChargeCategory", "ResourceId", "ResourceName", "x_ResourceType", "ServiceName",
                  "SubAccountName", "x_ResourceGroupName", "PricingCategory", "CommitmentDiscountId", "CommitmentDiscountType",
                  "CommitmentDiscountStatus", "ConsumedQuantity", "ListCost", "EffectiveCost", "BilledCost", "BillingCurrency", "Tags"]
        rows = [["2026-10-01T00:00:00Z", "2026-10-05T00:00:00Z", "Usage", vm_id("VM1").upper(), "VM1", "Microsoft.Compute/virtualMachines",
                 "Virtual Machines", "sub-app", "RG-APP", "Committed", "/providers/microsoft.capacity/reservationorders/o/reservations/r",
                 "Reservation", "Used", "24", "10.5", "6.3", "0", "EUR", '{"CostCentre":"CC-1","env":"prod"}'],
                ["2026-10-01T00:00Z", "2026-10-05T00:00Z", "Usage", "", "RI", "", "Virtual Machines", "sub-app", "", "Committed", "r", "Reservation",
                 "Unused", "3", "0", "1.2", "0", "EUR", '"env": "dev"']]
        with tempfile.TemporaryDirectory() as t:
            p = write(Path(t) / "c.csv", header, rows)
            gz = Path(t) / "costs.csv.gz"
            gz.write_bytes(gzip.compress(p.read_bytes()))
            a, b = load_costs(p), load_costs(gz)
        self.assertEqual([(c.period, c.day, c.resource_id, c.arm_type, c.rg, c.is_vm, c.committed, c.unused) for c in a],
                         [(c.period, c.day, c.resource_id, c.arm_type, c.rg, c.is_vm, c.committed, c.unused) for c in b])
        c0, c1 = a
        self.assertEqual((c0.period, c0.day, c0.resource_id, c0.is_vm, c0.committed, c0.unused, c0.list_cost, c0.effective, c0.tags["costcentre"]),
                         ("2026-10", date(2026, 10, 5), vm_id("vm1"), True, True, False, 10.5, 6.3, "CC-1"))
        self.assertEqual((c1.unused, c1.tags), (True, {"env": "dev"}))
        self.assertEqual(parse_tags("env=dev;owner=x"), {"env": "dev", "owner": "x"})
        self.assertEqual((month_key("10/2026"), month_key("2026-9")), ("2026-10", "2026-09"))

    def test_inventory_metrics_disks_ips_and_commitments(self):
        with tempfile.TemporaryDirectory() as t:
            vp = write(Path(t) / "v.csv", ["Name", "Id", "Subscription", "ResourceGroupName", "VmSize", "NumberOfCores", "MemoryInMB", "PowerState",
                                           "Tags"], [["VM1", vm_id("VM1"), "sub-app", "RG-APP", "Standard_E16s_v5", "16", "131072", "VM stopped",
                                                      "env=dev;costcentre=CC-1"]], delimiter=";")
            mp = write(Path(t) / "m.csv", ["ResourceId", "cpu_avg_pct", "cpu_p95_pct", "available_memory_p5_gb", "running_hours", "window_hours"],
                       [[vm_id("VM1"), "5,5", "12.0", "80", "700", "720"]])
            dp = write(Path(t) / "d.csv", ["Name", "Id", "Sku", "DiskSizeGB", "DiskState", "ManagedBy", "LastOwnershipUpdateTime"],
                       [["d1", "/x/d1", "Premium_LRS", "1024", "Unattached", "", "2026-08-27T18:41:00Z"]])
            ip = write(Path(t) / "i.csv", ["Name", "Id", "Sku", "IpConfigurationId", "NatGatewayId"], [["p1", "/x/p1", "Standard", "", ""],
                                                                                                      ["p2", "/x/p2", "Standard", "", "/x/nat"]])
            cp = write(Path(t) / "c.csv", ["id", "displayName", "type", "sku", "term", "quantity", "utilization30Days", "expiryDate", "renew"],
                       [["/providers/Microsoft.Capacity/reservationOrders/o/reservations/r", "RI1", "", "Standard_F8s_v2", "P1Y", "10", "55 %",
                         "2026-12-15", "On"],
                        ["/providers/microsoft.billingbenefits/savingsplanorders/o/savingsplans/s", "SP1", "", "", "P3Y", "", "0.991", "2029-03-31",
                         "false"]])
            v, m, dk, ips, cm = load_vms(vp)[0], load_metrics(mp), load_disks(dp)[0], load_ips(ip), load_commitments(cp)
        self.assertEqual((v.id, v.vcpus, v.memory_gb, v.stopped_billed, v.running, v.rg, v.tags["env"]), (vm_id("vm1"), 16, 128, True, False, "rg-app", "dev"))
        self.assertEqual((m[vm_id("vm1")].cpu_avg, m[vm_id("vm1")].hours), (5.5, 700))
        self.assertEqual((dk.unattached, dk.last_owner), (True, datetime(2026, 8, 27, 18, 41)))
        self.assertEqual([x.unassociated for x in ips], [True, False])
        self.assertEqual([(c.kind, c.util_30d, c.renew) for c in cm], [("Reservation", 0.55, True), ("Savings Plan", 0.991, False)])

    def test_size_down(self):
        self.assertEqual([size_down(x) for x in ("Standard_E16s_v5", "Standard_D8ds_v5", "Standard_F8s_v2", "Standard_D2s_v5", "Standard_E16-4s_v5",
                                                 "Standard_B2ms", "Standard_D96as_v5", "weird")],
                         ["Standard_E8s_v5", "Standard_D4ds_v5", "Standard_F4s_v2", None, None, None, "Standard_D48as_v5", None])


class TestChecks(unittest.TestCase):
    def test_rightsizing_and_idle(self):
        vms = [vm("big"), vm("busy"), vm("fullmem"), vm("idle"), vm("covered")]
        mets = [metric("big", 20, 24), metric("busy", 70, 24), metric("fullmem", 20, 8), metric("idle", 2, 30), metric("covered", 20, 24)]
        cst = daily(vm_id("big"), 20) + daily(vm_id("busy"), 20) + daily(vm_id("fullmem"), 20) + daily(vm_id("idle"), 10) \
            + daily(vm_id("covered"), 12, pricing="Committed", cd_id="r1", cd_type="Reservation", cd_status="Used", list_cost=20)
        rv = review(data(cst, vms, mets))
        rs = {i.resource: i for i in issues(rv, "RS-01")}
        self.assertEqual(sorted(rs), ["big", "covered"])
        self.assertAlmostEqual(rs["big"].saving, 20 * 31 / 2)
        self.assertIn("-> Standard_D4s_v5", rs["big"].text)
        self.assertIn("covered by a commitment", rs["covered"].text)
        self.assertEqual([(i.resource, round(i.saving)) for i in issues(rv, "RS-02")], [("idle", 310)])
        row = [r for r in rv["rightsizing"] if r["vm"].name == "big"][0]
        self.assertEqual((round(row["cpu_after"]), round(row["mem_after"], 2)), (40, 0.5))

    def test_stopped_not_deallocated_and_nonprod_always_on(self):
        dev = {"costcentre": "CC-1", "env": "dev", "owner": "o"}
        vms = [vm("stopped", power="VM stopped"), vm("dealloc", power="VM deallocated"), vm("dev24", tags=dev),
               vm("devsched", tags=dict(dev, schedule="office-hours")), vm("prod24")]
        mets = [metric("dev24"), metric("devsched", hours=250), metric("prod24")]
        cst = daily(vm_id("stopped"), 14) + daily(vm_id("dev24"), 7) + daily(vm_id("devsched"), 3) + daily(vm_id("prod24"), 7)
        rv = review(data(cst, vms, mets))
        st = issues(rv, "WST-01")
        self.assertEqual([(i.resource, i.severity, round(i.saving)) for i in st], [("stopped", CRITICAL, 434)])
        w2 = issues(rv, "WST-02")
        self.assertEqual([i.resource for i in w2], ["dev24"])
        self.assertAlmostEqual(w2[0].saving, 7 * 31 * (1 - 60 / 168))

    def test_unattached_disks_and_public_ips(self):
        base = f"{SUB}/resourcegroups/rg-old/providers"
        dks = [Disk(f"{base}/microsoft.compute/disks/old", "old", SUB, "sub-app", "rg-old", "Premium_LRS", 1024, "Unattached", "",
                    datetime(2026, 8, 27), None, {}),
               Disk(f"{base}/microsoft.compute/disks/new", "new", SUB, "sub-app", "rg-old", "Premium_LRS", 128, "Unattached", "",
                    datetime(2026, 10, 30), None, {}),
               Disk(f"{base}/microsoft.compute/disks/res", "res", SUB, "sub-app", "rg-old", "Premium_LRS", 128, "Reserved", vm_id("x"),
                    datetime(2026, 1, 1), None, {})]
        ips = [PublicIP(f"{base}/microsoft.network/publicipaddresses/p1", "p1", sku="Standard"),
               PublicIP(f"{base}/microsoft.network/publicipaddresses/p2", "p2", nat_gateway="/x/nat"),
               PublicIP(f"{base}/microsoft.network/publicipaddresses/p3", "p3", ip_config="/x/nic/ipconfigurations/ipconfig1")]
        cst = daily(dks[0].id, 4.5, arm="microsoft.compute/disks", tags={}) + daily(ips[0].id, 0.11, arm="microsoft.network/publicipaddresses")
        rv = review(data(cst, disks=dks, ips=ips))
        self.assertEqual([(i.resource, round(i.saving)) for i in issues(rv, "WST-03")], [("old", 140)])
        self.assertIn("unattached for 69 days", issues(rv, "WST-03")[0].text)
        self.assertEqual([i.resource for i in issues(rv, "WST-04")], ["p1"])

    def test_commitment_utilisation_expiry_and_savings(self):
        ri = "/providers/microsoft.capacity/reservationorders/o/reservations/r1"
        cms = [Commitment(ri, "RI-low", "Reservation", "Standard_F8s_v2", term="P1Y", quantity=10, util_30d=0.55, expiry=date(2026, 12, 15), renew=True),
               Commitment("r2", "RI-good", "Reservation", "Standard_E8s_v5", term="P1Y", quantity=6, util_30d=0.96, expiry=date(2027, 1, 20)),
               Commitment("r3", "RI-mid", "Reservation", "Standard_D4s_v5", term="P1Y", quantity=4, util_30d=0.7, expiry=date(2026, 12, 24)),
               Commitment("r4", "RI-rows", "Reservation", "Standard_D2s_v5", term="P1Y", quantity=2, expiry=date(2028, 1, 1))]
        cst = (daily(vm_id("a"), 6, pricing="Committed", cd_id=ri, cd_type="Reservation", cd_status="Used", list_cost=10)
               + daily(ri, 5, arm="microsoft.capacity/reservationorders/reservations", tags={}, pricing="Committed", cd_id=ri, cd_type="Reservation",
                       cd_status="Unused", list_cost=0)
               + daily(vm_id("b"), 3, pricing="Committed", cd_id="r4", cd_type="Reservation", cd_status="Used", list_cost=5)
               + daily("r4", 1, arm="microsoft.capacity/reservationorders/reservations", tags={}, pricing="Committed", cd_id="r4",
                       cd_status="Unused", list_cost=0))
        d = data(cst, commitments=cms)
        rv = review(d)
        c1 = {i.resource: i.severity for i in issues(rv, "COM-01")}
        c2 = {i.resource: i.severity for i in issues(rv, "COM-02")}
        self.assertEqual(c1, {"RI-low": CRITICAL, "RI-mid": WARNING, "RI-rows": WARNING})
        self.assertEqual(c2, {"RI-low": CRITICAL, "RI-good": INFO, "RI-mid": WARNING})
        self.assertIn("auto-renews at 55% utilisation", issues(rv, "COM-02")[0].text)
        rows = {r["c"].name: r for r in rv["commitments"]}
        self.assertEqual((round(rows["RI-low"]["unused"]), round(rows["RI-low"]["savings"]), round(rows["RI-rows"]["util"], 2)), (155, 124, 0.75))
        t = rv["totals"]
        self.assertEqual((round(t["gross_savings"]), round(t["unused"]), round(t["net_savings"])), (186, 186, 0))

    def test_coverage_of_steady_vms(self):
        vms = [vm(f"web{k}") for k in range(4)] + [vm("dev", tags={"costcentre": "CC-1", "env": "dev", "owner": "o", "schedule": "x"}), vm("ri")]
        mets = [metric(f"web{k}") for k in range(4)] + [metric("dev"), metric("ri")]
        cst = [c for k in range(4) for c in daily(vm_id(f"web{k}"), 20)] + daily(vm_id("dev"), 20) \
            + daily(vm_id("ri"), 12, pricing="Committed", cd_id="r", cd_type="Reservation", cd_status="Used", list_cost=20)
        rv = review(data(cst, vms, mets))
        cov = rv["coverage"]
        self.assertEqual((len(cov["steady"]), round(cov["coverage"], 3)), (4, round(20 / 120, 3)))
        self.assertAlmostEqual(issues(rv, "COM-03")[0].saving, 4 * 20 * 31 * 0.3)
        rv = review(data(cst, vms, mets, coverage_target=0.1))
        self.assertFalse(issues(rv, "COM-03"))

    def test_showback_gap_budget_forecast_and_unit_cost(self):
        a, b = {"costcentre": "CC-A", "env": "prod", "owner": "o"}, {"costcentre": "CC-B", "env": "prod", "owner": "o"}
        cst = (daily("/x/a", 100, arm="x", tags=a) + daily("/x/b", 50, arm="x", tags=b) + daily("/x/none", 20, arm="x", tags={})
               + daily("/x/a", 130, month="2026-11", days=3, arm="x", tags=a))
        bud = [Budget("CC-A", "Alpha", "2026-10", 2800, "Ann"), Budget("CC-B", "Beta", "2026-10", 1500), Budget("CC-A", "Alpha", "2026-11", 3500)]
        units = [Unit("2026-09", "CC-B", "order", 1000, 1300), Unit("2026-10", "CC-B", "order", 1000), Unit("2026-10", "CC-A", "user", 10)]
        d = data(cst, budgets=bud, units=units)
        rv = review(d)
        gap = issues(rv, "SHB-01")[0]
        self.assertEqual(gap.severity, CRITICAL)
        self.assertIn("11.8% of October 2026", gap.text)
        self.assertEqual([(i.resource, i.severity, i.owner) for i in issues(rv, "SHB-02")], [("CC-A", CRITICAL, "Ann"), ("CC-B", WARNING, "FinOps lead")])
        self.assertEqual([i.resource for i in issues(rv, "SHB-03")], ["CC-A"])
        self.assertIn("forecast €3,900", issues(rv, "SHB-03")[0].text)
        self.assertEqual([i.resource for i in issues(rv, "SHB-04")], ["CC-B"])
        self.assertIn("up 19%", issues(rv, "SHB-04")[0].text)
        rv = review(data(cst, budgets=bud, units=units, gap_critical=0.2, budget_critical=1.5))
        self.assertEqual([i.severity for i in issues(rv, "SHB-01") + issues(rv, "SHB-02")], [WARNING, WARNING, WARNING])

    def test_missing_tags_and_custom_tag_keys(self):
        vms = [vm("ok"), vm("bare", tags={}), vm("upper", tags={"costcenter": "CC-9", "environment": "prod", "techowner": "x"})]
        rv = review(data(daily(vm_id("ok"), 1), vms))
        self.assertIn("2 VMs missing tags: bare (cost centre, owner, env), upper (owner)", issues(rv, "SHB-05")[0].text)
        rv = review(data(daily(vm_id("ok"), 1), vms, tag_keys={"owner": ["owner", "techowner"]}))
        self.assertNotIn("upper", issues(rv, "SHB-05")[0].text)

    def test_verdict_and_area_status(self):
        quiet = data(daily(vm_id("ok"), 10), [vm("ok")], [metric("ok")], budgets=[Budget("CC-1", "", "2026-10", 1000)], coverage_target=0)
        f = run_all(quiet)
        self.assertEqual((overall(f), set(area_status(f).values())), ("on track", {"ok"}))
        f = run_all(data(daily(vm_id("ok"), 10), [vm("ok")], [metric("ok", 20, 24)]))
        self.assertEqual((overall(f), area_status(f)["Right-sizing"]), ("attention", WARNING))
        f = run_all(data(daily(vm_id("s"), 10), [vm("s", power="VM stopped")]))
        self.assertEqual((overall(f), area_status(f)["Waste"]), ("action needed", CRITICAL))
        self.assertEqual([x.severity for x in f], sorted((x.severity for x in f), key=ORDER.get))

    def test_settings_override_thresholds(self):
        vms, mets = [vm("big")], [metric("big", 30, 24)]
        cst = daily(vm_id("big"), 20)
        self.assertTrue(issues(review(data(cst, vms, mets)), "RS-01"))
        self.assertFalse(issues(review(data(cst, vms, mets, rs_cpu_p95=25)), "RS-01"))
        self.assertFalse(issues(review(data(cst, vms, mets, rs_min_saving=400)), "RS-01"))
        dev = [vm("d", tags={"costcentre": "C", "env": "uat", "owner": "o"})]
        self.assertTrue(issues(review(data(daily(vm_id("d"), 7), dev, [metric("d")])), "WST-02"))
        self.assertFalse(issues(review(data(daily(vm_id("d"), 7), dev, [metric("d")], nonprod_envs=["dev"])), "WST-02"))


class TestEndToEnd(unittest.TestCase):
    def test_demo_end_to_end_is_deterministic(self):
        hashes = []
        with tempfile.TemporaryDirectory() as t:
            for k in range(2):
                out = Path(t) / f"out{k}"
                self.assertEqual(cli.main(["demo", "--data", str(Path(t) / f"data{k}"), "--out", str(out)]), 0)
                self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(cli.OUTPUTS))
                hashes.append({n: hashlib.sha1((out / n).read_bytes()).hexdigest() for n in cli.OUTPUTS}
                              | {n.name: hashlib.sha1(n.read_bytes()).hexdigest() for n in (Path(t) / f"data{k}").iterdir()})
            self.assertEqual(hashes[0], hashes[1])
            self.assertEqual(json.loads((Path(t) / "data0" / "settings.json").read_text(encoding="utf-8"))["as_of"], "2026-11-04 08:00")
            d = load(Path(t) / "data0")
        rv = review(d)
        f = run_all(d, rv)
        sm = summary(d, rv)
        self.assertEqual(overall(f), "action needed")
        for c in ("RS-01", "RS-02", "WST-01", "WST-02", "WST-03", "WST-04", "COM-01", "COM-02", "COM-03", "SHB-01", "SHB-02", "SHB-03", "SHB-04",
                  "SHB-05"):
            self.assertIn(c, {x.code for x in f})
        self.assertEqual((d.settings.organisation, d.month, sm["vms"], sm["resize"], sm["idle"], sm["stopped"], sm["always_on"], sm["disks"], sm["ips"],
                          sm["over_budget"]), ("Litware Group", "2026-10", 133, 8, 2, 3, 14, 10, 5, 1))
        self.assertTrue(0.10 <= sm["gap"] <= 0.13)
        self.assertIn("RI-F8sv2-PolicyBatch", codes(f, "COM-01")[0].detail)
        self.assertIn("55% used", codes(f, "COM-01")[0].detail)
        self.assertEqual([x.severity for x in codes(f, "COM-02")], [CRITICAL, INFO])
        self.assertIn("CC-1300 Data and analytics", codes(f, "SHB-02")[0].detail)
        self.assertIn("Digital channels", codes(f, "SHB-04")[0].detail)
        self.assertTrue(all("halcyon" in x or "devold" in x for x in codes(f, "WST-03")[0].items))

    def test_pack_and_csv_outputs(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(cli.main(["demo", "--data", str(Path(t) / "d"), "--out", str(Path(t) / "o")]), 0)
            o = Path(t) / "o"
            pack = (o / "savings-pack.md").read_text(encoding="utf-8")
            html = (o / "report.html").read_text(encoding="utf-8")
            rs = list(csv.DictReader(open(o / "rightsizing.csv", encoding="utf-8")))
            sb = list(csv.DictReader(open(o / "showback.csv", encoding="utf-8")))
            act = list(csv.DictReader(open(o / "actions.csv", encoding="utf-8")))
            cm = list(csv.DictReader(open(o / "commitments.csv", encoding="utf-8")))
        for h in ("## Headlines", "## 1. Where the money went", "## 2. Savings identified", "## 3. Waste", "## 4. Commitments",
                  "## 5. Showback and budgets", "## 6. Unit cost", "## Actions", "## Sign-off"):
            self.assertIn(h, pack)
        self.assertEqual(len(rs), 133)
        self.assertEqual({r["recommendation"] for r in rs} >= {"resize", "keep", "schedule", "deallocate", "commit"}, True)
        self.assertEqual([r["cost_centre"] for r in sb][-1], "(unallocated)")
        total = sum(float(r["effective_cost"]) for r in sb)
        self.assertIn(f"€{total:,.0f}", pack)
        self.assertTrue(all(a["severity"] in ("critical", "warning") for a in act))
        self.assertEqual(len(cm), 4)
        self.assertIn("Showback by cost centre", html)
        self.assertNotIn("\r", pack)

    def test_review_reports_missing_files(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(cli.main(["review", t, "--out", str(Path(t) / "o")]), 2)
            self.assertEqual(cli.main(["review", "--out", str(Path(t) / "o")]), 2)
            write(Path(t) / "costs.csv", ["BillingPeriodStart"], [])
            self.assertEqual(cli.main(["review", t, "--out", str(Path(t) / "o")]), 2)
            write(Path(t) / "costs.csv", ["BillingPeriodStart", "ChargePeriodStart", "ResourceId", "EffectiveCost", "Tags"],
                  [["2026-10-01", "2026-10-02", "/x/a", "12.5", '{"costcentre":"CC-1"}']])
            self.assertEqual(cli.main(["review", t, "--out", str(Path(t) / "o"), "--as-of", "2026-11-04", "--month", "2026-10"]), 0)


if __name__ == "__main__":
    unittest.main()
