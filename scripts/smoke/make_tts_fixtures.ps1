<#
scripts/smoke/make_tts_fixtures.ps1: offline TTS fixtures for the voice probe (plan task C-40).

    powershell -ExecutionPolicy Bypass -File scripts/smoke/make_tts_fixtures.ps1 [-OutDir <dir>] [-Force]

Windows' own System.Speech voice writes each phrase as a 16 kHz mono 16-bit WAV, the format that
scripts/smoke/voice_probe.py streams, into tests/integration/fixtures/audio (default). The files are generated and
never committed (*.wav is gitignored). An existing file is kept unless -Force is given. System.Speech ships with
Windows PowerShell; started from PowerShell 7 the script runs itself again under powershell.exe.
#>
param(
    [string]$OutDir = (Join-Path $PSScriptRoot "..\..\tests\integration\fixtures\audio"),
    [switch]$Force
)
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -eq "Core") {
    $relaunch = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath, "-OutDir", $OutDir)
    if ($Force) { $relaunch += "-Force" }
    & powershell.exe @relaunch
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Speech

$phrases = [ordered]@{
    "tts_jarvis_time.wav"   = "Jarvis, what time is it right now?"
    "tts_no_wake_time.wav"  = "What time is it right now?"
    "tts_jarvis_math.wav"   = "Jarvis, what is two plus two?"
    "tts_jarvis_secret.wav" = "Jarvis, please remember this. The secret word is mango."
    "tts_recall.wav"        = "Jarvis, what was the secret word I told you?"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    foreach ($name in $phrases.Keys) {
        $path = Join-Path $OutDir $name
        if ((Test-Path $path) -and -not $Force) {
            Write-Host "keep  $name"
            continue
        }
        $synth.SetOutputToWaveFile($path, $format)
        $synth.Speak($phrases[$name])
        $synth.SetOutputToNull()
        Write-Host ("wrote {0} ({1:N1} KB)" -f $name, ((Get-Item $path).Length / 1KB))
    }
}
finally {
    $synth.Dispose()
}
