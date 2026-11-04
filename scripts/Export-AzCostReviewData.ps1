<#
.SYNOPSIS
    Read-only export of the Azure inventory, metrics and commitments that azcostreview reads: virtual machines, 30 days of CPU and memory
    metrics, managed disks, public IP addresses, reservations and savings plans; and a copy of your Cost Management FOCUS export.

.DESCRIPTION
    Only read operations. Nothing is created, changed, resized, stopped, deallocated or deleted, and no secret is written to disk.

    Az PowerShell modules (Az.Accounts, Az.Compute, Az.Network, Az.Monitor), signed in with Connect-AzAccount. The Reader role on the
    subscriptions is enough for the inventory and the metrics; the reservations and savings plans need Reader on them (or the Reservations
    Reader role at tenant level).

      - vms.csv          Get-AzVM -Status in every subscription: name, resource group, size, power state ('VM running', 'VM stopped',
                         'VM deallocated'), OS and tags; vCPUs and memory from Get-AzVMSize -Location (NumberOfCores, MemoryInMB).
      - vm_metrics.csv   Get-AzMetric per running or stopped VM over the last -Days days at a one-hour grain: 'Percentage CPU' (Average and
                         Maximum) and 'Available Memory Bytes' (Minimum). The script computes the average and the 95th percentile of the hourly
                         CPU maximum, the 5th percentile of the hourly minimum available memory (GB), and the hours with data (running hours).
                         One call per VM and metric: for a few hundred VMs this takes a few minutes.
      - disks.csv        Get-AzDisk: Sku, DiskSizeGB, DiskState (Attached, Unattached, Reserved...), ManagedBy, LastOwnershipUpdateTime,
                         TimeCreated and tags.
      - public_ips.csv   Get-AzPublicIpAddress: Sku, allocation method, address, the IP configuration or NAT gateway it is associated with.
      - commitments.csv  GET https://management.azure.com/providers/Microsoft.Capacity/reservations?api-version=2022-11-01 (Reservation - List
                         All) and GET https://management.azure.com/providers/Microsoft.BillingBenefits/savingsPlans?api-version=2022-11-01
                         (Savings Plan - List All) through Invoke-AzRestMethod: display name, SKU, scope, term, quantity or hourly commitment,
                         the 1, 7 and 30-day utilisation aggregates, expiry and renew. Both are GET requests.
      - costs.csv        With -FocusExport, a copy of the file of your Cost Management export of type "Cost and usage details (FOCUS)" for the
                         month (CSV, or CSV compressed with gzip), downloaded from the export's storage account. The export itself is set up
                         once in Cost Management > Exports; this script does not create or run it.

    Not exported by this script (see docs/data-format.md): budgets.csv (finance) and units.csv (the business metric per cost centre), and
    settings.json.

.PARAMETER OutputFolder
    Where the CSV files are written (default .\data).

.PARAMETER SubscriptionId
    Subscriptions to read (default: every enabled subscription the account can see).

.PARAMETER Days
    Days of metrics (default 30).

.PARAMETER FocusExport
    Path of the FOCUS export file of the month, downloaded from the storage account (part_0_0001.csv, or .csv.gz). Copied as costs.csv or
    costs.csv.gz. For an export split in several parts, combine them first or pass the first one and add the others by hand.

.PARAMETER SkipMetrics
    Do not read the metrics (for a quick inventory run).

.EXAMPLE
    .\scripts\Export-AzCostReviewData.ps1 -OutputFolder .\data -FocusExport .\downloads\part_0_0001.csv.gz

.EXAMPLE
    .\scripts\Export-AzCostReviewData.ps1 -OutputFolder .\data -SubscriptionId '00000000-0000-0000-0000-000000000001' -Days 14
#>
[CmdletBinding()]
param(
    [string] $OutputFolder = '.\data',
    [string[]] $SubscriptionId,
    [int] $Days = 30,
    [string] $FocusExport,
    [switch] $SkipMetrics
)

$ErrorActionPreference = 'Stop'
Import-Module Az.Accounts, Az.Compute, Az.Network, Az.Monitor
New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null
if (-not (Get-AzContext)) { Connect-AzAccount | Out-Null }

function Write-Csv([object[]] $Rows, [string] $Name, [string[]] $Columns) {
    $path = Join-Path $OutputFolder $Name
    @($Rows) | Select-Object -Property $Columns | Export-Csv -Path $path -NoTypeInformation -Encoding utf8
    Write-Host ("{0,7} rows -> {1}" -f @($Rows).Count, $path)
}

function Format-Date($Value) {
    if (-not $Value) { return '' }
    return ([datetime] $Value).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}

function ConvertTo-TagJson($Tags) {
    if (-not $Tags -or $Tags.Count -eq 0) { return '' }
    $h = [ordered]@{}
    foreach ($k in $Tags.Keys) { $h[$k] = $Tags[$k] }
    return ($h | ConvertTo-Json -Compress)
}

function Get-Percentile([double[]] $Values, [double] $P) {
    if (-not $Values -or $Values.Count -eq 0) { return $null }
    $s = $Values | Sort-Object
    $i = [math]::Ceiling($P / 100 * $s.Count) - 1
    return $s[[math]::Max(0, [math]::Min($s.Count - 1, $i))]
}

$subs = if ($SubscriptionId) { $SubscriptionId | ForEach-Object { Get-AzSubscription -SubscriptionId $_ } }
        else { Get-AzSubscription | Where-Object { $_.State -eq 'Enabled' } }

$vmRows = @(); $metricRows = @(); $diskRows = @(); $ipRows = @()
$end = (Get-Date).ToUniversalTime().Date
$start = $end.AddDays(-$Days)
$sizeCache = @{}

foreach ($sub in $subs) {
    Set-AzContext -SubscriptionId $sub.Id | Out-Null
    Write-Host "Subscription $($sub.Name)"

    # ------------------------------------------------------------ virtual machines
    foreach ($vm in Get-AzVM -Status) {
        $key = "$($vm.Location)|$($vm.HardwareProfile.VmSize)"
        if (-not $sizeCache.ContainsKey($vm.Location)) {
            foreach ($s in Get-AzVMSize -Location $vm.Location) { $sizeCache["$($vm.Location)|$($s.Name)"] = $s }
            $sizeCache[$vm.Location] = $true
        }
        $size = $sizeCache[$key]
        $vmRows += [pscustomobject]@{
            SubscriptionId    = $sub.Id
            Subscription      = $sub.Name
            ResourceGroupName = $vm.ResourceGroupName
            Name              = $vm.Name
            Id                = $vm.Id
            Location          = $vm.Location
            VmSize            = $vm.HardwareProfile.VmSize
            vCPUs             = if ($size) { $size.NumberOfCores } else { '' }
            MemoryGB          = if ($size) { [math]::Round($size.MemoryInMB / 1024, 2) } else { '' }
            PowerState        = $vm.PowerState
            OsType            = $vm.StorageProfile.OsDisk.OsType
            Tags              = ConvertTo-TagJson $vm.Tags
        }
        if ($SkipMetrics -or $vm.PowerState -eq 'VM deallocated') { continue }
        $cpuAvg = Get-AzMetric -ResourceId $vm.Id -MetricName 'Percentage CPU' -TimeGrain 01:00:00 -AggregationType Average -StartTime $start -EndTime $end -WarningAction SilentlyContinue
        $cpuMax = Get-AzMetric -ResourceId $vm.Id -MetricName 'Percentage CPU' -TimeGrain 01:00:00 -AggregationType Maximum -StartTime $start -EndTime $end -WarningAction SilentlyContinue
        $mem = Get-AzMetric -ResourceId $vm.Id -MetricName 'Available Memory Bytes' -TimeGrain 01:00:00 -AggregationType Minimum -StartTime $start -EndTime $end -WarningAction SilentlyContinue
        $avg = @($cpuAvg.Data | Where-Object { $null -ne $_.Average } | ForEach-Object { [double] $_.Average })
        $max = @($cpuMax.Data | Where-Object { $null -ne $_.Maximum } | ForEach-Object { [double] $_.Maximum })
        $free = @($mem.Data | Where-Object { $null -ne $_.Minimum } | ForEach-Object { [double] $_.Minimum / 1GB })
        $metricRows += [pscustomobject]@{
            ResourceId             = $vm.Id
            Name                   = $vm.Name
            window_hours           = $Days * 24
            running_hours          = $avg.Count
            cpu_avg_pct            = if ($avg.Count) { [math]::Round(($avg | Measure-Object -Average).Average, 1) } else { '' }
            cpu_p95_pct            = if ($max.Count) { [math]::Round((Get-Percentile $max 95), 1) } else { '' }
            available_memory_p5_gb = if ($free.Count) { [math]::Round((Get-Percentile $free 5), 2) } else { '' }
        }
    }

    # ------------------------------------------------------------ managed disks
    foreach ($d in Get-AzDisk) {
        $diskRows += [pscustomobject]@{
            SubscriptionId          = $sub.Id
            Subscription            = $sub.Name
            ResourceGroupName       = $d.ResourceGroupName
            Name                    = $d.Name
            Id                      = $d.Id
            Sku                     = $d.Sku.Name
            DiskSizeGB              = $d.DiskSizeGB
            DiskState               = $d.DiskState
            ManagedBy               = $d.ManagedBy
            LastOwnershipUpdateTime = Format-Date $d.LastOwnershipUpdateTime
            TimeCreated             = Format-Date $d.TimeCreated
            Tags                    = ConvertTo-TagJson $d.Tags
        }
    }

    # ------------------------------------------------------------ public IP addresses
    foreach ($p in Get-AzPublicIpAddress) {
        $ipRows += [pscustomobject]@{
            SubscriptionId           = $sub.Id
            Subscription             = $sub.Name
            ResourceGroupName        = $p.ResourceGroupName
            Name                     = $p.Name
            Id                       = $p.Id
            Sku                      = $p.Sku.Name
            PublicIpAllocationMethod = $p.PublicIpAllocationMethod
            IpAddress                = $p.IpAddress
            IpConfigurationId        = $p.IpConfiguration.Id
            NatGatewayId             = $p.NatGateway.Id
            Tags                     = ConvertTo-TagJson $p.Tag
        }
    }
}

Write-Csv $vmRows 'vms.csv' @('SubscriptionId', 'Subscription', 'ResourceGroupName', 'Name', 'Id', 'Location', 'VmSize', 'vCPUs', 'MemoryGB',
    'PowerState', 'OsType', 'Tags')
if (-not $SkipMetrics) {
    Write-Csv $metricRows 'vm_metrics.csv' @('ResourceId', 'Name', 'window_hours', 'running_hours', 'cpu_avg_pct', 'cpu_p95_pct', 'available_memory_p5_gb')
}
Write-Csv $diskRows 'disks.csv' @('SubscriptionId', 'Subscription', 'ResourceGroupName', 'Name', 'Id', 'Sku', 'DiskSizeGB', 'DiskState', 'ManagedBy',
    'LastOwnershipUpdateTime', 'TimeCreated', 'Tags')
Write-Csv $ipRows 'public_ips.csv' @('SubscriptionId', 'Subscription', 'ResourceGroupName', 'Name', 'Id', 'Sku', 'PublicIpAllocationMethod', 'IpAddress',
    'IpConfigurationId', 'NatGatewayId', 'Tags')

# ---------------------------------------------------------------- reservations and savings plans (GET only)
function Get-AllPages([string] $Uri) {
    $items = @()
    $next = $Uri
    while ($next) {
        $r = Invoke-AzRestMethod -Method GET -Uri $next
        if ($r.StatusCode -ne 200) { Write-Warning "GET $next returned $($r.StatusCode)"; break }
        $body = $r.Content | ConvertFrom-Json
        $items += @($body.value)
        $next = $body.nextLink
    }
    return $items
}

function Get-Utilisation($Props, [int] $Grain) {
    $a = @($Props.utilization.aggregates | Where-Object { $_.grain -eq $Grain })
    if ($a.Count) { return $a[0].value }
    return ''
}

$commitRows = @()
foreach ($r in Get-AllPages 'https://management.azure.com/providers/Microsoft.Capacity/reservations?api-version=2022-11-01') {
    $p = $r.properties
    if ($p.provisioningState -in @('Cancelled', 'Expired', 'Split', 'Merged')) { continue }
    $commitRows += [pscustomobject]@{
        id = $r.id; displayName = $p.displayName; type = 'Reservation'; sku = $r.sku.name; reservedResourceType = $p.reservedResourceType
        appliedScopeType = $p.appliedScopeType; appliedScopeDisplayName = $p.appliedScopeProperties.displayName; term = $p.term; quantity = $p.quantity
        commitmentAmount = ''; utilization1Day = Get-Utilisation $p 1; utilization7Days = Get-Utilisation $p 7; utilization30Days = Get-Utilisation $p 30
        expiryDate = $p.expiryDate; renew = $p.renew
    }
}
foreach ($s in Get-AllPages 'https://management.azure.com/providers/Microsoft.BillingBenefits/savingsPlans?api-version=2022-11-01') {
    $p = $s.properties
    if ($p.provisioningState -in @('Cancelled', 'Expired')) { continue }
    $commitRows += [pscustomobject]@{
        id = $s.id; displayName = $p.displayName; type = 'Savings Plan'; sku = $s.sku.name; reservedResourceType = ''
        appliedScopeType = $p.appliedScopeType; appliedScopeDisplayName = $p.appliedScopeProperties.displayName; term = $p.term; quantity = ''
        commitmentAmount = $p.commitment.amount; utilization1Day = Get-Utilisation $p 1; utilization7Days = Get-Utilisation $p 7
        utilization30Days = Get-Utilisation $p 30; expiryDate = Format-Date $p.expiryDateTime; renew = $p.renew
    }
}
Write-Csv $commitRows 'commitments.csv' @('id', 'displayName', 'type', 'sku', 'reservedResourceType', 'appliedScopeType', 'appliedScopeDisplayName', 'term',
    'quantity', 'commitmentAmount', 'utilization1Day', 'utilization7Days', 'utilization30Days', 'expiryDate', 'renew')

# ---------------------------------------------------------------- the FOCUS export (copy only)
if ($FocusExport) {
    $target = if ($FocusExport -like '*.gz') { 'costs.csv.gz' } else { 'costs.csv' }
    Copy-Item -Path $FocusExport -Destination (Join-Path $OutputFolder $target)
    Write-Host "        FOCUS export copied -> $(Join-Path $OutputFolder $target)"
} else {
    Write-Warning 'No -FocusExport: put the Cost Management FOCUS export of the month in the folder as costs.csv (or costs.csv.gz).'
}

Write-Host "Done. Add budgets.csv (and units.csv), check settings.json, then run: python -m azcostreview review $OutputFolder"
