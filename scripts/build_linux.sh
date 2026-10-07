#!/bin/sh
# Linux packaging contract. It is intentionally not accepted from macOS.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
core_source=${1:?"first argument must be a clean SEOHEAD Tools checkout"}
output=${2:?"second argument must be a new output directory"}
python_bin=${PYTHON_BIN:-"$project_dir/.venv/bin/python"}

[ "$(uname -s)" = "Linux" ] || { echo "build_linux.sh must run on Linux" >&2; exit 69; }
[ ! -e "$output" ] || { echo "Refusing to replace an existing bundle: $output" >&2; exit 73; }
mkdir -p "$project_dir/.build/scratch"
scratch=$(mktemp -d "$project_dir/.build/scratch/packaging.XXXXXX")
trap 'rm -rf "$scratch"' EXIT HUP INT TERM

"$python_bin" -m PyInstaller --clean --noconfirm --onedir --console --name seohead \
    --paths "$core_source" --collect-all seohead --distpath "$scratch/core-dist" \
    --workpath "$scratch/core-work" --specpath "$scratch" "$core_source/seohead/cli.py"
"$python_bin" -m PyInstaller --clean --noconfirm --onedir --console --name seohead-desktop-agent \
    --collect-data seohead_desktop --collect-data mcp --distpath "$scratch/agent-dist" \
    --workpath "$scratch/agent-work" --specpath "$scratch" "$project_dir/scripts/control_entrypoint.py"
"$python_bin" -m PyInstaller --clean --noconfirm --onedir --windowed --name "SEOHEAD Desktop" \
    --collect-data seohead_desktop --add-data "$scratch/core-dist/seohead:resources/core/seohead" \
    --distpath "$scratch/app-dist" --workpath "$scratch/app-work" --specpath "$scratch" \
    "$project_dir/scripts/entrypoint.py"

bundle="$scratch/app-dist/SEOHEAD Desktop"
resources="$bundle/resources"
mkdir -p "$resources/agent"
cp -R "$scratch/agent-dist/seohead-desktop-agent" "$resources/agent/seohead-desktop-agent"
mkdir -p "$resources/licenses"
cp "$project_dir/LICENSE" "$resources/licenses/SEOHEAD-Desktop-GPL-3.0-or-later.txt"
cp "$project_dir/THIRD_PARTY_NOTICES.md" "$resources/licenses/THIRD_PARTY_NOTICES.md"
cp "$core_source/LICENSE" "$resources/licenses/SEOHEAD-Tools-MIT.txt"
cp "$core_source/THIRD_PARTY_NOTICES.md" "$resources/licenses/SEOHEAD-Tools-THIRD_PARTY_NOTICES.md"
cp "$project_dir/src/seohead_desktop/assets/fonts/OFL.txt" "$resources/licenses/Roboto-OFL-1.1.txt"
cp "$project_dir/src/seohead_desktop/assets/icons/LICENSE.txt" "$resources/licenses/Material-Design-Icons-Apache-2.0.txt"
cp "$project_dir/src/seohead_desktop/assets/asset-manifest.json" "$resources/licenses/desktop-assets-manifest.json"
"$python_bin" "$project_dir/scripts/copy_runtime_notices.py" --output "$resources/licenses"
"$python_bin" "$project_dir/scripts/bundle_manifest.py" create --core-source "$core_source" --platform linux --output "$resources/core-manifest.json"
"$python_bin" "$project_dir/scripts/bundle_manifest.py" finalize --manifest "$resources/core-manifest.json" --cli "$resources/core/seohead/seohead" --agent "$resources/agent/seohead-desktop-agent/seohead-desktop-agent"
"$python_bin" "$project_dir/scripts/smoke_bundle.py" --bundle "$bundle"
mkdir -p "$(dirname -- "$output")"
mv "$bundle" "$output"
