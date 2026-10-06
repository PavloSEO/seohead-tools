"""Find claims a finished run makes that cannot all be true at once.

Every defect this toolkit had on live sites was found by reading a report and noticing an
impossible number, never by a failing test:

* a 739 KB WebP recorded as 1.27 MB — the file on disk contradicted the report (#99);
* 392 findings of one check on a site with 124 HTML pages — more findings than pages (#94);
* 78 pages canonicalising "to a redirect" whose canonical answers 200 (#95);
* 433 content words on a page whose ``<main>`` holds 429 (#96).

None of them needed a live site to *detect*. Each is arithmetic over what the run already
wrote down, and each survived sixteen hundred tests, because a test asserts what a fixture
does and every fixture was clean. This module applies that arithmetic to a real run.

What it reports is deliberately narrow. Not SEO findings — the audit does those. Not
thresholds — a slow page or a long title is a judgement, not a contradiction. Only pairs of
facts from the same run that cannot both be right, each anomaly naming both values and where
each came from, so the reader can check the claim rather than trust it.

Pure and network-free: it reads artifacts a run already produced.

``audit.json`` and ``pages.jsonl`` are outputs; ``decisions.jsonl`` (issue #134), when a native
crawl wrote one, is closer to a trace of how they were produced — a wrong decision that still
adds up to a consistent-looking output is invisible to a rule that reads only the outputs. See
``rule_outside_host_exclusion_matches_its_own_host`` for the shape of that gap.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import asdict, dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# The one definition of "most of the site", shared with the aggregator that
# writes summary.implausible_checks -- two thresholds would eventually differ
# and the scanner would contradict the report it reads.
from seohead.sf.core.aggregate import IMPLAUSIBLE_SHARE

__all__ = ["RULES", "Anomaly", "RunArtifacts", "load_run", "scan"]


@dataclass
class Anomaly:
    """One pair of facts from a run that cannot both be true.

    ``observed`` and ``expected`` are the two disagreeing values, and ``sources`` says where
    each was read from. There is no severity: an anomaly is either proven by the artifacts or
    it is not reported.
    """

    rule: str
    message: str
    observed: Any = None
    expected: Any = None
    target: str = ""
    sources: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunArtifacts:
    """Everything a scan reads. Any of it may be absent; a rule that needs a missing
    artifact does not run rather than guessing."""

    audit: dict[str, Any] | None = None
    pages: Any = field(default_factory=list)
    files: dict[str, int] = field(default_factory=dict)  # URL -> bytes on disk
    decisions: list[dict[str, Any]] = field(default_factory=list)  # decisions.jsonl (issue #134)
    audit_path: str = ""
    pages_path: str = ""
    decisions_path: str = ""
    native: bool = False
    _audit_source: Any = field(default=None, repr=False)

    def close(self):
        if self.native:
            self.pages.close()
            if self._audit_source is not None:
                self._audit_source.close()
                self._audit_source = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _ScanPages:
    """Re-iterable native facts with indexed URL membership for consistency rules."""

    def __init__(self, source):
        descriptor, self.path = tempfile.mkstemp(prefix="seohead-logscan-", suffix=".sqlite")
        os.close(descriptor)
        self.con = sqlite3.connect(self.path)
        self.closed = False
        self.con.executescript(
            "CREATE TABLE pages(ordinal INTEGER PRIMARY KEY,url TEXT UNIQUE,norm TEXT,status INTEGER,value TEXT);"
            "CREATE INDEX answering ON pages(norm,status);"
            "CREATE TABLE seen(check_id TEXT,url TEXT,PRIMARY KEY(check_id,url)) WITHOUT ROWID;"
        )
        self.count = 0
        for row in source.execute(
            "SELECT u.url,p.status_code,p.content_type,p.size_bytes,p.word_count,p.text_ratio,p.representation "
            "FROM pages p JOIN urls u USING(url_id) ORDER BY p.page_ordinal"
        ):
            item = dict(row)
            self.con.execute(
                "INSERT INTO pages VALUES(?,?,?,?,?)",
                (
                    self.count,
                    item["url"],
                    item["url"].rstrip("/"),
                    item["status_code"],
                    json.dumps(item),
                ),
            )
            self.count += 1
        self.con.commit()

    def __len__(self):
        return self.count

    def __iter__(self):
        for row in self.con.execute("SELECT value FROM pages ORDER BY ordinal"):
            yield json.loads(row[0])

    def __contains__(self, url):
        return self.con.execute("SELECT 1 FROM pages WHERE url=?", (url,)).fetchone() is not None

    def answers(self, url):
        return (
            self.con.execute(
                "SELECT 1 FROM pages WHERE norm=? AND status BETWEEN 200 AND 299 LIMIT 1", (url,)
            ).fetchone()
            is not None
        )

    def new_finding(self, check, target):
        return bool(
            self.con.execute("INSERT OR IGNORE INTO seen VALUES(?,?)", (check, target)).rowcount
        )

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.con.close()
        os.unlink(self.path)

    def __del__(self):
        self.close()


class _AuditIssues:
    def __init__(self, source):
        self.source = source

    def __len__(self):
        return self.source.count("/issues")

    def __iter__(self):
        return self.source.iter_collection("/issues")


def _load_native(path: Path, images_dir):
    from seohead.storage import open_scan
    from seohead.storage.audit_v2 import audit_v2_path
    from seohead.storage.inputs import load_audit_source

    artifacts = RunArtifacts(native=True, audit_path=str(path), pages_path=str(path) + "#pages")
    with closing(open_scan(path, require_audit=False)) as con:
        artifacts.pages = _ScanPages(con)
        has_audit = (
            con.execute("SELECT 1 FROM audit WHERE singleton=1").fetchone() is not None
            or audit_v2_path(path).exists()
        )
    try:
        if has_audit:
            source = load_audit_source(path, "run")
            if hasattr(source, "iter_collection"):
                artifacts._audit_source = source
                artifacts.audit = dict(source.header)
                artifacts.audit["issues"] = _AuditIssues(source)
            else:
                artifacts.audit = source
        if images_dir:
            artifacts.files = _downloaded_sizes(Path(os.path.expanduser(images_dir)))
        return artifacts
    except Exception:
        artifacts.close()
        raise


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue  # a truncated final line from an interrupted run, not a finding
            if isinstance(value, dict):
                out.append(value)
    return out


def _downloaded_sizes(images_dir: Path) -> dict[str, int]:
    """Map each downloaded file back to the URL it came from, by its manifest.

    ``images-download`` writes a manifest beside the files; without one there is no way to
    say which URL a file on disk came from, and guessing from the path would produce exactly
    the kind of unfounded claim this module exists to avoid.
    """
    manifest = images_dir / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    entries = raw.get("images") if isinstance(raw, dict) else raw
    sizes: dict[str, int] = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        path = entry.get("path") or entry.get("file")
        if not url or not path:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = images_dir / candidate
        if candidate.is_file():
            sizes[str(url)] = candidate.stat().st_size
    return sizes


def load_run(run_dir: str, images_dir: str | None = None) -> RunArtifacts:
    """Read the artifacts of one finished run from a directory."""
    base = Path(os.path.expanduser(run_dir))
    from seohead.storage.inputs import is_sqlite_input

    native_path = base if base.is_file() else base / "scan.sqlite"
    if is_sqlite_input(native_path):
        return _load_native(native_path, images_dir)
    artifacts = RunArtifacts()

    audit_path = base / "audit.json"
    if audit_path.is_file():
        try:
            artifacts.audit = json.loads(audit_path.read_text(encoding="utf-8"))
            artifacts.audit_path = str(audit_path)
        except ValueError:
            artifacts.audit = None

    pages_path = base / "pages.jsonl"
    if pages_path.is_file():
        artifacts.pages = _read_jsonl(pages_path)
        artifacts.pages_path = str(pages_path)

    decisions_path = base / "decisions.jsonl"
    if decisions_path.is_file():
        artifacts.decisions = _read_jsonl(decisions_path)
        artifacts.decisions_path = str(decisions_path)

    if images_dir:
        artifacts.files = _downloaded_sizes(Path(os.path.expanduser(images_dir)))
    return artifacts


# ── rules ────────────────────────────────────────────────────────────────────
#
# Each rule takes the artifacts and yields Anomaly objects. A rule that cannot run — the
# artifact it needs is absent, or the field it compares was never recorded — yields nothing.
# Silence from a rule means "not checked here", which is why the scan result also reports
# which rules ran.


def rule_recorded_size_matches_the_file(run: RunArtifacts) -> Iterator[Anomaly]:
    """A page's recorded size against the same URL's bytes on disk (#99)."""
    for page in run.pages:
        url = page.get("url")
        recorded = page.get("size_bytes")
        actual = run.files.get(str(url))
        if actual is None or not isinstance(recorded, int) or recorded == actual:
            continue
        yield Anomaly(
            rule="size_matches_file",
            message="recorded size differs from the downloaded file",
            observed=recorded,
            expected=actual,
            target=str(url),
            sources={"observed": "pages.jsonl:size_bytes", "expected": "downloaded file"},
        )
    return


def rule_text_ratio_is_a_percentage(run: RunArtifacts) -> Iterator[Anomaly]:
    """Text cannot be more than all of the bytes it came from (#99).

    A ratio above 100 means the numerator and the denominator were measured differently —
    which is exactly what a decoded-text length over a wire-byte size produces.
    """
    for page in run.pages:
        ratio = page.get("text_ratio")
        if not isinstance(ratio, (int, float)) or ratio <= 100:
            continue
        yield Anomaly(
            rule="text_ratio_is_a_percentage",
            message="text ratio exceeds 100%: the text and the size were not measured alike",
            observed=ratio,
            expected="<= 100",
            target=str(page.get("url")),
            sources={"observed": "pages.jsonl:text_ratio"},
        )
    return


def rule_a_200_has_bytes(run: RunArtifacts) -> Iterator[Anomaly]:
    """A page that answered 2xx with a zero-byte body, or words counted out of no bytes."""
    for page in run.pages:
        status = page.get("status_code")
        size = page.get("size_bytes")
        words = page.get("word_count") or 0
        if not isinstance(status, int) or not (200 <= status < 300):
            continue
        if size == 0 and words:
            yield Anomaly(
                rule="words_without_bytes",
                message="words were counted on a page recorded as zero bytes",
                observed=words,
                expected=0,
                target=str(page.get("url")),
                sources={
                    "observed": "pages.jsonl:word_count",
                    "expected": "pages.jsonl:size_bytes",
                },
            )
    return


def _crawled_urls(run: RunArtifacts) -> set[str]:
    return run.pages if run.native else {str(p.get("url")) for p in run.pages if p.get("url")}


def _issues(run: RunArtifacts) -> Iterable[dict[str, Any]]:
    return (run.audit or {}).get("issues") or ()


def _site_lastmod_target(run: RunArtifacts, issue: dict[str, Any], target: str) -> bool:
    """The producer's site-wide lastmod finding may name an uncrawled origin."""
    if issue.get("check") != "SITEMAP_STALE_LASTMOD" or issue.get("source") != "sitemap":
        return False
    measured = ((run.audit or {}).get("summary", {}).get("sitemap") or {}).get("lastmod")
    if not isinstance(measured, dict) or not measured or issue.get("details") != measured:
        return False
    try:
        parts = urlsplit(target)
        source = urlsplit(str(((run.audit or {}).get("run") or {}).get("source") or ""))
    except ValueError:
        return False
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        return False
    origin = (parts.scheme.lower(), parts.netloc.lower())
    if source.scheme in {"http", "https"} and source.netloc:
        return (source.scheme.lower(), source.netloc.lower()) == origin
    # SF export audits may name a directory as their input source. A retained
    # page must still establish that this origin belongs to the audited site.
    for page in run.pages:
        try:
            value = urlsplit(str(page.get("url") or ""))
        except ValueError:
            continue
        if (value.scheme.lower(), value.netloc.lower()) == origin:
            return True
    return False


def rule_findings_are_about_crawled_urls(run: RunArtifacts) -> Iterator[Anomaly]:
    """A finding about a URL the run never fetched is evidence of nothing (#94).

    Only applied to checks whose target is a page of this crawl. A check that deliberately
    reports a URL the crawl did not fetch — a sitemap orphan, a broken destination — names
    that in its own definition, so those are exempt by id rather than by guesswork.
    """
    if not run.pages or not run.audit:
        return
    crawled = _crawled_urls(run)
    exempt = {
        "SITEMAP_ORPHAN",  # declared in the sitemap, by definition possibly not crawled
        "BROKEN_LINK",
        "HREFLANG_BROKEN_TARGET",
        "UNLINKED_CANONICAL",
        # Its target names an in-host edge destination, not a fetched page (#285): the
        # finding exists precisely because some inlinks to it carried nofollow, so the
        # crawl may have had no other reason to fetch it -- query-variant caps or an
        # out-of-scope exclusion land it outside pages.jsonl just as legitimately as a
        # dedicated exclusion would. That is a page-versus-edge distinction, not a
        # crawl gap this rule can honestly report.
        "FOLLOW_AND_NOFOLLOW_INLINKS",
    }
    seen: set[tuple[str, str]] = set()
    if run.native:
        run.pages.con.execute("DELETE FROM seen")
    for issue in _issues(run):
        check = str(issue.get("check") or "")
        target = str(issue.get("target_url") or "")
        if (
            not target
            or check in exempt
            or target in crawled
            or _site_lastmod_target(run, issue, target)
        ):
            continue
        if run.native:
            if not run.pages.new_finding(check, target):
                continue
        else:
            if (check, target) in seen:
                continue
            seen.add((check, target))
        yield Anomaly(
            rule="findings_are_about_crawled_urls",
            message=f"{check} reports a URL absent from this run's own page list",
            observed=target,
            expected="a URL in pages.jsonl",
            target=target,
            sources={"observed": "audit.json:issues", "expected": "pages.jsonl:url"},
        )
    return


def rule_a_check_cannot_exceed_its_population(run: RunArtifacts) -> Iterator[Anomaly]:
    """A per-page check firing more often than there are pages (#94).

    On the crawl that prompted this, one check fired 392 times on 124 HTML pages — three
    times more findings than there were pages to have them.
    """
    if not run.pages or not run.audit:
        return
    html_pages = sum(1 for p in run.pages if "html" in str(p.get("content_type") or "").lower())
    if not html_pages:
        return
    by_check = (run.audit.get("summary") or {}).get("by_check") or {}
    for check, count in by_check.items():
        if not isinstance(count, int) or count <= html_pages:
            continue
        yield Anomaly(
            rule="check_within_its_population",
            message=f"{check} fired more often than there are HTML pages to fire on",
            observed=count,
            expected=html_pages,
            target=str(check),
            sources={
                "observed": "audit.json:summary.by_check",
                "expected": "pages.jsonl: pages with an HTML content type",
            },
        )
    return


def rule_summary_matches_the_issue_rows(run: RunArtifacts) -> Iterator[Anomaly]:
    """``summary.by_check`` is a count of the rows below it, or it is wrong."""
    if not run.audit:
        return
    by_check = (run.audit.get("summary") or {}).get("by_check") or {}
    if not by_check:
        return
    counted: dict[str, int] = {}
    for issue in _issues(run):
        check = str(issue.get("check") or "")
        counted[check] = counted.get(check, 0) + 1
    for check, claimed in sorted(by_check.items()):
        actual = counted.get(check, 0)
        if not isinstance(claimed, int) or claimed == actual:
            continue
        yield Anomaly(
            rule="summary_matches_detail",
            message=f"summary claims {claimed} {check} findings, the issue list holds {actual}",
            observed=claimed,
            expected=actual,
            target=str(check),
            sources={
                "observed": "audit.json:summary.by_check",
                "expected": "audit.json:issues",
            },
        )
    return


def rule_canonical_to_redirect_has_no_answering_twin(run: RunArtifacts) -> Iterator[Anomaly]:
    """CANONICAL_TO_REDIRECT while a crawled URL differing only by a trailing slash
    answered 2xx (#95)."""
    if not run.pages or not run.audit:
        return
    answering = (
        None
        if run.native
        else {
            str(p.get("url")).rstrip("/")
            for p in run.pages
            if isinstance(p.get("status_code"), int) and 200 <= p["status_code"] < 300
        }
    )
    for issue in _issues(run):
        if str(issue.get("check")) != "CANONICAL_TO_REDIRECT":
            continue
        canonical = str((issue.get("details") or {}).get("canonical") or "")
        if not canonical or not (
            run.pages.answers(canonical.rstrip("/"))
            if run.native
            else canonical.rstrip("/") in answering
        ):
            continue
        yield Anomaly(
            rule="canonical_to_redirect_has_no_answering_twin",
            message="canonical reported as a redirect, but that URL answered 2xx in this run",
            observed="3xx",
            expected="2xx",
            target=canonical,
            sources={
                "observed": "audit.json:issues[CANONICAL_TO_REDIRECT]",
                "expected": "pages.jsonl:status_code",
            },
        )
    return


def rule_representation_is_recorded(run: RunArtifacts) -> Iterator[Anomaly]:
    """Numbers measured two different ways must say which they are.

    A crawl that rendered some pages and not others holds two populations in one table; a
    page with no representation recorded is a number nobody can compare to anything.
    """
    if not run.pages:
        return
    labelled = sum(1 for p in run.pages if p.get("representation"))
    if not labelled or labelled == len(run.pages):
        return
    yield Anomaly(
        rule="representation_is_recorded",
        message="some pages record how they were measured and some do not",
        observed=len(run.pages) - labelled,
        expected=0,
        sources={"observed": "pages.jsonl:representation"},
    )


def rule_a_check_does_not_describe_most_of_the_site(run: RunArtifacts) -> Iterator[Anomaly]:
    """A check covering more than half the crawl, named for a reviewer (#98).

    Sibling of ``rule_a_check_cannot_exceed_its_population`` above, and the weaker
    of the two on purpose: firing more often than there are pages is arithmetically
    impossible and always a defect, while covering most of the pages is merely
    suspicious. Three defects found on live sites (#94, #95, #96) all looked like
    this and all passed their own unit tests, so the report itself has to say it.

    Read from the audit rather than recomputed: ``summary.implausible_checks`` is
    the same measure the report prints, and a scanner that computed its own would
    eventually disagree with the document it is scanning.
    """
    if not run.audit:
        return
    flagged = (run.audit.get("summary") or {}).get("implausible_checks") or []
    for row in flagged:
        if not isinstance(row, dict):
            continue
        yield Anomaly(
            rule="check_describes_most_of_the_site",
            message=(
                f"{row.get('check')} describes "
                f"{float(row.get('share') or 0):.0%} of the crawled pages -- true of some "
                "sites, and what a broken check looks like on the rest"
            ),
            observed=row.get("pages"),
            expected=f"under {IMPLAUSIBLE_SHARE:.0%} of pages, for a check about the unusual",
            target=str(row.get("check")),
            sources={"observed": "audit.json:summary.implausible_checks"},
        )
    return


def rule_outside_host_exclusion_matches_its_own_host(run: RunArtifacts) -> Iterator[Anomaly]:
    """A URL rejected as off-host whose hostname is the crawl's own host (issue #134).

    ``audit.json`` only ever sees the *count* of ``outside_host`` exclusions
    (``run.excluded``); the URL and the host the crawl compared it against
    exist only in ``decisions.jsonl``, so this contradiction is invisible to
    every rule above that reads only ``audit`` and ``pages``. A mismatch here
    is a scope bug, never a judgement call — the hostname literally recorded
    alongside the decision is the one it was supposedly rejected against.
    """
    seen: set[str] = set()
    for entry in run.decisions:
        if entry.get("type") != "exclude":
            continue
        reason = entry.get("reason")
        if reason not in ("outside_host", "redirect_off_host"):
            continue
        url = str(entry.get("url") or "")
        crawl_host = str(entry.get("host") or "").lower()
        if not url or not crawl_host or url in seen:
            continue
        if (urlsplit(url).hostname or "").lower() != crawl_host:
            continue
        seen.add(url)
        yield Anomaly(
            rule="outside_host_exclusion_matches_its_own_host",
            message=f"excluded as {reason}, but its host is the crawl's own host",
            observed=urlsplit(url).hostname,
            expected=f"a host other than {crawl_host!r}",
            target=url,
            sources={"observed": "decisions.jsonl:url", "expected": "decisions.jsonl:host"},
        )
    return


# Separate from RULES on purpose. An Anomaly is a pair of facts that cannot both
# be true, and log-scan exits 2 for one. A check describing most of the site is
# not that: on a site with no meta descriptions anywhere it is simply correct, so
# treating it as a contradiction would fail every run on a uniform site and the
# exit code would stop meaning anything. These are reported beside the anomalies,
# under their own key, and never change the exit code (issue #98).
REVIEW_RULES = (rule_a_check_does_not_describe_most_of_the_site,)

RULES = (
    rule_recorded_size_matches_the_file,
    rule_text_ratio_is_a_percentage,
    rule_a_200_has_bytes,
    rule_findings_are_about_crawled_urls,
    rule_a_check_cannot_exceed_its_population,
    rule_summary_matches_the_issue_rows,
    rule_canonical_to_redirect_has_no_answering_twin,
    rule_outside_host_exclusion_matches_its_own_host,
    rule_representation_is_recorded,
)


def scan(run: RunArtifacts, max_per_rule: int = 20) -> dict[str, Any]:
    """Apply every rule and return the anomalies, with what was read and what was not.

    ``max_per_rule`` caps how many examples of one contradiction are listed — a broken chain
    produces the same anomaly on every page, and the hundredth copy adds nothing — while the
    count reports how many there were in total.
    """
    if type(max_per_rule) is not int or max_per_rule < 0:
        raise ValueError("max_per_rule must be a nonnegative integer")

    def public(item):
        value = item.as_dict()
        if run.native:
            value["sources"] = {
                key: source.replace("audit.json", run.audit_path).replace(
                    "pages.jsonl", run.pages_path
                )
                for key, source in value["sources"].items()
            }
        return value

    anomalies: list[dict[str, Any]] = []
    per_rule: dict[str, int] = {}
    for rule in RULES:
        for index, found in enumerate(rule(run)):
            per_rule[found.rule] = per_rule.get(found.rule, 0) + 1
            if index < max_per_rule:
                anomalies.append(public(found))
    review: list[dict[str, Any]] = []
    for rule in REVIEW_RULES:
        review.extend(public(item) for item in islice(rule(run), max_per_rule))
    return {
        "ok": True,
        "anomalies": anomalies,
        "anomaly_count": sum(per_rule.values()),
        # Not anomalies and deliberately not counted as such: things a person
        # should confirm before trusting the report, which do not make the run
        # self-contradictory and do not affect the exit code.
        "review": review,
        "by_rule": {k: v for k, v in per_rule.items() if v},
        "read": {
            "audit": bool(run.audit),
            "pages": len(run.pages),
            "downloaded_files": len(run.files),
            "decisions": len(run.decisions),
        },
        # Named so a clean result cannot be mistaken for a complete one: a rule that had no
        # artifact to read is silent for the same reason a rule that found nothing is.
        "rules_run": [r.__name__.removeprefix("rule_") for r in RULES],
        "limitations": (
            [
                "Native scan has no decisions.jsonl exclusion trace; the outside-host decision rule was not measured."
            ]
            if run.native
            else []
        ),
    }
