param(
  [Parameter(Mandatory=$true)][string]$ServerUrl,
  [Parameter(Mandatory=$true)][string]$ServerPublicKey,
  [string]$BrowserHosts = '',
  [string]$HttpHosts = '',
  [string]$MailHosts = ''
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { throw 'Build the Windows runner on Windows.' }
if (-not $ServerUrl.StartsWith('https://')) { throw 'Customer builds require the real HTTPS portal origin.' }
if ((node --version) -ne 'v24.21.0') { throw 'Node 24.21.0 LTS is required.' }
if (-not $env:CSC_LINK -and -not $env:CSC_NAME) { throw 'Configure the Windows signing certificate in the build secret store.' }
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
$DesktopRoot = Join-Path $ProjectRoot 'desktop'
Push-Location $ProjectRoot
try {
  py -3.12 -m venv (Join-Path $DesktopRoot '.build-python')
  if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
  $Python = Join-Path $DesktopRoot '.build-python/Scripts/python.exe'
  & $Python -m pip install -r desktop/requirements-runtime.txt -r desktop/requirements-build.txt
  if ($LASTEXITCODE -ne 0) { throw 'Pinned Python dependency installation failed.' }
  $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $DesktopRoot '.browser-build'
  & $Python -m playwright install chromium --only-shell
  if ($LASTEXITCODE -ne 0) { throw 'Native Chromium installation failed.' }
  & $Python desktop/scripts/build-runner.py
  if ($LASTEXITCODE -ne 0) { throw 'Native runner build failed.' }
  & $Python desktop/scripts/smoke-runner.py
  if ($LASTEXITCODE -ne 0) { throw 'Native signed-lease file/CSV/Excel smoke failed.' }
  $env:MULTIRPA_DESKTOP_SERVER_URL = $ServerUrl
  $env:MULTIRPA_DESKTOP_SERVER_PUBLIC_KEY = $ServerPublicKey
  $env:MULTIRPA_DESKTOP_BROWSER_HOSTS = $BrowserHosts
  $env:MULTIRPA_DESKTOP_HTTP_HOSTS = $HttpHosts
  $env:MULTIRPA_DESKTOP_MAIL_HOSTS = $MailHosts
  $env:MULTIRPA_DESKTOP_RELEASE = '1'
  Push-Location $DesktopRoot
  try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Pinned desktop dependency installation failed.' }
    node node_modules/electron/install.js
    if ($LASTEXITCODE -ne 0) { throw 'Electron binary installation failed.' }
    npm test
    if ($LASTEXITCODE -ne 0) { throw 'Desktop protocol tests failed.' }
    npm run build:windows
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed.' }
  } finally { Pop-Location }
} finally { Pop-Location }
# Configure CSC_LINK / CSC_KEY_PASSWORD in the build secret store to sign releases.
# No installer is automatically published by this script.
