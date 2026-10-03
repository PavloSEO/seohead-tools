"""Provider capability and specialist workflow matrix (issue #779, epic #753).

Data-only module. The provider inventory table is rendered straight from
``seohead.data_sources.providers.provider_registry()``, so it can never drift
ahead of the declared registry. The workflow rows are a finite curated
catalogue whose command and provider references are kept honest by
``tests/test_provider_matrix.py`` — the same boundary pattern as
``seohead/input_contracts.py`` — rather than by importing the interface layers
at runtime.

Two distinctions the matrix exists to keep visible:

- ``declared`` is not ``verified``. A provider listed in the registry is a
  declared contract; a present credential is configuration state. Only an
  explicit ``provider-verify`` read that returns ``target_access=verified``
  proves live access, and this document asserts none.
- ``registered operation`` is not ``shipped surface``. An operation the
  registry declares but no handler or collection dispatch reaches stays
  ``unsupported`` here, by name.
"""

from __future__ import annotations

from dataclasses import dataclass

from seohead.data_sources.providers import provider_registry

SUPPORT_STATES = ("supported", "partial", "unsupported", "unverified")

# Providers whose credentials are optional-by-default or paid per response;
# the registry's ``default_enabled=False`` already marks them.
_ACCESS_LABELS = {
    "read_only": "read-only",
    "read_only_paid": "read-only, paid",
    "read_only_optional_paid": "read-only, paid, off by default",
    "confirmed_write": "confirmed write, off by default",
}

_PRIVACY_LABELS = {
    "public": "public data",
    "public_url_list": "caller-supplied URL list, public endpoint",
    "aggregate": "aggregate statistics",
    "restricted": "restricted site/account data",
}


@dataclass(frozen=True)
class WorkflowRow:
    """One specialist workflow against the shipped provider surface.

    ``surface`` holds CLI command names; each is verified against the command
    registry by tests. ``status`` is one of :data:`SUPPORT_STATES` and reflects
    code and test evidence, never an assumed provider capability.
    """

    workflow: str
    use_case: str
    providers: tuple[str, ...]
    surface: tuple[str, ...]
    status: str
    auth: str
    cost_quota: str
    privacy: str
    limitations: str
    csv_fallback: str


WORKFLOWS: tuple[WorkflowRow, ...] = (
    WorkflowRow(
        workflow="yandex-demand",
        use_case="Expand a seed phrase and read demand seasonality for Yandex",
        providers=("yandex_cloud",),
        surface=("keywords-expand", "keywords-seasonality", "regions-tree"),
        status="supported",
        auth="API key + folder ID",
        cost_quota="Paid; Wordstat hourly quota; charges journaled in spend-report",
        privacy="aggregate",
        limitations=(
            "Base frequency only — the API has no ! / + / [] operators and base "
            "counts run roughly 9x exact; use Arsenkin for exact values"
        ),
        csv_fallback="not applicable — demand data is not URL-keyed",
    ),
    WorkflowRow(
        workflow="yandex-exact-frequency",
        use_case="Exact !W frequency the Wordstat API does not expose",
        providers=("arsenkin",),
        surface=("keywords-exact",),
        status="supported",
        auth="API token",
        cost_quota="Paid; consumes Arsenkin account limits; task_id journaled at billing time",
        privacy="aggregate",
        limitations="Billed at task creation; a timed-out poll is retrievable by task_id",
        csv_fallback="not applicable",
    ),
    WorkflowRow(
        workflow="google-demand",
        use_case="Search volume, seed expansion, and keyword difficulty for Google",
        providers=("dataforseo_backlinks",),
        surface=("google-keywords",),
        status="supported",
        auth="Login + password",
        cost_quota="Paid per response; sandbox is the default and is free",
        privacy="restricted",
        limitations=(
            "Keyword endpoints run through the dedicated DataForSEO module, not the "
            "registry; Russia and Belarus locations are refused by the coverage guard "
            "before any paid call"
        ),
        csv_fallback="not applicable",
    ),
    WorkflowRow(
        workflow="serp-collection",
        use_case="Fetch ranked results for queries on Yandex or Google",
        providers=("yandex_cloud", "dataforseo_backlinks"),
        surface=("serp-fetch", "google-serp"),
        status="supported",
        auth="Yandex: API key + folder ID; DataForSEO: login + password",
        cost_quota="Metered; Yandex async endpoint only — the ~16x-costlier sync endpoint is excluded",
        privacy="aggregate",
        limitations=(
            "Queries billed but not returned before timeout stay visible in the spend "
            "journal as named operations"
        ),
        csv_fallback="not applicable",
    ),
    WorkflowRow(
        workflow="serp-clustering",
        use_case="Cluster a keyword set by overlapping search results",
        providers=("arsenkin",),
        surface=("keywords-cluster",),
        status="partial",
        auth="None for keywords-cluster; Arsenkin API token would be required for the declared provider operation",
        cost_quota="keywords-cluster is free; the declared Arsenkin operation is paid",
        privacy="aggregate",
        limitations=(
            "The registry declares arsenkin serp_clustering, but provider-collect "
            "deliberately refuses the paid Arsenkin contract and no dedicated handler "
            "ships it — the shipped surface is the free draft clustering only"
        ),
        csv_fallback="a user-supplied keyword list can seed keywords-cluster; no provider CSV join",
    ),
    WorkflowRow(
        workflow="search-console-evidence",
        use_case="Clicks, impressions, position, indexing verdicts, and sitemap status for a property you own",
        providers=("gsc",),
        surface=("gsc-query", "provider-auth", "provider-verify", "provider-collect"),
        status="supported",
        auth="OAuth bearer or service account; durable grant via provider-auth",
        cost_quota="Free within Search Console row and request limits",
        privacy="restricted",
        limitations=(
            "Verification distinguishes an authenticated account from verified access "
            "to the requested property"
        ),
        csv_fallback="yes — an exported Search Console CSV joins the crawl via provider-join",
    ),
    WorkflowRow(
        workflow="webmaster-evidence",
        use_case="Yandex and Bing webmaster data: hosts, indexing, diagnostics, search performance, history",
        providers=("yandex_webmaster", "bing_webmaster"),
        surface=("provider-verify", "provider-collect"),
        status="supported",
        auth="Yandex: OAuth bearer; Bing: API key",
        cost_quota="Free within each webmaster API quota",
        privacy="restricted",
        limitations=(
            "Reachable only through the generic provider-verify/provider-collect "
            "commands — no dedicated CLI command ships for either provider"
        ),
        csv_fallback="yes — URL-keyed exports join via provider-join",
    ),
    WorkflowRow(
        workflow="traffic-analytics",
        use_case="Counter configuration, aggregate reports, and landing-page evidence",
        providers=("metrika", "ga4"),
        surface=(
            "metrika-counters",
            "metrika-setup",
            "metrika-report",
            "metrika-traffic-pdf",
            "provider-collect",
        ),
        status="partial",
        auth="OAuth bearer for both providers",
        cost_quota="Free within Metrika and GA4 Data API quotas",
        privacy="restricted — may contain personal identifiers; kept out of reports and commits",
        limitations=(
            "Metrika raw Logs API is intentionally unreachable; GA4 ships only the "
            "landing_pages operation and only via provider-collect"
        ),
        csv_fallback="yes — URL-keyed analytics exports join via provider-join",
    ),
    WorkflowRow(
        workflow="field-vitals",
        use_case="Core Web Vitals as real users measured them, at origin or URL level",
        providers=("crux", "pagespeed"),
        surface=("crux-report", "provider-collect"),
        status="supported",
        auth="Google Cloud API key for both providers",
        cost_quota="Free within Google API quotas",
        privacy="aggregate",
        limitations=(
            "PageSpeed mobile_samples/desktop_samples run only via provider-collect — "
            "no dedicated command ships"
        ),
        csv_fallback="no provider CSV join; CrUX/PSI have no user-export path here",
    ),
    WorkflowRow(
        workflow="link-evidence",
        use_case="Backlink summary for a target from a paid index",
        providers=("dataforseo_backlinks",),
        surface=("provider-collect",),
        status="partial",
        auth="Login + password",
        cost_quota="Paid per provider response; provider is off by default",
        privacy="restricted",
        limitations=(
            "Only backlinks_summary is declared; discovering a competitor's full "
            "profile is out of scope — backlinks-check audits a caller-supplied donor "
            "list instead and is not a provider"
        ),
        csv_fallback="a supplied donor-page list is the input to backlinks-check; no provider CSV join",
    ),
    WorkflowRow(
        workflow="url-submission",
        use_case="Notify Bing, Yandex, Naver, and Seznam that URLs changed",
        providers=("indexnow",),
        surface=("indexnow-submit",),
        status="supported",
        auth="Self-generated key hosted on the target site",
        cost_quota="Free; provider submission quota; off by default",
        privacy="caller-supplied URL list sent to a public endpoint",
        limitations=(
            "Confirmed write, not a collection — provider-collect refuses it by "
            "contract; Google has not joined IndexNow"
        ),
        csv_fallback="not applicable — write operation",
    ),
    WorkflowRow(
        workflow="public-recon",
        use_case="Snapshot history of a URL and subdomains named in public certificate logs",
        providers=("wayback", "crtsh"),
        surface=("wayback-history", "crtsh-subdomains"),
        status="supported",
        auth="None — public services",
        cost_quota="Free; public service pacing and availability",
        privacy="public",
        limitations="provider-verify reports not_required — there is no access contract to verify",
        csv_fallback="not applicable",
    ),
    WorkflowRow(
        workflow="evidence-join",
        use_case="Attach external URL-keyed rows to a crawl and surface orphan candidates for explicit review",
        providers=("gsc", "ga4", "metrika", "yandex_webmaster", "bing_webmaster"),
        surface=("provider-join", "provider-replay"),
        status="supported",
        auth="None — operates on already-collected or user-supplied rows",
        cost_quota="Free; local operation",
        privacy="restricted inputs stay local; artifact directory is created mode 0700",
        limitations=(
            "Never changes the crawl frontier; unkeyable, crawl-only, and "
            "external-only populations stay named and separate"
        ),
        csv_fallback="yes — this is the CSV fallback path itself",
    ),
)

UNSUPPORTED_WORK: tuple[tuple[str, str], ...] = (
    (
        "arsenkin serp_clustering via provider-collect",
        "declared in the registry but deliberately refused by the collection dispatch; "
        "tracked by the dedicated-operation contract rather than duplicated here",
    ),
    (
        "metrika raw_logs (Yandex Logs API)",
        "excluded by design — raw logs may contain personal identifiers and must not "
        "reach reports or commits",
    ),
    (
        "dedicated CLI commands for ga4, pagespeed, yandex_webmaster, and bing_webmaster",
        "these providers are reachable only through provider-verify/provider-collect",
    ),
    (
        "Google IndexNow submission",
        "Google has not joined IndexNow; no operation can claim it",
    ),
    (
        "competitor backlink discovery beyond backlinks_summary",
        "out of declared scope; backlinks-check covers a caller-supplied donor list only",
    ),
)

RELATED_ISSUES = ("#716", "#718", "#719", "#724", "#730")


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render() -> str:
    """Build docs/PROVIDERS.md from the live registry and the workflow catalogue."""
    registry = provider_registry()["providers"]
    lines = [
        "# Provider capability and workflow matrix",
        "",
        "Generated from `seohead/data_sources/providers.py` and the workflow catalogue in "
        "`seohead/provider_matrix.py` — do not edit by hand. Regenerate with:",
        "",
        "```bash",
        "python scripts/generate_provider_matrix.py",
        "```",
        "",
        "Issue #779 (epic #753). Related implementation issues: "
        + ", ".join(RELATED_ISSUES)
        + " — this matrix links them instead of duplicating their scopes.",
        "",
        "## How to read the statuses",
        "",
        "- **supported** — a shipped command or collection dispatch reaches the operation, "
        "with offline tests behind it.",
        "- **partial** — the workflow works but the shipped surface is narrower than the "
        "declared registry entry; the gap is named in the limitations column.",
        "- **unsupported** — declared or plausible work the code deliberately does not do; "
        "see *Unsupported work* below.",
        "- **unverified** — no code path exists to confirm or deny; nothing in this matrix "
        "is labelled live-verified: a registry entry is a declared contract, a present "
        "credential is configuration state, and only an explicit `provider-verify` read "
        "returning `target_access=verified` proves live target access.",
        "",
        "## Provider inventory",
        "",
        "Rendered from the registry exactly as `provider-registry` reports it.",
        "",
        "| Provider | Access | Operations | Credentials | Quota / cost | Privacy |",
        "|---|---|---|---|---|---|",
    ]
    for name, meta in registry.items():
        operations = list(meta["operations"])
        excluded = meta.get("excluded_operations")
        if excluded:
            operations.append(f"~~{', '.join(excluded)}~~ (excluded)")
        credentials = ", ".join(meta["credential_components"]) or "none"
        default_note = "" if meta.get("default_enabled", True) else " (off by default)"
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    f"`{name}`{default_note}",
                    _ACCESS_LABELS.get(meta["access"], meta["access"]),
                    ", ".join(operations),
                    credentials,
                    meta["quota_mode"],
                    _PRIVACY_LABELS.get(meta["privacy_class"], meta["privacy_class"]),
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Specialist workflow matrix",
        "",
        "| Workflow | Use case | Providers | Shipped surface | Status | Auth | Cost / quota | "
        "Privacy | Limitations | CSV fallback |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in WORKFLOWS:
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    f"`{row.workflow}`",
                    row.use_case,
                    ", ".join(f"`{p}`" for p in row.providers),
                    ", ".join(f"`{c}`" for c in row.surface),
                    row.status,
                    row.auth,
                    row.cost_quota,
                    row.privacy,
                    row.limitations,
                    row.csv_fallback,
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Unsupported work, stated explicitly",
        "",
    ]
    lines += [f"- **{name}** — {reason}" for name, reason in UNSUPPORTED_WORK]
    lines += [
        "",
        "## Phased first release",
        "",
        "The first release phase covers the workflows whose evidence is free or already "
        "bounded by first-party quotas: `public-recon`, `evidence-join`, "
        "`search-console-evidence`, `webmaster-evidence`, `field-vitals`, "
        "`traffic-analytics`, and `url-submission` behind its existing confirmed-write "
        "gate. Phase two adds the metered demand and SERP workflows (`yandex-demand`, "
        "`yandex-exact-frequency`, `google-demand`, `serp-collection`) once the caller "
        "deliberately configures the paid credentials they require — sandbox remains the "
        "DataForSEO default. Deferred by design, not by omission: `serp-clustering` via "
        "the paid Arsenkin contract, Metrika raw logs, dedicated CLI commands for the "
        "collect-only providers, and any backlink discovery beyond `backlinks_summary`. "
        "This is a selected subset of the provider landscape, not a claim that every "
        "SEO data service is covered.",
        "",
        "Every row above reflects code and test evidence in this repository; no provider "
        "is described as live-verified by this document.",
    ]
    return "\n".join(lines).rstrip() + "\n"
