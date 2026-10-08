<#
.SYNOPSIS
    Installs or repairs the JARVIS desktop dependencies on this PC, idempotently (plan task C-84 step 6):
    FFmpeg, PinchTab and their settings.

.DESCRIPTION
    Run it from anywhere (the repo root is found from this file's location):
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pc\install_desktop_deps.ps1
    Each step acts only when something is missing, so a second run changes nothing.

    1. FFmpeg: 'winget install --id Gyan.FFmpeg -e' (agreements accepted) only when no ffmpeg.exe is found
       in FFMPEG_DIR, the winget package folder, on PATH or in C:\ffmpeg\bin.
    2. PinchTab (version pinned below): 'npm install -g pinchtab@<version> --ignore-scripts', then the
       package's own checksum-verified binary download (dist/src/download.js ensureBinary), only when the
       managed binary is missing. Why --ignore-scripts: the package's postinstall does two things, the
       binary download and a copy of PinchTab's bundled agent skill into every agent folder it finds in the
       user profile (~/.claude/skills, ~/.cursor/skills, ~/.codex/skills, ~/.windsurf/skills and the
       OpenClaw one). Those folders must not change, so only the download half runs here: ensureBinary
       fetches the release binary and checks its SHA-256 against the release's checksums.txt.
    3. PinchTab config: 'pinchtab config init' when the config file is missing (its output is discarded,
       because the new config holds a generated token), then server.bind 127.0.0.1 and server.port 9867
       when they differ. JARVIS hands its own PINCHTAB_TOKEN to the server through the environment
       (scripts\pc\start_pinchtab.ps1), and that token wins over the one in the config file.
    4. FFMPEG_DIR and PINCHTAB_BIN in both PC env files through scripts\ops\env_set.py (set-if-missing: an
       existing value is kept; one that points nowhere is reported, never overwritten).

    No secret is ever printed. Exit codes: 0 everything is in place; 1 a step failed (see the FAIL line);
    2 a required tool is missing (winget for FFmpeg, node/npm for PinchTab, or the project venv).
#>

$ErrorActionPreference = 'Stop'

$PinchTabVersion = '0.15.2'   # pinned: a newer release is taken only by changing this line
$FFmpegWingetId = 'Gyan.FFmpeg'
$PinchTabBind = '127.0.0.1'
$PinchTabPort = '9867'
# 'always-on' (PinchTab's default) keeps a headless Chrome of ~500 MB running at idle; 'explicit' starts a browser
# only when a client asks (POST /instances/start, which the JARVIS client does), ~35 MB at idle (08 Oct 2026).
$PinchTabStrategy = 'explicit'
$EnvComment = 'C-84: desktop tools (scripts/pc/install_desktop_deps.ps1)'

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:Failed = 0
$script:MissingTool = $false
$script:StaleFFmpegDir = $null

function Write-Line([string]$Status, [string]$Item, [string]$Detail) {
    # Straight to the console: the functions below return values, so their status lines must not be output.
    Write-Host ('{0,-8} {1,-16} {2}' -f $Status, $Item, $Detail)
}

function Fail([string]$Item, [string]$Detail, [switch]$Tool) {
    Write-Line 'FAIL' $Item $Detail
    $script:Failed++
    if ($Tool) { $script:MissingTool = $true }
}

function Invoke-Native([string]$File, [string[]]$Arguments, [switch]$Quiet) {
    # Runs a program and returns its exit code. -Quiet discards all of its output (used where that output
    # can hold a token). Windows PowerShell 5.1 turns redirected stderr into errors, hence 'Continue'.
    $ErrorActionPreference = 'Continue'
    if ($Quiet) { & $File @Arguments *> $null } else { & $File @Arguments | Out-Host }
    return $LASTEXITCODE
}

function Get-NativeLine([string]$File, [string[]]$Arguments) {
    # The last non-empty stdout line of a program (stderr goes to the console), or '' on failure.
    $ErrorActionPreference = 'Continue'
    $lines = @(& $File @Arguments | Where-Object { "$_".Trim() })
    if ($LASTEXITCODE -ne 0 -or $lines.Count -eq 0) { return '' }
    return "$($lines[-1])".Trim()
}

function ConvertFrom-DotEnvValue([string]$Text) {
    # The same reader as start_pinchtab.ps1 (python-dotenv's quoting and ' # comment' rules).
    if ($Text.StartsWith('"')) {
        $m = [regex]::Match($Text, '^"((?:\\.|[^"\\])*)"')
        if ($m.Success) {
            $escapes = @{ 'n' = "`n"; 'r' = "`r"; 't' = "`t" }
            $decode = [System.Text.RegularExpressions.MatchEvaluator] {
                param($match)
                $c = $match.Groups[1].Value
                if ($escapes.ContainsKey($c)) { return $escapes[$c] }
                return $c
            }
            return [regex]::Replace($m.Groups[1].Value, '\\([\\''"nrt])', $decode)
        }
    } elseif ($Text.StartsWith("'")) {
        $m = [regex]::Match($Text, "^'((?:\\.|[^'\\])*)'")
        if ($m.Success) { return [regex]::Replace($m.Groups[1].Value, "\\([\\'])", '$1') }
    }
    return ($Text -replace '\s+#.*$', '').Trim()
}

function Get-Setting([string]$Key) {
    # A non-secret setting the way JARVIS reads it: process environment, root .env, deploy\windows-local\.env.
    $value = [Environment]::GetEnvironmentVariable($Key, 'Process')
    if ($value -and $value.Trim()) { return $value.Trim() }
    foreach ($path in @((Join-Path $Root '.env'), (Join-Path $Root 'deploy\windows-local\.env'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $found = $null
        foreach ($raw in [System.IO.File]::ReadAllLines($path)) {
            $line = $raw.TrimStart([char]0xFEFF).Trim()
            if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
            $m = [regex]::Match($line, '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
            if ($m.Success -and $m.Groups[1].Value -ceq $Key) { $found = ConvertFrom-DotEnvValue $m.Groups[2].Value }
        }
        if ($found -and $found.Trim()) { return $found.Trim() }
    }
    return $null
}

# ------------------------------------------------------------------------------------------------ FFmpeg

function Find-FFmpegDir {
    # @{Dir; How} for the first folder holding ffmpeg.exe, else $null. A stale FFMPEG_DIR is reported.
    $configured = Get-Setting 'FFMPEG_DIR'
    if ($configured) {
        $dir = [Environment]::ExpandEnvironmentVariables($configured.Trim('"'))
        if (Test-Path -LiteralPath (Join-Path $dir 'ffmpeg.exe') -PathType Leaf) { return @{ Dir = $dir; How = 'FFMPEG_DIR' } }
        $script:StaleFFmpegDir = $dir
    }
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $bins = @(Get-ChildItem -LiteralPath $packages -Directory -Filter "$FFmpegWingetId*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue } |
        ForEach-Object { Join-Path $_.FullName 'bin' } |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ 'ffmpeg.exe') -PathType Leaf } |
        Sort-Object { (Get-Item -LiteralPath (Join-Path $_ 'ffmpeg.exe')).LastWriteTime } -Descending)
    if ($bins.Count -gt 0) { return @{ Dir = $bins[0]; How = 'winget package folder' } }
    $command = Get-Command -Name 'ffmpeg.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        $path = $command.Path
        $item = Get-Item -LiteralPath $path
        if ($item.LinkType -and $item.Target) { $path = @($item.Target)[0] }  # winget's Links alias -> real file
        return @{ Dir = (Split-Path -Parent $path); How = 'PATH' }
    }
    if (Test-Path -LiteralPath 'C:\ffmpeg\bin\ffmpeg.exe' -PathType Leaf) {
        return @{ Dir = 'C:\ffmpeg\bin'; How = 'C:\ffmpeg\bin' }
    }
    return $null
}

function Install-FFmpeg {
    $found = Find-FFmpegDir
    if (-not $found) {
        if (-not (Get-Command -Name 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue)) {
            Fail 'ffmpeg' ('not found, and winget is missing (install App Installer from the Microsoft Store, ' +
                'or unpack FFmpeg to C:\ffmpeg\bin)') -Tool
            return $null
        }
        Write-Line 'INSTALL' 'ffmpeg' "winget install --id $FFmpegWingetId -e"
        $code = Invoke-Native 'winget.exe' @('install', '--id', $FFmpegWingetId, '-e',
            '--accept-source-agreements', '--accept-package-agreements')
        $found = Find-FFmpegDir
        if (-not $found) {
            Fail 'ffmpeg' "winget exit code $code, and still no ffmpeg.exe in the winget package folder"
            return $null
        }
    }
    Write-Line 'OK' 'ffmpeg' ('{0} ({1})' -f $found.Dir, $found.How)
    if ($script:StaleFFmpegDir) {
        Write-Line 'WARN' 'FFMPEG_DIR' (('points to a folder without ffmpeg.exe ({0}); env_set.py keeps existing ' +
            'values, so replace it in both env files with: {1}') -f $script:StaleFFmpegDir, $found.Dir)
    }
    return $found.Dir
}

# ----------------------------------------------------------------------------------------------- PinchTab

function Install-PinchTab {
    $npm = Get-Command -Name 'npm.cmd' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $node = Get-Command -Name 'node.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $npm -or -not $node) {
        Fail 'pinchtab' 'node and npm must be on PATH (winget install --id OpenJS.NodeJS.LTS -e, then open a new terminal)' -Tool
        return $null
    }
    $npmRoot = Get-NativeLine $npm.Path @('root', '-g')
    if (-not $npmRoot -or -not (Test-Path -LiteralPath $npmRoot -PathType Container)) {
        Fail 'pinchtab' "'npm root -g' did not name an existing folder"
        return $null
    }
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    $archName = 'amd64'
    if ($arch -eq 'ARM64') { $archName = 'arm64' }
    $package = Join-Path $npmRoot 'pinchtab'
    $bin = Join-Path $package (".managed-bin\{0}\pinchtab-windows-{1}.exe" -f $PinchTabVersion, $archName)

    if (-not (Test-Path -LiteralPath $bin -PathType Leaf)) {
        Write-Line 'INSTALL' 'pinchtab' "npm install -g pinchtab@$PinchTabVersion --ignore-scripts (postinstall skipped, see the header)"
        $code = Invoke-Native $npm.Path @('install', '-g', "pinchtab@$PinchTabVersion", '--ignore-scripts')
        if ($code -ne 0) {
            Fail 'pinchtab' "npm install exit code $code"
            return $null
        }
        $download = Join-Path $package 'dist\src\download.js'
        if (-not (Test-Path -LiteralPath $download -PathType Leaf)) {
            Fail 'pinchtab' 'the npm package has no dist\src\download.js (unexpected package layout)'
            return $null
        }
        Write-Line 'INSTALL' 'pinchtab' 'binary download with SHA-256 check (download.js ensureBinary)'
        $js = "require(process.argv[1]).ensureBinary().then(function (p) { console.log('binary ready: ' + p); }, " +
              "function (e) { console.error(String(e && e.message ? e.message : e)); process.exit(1); });"
        $code = Invoke-Native $node.Path @('-e', $js, $download)
        if ($code -ne 0 -or -not (Test-Path -LiteralPath $bin -PathType Leaf)) {
            Fail 'pinchtab' "binary download failed (node exit code $code)"
            return $null
        }
    }
    $version = Get-NativeLine $bin @('--version')
    if ($version -notmatch [regex]::Escape($PinchTabVersion)) {
        Fail 'pinchtab' "the binary does not report version $PinchTabVersion"
        return $null
    }
    Write-Line 'OK' 'pinchtab' "$version at $bin"
    $configured = Get-Setting 'PINCHTAB_BIN'
    if ($configured) {
        $configuredPath = [Environment]::ExpandEnvironmentVariables($configured.Trim('"'))
        if (-not (Test-Path -LiteralPath $configuredPath -PathType Leaf)) {
            Write-Line 'WARN' 'PINCHTAB_BIN' ('points to a missing file; env_set.py keeps existing values, ' +
                "so replace it in both env files with: $bin")
        }
    }
    return $bin
}

function Set-PinchTabConfig([string]$Bin) {
    $configPath = Get-NativeLine $Bin @('config', 'path')
    if (-not $configPath) {
        Fail 'pinchtab config' "'pinchtab config path' failed"
        return
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        $code = Invoke-Native $Bin @('config', 'init') -Quiet  # output discarded: it can show the new token
        if ($code -ne 0 -or -not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
            Fail 'pinchtab config' "'pinchtab config init' failed (exit code $code)"
            return
        }
        Write-Line 'INSTALL' 'pinchtab config' 'created with pinchtab config init'
    }
    foreach ($pair in @(@('server.bind', $PinchTabBind), @('server.port', $PinchTabPort))) {
        $config = [System.IO.File]::ReadAllText($configPath) | ConvertFrom-Json
        $name = $pair[0].Split('.')[1]
        $current = ''
        if ($config.server -and $null -ne $config.server.$name) { $current = [string]$config.server.$name }
        if ($current -ne $pair[1]) {
            $code = Invoke-Native $Bin @('config', 'set', $pair[0], $pair[1]) -Quiet
            if ($code -ne 0) { Fail 'pinchtab config' "'pinchtab config set $($pair[0])' failed (exit code $code)"; return }
        }
    }
    $config = [System.IO.File]::ReadAllText($configPath) | ConvertFrom-Json
    $strategy = ''
    if ($config.multiInstance -and $null -ne $config.multiInstance.strategy) { $strategy = [string]$config.multiInstance.strategy }
    if ($strategy -ne $PinchTabStrategy) {
        $code = Invoke-Native $Bin @('config', 'set', 'multiInstance.strategy', $PinchTabStrategy) -Quiet
        if ($code -ne 0) { Fail 'pinchtab config' "'pinchtab config set multiInstance.strategy' failed (exit code $code)"; return }
    }
    $config = [System.IO.File]::ReadAllText($configPath) | ConvertFrom-Json
    $bind = [string]$config.server.bind
    $port = [string]$config.server.port
    $strategy = [string]$config.multiInstance.strategy
    if ($bind -ne $PinchTabBind -or $port -ne $PinchTabPort -or $strategy -ne $PinchTabStrategy) {
        Fail 'pinchtab config' "server.bind/port/strategy are $bind/$port/$strategy after the update, expected $PinchTabBind/$PinchTabPort/$PinchTabStrategy"
        return
    }
    Write-Line 'OK' 'pinchtab config' "server.bind $bind, server.port $port, multiInstance.strategy $strategy ($configPath)"
}

# ------------------------------------------------------------------------------------------------ main

try {
    Write-Host "JARVIS desktop dependencies (C-84): FFmpeg, PinchTab $PinchTabVersion, env keys"
    $ffmpegDir = Install-FFmpeg
    $pinchTabBin = Install-PinchTab
    if ($pinchTabBin) { Set-PinchTabConfig $pinchTabBin }

    $items = @()
    if ($ffmpegDir) { $items += "FFMPEG_DIR=$ffmpegDir" }
    if ($pinchTabBin) { $items += "PINCHTAB_BIN=$pinchTabBin" }
    if ($items.Count -gt 0) {
        $python = Join-Path $Root '.venv_312\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            Fail 'env keys' 'the project venv is missing (run deploy\windows-local\setup.bat first)' -Tool
        } else {
            # env_set.py prints key names and what it did, never a value.
            $code = Invoke-Native $python (@((Join-Path $Root 'scripts\ops\env_set.py'), '--comment', $EnvComment) + $items)
            if ($code -ne 0) {
                Fail 'env keys' "env_set.py exit code $code"
            } else {
                Write-Line 'OK' 'env keys' 'FFMPEG_DIR / PINCHTAB_BIN checked in both env files'
            }
        }
    }
} catch {
    Fail 'installer' ('{0}: {1}' -f $_.Exception.GetType().Name, $_.Exception.Message)
}

if ($script:Failed -gt 0) {
    $code = 1
    if ($script:MissingTool) { $code = 2 }
    Write-Host "Done with $($script:Failed) failure(s); exit code $code."
    exit $code
}
Write-Host ('Done: everything is in place. Start PinchTab with scripts\pc\start_pinchtab.ps1 ' +
    '(start-agent.bat runs it before the worker).')
exit 0
