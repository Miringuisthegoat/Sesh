# Pre-release checks on the built agent. Usage (project root):  .\build\smoke.ps1
# Does NOT replace the clean-machine test in docs/PILOT_CHECKLIST.md.
$ErrorActionPreference = "Stop"
$fail = 0
function Check($ok, $msg) { if ($ok) { Write-Host "PASS  $msg" } else { Write-Host "FAIL  $msg"; $script:fail++ } }
$setup = "dist\installer\SeshAgentSetup.exe"
Check (Test-Path $setup) "installer exists ($setup)"
if (Test-Path $setup) { Write-Host ("      SHA-256: " + (Get-FileHash $setup -Algorithm SHA256).Hash + "  size: " + [math]::Round((Get-Item $setup).Length / 1MB, 1) + " MB") }
Check (Test-Path "dist\SeshAgent\SeshAgent.exe") "SeshAgent.exe built"
$url = if (Test-Path "dist\SeshAgent\server_url.txt") { (Get-Content "dist\SeshAgent\server_url.txt" -TotalCount 1).Trim() } else { "" }
Check ($url -match '^https://') "server_url.txt holds an https address ($url)"
$bad = Get-ChildItem dist\SeshAgent -Recurse -Include ".env","agent.db","*.log","*.pem","*.key" -ErrorAction SilentlyContinue
Check (-not $bad) "no secrets, databases or logs inside the build"
$sum = Get-ChildItem dist\SeshAgent -Recurse -Include "SumatraPDF.exe" -ErrorAction SilentlyContinue
Write-Host ("INFO  SumatraPDF bundled: " + [bool]$sum + " (decision 4: licence review still open; not bundled by default)")
$sig = if (Test-Path $setup) { (Get-AuthenticodeSignature $setup).Status } else { "n/a" }
Write-Host "INFO  Authenticode signature: $sig (unsigned installers trigger Windows SmartScreen warnings)"
if ($fail) { Write-Host "$fail check(s) failed"; exit 1 } else { Write-Host "All checks passed" }
