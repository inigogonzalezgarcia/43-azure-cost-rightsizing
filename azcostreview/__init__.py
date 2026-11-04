"""Monthly Azure cost review: right-sizing (VMs one size down, idle VMs), waste (VMs stopped but not deallocated, non-production VMs on around
the clock, unattached disks, unassociated public IPs), commitments (reservation and savings plan utilisation, expiry and coverage) and showback
(cost by cost centre against budget and forecast, unallocated cost, unit cost), from the Azure Cost Management FOCUS export and read-only Az
exports; with a verdict for the month and the pack for the monthly FinOps review."""
__version__ = "1.0.0"
