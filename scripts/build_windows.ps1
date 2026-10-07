param(
    [Parameter(Mandatory = $true)][string]$CoreSource,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
if (Test-Path $Output) { throw "Refusing to replace an existing bundle: $Output" }
if (-not (Test-Path "$CoreSource\pyproject.toml")) { throw "Core source is not a toolkit checkout" }
$Scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("seohead-desktop-build-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Scratch | Out-Null
try {
    & $Python -m PyInstaller --clean --noconfirm --onedir --console --name seohead --paths $CoreSource --collect-all seohead --distpath "$Scratch\core-dist" --workpath "$Scratch\core-work" --specpath $Scratch "$CoreSource\seohead\cli.py"
    & $Python -m PyInstaller --clean --noconfirm --onedir --windowed --name "SEOHEAD Desktop" --collect-data seohead_desktop --add-data "$Scratch\core-dist\seohead;resources\core\seohead" --distpath "$Scratch\app-dist" --workpath "$Scratch\app-work" --specpath $Scratch "$ProjectRoot\scripts\entrypoint.py"
    $Bundle = "$Scratch\app-dist\SEOHEAD Desktop"
    $Resources = "$Bundle\resources"
    New-Item -ItemType Directory -Force -Path "$Resources\licenses" | Out-Null
    Copy-Item "$ProjectRoot\LICENSE" "$Resources\licenses\SEOHEAD-Desktop-GPL-3.0-or-later.txt"
    Copy-Item "$ProjectRoot\THIRD_PARTY_NOTICES.md" "$Resources\licenses\THIRD_PARTY_NOTICES.md"
    Copy-Item "$CoreSource\LICENSE" "$Resources\licenses\SEOHEAD-Tools-MIT.txt"
    Copy-Item "$CoreSource\THIRD_PARTY_NOTICES.md" "$Resources\licenses\SEOHEAD-Tools-THIRD_PARTY_NOTICES.md"
    Copy-Item "$ProjectRoot\src\seohead_desktop\assets\fonts\OFL.txt" "$Resources\licenses\Roboto-OFL-1.1.txt"
    Copy-Item "$ProjectRoot\src\seohead_desktop\assets\icons\LICENSE.txt" "$Resources\licenses\Material-Design-Icons-Apache-2.0.txt"
    Copy-Item "$ProjectRoot\src\seohead_desktop\assets\asset-manifest.json" "$Resources\licenses\desktop-assets-manifest.json"
    & $Python "$ProjectRoot\scripts\copy_runtime_notices.py" --output "$Resources\licenses"
    & $Python "$ProjectRoot\scripts\bundle_manifest.py" create --core-source $CoreSource --platform windows --output "$Resources\core-manifest.json"
    & $Python "$ProjectRoot\scripts\bundle_manifest.py" finalize --manifest "$Resources\core-manifest.json" --cli "$Resources\core\seohead\seohead.exe"
    & $Python "$ProjectRoot\scripts\smoke_bundle.py" --bundle $Bundle
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
    Move-Item $Bundle $Output
} finally {
    if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
}
