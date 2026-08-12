param(
    [string]$DumpFolder = "C:\ai\LIARA\logs\dumps",
    [int]$DumpCount = 5,
    [int]$DumpType = 2
)

$ErrorActionPreference = "Stop"

$dumpKey = "HKCU:\Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\LiaraEmbeddingService.exe"

New-Item -ItemType Directory -Force -Path $DumpFolder | Out-Null
New-Item -Force -Path $dumpKey | Out-Null
New-ItemProperty -Path $dumpKey -Name DumpFolder -Value $DumpFolder -PropertyType ExpandString -Force | Out-Null
New-ItemProperty -Path $dumpKey -Name DumpCount -Value $DumpCount -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $dumpKey -Name DumpType -Value $DumpType -PropertyType DWord -Force | Out-Null

[pscustomobject]@{
    ok = $true
    scope = "HKCU"
    executable = "LiaraEmbeddingService.exe"
    dump_folder = $DumpFolder
    dump_count = $DumpCount
    dump_type = $DumpType
} | ConvertTo-Json
