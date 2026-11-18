# Run manually on Windows with Silhouette Studio installed.
# Opens a generated DXF and selects it; never sends a cut or print.
# Output remains local: screenshots may include the current Studio workspace.
param(
  [Parameter(Mandatory=$true)][string]$OutputDirectory,
  [Parameter(Mandatory=$true)][string]$DxfPath
)
$ErrorActionPreference = 'Stop' 
$out = $OutputDirectory
New-Item -ItemType Directory -Force $out | Out-Null
$report = Join-Path $out 'report.txt'
Set-Content $report "START $(Get-Date -Format o)"
try {
  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $exe = 'C:\Program Files\Silhouette America\Silhouette Studio\Silhouette Studio.exe'
  $p = Get-Process | Where-Object { $_.ProcessName -eq 'Silhouette Studio' } | Select-Object -First 1
  if (!$p) { $p = Start-Process $exe -PassThru }
  Start-Sleep -Seconds 15
  $shell = New-Object -ComObject WScript.Shell
  if (!$shell.AppActivate($p.Id)) { throw 'Studio could not be activated' }
  Start-Sleep -Seconds 2
  [System.Windows.Forms.SendKeys]::SendWait('^o')
  Start-Sleep -Seconds 2
  # Clipboard avoids SendKeys interpreting punctuation in a filename.
  [System.Windows.Forms.Clipboard]::SetText($DxfPath)
  [System.Windows.Forms.SendKeys]::SendWait('^v')
  [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
  Start-Sleep -Seconds 8
  [System.Windows.Forms.SendKeys]::SendWait('^a')
  Start-Sleep -Seconds 2
  Add-Content $report "VERSION $((Get-Item $exe).VersionInfo.FileVersion)"
  $desktop = [System.Windows.Automation.AutomationElement]::RootElement
  $condition = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $p.Id)
  $windows = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)
  foreach ($w in $windows) {
    Add-Content $report "WINDOW $($w.Current.Name)"
    $elements = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    Add-Content $report "ELEMENTS $($elements.Count)"
    foreach ($e in $elements) {
      $c = $e.Current
      Add-Content $report "$($c.ControlType.ProgrammaticName) | $($c.AutomationId) | $($c.Name) | $($c.BoundingRectangle)"
    }
  }
  $r = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap($r.Width, $r.Height)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
  $bmp.Save((Join-Path $out 'screen.png'))
  $g.Dispose(); $bmp.Dispose()
  Add-Content $report 'COMPLETE'
} catch { Add-Content $report "FAILED $($_.Exception.Message)"; exit 1 }
