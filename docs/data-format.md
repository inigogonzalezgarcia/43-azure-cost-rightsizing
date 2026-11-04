# Data format

Put the files in one folder and run `python -m azcostreview review <folder>`. `costs.csv` (or `costs.csv.gz`) is required; every other file adds its checks. Comma, semicolon or tab separated, UTF-8. Column names are matched loosely (case, accents and punctuation are ignored), and the alternatives below are accepted. Dates as ISO 8601 (`2026-10-01T00:00:00Z`, the FOCUS 1.0r2 format, or `2026-10-01T00:00Z`), `yyyy-MM-dd`, `dd/mm/yyyy` or `MM/dd/yyyy HH:mm`; the time zone suffix is ignored. Percentages as `55`, `55 %` or `0.55`. Resource ids are compared in lower case, so the inventory and the cost export join whatever case each tool uses.

`scripts/Export-AzCostReviewData.ps1` writes `vms.csv`, `vm_metrics.csv`, `disks.csv`, `public_ips.csv` and `commitments.csv` with the Az PowerShell modules, and copies your FOCUS export as `costs.csv`. `budgets.csv` and `units.csv` come from finance and the business.

## How the review works

| Step | Rule |
|---|---|
| Month | `month` in settings.json (`2026-10`), `--month`, or the month before the reference date. Rows are in the month by `BillingPeriodStart` |
| Spend | Effective cost (`EffectiveCost`, amortised): commitment purchases are spread over the usage they cover. Billed cost (`BilledCost`) is shown next to it |
| Run rate | A resource's monthly cost at the rate of the last seven days of the month (`sum / 7 × days in the month`). Every saving uses it, so a VM stopped on the 12th still shows its full monthly cost |
| Cost centre | The first tag of `tag_keys.cost_centre` on the cost row (FOCUS `Tags`); on the inventory files, the resource's own tags first |
| Forecast | The current month's cost so far (days before the reference date) divided by the days elapsed, times the days in the month |
| Severity | Critical makes the area **action needed**, a warning **attention**; the overall verdict is the worst area |

## costs.csv (FOCUS 1.0)

The file of a Cost Management export of type *Cost and usage details (FOCUS)*, daily rows, one month or more. Only these columns are read; the export has more and they are ignored. Column names are the FOCUS 1.0 ones as Microsoft exports them (see the Microsoft Learn page *FOCUS cost and usage details file schema*).

| Column | Notes |
|---|---|
| BillingPeriodStart | The month the row belongs to |
| ChargePeriodStart | The day; used for the run rate and the forecast |
| ChargeCategory | `Usage`, `Purchase`, `Tax`, `Credit`, `Adjustment`. Commitment savings use `Usage` rows only |
| ResourceId, ResourceName | Joins the inventory files |
| x_ResourceType | Azure Resource Manager type (`microsoft.compute/virtualmachines`, `microsoft.compute/disks`…). Without it, `ServiceName` Virtual Machines marks VM rows |
| ServiceName, ServiceCategory | "Where the money went" |
| SubAccountName, x_ResourceGroupName | Subscription and resource group, to locate unallocated cost |
| PricingCategory | `Standard` (pay-as-you-go), `Committed` (reservation or savings plan), `Dynamic` (spot) |
| CommitmentDiscountId, CommitmentDiscountType, CommitmentDiscountStatus | The reservation or savings plan, `Reservation` or `Savings Plan`, and `Used` or `Unused`. Unused rows are the commitment paid and not used |
| ConsumedQuantity | Hours of VM rows: a covered VM still has pay-as-you-go rows for its Windows or SQL Server licence, so coverage is measured in hours |
| ListCost, EffectiveCost, BilledCost | Savings from commitments = `ListCost - EffectiveCost` of the `Used` rows; unused = `EffectiveCost` of the `Unused` rows |
| BillingCurrency | Shown in the outputs |
| Tags | JSON object (`{"costcentre":"CC-1100","env":"prod"}`); exports that drop the braces and `key=value;key=value` are also read |

## vms.csv

`Get-AzVM -Status` with the size's cores and memory from `Get-AzVMSize`.

| Column | Also accepted | Notes |
|---|---|---|
| Id, Name, SubscriptionId, Subscription, ResourceGroupName, Location | | |
| VmSize | size | `Standard_D8s_v5`. One size down halves the vCPUs in the same family, features and version (`Standard_E16s_v5` → `Standard_E8s_v5`); constrained-core sizes (`E16-4s_v5`) and 2 vCPUs are left alone |
| vCPUs, MemoryGB | NumberOfCores, MemoryInMB | |
| PowerState | | `VM running`; `VM stopped` is still allocated and billed (WST-01); `VM deallocated` is not |
| OsType, Tags | | Tags as JSON or `key=value;…`: cost centre, owner, env and schedule (`tag_keys`) |

## vm_metrics.csv

Thirty days of Azure Monitor platform metrics per VM, from `Get-AzMetric` at a one-hour grain.

| Column | Notes |
|---|---|
| ResourceId | |
| window_hours, running_hours | Hours in the window and hours with data. Running share = running / window: at least `always_on_share` for a non-production VM is "always on" (WST-02), at least `steady_share` for a production VM is "steady" (COM-03) |
| cpu_avg_pct | Average of `Percentage CPU` |
| cpu_p95_pct | 95th percentile of the hourly maximum of `Percentage CPU`: under `rs_cpu_p95` for a resize (RS-01), under `idle_cpu_p95` for idle (RS-02) |
| available_memory_p5_gb | 5th percentile of the hourly minimum of `Available Memory Bytes`, in GB. Peak memory use = 1 - this / MemoryGB, under `rs_mem_used` for a resize |

## disks.csv

`Get-AzDisk`: Id, Name, Subscription, ResourceGroupName, Sku, DiskSizeGB, DiskState, ManagedBy, LastOwnershipUpdateTime, TimeCreated, Tags. A disk with DiskState `Unattached` (or no state and no ManagedBy) whose LastOwnershipUpdateTime (TimeCreated when empty) is at least `unattached_days` old is WST-03. `Reserved` disks belong to deallocated VMs and are not waste.

## public_ips.csv

`Get-AzPublicIpAddress`: Id, Name, Subscription, ResourceGroupName, Sku, PublicIpAllocationMethod, IpAddress, IpConfigurationId, NatGatewayId, Tags. No IP configuration and no NAT gateway is WST-04.

## commitments.csv

Reservations (`Microsoft.Capacity/reservations`) and savings plans (`Microsoft.BillingBenefits/savingsPlans`).

| Column | Notes |
|---|---|
| id, displayName, type, sku, reservedResourceType, appliedScopeType, term | The id joins `CommitmentDiscountId` in the costs |
| quantity, commitmentAmount | Reservation quantity; savings plan hourly commitment |
| utilization1Day, utilization7Days, utilization30Days | The utilisation aggregates of the API. Without them, the 30-day figure is worked out from the used and unused rows of the month. Under `util_warning` is COM-01 (critical under `util_critical`) |
| expiryDate, renew | Expiring within `expiry_days` is COM-02: critical when it auto-renews under `util_warning`, a warning within `decision_days`, information otherwise |

## budgets.csv

| Column | Notes |
|---|---|
| cost_centre, name, owner | The owner gets the SHB-02 and SHB-03 findings |
| month, budget | One row per cost centre and month: the reviewed month for SHB-02 (critical over `budget_critical`), the current month for the forecast (SHB-03) |

## units.csv (optional)

| Column | Notes |
|---|---|
| month, cost_centre, unit, units | A business volume per cost centre: claims processed, policies in force, active users |
| effective_cost | For past months, the cost of the cost centre from that month's showback.csv; empty for the month reviewed (it is worked out). A unit cost up more than `unit_cost_warning` on the previous month is SHB-04 |

## settings.json

| Setting | Default | Used by |
|---|---|---|
| as_of, month, organisation, currency | now, previous month | Reference date, month reviewed |
| rs_cpu_p95, rs_mem_used, rs_min_saving | 40, 0.4, 20 | RS-01: thresholds and the smallest saving worth a line |
| idle_cpu_p95 | 5 | RS-02 |
| nonprod_envs, always_on_share, office_hours_per_week | dev, test, uat, sandbox; 0.9; 60 | WST-02 and its saving (`1 - office hours / 168`) |
| unattached_days | 30 | WST-03 |
| util_warning, util_critical, expiry_days, decision_days | 0.8, 0.6, 90, 60 | COM-01, COM-02 |
| coverage_target, steady_share, commitment_discount | 0.6, 0.95, 0.3 | COM-03: an estimate on the whole VM cost; licences are not discounted, so it is an upper bound |
| gap_warning, gap_critical, budget_critical, unit_cost_warning | 0.05, 0.1, 1.1, 0.1 | SHB-01, SHB-02, SHB-04 |
| tag_keys | costcentre/costcenter…, owner, env/environment, schedule | Tag names for each purpose, first match wins |
| owners, cost_centres | | Owner per area (rightsizing, waste, commitments, showback); cost centre names |
