<#
    SH Download Manager - browser extension helper.

    Chromium browsers refuse silent installation of local (non-Web-Store)
    extensions, so this helper prepares the folder, opens it in Explorer and
    copies its path to the clipboard. The user then finishes the one-time
    "Load unpacked" step in the browser.
#>
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms | Out-Null

$appDir    = Split-Path -Parent $PSScriptRoot
$source    = Join-Path $appDir 'browser_extension'
$userRoot  = Join-Path $env:APPDATA 'SHDownloadManager'
$userExt   = Join-Path $userRoot 'browser_extension'

# Keep a writable copy in %APPDATA% (the app refreshes this copy on every start).
if (Test-Path (Join-Path $source 'manifest.json')) {
    New-Item -ItemType Directory -Force -Path $userRoot | Out-Null
    Copy-Item -Path $source -Destination $userRoot -Recurse -Force
}
$ext = if (Test-Path (Join-Path $userExt 'manifest.json')) { $userExt } else { $source }

function Find-Browser([string]$relative) {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)
    foreach ($root in $roots) {
        if ([string]::IsNullOrEmpty($root)) { continue }
        $candidate = Join-Path $root $relative
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$browsers = @()
$chrome = Find-Browser 'Google\Chrome\Application\chrome.exe'
$edge   = Find-Browser 'Microsoft\Edge\Application\msedge.exe'
$brave  = Find-Browser 'BraveSoftware\Brave-Browser\Application\brave.exe'
if ($chrome) { $browsers += 'chrome://extensions   (Google Chrome)' }
if ($edge)   { $browsers += 'edge://extensions     (Microsoft Edge)' }
if ($brave)  { $browsers += 'brave://extensions    (Brave)' }

# A browser that is not running yet opens the extensions page directly.
# When it is already running Chrome/Edge ignore chrome:// links from scripts,
# so the message below carries the URL instead.
foreach ($browser in @($chrome, $edge, $brave)) {
    if (-not $browser) { continue }
    $name = [System.IO.Path]::GetFileNameWithoutExtension($browser)
    if (-not (Get-Process -Name $name -ErrorAction SilentlyContinue)) {
        $page = switch ($name) {
            'chrome' { 'chrome://extensions/' }
            'msedge' { 'edge://extensions/' }
            default  { 'brave://extensions/' }
        }
        Start-Process -FilePath $browser -ArgumentList $page
    }
}

if ($browsers.Count -gt 0) {
    $pages = "  1. Open your browser and go to:`r`n`r`n     " + ($browsers -join "`r`n     ")
} else {
    $pages = "  1. Install Google Chrome, Microsoft Edge or Brave first,`r`n" +
             "     then run this helper again from the Start Menu."
}

$text = @"
The browser extension files are ready.

Folder (its path is already on your clipboard):
$ext

Finish the one-time setup:
$pages

  2. Turn on "Developer mode" (top-right corner of that page).
  3. Click "Load unpacked", then paste the folder path (Ctrl+V) and press Enter.

Note: Chrome and Edge do not allow silent extension installation for security,
so this one-time step cannot be automated. After it, the video/audio quality
panel appears on supported sites while SH Download Manager runs in the
background. This is needed once per browser.
"@

Set-Clipboard -Value $ext
Start-Process explorer.exe -ArgumentList "`"$ext`""
[System.Windows.Forms.MessageBox]::Show($text, 'SH Download Manager - browser extension', 'OK', 'Information') | Out-Null
