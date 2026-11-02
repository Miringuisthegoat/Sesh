# Build SeshAgentSetup.exe.  Usage (project root, venv active):  .\build\build.ps1 -ServerUrl https://sesh.example.com
param([Parameter(Mandatory = $true)][string]$ServerUrl)
$ErrorActionPreference = "Stop"
if ($ServerUrl -notmatch '^https://') { throw "ServerUrl must start with https://" }
pip install -r requirements-agent.txt
Remove-Item -Recurse -Force build\work, dist\SeshAgent -ErrorAction SilentlyContinue
pyinstaller --noconfirm --workpath build\work --distpath dist build\SeshAgent.spec
Set-Content -Path dist\SeshAgent\server_url.txt -Value $ServerUrl -Encoding ascii   # default address shown at first launch
$iscc = @("$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it from https://jrsoftware.org/isinfo.php" }
& $iscc build\installer.iss
Write-Host "Done: dist\installer\SeshAgentSetup.exe"
