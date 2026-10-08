<#
.SYNOPSIS
    Starts the local PinchTab server for the JARVIS Chrome tools when it is not answering yet
    (plan task C-84, contract K-23).

.DESCRIPTION
    deploy\windows-local\start-agent.bat runs this right before the agent worker starts:
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pc\start_pinchtab.ps1
    It can be run the same way by hand, in Windows PowerShell 5.1 or PowerShell 7.

    Settings are looked up the way JARVIS reads them (K-47): the process environment first, then the root
    .env, then deploy\windows-local\.env. Only PINCHTAB_URL, PINCHTAB_TOKEN and PINCHTAB_BIN are kept; every
    other line is skipped, and no value is ever printed.

    1. GET <PINCHTAB_URL>/health with 'Authorization: Bearer <PINCHTAB_TOKEN>' (2 s timeout, no proxy, no
       redirects; only for a PINCHTAB_URL on this PC, so the token never leaves it). 200 -> PINCHTAB_OK.
    2. No answer -> start '<PINCHTAB_BIN> server --bind 127.0.0.1 --port <port of PINCHTAB_URL>' in a
       minimized window. The token reaches the server only through this script's own environment, which
       the child inherits; it is never an argument, so it never shows up in a process command line.
    3. Poll /health for up to 15 s -> PINCHTAB_STARTED or PINCHTAB_NOT_READY.

    Output: one line, a status word and a short reason, never a value. The other status words:
      PINCHTAB_AUTH_FAILED  a server answers on the port but refuses the token (it runs with another
                            token); no second server is started.
      PINCHTAB_UNHEALTHY    something else answers on the port with another HTTP status; nothing started.
      PINCHTAB_SKIPPED      no PINCHTAB_TOKEN, no PinchTab binary, or PINCHTAB_URL is not on this PC.
      PINCHTAB_ERROR        an unexpected failure (the reason never contains the token).
    The exit code is always 0: the worker starts either way, and the Chrome tools then report that
    PinchTab is unavailable. Install or repair PinchTab with scripts\pc\install_desktop_deps.ps1.

.PARAMETER EnvFile
    Read this file instead of the two env files (like env_set.py --env-file; used by the tests, so they
    never read the real .env). start-agent.bat passes nothing.
#>
param(
    [string[]]$EnvFile
)

$ErrorActionPreference = 'Stop'

$SettingKeys = @('PINCHTAB_URL', 'PINCHTAB_TOKEN', 'PINCHTAB_BIN')
$DefaultUrl = 'http://127.0.0.1:9867'
$ReadyTimeoutS = 15
$RequestTimeoutMs = 2000
$script:Token = $null

function Write-Status([string]$Word, [string]$Reason) {
    Write-Output ('{0}: {1}' -f $Word, $Reason)
}

function ConvertFrom-DotEnvValue([string]$Text) {
    # The parts of python-dotenv's value syntax a JARVIS .env uses: "double" or 'single' quotes with their
    # escapes, or an unquoted value whose ' # comment' and trailing blanks are dropped.
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

function Read-DotEnv([string]$Path, [string[]]$Wanted) {
    $found = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $found }
    foreach ($raw in [System.IO.File]::ReadAllLines($Path)) {
        $line = $raw.TrimStart([char]0xFEFF).Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
        $m = [regex]::Match($line, '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
        if (-not $m.Success -or $Wanted -cnotcontains $m.Groups[1].Value) { continue }
        $found[$m.Groups[1].Value] = ConvertFrom-DotEnvValue $m.Groups[2].Value  # the last one wins, as in python-dotenv
    }
    return $found
}

function Get-Settings([string]$Root) {
    $settings = @{}
    foreach ($key in $SettingKeys) {
        $value = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($value -and $value.Trim()) { $settings[$key] = $value.Trim() }
    }
    $paths = @((Join-Path $Root '.env'), (Join-Path $Root 'deploy\windows-local\.env'))
    if ($EnvFile) { $paths = $EnvFile }
    foreach ($path in $paths) {
        $values = Read-DotEnv $path $SettingKeys
        foreach ($key in $SettingKeys) {
            if (-not $settings.ContainsKey($key) -and $values.ContainsKey($key) -and $values[$key].Trim()) {
                $settings[$key] = $values[$key].Trim()
            }
        }
    }
    return $settings
}

function Get-HealthStatus([string]$Url) {
    # HTTP status of GET /health with the Bearer token; 0 when nothing answers (refused or timed out).
    $request = [System.Net.WebRequest]::Create($Url)
    $request.Method = 'GET'
    $request.Timeout = $RequestTimeoutMs
    $request.ReadWriteTimeout = $RequestTimeoutMs
    $request.Proxy = $null              # straight to the local port, never through a proxy
    $request.AllowAutoRedirect = $false # a redirect must not carry the token anywhere else
    $request.KeepAlive = $false
    $request.Headers.Add('Authorization', 'Bearer ' + $script:Token)
    $response = $null
    try {
        $response = $request.GetResponse()
        return [int]$response.StatusCode
    } catch {
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [System.Net.WebException])) { $ex = $ex.InnerException }
        if ($ex -and $ex.Response) {
            $response = $ex.Response
            return [int]$response.StatusCode
        }
        return 0
    } finally {
        if ($response) { $response.Close() }
    }
}

function Find-PinchTab($Settings) {
    if ($Settings.ContainsKey('PINCHTAB_BIN')) {
        $path = [Environment]::ExpandEnvironmentVariables($Settings['PINCHTAB_BIN'].Trim('"'))
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    }
    # Like JARVIS's own auto-detection: a pinchtab on PATH (the standalone binary or the npm shim).
    $command = Get-Command -Name 'pinchtab.exe', 'pinchtab.cmd' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { return $command.Path }
    return $null
}

function Invoke-StartPinchTab {
    $root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $settings = Get-Settings $root

    $urlText = $DefaultUrl
    if ($settings.ContainsKey('PINCHTAB_URL')) { $urlText = $settings['PINCHTAB_URL'] }
    $uri = $null
    if (-not [System.Uri]::TryCreate($urlText, [System.UriKind]::Absolute, [ref]$uri) -or
        ($uri.Scheme -ne 'http' -and $uri.Scheme -ne 'https')) {
        Write-Status 'PINCHTAB_SKIPPED' 'PINCHTAB_URL is not an http(s) URL'
        return
    }
    if (-not $uri.IsLoopback) {
        Write-Status 'PINCHTAB_SKIPPED' 'PINCHTAB_URL is not on this PC, so there is no local server to start'
        return
    }
    if (-not $settings.ContainsKey('PINCHTAB_TOKEN')) {
        Write-Status 'PINCHTAB_SKIPPED' ('PINCHTAB_TOKEN is not set ' +
            '(create it with scripts\ops\env_set.py --secret PINCHTAB_TOKEN)')
        return
    }
    $script:Token = $settings['PINCHTAB_TOKEN']
    $healthUrl = '{0}://{1}:{2}{3}/health' -f $uri.Scheme, $uri.Host, $uri.Port, $uri.AbsolutePath.TrimEnd('/')

    $status = Get-HealthStatus $healthUrl
    if ($status -eq 200) {
        Write-Status 'PINCHTAB_OK' 'already running (/health answered 200)'
        return
    }
    if ($status -eq 401 -or $status -eq 403) {
        Write-Status 'PINCHTAB_AUTH_FAILED' ("a server on the PinchTab port refuses PINCHTAB_TOKEN (HTTP $status); " +
            'it runs with another token, so no second server is started (stop it, then run this again)')
        return
    }
    if ($status -ne 0) {
        Write-Status 'PINCHTAB_UNHEALTHY' "something else answers on the PinchTab port (HTTP $status); nothing started"
        return
    }

    $bin = Find-PinchTab $settings
    if (-not $bin) {
        Write-Status 'PINCHTAB_SKIPPED' 'PinchTab is not installed (run scripts\pc\install_desktop_deps.ps1)'
        return
    }
    $bind = '127.0.0.1'
    if ($uri.HostNameType -eq [System.UriHostNameType]::IPv6) { $bind = '::1' }
    $workDir = Join-Path $env:APPDATA 'pinchtab'  # PinchTab's own folder (config.json), never the repo
    if (-not (Test-Path -LiteralPath $workDir -PathType Container)) { $workDir = $env:TEMP }

    # The child inherits this process's environment: the token goes there for the start only, never into
    # the argument list, and the previous value is put back right after.
    $previous = [Environment]::GetEnvironmentVariable('PINCHTAB_TOKEN', 'Process')
    [Environment]::SetEnvironmentVariable('PINCHTAB_TOKEN', $script:Token, 'Process')
    try {
        $process = Start-Process -FilePath $bin -ArgumentList @('server', '--bind', $bind, '--port', [string]$uri.Port) `
            -WorkingDirectory $workDir -WindowStyle Minimized -PassThru
    } finally {
        [Environment]::SetEnvironmentVariable('PINCHTAB_TOKEN', $previous, 'Process')
    }

    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $exited = $false
    while ($clock.Elapsed.TotalSeconds -lt $ReadyTimeoutS) {
        Start-Sleep -Milliseconds 500
        $status = Get-HealthStatus $healthUrl
        if ($status -eq 200) { break }
        if ($process -and $process.HasExited) { $exited = $true; break }
    }
    if ($status -eq 200) {
        Write-Status 'PINCHTAB_STARTED' ('/health answered 200 after {0:N1} s' -f $clock.Elapsed.TotalSeconds)
    } elseif ($exited) {
        Write-Status 'PINCHTAB_NOT_READY' 'the PinchTab server exited right after the start (run it by hand to see why)'
    } else {
        $last = 'no answer'
        if ($status -ne 0) { $last = "HTTP $status" }
        Write-Status 'PINCHTAB_NOT_READY' ("no 200 from /health within $ReadyTimeoutS s ($last); " +
            'see the minimized PinchTab window')
    }
}

try {
    Invoke-StartPinchTab
} catch {
    $reason = '{0}: {1}' -f $_.Exception.GetType().Name, $_.Exception.Message
    if ($script:Token) { $reason = $reason.Replace($script:Token, '<redacted>') }
    if ($reason.Length -gt 200) { $reason = $reason.Substring(0, 200) + '...' }
    Write-Status 'PINCHTAB_ERROR' $reason
}
exit 0
