# Monthly cost review

A monthly review of the Azure bill, run in the first week of the month on the month just closed, ready for the **monthly FinOps review** with the cost centre owners and the cloud platform team. The tool only reads exports; resizes, schedules, deletions and commitment purchases or exchanges are made in Azure, through the usual change process.

It complements a daily or weekly anomaly check on spend (for example the cost anomaly dashboard of repo 01): that one catches the week that looks wrong while it happens; this one decides, once a month, what to cut, what to commit and who pays for what.

## The monthly cycle

| When | What | From the outputs |
|---|---|---|
| Day 1–3 | The FOCUS export of the closed month lands in the storage account (Cost Management > Exports, monthly, type *Cost and usage details (FOCUS)*); finance sends the budgets, the business owners the units | – |
| Day 3–4 | Run `Export-AzCostReviewData.ps1` (inventory, 30 days of metrics, commitments) and the review | `savings-pack.md`, `report.html` |
| Day 4 | FinOps lead reads the pack, checks the largest savings against Azure Advisor and the price sheet, and sends each owner their lines | `rightsizing.csv`, `waste.csv` filtered by owner |
| Day 5–7 | Monthly FinOps review (below) | The pack |
| During the month | Changes go through the CAB; deletions after the owner says yes | `actions.csv` |

## Export and review

1. Export (read-only):
   ```powershell
   .\scripts\Export-AzCostReviewData.ps1 -OutputFolder .\data -FocusExport .\downloads\part_0_0001.csv.gz
   ```
2. Add `budgets.csv` and `units.csv` (copy last month's `output/showback.csv` costs into the `effective_cost` of last month's units), and check `settings.json` (`month`, `as_of`, thresholds, owners).
3. Build the outputs:
   ```bash
   python -m azcostreview review data/ --month 2026-10
   ```

## Monthly FinOps review (45 minutes)

| Minutes | Topic | From the pack | Who |
|---|---|---|---|
| 5 | Verdict and headlines: spend, budget, savings identified | Headlines | FinOps lead |
| 10 | Showback: cost centres over budget or forecast over, the unallocated cost and who will tag it, unit cost | 5, 6 | Cost centre owners, finance |
| 10 | Commitments: utilisation, expiries, auto-renew, coverage | 4 | FinOps lead, cloud platform |
| 15 | Resizes, idle VMs and waste: decide each line | 2, 3 | Cloud platform, application owners |
| 5 | Actions, owners and dates; what was done since last month | Actions | FinOps lead |

Keep each month's folder (exports, pack, CSVs). Next month's pack starts with what was decided this month.

## How to decide a resize

- **Look at the peak, not the average.** The tool uses the 95th percentile of the hourly CPU maximum and the lowest available memory over 30 days. A month-end batch or a quarterly close may not be in the window: ask the owner before resizing a finance or reporting VM.
- **One size down, same family.** Half the vCPUs in the same family and generation keeps the same memory per vCPU, disk and network features; the projected CPU and memory use after the resize are in the report. Halve only if both stay under about 80%.
- **Database servers need a second look.** SQL Server uses the memory it is given; low free memory may be the buffer pool, not need. Check the SQL Server maximum memory setting and the waits before resizing.
- **A covered VM saves nothing on its own.** If a reservation covers it, the saving comes when the freed capacity covers another VM (instance size flexibility does this within the same group) or the reservation is exchanged.
- **Resize in a change window, check two weeks later.** A resize restarts the VM. Record the change and look at the metrics after two weeks.

## How to decide a commitment

- **Fix the waste first, then commit.** Resize and schedule before buying, otherwise the reservation fixes today's oversizing for three years.
- **Under 80% is money paid for nothing.** Widen the scope to shared, check instance size flexibility, or exchange the unused part for the sizes in use. Never let one auto-renew at low utilisation.
- **Renew what is used, before it expires.** Expiry turns the covered VMs back to pay-as-you-go overnight. Put every expiry within 90 days on the agenda.
- **Size new commitments on the steady base.** Production VMs that run all month are the candidates; the 30% in the pack is an upper estimate. Use the Azure Advisor or Cost Management recommendations for the amount, and a savings plan where the sizes will keep changing.

## How to record decisions

- In the pack: the *Decision* column of the resize and commitment tables, and *Decisions of the meeting* under Notes.
- In `actions.csv`: owner, due date, decision (do, defer with a reason, reject with a reason) and done. Import it into the tracker the team uses.
- A rejected resize needs a reason (month-end batch, licence tied to cores, vendor support): next month it is still in the list, and the reason saves the discussion.
- Savings are counted when the change is made, from the next month's export, not when they are identified.

## How to read it

- **Stopped is not deallocated.** A VM shut down from inside the guest OS keeps its compute allocated and billed; only *Stop* in the portal, `Stop-AzVM` or a schedule deallocates it.
- **An unattached disk is billed in full.** Disks stay when a VM is deleted; snapshot the ones the owner wants to keep and delete the rest.
- **Untagged cost has no owner.** Every month it stays untagged, nobody is asked about it. Tag the resource groups, or use a tag inheritance policy and Azure Policy to require the tags.
- **Unit cost is the number the business reads.** Cost going up with volume is growth; cost going up while volume falls needs an explanation.
