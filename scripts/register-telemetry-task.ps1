# OJU Video Forge - ERE host telemetry publisher scheduled-task registration.
#
# Registers the OJU host telemetry publisher as a Windows Scheduled Task so
# signed observations are published on a 10-second cadence.
#
# Prerequisites:
#   - Run from an elevated PowerShell session (Register-ScheduledTask requires it).
#   - The publisher script publish-host-telemetry.py must exist alongside this file.
#   - A Python interpreter must exist at the path given by -PythonExe.
#
# Security:
#   - Runs under a specified development account (default: ERE\akoso).
#   - No dedicated service account is created.
#   - Run level is Limited (not elevated); the publisher needs no admin rights.
#   - No secrets are embedded: the publisher reads the HMAC key from the
#     protected file mount / environment configuration.
#
# Parameters:
#   -TaskName        Name of the scheduled task (default: 'OJU Host Telemetry Publisher')
#   -Account         Windows account under which the task runs (default: 'ERE\akoso')
#   -PythonExe       Path to the python.exe interpreter
#   -Script          Path to publish-host-telemetry.py
#   -WorkDir         Working directory for the publisher (default: scripts parent)
#   -StartImmediate  Whether to start the task immediately after registration
#
# Examples:
#   .\scripts\register-telemetry-task.ps1
#   .\scripts\register-telemetry-task.ps1 -Account 'ERE\deploy' -StartImmediate:$false

[CmdletBinding()]
param(
    [string]$TaskName = 'OJU Host Telemetry Publisher',
    [string]$Account = 'ERE\akoso',
    [string]$PythonExe = "$PSScriptRoot\..\.venv\Scripts\python.exe",
    [string]$Script = "$PSScriptRoot\publish-host-telemetry.py",
    [string]$WorkDir = "$PSScriptRoot\..",
    [switch]$StartImmediate
)

$ErrorActionPreference = 'Stop'

# Preconditions
if (-not ([Security.Principal.WindowsPrincipal]::new(
            [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator))) {
    throw 'This script must be run from an elevated PowerShell session.'
}
if (-not (Test-Path $PythonExe)) { throw "Python interpreter not found: $PythonExe" }
if (-not (Test-Path $Script)) { throw "Publisher script not found: $Script" }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity -ne $Account) {
    Write-Warning "Elevated identity is $identity (expected $Account); continuing."
}

# The publisher daemon enforces the 10-second cadence internally; Task Scheduler
# only launches the daemon (logon + boot triggers). A 10s task repetition is not
# permitted by Task Scheduler (minimum 1 minute) and is not needed here.
$action = New-ScheduledTaskAction -Execute $PythonExe `
    -Argument ('-X utf8 "' + $Script + '" --daemon') `
    -WorkingDirectory $WorkDir

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $Account

# Boot trigger with 60s delay for reboot recovery.
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$bootTrigger.Delay = 'PT60S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# Run level: Limited (not elevated) - publisher needs no admin rights at runtime.
$principal = New-ScheduledTaskPrincipal -UserId $Account -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger $bootTrigger, $logonTrigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'OJU Video Forge host telemetry publisher (signed observation every 10s).' | Out-Null

Write-Output "Registered task: $TaskName"

if ($StartImmediate) {
    # Activate immediately for this session's validation.
    Start-ScheduledTask -TaskName $TaskName
    Write-Output 'Task started.'
}

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
