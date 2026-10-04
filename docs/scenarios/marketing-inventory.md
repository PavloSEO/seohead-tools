# CTA and embedded-form inventory

## Question

> Which CTA labels lead to the same destination, and where does each embedded
> lead form appear?

`marketing-inventory` reads HTML supplied by the operator. It does not fetch a
site, submit a form, or inspect an iframe document. Each result row comes from
one matched DOM element, so a missing `href` stays null on that element rather
than shifting a later destination onto it.

## Input

Prepare a bounded JSON document list. `document_ref` may name a retained scan
document chosen by the caller, but this command does not open or alter a scan.
Use `body_state: "unavailable"` or `"partial"` when a retained body cannot be
supplied in full; the result reports incomplete coverage instead of treating it
as a page with no CTAs or forms.

```json
{
  "documents": [
    {
      "url": "https://example.com/pricing",
      "document_ref": "scan:demo/document:12",
      "representation": "raw",
      "html": "<a class='cta' href='/demo'>Book a demo</a><a class='cta'>Talk to us</a><form data-form-id='lead' action='/send'></form>"
    },
    {
      "url": "https://example.com/old",
      "document_ref": "scan:demo/document:9",
      "representation": "rendered",
      "body_state": "unavailable"
    }
  ]
}
```

## Run

```bash
seohead marketing-inventory --input '{"documents":[{"url":"https://example.com/pricing","document_ref":"scan:demo/document:12","representation":"raw","html":"<a class=\"cta\" href=\"/demo\">Book a demo</a><a class=\"cta\">Talk to us</a><form data-form-id=\"lead\" action=\"/send\"></form>"}]}'
```

To write inspectable local artifacts, select a new directory explicitly:

```bash
seohead marketing-inventory --input '{"documents":[{"url":"https://example.com/","html":"<a class=\"cta\" href=\"/demo\">Demo</a>"}],"out_dir":"./marketing-inventory"}'
```

The directory contains `marketing-inventory.json` and `marketing-occurrences.csv`.
CSV cells that begin with a spreadsheet formula character are escaped. The MCP
tool is `seo_marketing_inventory` with the same JSON input.

## Read the result

- `occurrences[]` preserves source URL, document reference, raw/rendered
  representation, occurrence identity, raw target and resolved target.
- `target_state: "missing"` means that matched element had no relevant
  attribute. It is not associated with the next element's target.
- `cta_groups[]` groups normalized label variants by resolved destination;
  labels are review candidates, not automatically wrong wording.
- `form_groups[]` groups repeated observed identifiers with contributing pages.
  Identifiers come only from configured attributes (`id`, `name`,
  `data-form-id` by default) or explicit URL parameter names.
- `coverage.documents_unavailable[]` names input bodies that were not eligible.
  A partial or unavailable body prevents a complete-site claim.

## Boundaries

The default CTA selector is `a.cta, [data-cta]`; the default form selector is
`form, iframe`. Pass site-specific CSS selectors and identifier rules when the
markup uses a different convention. An iframe `src` is an observed embed URL;
even for the same origin it does not authorize inspecting the framed content.
