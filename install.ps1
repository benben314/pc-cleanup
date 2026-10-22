<#
.SYNOPSIS
    pc-cleanup Skill installer (Windows)

.DESCRIPTION
    Installs the pc-cleanup skill into the WorkBuddy user-level skill directory.

    Two ways to use:
      1) From the cloned repo:
         powershell -ExecutionPolicy Bypass -File .\install.ps1
      2) One-liner from GitHub:
         irm https://raw.githubusercontent.com/benben314/pc-cleanup/main/install.ps1 | iex

    NOTE: This file is intentionally pure ASCII. Windows PowerShell 5.1 reads
    BOM-less UTF-8 as ANSI, which garbles non-ASCII text and can break parsing.

.PARAMETER Dest
    Target directory. Default: %USERPROFILE%\.workbuddy-ai\skills\pc-cleanup

.PARAMETER Force
    Overwrite without asking. The existing install is backed up first.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Force
    .\install.ps1 -Dest "D:\skills\pc-cleanup"
#>
[CmdletBinding()]
param(
    [string]$Dest,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$RepoOwner = 'benben314'
$RepoName  = 'pc-cleanup'
$Branch    = 'main'
$SkillName = 'pc-cleanup'

function Info($msg) { Write-Host "[..] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!]  $msg" -ForegroundColor Yellow }

if (-not $Dest) {
    $Dest = Join-Path $env:USERPROFILE ".workbuddy-ai\skills\$SkillName"
}

Write-Host ""
Write-Host "  pc-cleanup installer" -ForegroundColor White
Write-Host "  --------------------" -ForegroundColor DarkGray
Info "target: $Dest"

# ---- 1. locate source ----
$localSkill = $null
if ($PSScriptRoot) {
    $localSkill = Join-Path $PSScriptRoot 'SKILL.md'
}

$tmp = $null

if ($localSkill -and (Test-Path $localSkill)) {
    Ok "source: local repo directory"
    $src = $PSScriptRoot
}
else {
    Info "source: GitHub ($RepoOwner/$RepoName@$Branch)"
    $tmp = Join-Path $env:TEMP ('pc-cleanup-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $zip = Join-Path $tmp 'repo.zip'
    $url = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Branch.zip"

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Info "download: $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $tmp -Force

    $root = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    if (-not $root) { throw "Cannot find extracted repo directory" }
    $src = $root.FullName
    Ok "downloaded"
}

# ---- 2. handle existing target ----
if (Test-Path $Dest) {
    if (-not $Force) {
        Warn "target already exists: $Dest"
        $ans = Read-Host "    overwrite? old version will be backed up [y/N]"
        if ($ans -notmatch '^[Yy]') {
            Write-Host "    cancelled, nothing changed." -ForegroundColor DarkGray
            if ($tmp -and (Test-Path $tmp)) { Remove-Item $tmp -Recurse -Force }
            return
        }
    }
    $bak = "$Dest.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Move-Item -Path $Dest -Destination $bak -Force
    Warn "old version backed up to: $bak"
}

# ---- 3. copy ----
Info "copying files..."
New-Item -ItemType Directory -Path $Dest -Force | Out-Null

foreach ($f in @('SKILL.md', 'README.md', 'LICENSE', '.gitignore')) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item -LiteralPath $p -Destination $Dest -Force }
}

foreach ($d in @('references', 'scripts')) {
    $p = Join-Path $src $d
    if (Test-Path $p) { Copy-Item -LiteralPath $p -Destination $Dest -Recurse -Force }
}

Get-ChildItem -Path $Dest -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

# ---- 4. verify ----
$skillMd = Join-Path $Dest 'SKILL.md'
if (-not (Test-Path $skillMd)) { throw "install failed: SKILL.md not found" }

$head = Get-Content -LiteralPath $skillMd -Raw
if ($head -notmatch 'name:\s*pc-cleanup') { throw "install failed: SKILL.md missing 'name: pc-cleanup'" }

foreach ($s in @('scan.py', 'clean.py')) {
    if (-not (Test-Path (Join-Path $Dest "scripts\$s"))) { throw "install failed: scripts\$s not found" }
}

# ---- 5. done ----
if ($tmp -and (Test-Path $tmp)) { Remove-Item $tmp -Recurse -Force }

Write-Host ""
Ok "install complete"
Write-Host "    path: $Dest" -ForegroundColor Gray
Write-Host "    files:" -ForegroundColor Gray
Get-ChildItem -Path $Dest -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($Dest.Length + 1)
    Write-Host ("      {0,-28} {1,8} B" -f $rel, $_.Length) -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "    next: restart WorkBuddy (or open a new chat), then say 'clean up my PC'." -ForegroundColor White
Write-Host ""
