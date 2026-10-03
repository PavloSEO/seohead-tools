# Report templates: what to provide to generate each file

Every report is rendered from one document: the result of `site-audit`. Report
generators do not calculate metrics or make network requests: **if a value is not
in the JSON document, it will not appear in the report**. These two samples show
which values belong in each field.

| File | Purpose |
|---|---|
| [`minimal.json`](minimal.json) | The smallest document that can produce a report |
| [`full.json`](full.json) | A fully populated sample containing every report section |
| [`seo-audit-sample/seo-audit-sample-ru.pdf`](seo-audit-sample/seo-audit-sample-ru.pdf) | 23-page Russian visual example |
| [`seo-audit-sample/seo-audit-sample-en.pdf`](seo-audit-sample/seo-audit-sample-en.pdf) | 23-page English visual example |

The two PDF examples use an invented demo company, the reserved `example.invalid`
domain and entirely synthetic figures. They contain no client logo, client identity,
analytics exports or real measurements. They illustrate a client-style monthly SEO
report; they are static visual examples and are not outputs of the `site-audit` JSON
report contract described below.

Rebuild them with `python examples/reports/build_seo_sample.py`. The self-contained
builder uses the repository's Chromium PDF renderer and synthetic data only. Its visual
direction is a restrained presentation deck: blue chapter markers, readable metric
cards, consistent search-source colors, concise chart titles, clear tables and a
fictional geometric demo mark. If you adapt the design with an image-generation tool,
create a new abstract mark for a fictional brand; do not extract or reuse a customer's
logo, names, URL, analytics values, screenshots or report-specific facts.
The [visual brief](seo-audit-sample/visual-brief.md) provides prompts for future layout
and fictional-logo work.

## Try it now

```bash
seohead report-build --audit examples/reports/full.json --format xlsx --out audit.xlsx
seohead report-build --audit examples/reports/full.json --format docx --out audit.docx
seohead report-build --audit examples/reports/full.json --format csv  --out audit.csv
seohead report-build --audit examples/reports/full.json --format md   --out audit.md
```

In normal use, you do not fill in the document manually. Run the audit and build
the report in one command:

```bash
seohead site-audit --url https://example.org/ --limit 50 --report xlsx --out audit.xlsx
```

## Document contract (`seohead.site-audit/1`)

### Required minimum

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | Always `seohead.site-audit/1`; it identifies the document structure |
| `domain` | string | Used in the report title and default output filename |
| `findings` | array | The report's primary content; see below |
| `pages` | array | Page table; may be empty |
| `summary` | object | Totals used in the summary and chart |

### `findings[]` — findings

```json
{"source": "render_check", "severity": "critical",
 "url": "https://example.org/catalog", "text": "A concise description of the problem"}
```

- `severity` — `critical`, `warning`, or `notice`. It is **assigned by the
  aggregator rules** (`SEVERITY_RULES` in `seohead/audit/site.py`), not measured
  by an individual tool. The document states this explicitly in
  `summary.severity_note`.
- `url` — optional; site-wide findings do not have one.
- `text` — a human-readable description. It is copied into the report verbatim,
  so write it exactly as it should appear in the client-facing document.

Findings are ordered from critical problems to informational notices. Word groups
them by severity; Excel provides a filter on the `Severity` column.

### `pages[]` — page table

Excel and Word columns are populated from `url`, `status`, `title`,
`title_length`, `description_length`, `h1`, `canonical`, `words`,
`schema_types`, `schema_errors`, and `social_missing`. Missing values remain
blank and do not cause an error.

### `summary` — summary

```json
{"pages_checked": 2, "findings_total": 4,
 "findings_by_severity": {"critical": 1, "warning": 1, "notice": 2},
 "tools_run": ["domain_profile", "tech_detect"],
 "tools_failed": [{"tool": "log_analyze", "error": "Log file was not provided"}],
 "severity_note": "Severity is assigned by the aggregator rules"}
```

**`tools_failed` is the most important field in the document.** A check that
could not run belongs here, and all report formats render it in a separate
`Not covered by this report` section. A silent check must not be interpreted as
`no problems found`; the report must distinguish those outcomes.

### `site` — raw tool responses

Responses are stored unchanged under their handler names, such as
`domain_profile`, `tech_detect`, `security_check`, and `regions_check`. Excel
uses them to build the `Technologies` sheet; the remaining data stays available
for readers who need implementation details.

## What each format provides

| Format | Intended use | Contents |
|---|---|---|
| `xlsx` | Working file | Four sheets, auto-filters, and a live Excel severity chart |
| `docx` | Client deliverable | Headings, findings grouped by severity, and the first 60 pages |
| `csv` | Import | Two files: findings and `*.pages.csv`; semicolon delimiter and Excel-compatible BOM |
| `md` | Git and reading | The complete report in one file |
| `json` | Data exchange | The same document, formatted as JSON |

## Optional dependencies

```bash
pip install -e ".[reports]"     # openpyxl + python-docx
```

Without these dependencies, `csv`, `md`, and `json` still work. The `xlsx` and
`docx` renderers return `ok: false` with the installation command; that failure
is represented as result data rather than a process crash.
