# Battery Tray - desinstallation
#   irm https://raw.githubusercontent.com/fbbbrand/battery-tray/main/uninstall.ps1 | iex

& {
    $ErrorActionPreference = 'Stop'
    $dir      = Join-Path $env:LOCALAPPDATA 'BatteryTray'
    $exe      = Join-Path $dir 'BatteryTray.exe'
    $shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Battery Tray.lnk'

    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', 47821)
        $s = $c.GetStream(); $b = [Text.Encoding]::ASCII.GetBytes('quit')
        $s.Write($b, 0, $b.Length); $c.Close()
    } catch { }
    for ($i = 0; $i -lt 50; $i++) {
        $running = Get-Process BatteryTray -ErrorAction SilentlyContinue |
                   Where-Object { $_.Path -eq $exe }
        if (-not $running) { break }
        Start-Sleep -Milliseconds 100
    }
    if ($running) { $running | Stop-Process -Force }

    if (Test-Path $shortcut) { Remove-Item $shortcut -Force }
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
    Write-Host 'Battery Tray desinstalle.' -ForegroundColor Green
}
