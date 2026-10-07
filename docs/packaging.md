# Packaging preparation

SEOHEAD Desktop is prepared as one local artifact: the native PyQt shell and
the exact compatible SEOHEAD Tools core. The desktop process invokes the
co-shipped `seohead` executable by a relative path; it does not fall back to a
global installation. That executable also exposes the core's local stdio MCP
mode through `seohead mcp`.

This document is a packaging contract, not a release instruction. macOS is the
only platform with a native preparation smoke run so far. Linux and Windows
layouts are specified, but neither has a packaged runtime acceptance yet.

## Provenance and compatibility

`scripts/bundle_manifest.py` refuses a dirty core checkout and writes
`core-manifest.json` into the artifact. It records:

- the core distribution and declared version;
- public source remote, exact Git commit, and SHA-256 of `git archive HEAD`;
- a relative core executable path and its SHA-256 after freezing.

The application resolves that manifest only inside its own resource directory.
An explicit `--core-cli` remains an override for development and diagnostics;
the packaged default is always the co-shipped executable. The bundled core is
therefore a precise source revision, not merely a compatible package range.

## macOS build contract

Use a dedicated build virtual environment. It needs the desktop package with
its `build` extra and all dependencies needed by the selected local core. Do
not install either into a global Python environment.

```bash
desktop=/path/to/seohead-desktop
core=/path/to/seohead-tools

python3 -m venv "$desktop/.build/venv"
"$desktop/.build/venv/bin/python" -m pip install -e "$desktop[build]"
"$desktop/.build/venv/bin/python" -m pip install -e "$core[all]"
"$desktop/scripts/build_macos.sh" \
  --python "$desktop/.build/venv/bin/python" \
  --core-source "$core" \
  --output "$desktop/dist/SEOHEAD Desktop.app"
```

The script refuses an existing output path and a dirty core checkout. It first
freezes the core CLI, adds that whole one-directory bundle below the desktop
app's `Contents/Resources/core/`, copies both projects' notices, writes the
provenance manifest, then runs a no-network `seohead --version` smoke test.
It does not start a crawl, submit a job, open a project, or call a provider.

## Clean-machine acceptance

On the target machine, copy the complete `.app` directory and run:

```bash
python3 scripts/smoke_bundle.py --bundle "/Applications/SEOHEAD Desktop.app"
```

The smoke check validates the manifest schema, rejects a path escaping the
resource directory, hashes the co-shipped executable, and runs only
`seohead --version`. Native UI acceptance remains a separate manual gate:
launch the copied app, confirm its project picker, open a known local project
read-only, and confirm that the desktop identifies the recorded bundled core.

## Rollback

The app has no installer, service, listener, or migration. Keep the previous
verified `.app` intact until the copied bundle has passed the clean-machine
smoke and native UI acceptance. To roll back, quit SEOHEAD Desktop and replace
the failed `.app` directory with that previous verified directory. Do not copy
individual files between bundles: the executable hash and source provenance
would no longer match the manifest.

## Release gates still open

- Include the full GPLv3 text and the notices produced by the exact PyQt5/Qt
  build environment before publishing a binary; `LICENSE` and
  `THIRD_PARTY_NOTICES.md` state this gate explicitly.
- Produce a separate native artifact and clean-machine acceptance record for
  Linux and Windows. Cross-packaging from macOS is not an acceptance result.
- Add code-signing, notarization, update, and release-retention decisions only
  when distribution is in scope.
