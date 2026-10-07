# Bundle acceptance smoke test (PACKAGING_PLAN P4), Windows. WRITTEN, NOT YET RUN.
#
#     powershell -ExecutionPolicy Bypass -File packaging\smoke.ps1 [BundleDir]
#
# The same checks as smoke.sh, where Windows allows them:
#   0. the layout (packaging/layout.py): VERSION and release.json; every
#      sketch dir under firmware\ and stable\firmware\; firmware\libraries;
#      tools\arduino-cli.exe runs offline (a dead proxy) and `core list`
#      shows arduino:avr and teensy:avr; tools\teensy_loader_cli.exe knows
#      the Teensy 3.5; stable\station-stable.exe --self-check. A missing
#      piece fails by name.
#   1. station-web.exe: /api/state 200; / is the bundle's index.html;
#      /api/theme.css; every Setup row ticked and set to SIM; Launch builds
#      six models; /api/estop_all latches every one; Setup's station_version
#      is VERSION's tag and build date; /api/quit exits 0 within 5 s; the
#      run's log names the stop, the quit and SDL teardown.
#   2. station-tk.exe and 3. station-qt.exe: open, then are stopped.
#      Windows has no SIGTERM to send another process: Stop-Process is
#      TerminateProcess, which runs no handler at all. So the desktop steps
#      assert that the view OPENED (its log lines) and that the process ends
#      when told, and say plainly that the stop path is not exercised here;
#      the Web step's estop_all + quit is the stop path on Windows.
param([string]$BundleDir = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $BundleDir) { $BundleDir = Join-Path $Root "dist\station" }
$Bundle = (Resolve-Path $BundleDir).Path
$Port = if ($env:SMOKE_PORT) { [int]$env:SMOKE_PORT } else { 8099 }
$Base = "http://127.0.0.1:$Port"
$DataRoot = if ($env:TRANSFER_STAGE_DATA_ROOT) { $env:TRANSFER_STAGE_DATA_ROOT } else { Join-Path $HOME "transfer-stage-runs" }
$LogDir = Join-Path $DataRoot "logs"
$Out = Join-Path ([IO.Path]::GetTempPath()) ("station-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Out, $LogDir | Out-Null
$PortRows = @("stepper_probe", "dc_probe", "chuck_positioner", "temperature_controller", "rotator")
# red_percent has no row since 2026-09-28: it launches with the Transfer Map.
# The Sample Map is off by default (owner 2026-10-06); add "sample_map" here when it is on.
$AllRows = $PortRows + @("transfer_map")
# The sketch directories firmware/flash_firmware.py's DEVICES table names
# (tests/test_packaging.py keeps this list equal to the table).
$Sketches = @("stepper_firmware", "high_polling_rate", "chuck_firmware", "temp_controller")
$Cores = @("arduino:avr", "teensy:avr")
$script:Failed = 0

function Pass($what) { Write-Host "PASS $what" }
function Fail($what) { Write-Host "FAIL $what"; $script:Failed++ }
function Check($what, [bool]$ok) { if ($ok) { Pass $what } else { Fail $what } }

function Get-Route($route) {
    try { return Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri "$Base$route" }
    catch { return $null }
}
function Post-Route($route, $body) {
    $json = $body | ConvertTo-Json -Compress -Depth 5
    # The station refuses a POST without an Origin naming it.
    return Invoke-RestMethod -Method Post -TimeoutSec 30 -ContentType "application/json" `
        -Headers @{ Origin = $Base } -Uri "$Base$route" -Body $json
}
function Run-Setup($command, $argList) {
    return Post-Route "/api/run" @{ name = "__setup__"; command = $command; args = @($argList) }
}
function Snapshot-Logs { $script:Before = @(Get-ChildItem $LogDir -File | ForEach-Object Name) }
function Find-Log($needle) {
    foreach ($f in (Get-ChildItem $LogDir -File | Sort-Object LastWriteTime -Descending)) {
        if ($script:Before -contains $f.Name) { continue }
        if (Select-String -Path $f.FullName -SimpleMatch -Quiet -Pattern $needle) { return $f.FullName }
    }
    return $null
}
function Log-Has($log, $needle) { return [bool](Select-String -Path $log -SimpleMatch -Quiet -Pattern $needle) }

Write-Host "bundle:  $Bundle"
Write-Host "logs:    $LogDir"
Write-Host "scratch: $Out"
foreach ($v in "tk", "qt", "web") {
    if (-not (Test-Path (Join-Path $Bundle "station-$v.exe"))) { Fail "station-$v.exe is not in the bundle" }
}
if ($script:Failed) { exit 1 }
if (Get-Route "/api/state") { Write-Host "FAIL port $Port is already answering"; exit 1 }

# -- 0. the layout ------------------------------------------------------------
Write-Host "== layout"
foreach ($f in "VERSION", "release.json") {
    Check "$f beside the launchers" (Test-Path (Join-Path $Bundle $f))
}
$Stamp = @(Get-Content (Join-Path $Bundle "VERSION") -ErrorAction SilentlyContinue)
$ExpectedVersion = if ($Stamp.Count -ge 3) { "$($Stamp[0]), $($Stamp[2].Substring(0, 10))" } else { "(no VERSION)" }
foreach ($sketch in $Sketches) {
    Check "firmware\$sketch\$sketch.ino" (Test-Path (Join-Path $Bundle "firmware\$sketch\$sketch.ino"))
    Check "stable\firmware\$sketch\$sketch.ino" (Test-Path (Join-Path $Bundle "stable\firmware\$sketch\$sketch.ino"))
}
Check "firmware\libraries\" (Test-Path (Join-Path $Bundle "firmware\libraries"))
$Tools = Join-Path $Bundle "tools"
$Cli = Join-Path $Tools "arduino-cli.exe"
Check "tools\arduino-cli.exe" (Test-Path $Cli)
Check "tools\arduino-cli.yaml" (Test-Path (Join-Path $Tools "arduino-cli.yaml"))
Check "tools\arduino-data\" (Test-Path (Join-Path $Tools "arduino-data\packages"))
function Offline-Cli([string[]]$cliArgs) {
    # Every request through a proxy that is not there; the config's
    # directories are relative to the working directory, so run from tools\.
    $saved = @($env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ARDUINO_NETWORK_PROXY)
    $env:HTTP_PROXY = $env:HTTPS_PROXY = $env:ARDUINO_NETWORK_PROXY = "http://127.0.0.1:9"
    Push-Location $Tools
    try { return (& $Cli --config-file arduino-cli.yaml @cliArgs 2>&1 | Out-String) }
    catch { return "" }
    finally {
        Pop-Location
        $env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ARDUINO_NETWORK_PROXY = $saved
    }
}
if (Test-Path $Cli) {
    $v = Offline-Cli @("version")
    Check "tools\arduino-cli.exe version runs offline ($($v.Trim()))" ($v -match "Version:")
    $cores = Offline-Cli @("core", "list")
    foreach ($core in $Cores) {
        Check "tools\arduino-cli.exe core list shows $core" ($cores -match "(?m)^$([regex]::Escape($core)) ")
    }
}
$Loader = Join-Path $Tools "teensy_loader_cli.exe"
Check "tools\teensy_loader_cli.exe" (Test-Path $Loader)
if (Test-Path $Loader) {
    $mcus = try { & $Loader --list-mcus 2>&1 | Out-String } catch { "" }
    Check "tools\teensy_loader_cli.exe knows the Teensy 3.5 (mk64fx512)" ($mcus -match "mk64fx512")
}
$Stable = Join-Path $Bundle "stable\station-stable.exe"
Check "stable\station-stable.exe" (Test-Path $Stable)
if (Test-Path $Stable) {
    $p = Start-Process -FilePath $Stable -ArgumentList "--self-check" -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Out "stable.out") -RedirectStandardError (Join-Path $Out "stable.err")
    $done = $p.WaitForExit(60000)
    if (-not $done) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    $text = Get-Content -Raw (Join-Path $Out "stable.out") -ErrorAction SilentlyContinue
    Check "stable\station-stable.exe --self-check resolves every stable module" ($done -and ($text -match "self-check ok"))
}

# -- 1. station-web ---------------------------------------------------------
Write-Host "== station-web"
Snapshot-Logs
$web = Start-Process -FilePath (Join-Path $Bundle "station-web.exe") `
    -ArgumentList "--no-browser", "--port", "$Port" -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $Out "web.out") -RedirectStandardError (Join-Path $Out "web.err")
$up = $false
for ($i = 0; $i -lt 60; $i++) {
    $r = Get-Route "/api/state"
    if ($r -and $r.StatusCode -eq 200) { $up = $true; break }
    if ($web.HasExited) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $up) {
    Fail "station-web answered /api/state within 30 s"
    if (-not $web.HasExited) { Stop-Process -Id $web.Id -Force }
} else {
    Pass "GET /api/state 200"
    $index = (Get-Route "/").Content
    $bundled = Get-Content -Raw (Join-Path $Bundle "_internal\views\web\static\index.html")
    Check "GET / is the bundle's own index.html" ($index -eq $bundled)
    $theme = Get-Route "/api/theme.css"
    Check "GET /api/theme.css served" ($theme -and $theme.StatusCode -eq 200 -and $theme.Content -match "--")

    Run-Setup "cancel_scan" @() | Out-Null
    $setup = Invoke-RestMethod -Uri "$Base/api/setup" -TimeoutSec 10
    $keys = @($setup.state.rows | ForEach-Object key)
    Check "Setup rows are the ones this script drives ($($keys -join ' '))" (($keys -join " ") -eq ($AllRows -join " "))
    Check "serial enumeration ran ($(@($setup.state.ports).Count) port(s))" ($null -ne $setup.state.PSObject.Properties["ports"])
    foreach ($row in $AllRows) {
        Check "tick $row" ((Run-Setup "set_${row}_enabled" @($true)).status -eq "ok")
    }
    foreach ($row in $PortRows) {
        Check "$row -> SIM" ((Run-Setup "set_${row}_port" @("SIM")).value -eq "SIM")
    }
    $r = Run-Setup "launch" @()
    Write-Host "     launch: $($r.value -join ', ')"
    Check "Launch built all six models" ($r.status -eq "ok")
    $state = $null
    for ($i = 0; $i -lt 20; $i++) {
        $state = Invoke-RestMethod -Uri "$Base/api/state" -TimeoutSec 10
        if (@($state.models.PSObject.Properties).Count -ge 6) { break }
        Start-Sleep -Milliseconds 500
    }
    Check "/api/state shows six models" (@($state.models.PSObject.Properties).Count -ge 6)

    $r = Post-Route "/api/estop_all" @{}
    Check "estop_all answered and latched" ($r.is_estopped -eq $true)
    $state = Invoke-RestMethod -Uri "$Base/api/state" -TimeoutSec 10
    $unlatched = @($state.models.PSObject.Properties | Where-Object { -not $_.Value.is_estopped } | ForEach-Object Name)
    Check "every model latched (not: $($unlatched -join ', '))" ($unlatched.Count -eq 0)

    # Setup reads the running version when an update check runs (the startup
    # check is off: STATION_NO_UPDATE_CHECK); Check again reads it.
    Run-Setup "check_updates" @() | Out-Null
    $reported = "unknown"
    for ($i = 0; $i -lt 40; $i++) {
        $reported = (Invoke-RestMethod -Uri "$Base/api/setup" -TimeoutSec 10).state.station_version
        if ($reported -and $reported -ne "unknown") { break }
        Start-Sleep -Milliseconds 500
    }
    Check "station-web reports VERSION ($reported = $ExpectedVersion)" ($reported -eq $ExpectedVersion)

    $r = Post-Route "/api/quit" @{}
    Check "POST /api/quit answered ok" ($r.status -eq "ok")
    $exited = $web.WaitForExit(5000)
    Check "station-web exited 0 within 5 s (exit $($web.ExitCode))" ($exited -and $web.ExitCode -eq 0)

    $log = Find-Log "view=web port=$Port"
    if ($log) {
        Pass "log file written: $log"
        Check "log: Web dashboard served" (Log-Has $log "Web Dashboard: serving at")
        Check "log: serial ports enumerated" (Log-Has $log "[Setup] Ports:")
        Check "log: FULL STOP latched" (Log-Has $log "FULL STOP")
        Check "log: Quit from the Web console" (Log-Has $log "Quit from the Web console")
        Check "log: gamepad hub closed (SDL down)" (Log-Has $log "[gamepad-hub] SDL down")
        Check "log: no traceback" (-not (Log-Has $log "Traceback"))
    } else { Fail "a new log file names view=web port=$Port in $LogDir" }
}

# -- 2 and 3. the desktop launchers ----------------------------------------
function Desktop($view, $ready, [string[]]$needles) {
    Write-Host "== station-$view"
    Snapshot-Logs
    $p = Start-Process -FilePath (Join-Path $Bundle "station-$view.exe") -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $Out "$view.out") -RedirectStandardError (Join-Path $Out "$view.err")
    $log = $null
    for ($i = 0; $i -lt 60; $i++) {
        $log = Find-Log "view=$view"
        if ($log -and (Log-Has $log $ready)) { break }
        $log = $null
        Start-Sleep -Milliseconds 500
    }
    if ($log) { Pass "station-$view opened ($ready) - log $log" } else { Fail "station-$view opened within 30 s" }
    Start-Sleep -Seconds 5
    Check "station-$view still running after 5 s" (-not $p.HasExited)
    # No SIGTERM on Windows: TerminateProcess runs no handler. Not a stop-path check.
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Check "station-$view ended when told" ($p.WaitForExit(5000))
    if ($log) {
        foreach ($n in $needles) { Check "log: $n" (Log-Has $log $n) }
        Check "log: no traceback" (-not (Log-Has $log "Traceback"))
    }
}

Desktop "tk" "Dashboard Open: Tk dashboard ready" @("[app] View: tk starting")
$env:QT_QPA_PLATFORM = if ($null -ne $env:SMOKE_QT_PLATFORM) { $env:SMOKE_QT_PLATFORM } else { "offscreen" }
if (-not $env:QT_QPA_PLATFORM) { Remove-Item Env:QT_QPA_PLATFORM }
Desktop "qt" "Qt dashboard shown" @("[app] View: qt starting", "[packaging] Qt Plugin Path:",
    "QT_QPA_PLATFORM_PLUGIN_PATH=$Bundle\_internal\PySide6")
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue

Write-Host ""
if ($script:Failed -eq 0) { Write-Host "SMOKE PASSED ($Bundle)"; exit 0 }
Write-Host "SMOKE FAILED: $($script:Failed) check(s) ($Bundle); outputs in $Out"
exit 1
