#Requires -RunAsAdministrator

param(
    [string]$DumpFolder = "C:\ai\LIARA\logs\dumps\embedding",
    [ValidateRange(1, 10)]
    [int]$DumpCount = 3,
    [ValidateSet(1, 2)]
    [int]$DumpType = 2
)

$ErrorActionPreference = "Stop"

$dumpKey = "HKLM:\Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\LiaraEmbeddingService.exe"

New-Item -ItemType Directory -Force -Path $DumpFolder | Out-Null
New-Item -Force -Path $dumpKey | Out-Null
New-ItemProperty -Path $dumpKey -Name DumpFolder -Value $DumpFolder -PropertyType ExpandString -Force | Out-Null
New-ItemProperty -Path $dumpKey -Name DumpCount -Value $DumpCount -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $dumpKey -Name DumpType -Value $DumpType -PropertyType DWord -Force | Out-Null

[pscustomobject]@{
    ok = $true
    scope = "HKLM"
    executable = "LiaraEmbeddingService.exe"
    dump_folder = $DumpFolder
    dump_count = $DumpCount
    dump_type = $DumpType
} | ConvertTo-Json
