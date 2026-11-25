# Battery Tray - installation
#   irm https://raw.githubusercontent.com/fbbbrand/battery-tray/main/install.ps1 | iex
#
# Installs BatteryTray.exe into %LOCALAPPDATA%\BatteryTray, starts it with
# Windows (shortcut in the user's Startup folder) and launches it.
# No admin rights, no Python needed.

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # much faster downloads on PS 5.1
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $repo     = 'fbbbrand/battery-tray'
    $dir      = Join-Path $env:LOCALAPPDATA 'BatteryTray'
    $exe      = Join-Path $dir 'BatteryTray.exe'
    $shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Battery Tray.lnk'
    # BATTERYTRAY_SOURCE: optional local path or URL of the exe (for testing).
    $source   = if ($env:BATTERYTRAY_SOURCE) { $env:BATTERYTRAY_SOURCE }
                else { "https://github.com/$repo/releases/latest/download/BatteryTray.exe" }

    Write-Host 'Battery Tray - installation' -ForegroundColor Cyan

    # 1. Close a running copy (it listens on localhost:47821 for "quit").
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', 47821)
        $s = $c.GetStream(); $b = [Text.Encoding]::ASCII.GetBytes('quit')
        $s.Write($b, 0, $b.Length); $c.Close()
        Write-Host '  Ancienne version fermee.'
    } catch { }
    for ($i = 0; $i -lt 50; $i++) {
        $running = Get-Process BatteryTray -ErrorAction SilentlyContinue |
                   Where-Object { $_.Path -eq $exe }
        if (-not $running) { break }
        Start-Sleep -Milliseconds 100
    }
    if ($running) { $running | Stop-Process -Force }

    # 2. Download / copy the exe.
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $tmp = "$exe.download"
    if (Test-Path $source) { Copy-Item $source $tmp -Force }
    else {
        Write-Host "  Telechargement de $source"
        Invoke-WebRequest -Uri $source -OutFile $tmp -UseBasicParsing
    }
    Move-Item $tmp $exe -Force
    Unblock-File $exe

    # 3. Start with Windows.
    $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $sh.TargetPath = $exe
    $sh.WorkingDirectory = $dir
    $sh.Description = 'Batterie casque et souris Logitech'
    $sh.Save()

    # 4. Launch.
    Start-Process $exe
    Write-Host "  Installe dans $dir" -ForegroundColor Green
    Write-Host '  Le widget apparait a gauche de la zone de notification et se lance au demarrage.'
    Write-Host '  Clic gauche : rafraichir   Clic droit : menu (Quitter)'
}
