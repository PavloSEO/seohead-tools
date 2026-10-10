# Naming convention

This exists because an audit of the tree (issue #17) found five module basenames used twice, a
validator and a JSON Schema sharing the word "schema" one directory apart, two report packages
with different suffix rules, and tests named after the review that produced them. None of that was
a bug; all of it was friction nobody had written a rule against. This is the rule.

## A module name says what the thing does, not where it lives

The package already provides the location: `seohead.recon.regions` and
`seohead.data_sources.yandex_regions` are unambiguous through their fully qualified path alone. A
filename should still carry the domain word on its own, because tracebacks, `grep`, and an agent
deciding which file to open see the basename before they see the path. Prefer `yandex_regions.py`
over `regions.py` inside `data_sources/`, where every sibling (`yandex_cloud.py`, `metrika.py`) is
already named after its source, not the generic word for what it returns.

Do not fix this by prefixing the package name onto the filename (`sf_config.py` inside `sf/`) —
that only restates the path the import statement already carries. Name the responsibility instead:
what `sf/config.py` loads is audit thresholds and severity overrides, so it should be named `audit_config.py` (the file is still `sf/config.py`).

## Two kinds of deliberate basename reuse

Two categories of same-named files across packages are correct and stay:

- **Entry-point role names.** `cli.py` is the standard name for a package's own argument-parsing
  entry point, the same way `__main__.py` is the standard name for a module entry point. Every
  package that exposes a console command gets one; the fully qualified path (`seohead.cli` vs.
  `seohead.sf.cli`) is what a reader or an import statement actually resolves, and needing a
  second glance at which `cli.py` opened is a fair price for not inventing a synonym per package.
- **Output-format token names.** A report-writer module is named after the exact format token it
  produces — the same string used in the public `--format` flag and `reports.FORMATS`. `md.py`
  exists in both `reports/` (writes the site-audit Markdown report) and `sf/reporters/` (writes
  `audit.md`) because both write Markdown, for different documents, and the format is what a
  reader needs to know before the pipeline.

Every other repeated basename is a bug: it means two unrelated things share a name because nobody
checked. `config.py`, `excel.py`, `regions.py`, and `sitemap.py` existing twice were exactly this
— generic nouns that said nothing about which configuration, which spreadsheet, which regions, or
which sitemap operation was inside.

## Format-token modules avoid shadowing the standard library

`csv.py` and `json.py` as module names would shadow `csv` and `json` from the standard library for
any sibling that writes `import csv` expecting the real one. Where the format token collides with a
stdlib module name, append `file`: `csvfile.py`, `jsonfile.py`. This is not an inconsistency to
"clean up" later — it is the one case where the token rule yields to a name that would break
imports.

## A name describing history is a name with an expiration date

A test module is named after what it tests, never after the process that produced it.
`test_review_fixes.py` and `test_edgecases.py` stop being findable the day nobody remembers which
review or which adversarial pass that was; a test for `sf/core/sitemap_coverage.py` belongs in
`test_sitemap_coverage.py` regardless of which pull request added it. When a module is split, the
new file name states what subset it covers (`test_check_coverage.py` for check-firing coverage,
not `test_extended_checks.py` for the batch they arrived in). A regression test for behavior a
module already has a test file for lives in that same file — `test_page_type_regressions.py`
duplicated `test_page_type.py`'s subject with no distinction a reader could act on.

## Generic names inside a single package are the lowest priority

`sf/core/rules.py`, `heuristics.py`, `models.py`, and `context.py` say nothing about the SF-audit
domain on their own, but all but `models.py` are the only file with that name anywhere in the tree, their
docstrings are accurate, and renaming them is churn for readers who already know the package.
Leave them unless a second module with the same name appears; that is the point at which a generic
name has actually become a collision. `models.py` has now reached that point (`sf/core/models.py` and `core/models.py`); the rename or an accepted-collision record is left to the owner.

## What this convention does not decide

The import package, the console command, the product line, and the website all currently share the
name `seohead`. Whether to rename the package, the command, both, or to split a neutral library
from a product-named CLI is a product decision blocked on choosing a new name — tracked in issue
#17 and left open there. `sf` as a package name (a third-party product abbreviation) and `recon`
(jargon, but internally consistent) are named in the same issue and deliberately left for that
decision, since renaming them now and renaming the top-level package later would mean contributors
relearn the tree twice.
