# Read-only reverse-engineering inventory. Run through winboat.run_script so
# the existing session guard, credential pipe and 600 s launch bound apply.
# Does not start Studio, send a job, install tools, or copy vendor resources.
param(
  [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$report = Join-Path $OutputDirectory 'inventory.json'
$state = [ordered]@{
  started = (Get-Date -Format o)
  complete = $false
  studio = $null
  captureTools = @()
  usbDevices = @()
  nativeSamples = @()
  errors = @()
}
try {
  $root = 'C:\Program Files\Silhouette America\Silhouette Studio'
  $exe = Get-Item (Join-Path $root 'Silhouette Studio.exe')
  $state.studio = [ordered]@{
    path = $exe.FullName
    fileVersion = $exe.VersionInfo.FileVersion
    productVersion = $exe.VersionInfo.ProductVersion
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe.FullName).Hash
  }
  # Check known paths only: absence here does not prove a tool is absent.
  $candidates = @(
    'C:\Program Files\Wireshark\tshark.exe',
    'C:\Program Files\Wireshark\dumpcap.exe',
    'C:\Program Files\USBPcap\USBPcapCMD.exe',
    'C:\Program Files\Wireshark\extcap\USBPcapCMD.exe',
    'C:\Windows\System32\drivers\USBPcap.sys'
  )
  $state.captureTools = @($candidates | ForEach-Object {
    [ordered]@{ path = $_; exists = (Test-Path -LiteralPath $_) }
  })
  try {
    $state.usbDevices = @(Get-CimInstance Win32_PnPEntity -Filter "PNPDeviceID LIKE 'USB%'" |
      Select-Object Name, PNPDeviceID, Status)
  } catch { $state.errors += 'USB inventory query failed' }
  # Bounded roots, not the entire user profile. Only metadata and the first
  # 64 bytes are recorded; the proprietary documents themselves stay in place.
  $roots = @($root, 'C:\Users\Public\Documents',
    [Environment]::GetFolderPath('MyDocuments')) | Select-Object -Unique
  foreach ($sampleRoot in $roots) {
    if (!(Test-Path -LiteralPath $sampleRoot)) { continue }
    $scanErrors = @()
    $files = @(Get-ChildItem -LiteralPath $sampleRoot -Recurse -File -ErrorAction SilentlyContinue -ErrorVariable scanErrors |
      Where-Object { $_.Extension -in @('.studio', '.studio3') })
    if ($scanErrors.Count) { $state.errors += "Some sample paths under $sampleRoot were unreadable" }
    foreach ($file in $files) {
      $stream = $null
      try {
        $stream = [System.IO.File]::OpenRead($file.FullName)
        $buffer = New-Object byte[] 64
        $count = $stream.Read($buffer, 0, $buffer.Length)
        $header = [BitConverter]::ToString($buffer, 0, $count).Replace('-', '')
        $state.nativeSamples += [ordered]@{
          path = $file.FullName
          bytes = $file.Length
          sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
          headerHex = $header
        }
      } catch { $state.errors += "Could not inspect sample $($file.FullName)" }
      finally { if ($null -ne $stream) { $stream.Dispose() } }
    }
  }
  $state.complete = $true
} catch { $state.errors += 'Inventory failed before completion' }
finally {
  $state['finished'] = (Get-Date -Format o)
  $state | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $report
}
if (!$state.complete) { exit 1 }
