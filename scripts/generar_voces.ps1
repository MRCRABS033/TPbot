# Genera las voces del temporizador de boss (assets/sounds/*.wav) con la voz "Sabina" de Windows.
# Uso (desde la raíz del proyecto):  powershell -ExecutionPolicy Bypass -File scripts\generar_voces.ps1
# Formato: WAV 48 kHz, 16 bits, estéreo (el que usa discord.py, así el bot no necesita FFmpeg).
# El bot recorta el silencio de los bordes y sube el volumen al cargarlas.

Add-Type -AssemblyName System.Speech

$dir = Join-Path $PSScriptRoot "..\assets\sounds"
New-Item -ItemType Directory -Force $dir | Out-Null

$voz = "Microsoft Sabina Desktop"   # es-MX; ver otras con (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()
$velocidad = 1                      # -10 (lento) a 10 (rápido); los números deben durar menos de 1 s

$frases = [ordered]@{
    "5"            = "Cinco"
    "4"            = "Cuatro"
    "3"            = "Tres"
    "2"            = "Dos"
    "1"            = "Uno"
    "mecanica"     = "¡Mecánica!"
    "fin_mecanica" = "Fin de la mecánica"
    "fin"          = "Tiempo terminado"
}

$formato = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    48000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Stereo
)

foreach ($nombre in $frases.Keys) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice($voz)
    $s.Rate = $velocidad
    $s.Volume = 100
    $s.SetOutputToWaveFile((Join-Path $dir "$nombre.wav"), $formato)
    $s.Speak($frases[$nombre])
    $s.Dispose()
    Write-Host "OK $nombre.wav -> $($frases[$nombre])"
}
