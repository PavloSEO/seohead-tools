# Offline BI evidence packages

`bi-export` projects saved evidence into typed pages, findings, metric observations,
link occurrences and coverage CSV partitions, plus a versioned manifest. It performs
no crawl, provider request or external publication. Supply exactly one `scan` or
`audit`, an explicit new output directory and optional saved provider join artifacts.

```bash
seohead bi-export --audit examples/audit.json --out-dir ./bi-package
```

Partitions declare row counts, schema/grain/keys, byte counts and SHA-256 hashes.
Null/unknown/unavailable states and reasons stay explicit; measured numeric zero
stays zero. Metric observations preserve their original provider/dimension/period
grain. Unmatched and unkeyable populations remain identifiable. This projection
does not add metric values or infer causal effects. CSV formula-like cells receive
an explicit safety prefix recorded in the manifest.

Output is staged and published only after bounds and row conservation pass; existing
output is refused. Scan access is read-only. A missing saved audit makes findings
unavailable, rather than a clean zero-findings audit. Valid audit.v2 companions are
read with an explicit 64 MiB compatibility bound so their findings cannot silently
vanish behind the inline slot. The scan input remains limited to 120 MiB and 50,000
pages; providers and total output have separate hard budgets. This implementation
materializes bounded pages/findings/provider observations; it does not demonstrate
streaming million-page BI, Google Sheets publication or the Looker Studio pack.
