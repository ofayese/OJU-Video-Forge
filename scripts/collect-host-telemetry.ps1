<#
.SYNOPSIS
Collects ERE Windows host and NVIDIA GPU telemetry for OJU inference admission.

Outputs exactly one JSON object to stdout. Exits 0 when a usable observation is
produced, non-zero when collection itself fails. GPU capacity and process
attribution are distinct: this script reports aggregate GPU capacity only and
never attempts WDDM process attribution.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [int64]$Sequence = 0,

    [Parameter(Mandatory = $false)]
    [string]$SourceId = "ere-host",

    [Parameter(Mandatory = $false)]
    [string]$CollectorVersion = "1.0.0"
)

$ErrorActionPreference = "Stop"

function Write-Result {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Result
    )
    $Result | ConvertTo-Json -Depth 10
}

try {
    # --- Available physical memory in bytes (Win32_OperatingSystem.FreePhysicalMemory is KB) ---
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -Property FreePhysicalMemory | Select-Object -First 1
    if (-not $os -or $os.FreePhysicalMemory -eq $null) {
        throw "Win32_OperatingSystem query returned no FreePhysicalMemory"
    }
    $freePhysicalMemoryKb = [int64]$os.FreePhysicalMemory
    if ($freePhysicalMemoryKb -le 0) {
        throw "Available physical memory is not positive: $freePhysicalMemoryKb KB"
    }
    $availableRamBytes = $freePhysicalMemoryKb * 1024

    # --- NVIDIA GPU aggregate capacity (total/free VRAM in bytes). ---
    # nvidia-smi is used for capacity only; WDDM process attribution is out of scope.
    $gpuTotalBytes = $null
    $gpuFreeBytes = $null
    $gpuCapacityOk = $false
    $collectionErrors = @()

    $nvidiaSmi = Get-Command -Name "nvidia-smi" -ErrorAction SilentlyContinue
    if ($nvidiaSmi) {
        try {
            $csv = & nvidia-smi --query-gpu=index,name,memory.total,memory.free,driver_version --format=csv,noheader,nounits
            if ($LASTEXITCODE -ne 0) {
                throw "nvidia-smi exited with code $LASTEXITCODE"
            }
            # Split by newline explicitly so single-line output is still an array.
            # PowerShell captures single-line native output as a scalar string,
            # and -split unrolls single-element arrays back to a scalar.
            # @() forces array context so index-based access always works.
            $lines = @($csv -split "`r?`n" | Where-Object { $_.Trim() -ne "" })
            if ($lines.Count -eq 0) {
                throw "nvidia-smi returned no GPU records"
            }
            # Aggregate capacity from the first GPU; process attribution is distinct and not collected here.
            $columns = $lines[0] -split ","
            if ($columns.Count -lt 4) {
                throw "nvidia-smi output has unexpected column count: $($columns.Count)"
            }
            $gpuTotalMiB = [int64]$columns[2].Trim()
            $gpuFreeMiB = [int64]$columns[3].Trim()
            if ($gpuTotalMiB -le 0) {
                throw "GPU total memory is not positive: $gpuTotalMiB MiB"
            }
            if ($gpuFreeMiB -lt 0) {
                throw "GPU free memory is negative: $gpuFreeMiB MiB"
            }
            if ($gpuFreeMiB -gt $gpuTotalMiB) {
                throw "GPU free memory ($gpuFreeMiB MiB) exceeds total ($gpuTotalMiB MiB)"
            }
            $gpuTotalBytes = $gpuTotalMiB * 1024 * 1024
            $gpuFreeBytes = $gpuFreeMiB * 1024 * 1024
            $gpuCapacityOk = $true
        }
        catch {
            $gpuCapacityOk = $false
            $collectionErrors += "nvidia-smi collection failed: $($_.Exception.Message)"
        }
    }
    else {
        $collectionErrors += "nvidia-smi utility not found; GPU capacity unavailable"
    }

    # Derive both timestamps from a single UTC instant so collected_at and
    # iso_timestamp always represent the same measurement moment.
    $now = [DateTimeOffset]::UtcNow
    # collected_at is a Unix-epoch seconds number (with sub-second precision)
    # so the coordinator can compare it against time.time(); iso_timestamp keeps
    # the human-readable ISO-8601 form derived from the same instant.
    $collectedAtUnix = [double]$now.ToUnixTimeMilliseconds() / 1000.0
    $result = @{
        collected_at          = $collectedAtUnix
        max_age_seconds       = 30
        source_id             = [string]$SourceId
        available_ram_bytes   = $availableRamBytes
        gpu_capacity_ok       = [bool]$gpuCapacityOk
        gpu_free_bytes        = $gpuFreeBytes
        gpu_total_bytes       = $gpuTotalBytes
        measurement_status    = "ok"
        collector_version     = [string]$CollectorVersion
        host_id               = [string]$env:COMPUTERNAME
        sequence              = [int64]$Sequence
        iso_timestamp         = $now.UtcDateTime.ToString("o")
    }
    if ($collectionErrors.Count -gt 0) {
        $result["errors"] = $collectionErrors
    }

    Write-Result -Result $result
    exit 0
}
catch {
    $now = [DateTimeOffset]::UtcNow
    $collectedAtUnix = [double]$now.ToUnixTimeMilliseconds() / 1000.0
    $result = @{
        collected_at         = $collectedAtUnix
        max_age_seconds      = 30
        source_id            = [string]$SourceId
        available_ram_bytes  = $null
        gpu_capacity_ok      = $false
        gpu_free_bytes       = $null
        gpu_total_bytes      = $null
        measurement_status   = "error"
        collector_version    = [string]$CollectorVersion
        host_id              = [string]$env:COMPUTERNAME
        sequence             = [int64]$Sequence
        iso_timestamp        = $collectedAt.ToString("o")
        errors               = @("collection_failed: $($_.Exception.Message)")
    }
    Write-Result -Result $result
    exit 1
}
