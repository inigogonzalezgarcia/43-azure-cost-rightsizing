"""Command line: `demo` writes the fictional Litware Group exports to sample_data/ and builds the outputs; `review` runs on your own exports."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import report, simulate
from .checks import _n, month_label, money, overall, pct, review, run_all, summary
from .model import load, parse_dt

OUTPUTS = ("report.html", "savings-pack.md", "rightsizing.csv", "waste.csv", "commitments.csv", "showback.csv", "actions.csv", "findings.csv")


def run(data: Path, out: Path, title: str | None = None, as_of: datetime | None = None, month: str | None = None) -> dict:
    d = load(data, as_of)
    if month:
        d.settings.month = month
    rv = review(d)
    findings = run_all(d, rv)
    title = title or f"{d.settings.organisation} · Azure cost review, {month_label(d.month)}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(report.render_html(d, rv, findings, title), encoding="utf-8")
    (out / "savings-pack.md").write_text(report.pack_md(d, rv, findings, title), encoding="utf-8")
    report.write_rightsizing(d, rv, out / "rightsizing.csv")
    report.write_waste(d, rv, out / "waste.csv")
    report.write_commitments(d, rv, out / "commitments.csv")
    report.write_showback(d, rv, out / "showback.csv")
    report.write_actions(findings, out / "actions.csv")
    report.write_findings(findings, out / "findings.csv")
    sm = summary(d, rv)
    sm.update(overall=overall(findings), critical=sum(f.severity == "critical" for f in findings), warning=sum(f.severity == "warning" for f in findings))
    return sm


def _when(value: str) -> datetime:
    d = parse_dt(value)
    if not d:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD or 'YYYY-MM-DD HH:MM'): {value}")
    return d


def _month(value: str) -> str:
    from .model import month_key
    m = month_key(value)
    if not m:
        raise argparse.ArgumentTypeError(f"not a month (YYYY-MM): {value}")
    return m


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="azcostreview", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    dm = sub.add_parser("demo", help="write the fictional exports to sample_data/ and build the outputs")
    dm.add_argument("--data", type=Path, default=Path("sample_data"))
    dm.add_argument("--out", type=Path, default=Path("output"))
    rv = sub.add_parser("review", help="build the outputs from your own exports")
    rv.add_argument("data", type=Path, nargs="?", help="folder with costs.csv (or costs.csv.gz) and the other exports")
    rv.add_argument("--data", dest="data_opt", type=Path, help="same as the positional folder")
    rv.add_argument("--out", type=Path, default=Path("output"))
    rv.add_argument("--title", help="title of the report and the pack")
    rv.add_argument("--month", type=_month, help="month to review, YYYY-MM (default: settings.json, or the month before the reference date)")
    rv.add_argument("--as-of", type=_when, help="reference date (default: settings.json, or now)")
    a = p.parse_args(argv)
    if a.cmd == "demo":
        simulate.write(a.data)
        sm = run(a.data, a.out, f"{simulate.ORG} · Azure cost review, October 2026 (review of 4 November)")
    else:
        folder = a.data or a.data_opt
        if not folder:
            print("error: give the folder with the exports", file=sys.stderr)
            return 2
        try:
            sm = run(folder, a.out, a.title, a.as_of, a.month)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    c = sm["currency"]
    print(f"{sm['overall'].upper()} · {month_label(sm['month'])}: spend {money(sm['spend'], c)} (billed {money(sm['billed'], c)}) · commitments "
          f"saved {money(sm['net_savings'], c)} net, coverage {pct(sm['coverage'])} · savings identified {money(sm['identified'], c)} a month "
          f"(right-sizing {money(sm['rightsizing'], c)}, waste {money(sm['waste'], c)}, commitments {money(sm['commitment_saving'], c)}) · "
          f"unallocated {pct(sm['gap'], 1)} · {_n(sm['over_budget'], 'cost centre')} over budget · {sm['critical']} critical, {sm['warning']} warnings")
    print(f"outputs in {a.out}/: {', '.join(OUTPUTS)}")
    return 0
