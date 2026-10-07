#!/bin/sh
# Build a self-contained macOS app plus the exact local SEOHEAD core revision.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
core_source=
output=
python_bin=${PYTHON_BIN:-"$project_dir/.venv/bin/python"}

usage() {
    echo "Usage: $0 --core-source PATH --output PATH [--python PATH]" >&2
    exit 64
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --core-source) core_source=${2:-}; shift 2 ;;
        --output) output=${2:-}; shift 2 ;;
        --python) python_bin=${2:-}; shift 2 ;;
        *) usage ;;
    esac
done

[ -n "$core_source" ] && [ -n "$output" ] || usage
[ -x "$python_bin" ] || { echo "Python build environment is not executable: $python_bin" >&2; exit 66; }
[ -f "$core_source/pyproject.toml" ] || { echo "Core source is not a toolkit checkout: $core_source" >&2; exit 66; }
[ ! -e "$output" ] || { echo "Refusing to replace an existing bundle: $output" >&2; exit 73; }
"$python_bin" -c 'import PyInstaller, PyQt5' >/dev/null

case "$(uname -s)" in
    Darwin) ;;
    *) echo "build_macos.sh must run on macOS" >&2; exit 69 ;;
esac

mkdir -p "$project_dir/.build/scratch"
scratch=$(mktemp -d "$project_dir/.build/scratch/packaging.XXXXXX")
trap 'rm -rf "$scratch"' EXIT HUP INT TERM

"$python_bin" -m PyInstaller \
    --clean --noconfirm --onedir --console --name seohead \
    --paths "$core_source" --collect-all seohead \
    --distpath "$scratch/core-dist" --workpath "$scratch/core-work" --specpath "$scratch" \
    "$core_source/seohead/cli.py"

"$python_bin" -m PyInstaller \
    --clean --noconfirm --onedir --windowed --name "SEOHEAD Desktop" \
    --osx-bundle-identifier tech.seohead.desktop \
    --collect-data seohead_desktop \
    --add-data "$scratch/core-dist/seohead:core/seohead" \
    --distpath "$scratch/app-dist" --workpath "$scratch/app-work" --specpath "$scratch" \
    "$project_dir/scripts/entrypoint.py"

bundle="$scratch/app-dist/SEOHEAD Desktop.app"
resources="$bundle/Contents/Resources"
mkdir -p "$resources/licenses"
cp "$project_dir/LICENSE" "$resources/licenses/SEOHEAD-Desktop-GPL-3.0-or-later.txt"
cp "$project_dir/THIRD_PARTY_NOTICES.md" "$resources/licenses/THIRD_PARTY_NOTICES.md"
cp "$core_source/LICENSE" "$resources/licenses/SEOHEAD-Tools-MIT.txt"
cp "$core_source/THIRD_PARTY_NOTICES.md" "$resources/licenses/SEOHEAD-Tools-THIRD_PARTY_NOTICES.md"
cp "$project_dir/src/seohead_desktop/assets/fonts/OFL.txt" "$resources/licenses/Roboto-OFL-1.1.txt"
cp "$project_dir/src/seohead_desktop/assets/icons/LICENSE.txt" "$resources/licenses/Material-Design-Icons-Apache-2.0.txt"
cp "$project_dir/src/seohead_desktop/assets/asset-manifest.json" "$resources/licenses/desktop-assets-manifest.json"
"$python_bin" "$project_dir/scripts/copy_runtime_notices.py" --output "$resources/licenses"
"$python_bin" "$project_dir/scripts/bundle_manifest.py" create \
    --core-source "$core_source" --platform macos --output "$resources/core-manifest.json"
"$python_bin" "$project_dir/scripts/bundle_manifest.py" finalize \
    --manifest "$resources/core-manifest.json" --cli "$resources/core/seohead/seohead"
"$python_bin" "$project_dir/scripts/smoke_bundle.py" --bundle "$bundle"

mkdir -p "$(dirname -- "$output")"
mv "$bundle" "$output"
echo "Created $output"
