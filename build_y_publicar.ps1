<#
    build_y_publicar.ps1
    --------------------
    Automatiza todo el ciclo de release de Nexus JL:
      1. Lee la versión desde APP_VERSION en DESCARGADOR_DE_MUSICA_JL.py
      2. Compila el .exe con PyInstaller
      3. Genera el instalador con Inno Setup (usa NexusJL.iss)
      4. Crea un Release en GitHub y sube el instalador como asset

    Requisitos (una sola vez):
      - Python + pyinstaller instalados y en el PATH  (pip install pyinstaller)
      - Inno Setup 6 instalado (https://jrsoftware.org/isdl.php)
      - Un GitHub Personal Access Token con permiso "repo" (o "contents" + "releases"
        si usas un fine-grained token), ya que Nexus.JL es un repo privado.
        Créalo en: https://github.com/settings/tokens

    Uso:
      1. Sube APP_VERSION en DESCARGADOR_DE_MUSICA_JL.py (ej. "1.0.0" -> "1.1.0")
      2. Corre:  .\build_y_publicar.ps1
      3. Te pedirá el token la primera vez (o usa la variable de entorno GITHUB_TOKEN
         para no escribirlo cada vez)
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PyFile      = "DESCARGADOR_DE_MUSICA_JL.py"
$IssFile     = "NexusJL.iss"
$GithubRepo  = "Jose-5011/Nexus.JL"
$IsccPath    = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

function Fallar($mensaje) {
    Write-Host "`n❌ $mensaje" -ForegroundColor Red
    exit 1
}

# --- 1. Leer la versión directamente del código, para que nunca se desincronice ---
if (-not (Test-Path $PyFile)) { Fallar "No se encontró $PyFile en esta carpeta." }

$match = Select-String -Path $PyFile -Pattern 'APP_VERSION\s*=\s*"([^"]+)"'
if (-not $match) { Fallar "No se encontró APP_VERSION dentro de $PyFile." }
$Version = $match.Matches[0].Groups[1].Value
Write-Host "Versión detectada: $Version" -ForegroundColor Cyan

# --- 2. Copiar el código fuente actualizado (para el botón de créditos dentro de la app) ---
Copy-Item $PyFile "codigo_fuente.txt" -Force

# --- 2.5 Asegurar que yt-dlp.exe y ffmpeg.exe estén en bin\ para empacarlos con la app ---
if (-not (Test-Path "bin")) { New-Item -ItemType Directory -Path "bin" | Out-Null }

if (-not (Test-Path "bin\yt-dlp.exe")) {
    Write-Host "`n▶ Descargando yt-dlp.exe (no se encontró en bin\)..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" `
        -OutFile "bin\yt-dlp.exe"
}

if (-not (Test-Path "bin\ffmpeg.exe")) {
    Write-Host "▶ Descargando ffmpeg.exe (no se encontró en bin\)..." -ForegroundColor Cyan
    $zipTemp = "$env:TEMP\ffmpeg_nexusjl.zip"
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zipTemp
    $extractDir = "$env:TEMP\ffmpeg_nexusjl_extract"
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
    Expand-Archive -Path $zipTemp -DestinationPath $extractDir -Force
    $ffmpegExe = Get-ChildItem -Path $extractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $ffmpegExe) { Fallar "No se encontró ffmpeg.exe dentro del zip descargado." }
    Copy-Item $ffmpegExe.FullName "bin\ffmpeg.exe" -Force
    Remove-Item $zipTemp, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "✅ Binarios listos en bin\ (yt-dlp.exe, ffmpeg.exe)" -ForegroundColor Green

# --- 3. Compilar con PyInstaller ---
Write-Host "`n▶ Compilando con PyInstaller..." -ForegroundColor Cyan
pyinstaller --noconfirm --onefile --windowed --name "Nexus JL" `
    --add-data "codigo_fuente.txt;." `
    --add-binary "bin\yt-dlp.exe;bin" `
    --add-binary "bin\ffmpeg.exe;bin" `
    $PyFile
if ($LASTEXITCODE -ne 0) { Fallar "PyInstaller falló. Revisa el mensaje de arriba." }

# --- 4. Generar el instalador con Inno Setup ---
if (-not (Test-Path $IsccPath)) {
    Fallar "No se encontró ISCC.exe en '$IsccPath'. Ajusta la variable `$IsccPath` en este script con tu ruta real de Inno Setup."
}
Write-Host "`n▶ Generando instalador con Inno Setup..." -ForegroundColor Cyan
& $IsccPath "/DMyAppVersion=$Version" $IssFile
if ($LASTEXITCODE -ne 0) { Fallar "Inno Setup falló. Revisa el mensaje de arriba." }

$InstallerPath = "dist_installer\NexusJL_Setup_$Version.exe"
if (-not (Test-Path $InstallerPath)) { Fallar "No se generó el instalador esperado en $InstallerPath" }
Write-Host "✅ Instalador generado: $InstallerPath" -ForegroundColor Green

# --- 5. Publicar el Release en GitHub ---
$Token = $env:GITHUB_TOKEN
if (-not $Token) {
    $TokenSeguro = Read-Host "Pega tu GitHub Personal Access Token (no se muestra ni se guarda)" -AsSecureString
    $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($TokenSeguro)
    )
}

$Headers = @{
    Authorization = "Bearer $Token"
    Accept        = "application/vnd.github+json"
}

$ReleaseBody = @{
    tag_name   = "v$Version"
    name       = "v$Version"
    body       = "Publicado automáticamente por build_y_publicar.ps1"
    draft      = $false
    prerelease = $false
} | ConvertTo-Json

Write-Host "`n▶ Creando release v$Version en GitHub..." -ForegroundColor Cyan
try {
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$GithubRepo/releases" `
        -Method Post -Headers $Headers -Body $ReleaseBody -ContentType "application/json"
} catch {
    Fallar "No se pudo crear el release. ¿El token tiene permiso 'repo'? ¿Ya existe el tag v$Version? Detalle: $_"
}

$UploadUrl = $Release.upload_url -replace '\{\?name,label\}', ''
$FileName  = Split-Path -Leaf $InstallerPath

Write-Host "▶ Subiendo instalador ($FileName)..." -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri "$UploadUrl`?name=$FileName" -Method Post -Headers $Headers `
        -ContentType "application/octet-stream" -InFile $InstallerPath | Out-Null
} catch {
    Fallar "El release se creó pero falló la subida del instalador. Súbelo a mano desde GitHub. Detalle: $_"
}

Write-Host "`n✅ Listo. Release v$Version publicado:" -ForegroundColor Green
Write-Host "   https://github.com/$GithubRepo/releases/tag/v$Version" -ForegroundColor Green
