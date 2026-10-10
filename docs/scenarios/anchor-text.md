# Scenario 28 — Anchor text and inlink composition: who links here, and what they call it

## The question

> Every page has plenty of internal links. Why does none of it seem to help the pages we care
> about?

Counting inlinks answers the wrong question. A page with forty inlinks that are all the word
"here", all in the footer, and all `nofollow` is linked forty times and described zero times.
This chain measures the composition rather than the count.

## Covers

- **Links** — Non-Descriptive Anchor Text In Internal Outlinks · Internal Outlinks With No Anchor Text · Internal Nofollow Inlinks Only · Non-Indexable Page Inlinks Only · Internal Nofollow Outlinks · Follow & Nofollow Internal Inlinks To Page

## The chain

**1. Turn on link-position classification before crawling.**

```json
{"link_position": {"classify": true}}
```

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
```

Each link's DOM ancestry is classified as nav, header, sidebar, footer or content while the
page is being parsed — no extra requests. It is off by default because storing a position per
link costs memory on a large crawl, and on by choice whenever the question is about internal
linking rather than about pages.

That single setting produces `INLINK_BOILERPLATE_ONLY`: a page linked only from navigation,
header, sidebar or footer, and never from body copy. A page reachable only through boilerplate
is not linked the way a page in the content graph is linked, and no inlink count will tell you
that.

The same crawl also produces `INTERNAL_NOFOLLOW_OUTLINKS`, the page-level counterpart to the
inlink checks: a source page with one or more internal `nofollow` outlinks, with the count and a
bounded sample of destinations. It reads the same per-edge flag, so it needs no extra capture.

The same crawl also produces `FOLLOW_AND_NOFOLLOW_INLINKS` — a page reached one way from some
source and the other way from another — without needing `link_position.classify` at all: it
reads only each edge's destination and `nofollow`, which every crawl already records (issue
#125). A page inconsistently signalled that way is not a settled editorial choice; it is
usually two people, or two eras of a template, disagreeing about the same URL.

**2. Run the audit over the full edge list for the anchor-level findings.**

```bash
seohead sf run --exports-dir ./exports --out report --tasks
```

Three checks need the complete internal edge list — Screaming Frog's `All Inlinks` bulk export
— and each declares itself skipped by name when it is absent:

| Finding | The question it answers |
|---|---|
| `GENERIC_ANCHOR_TEXT` | is the link text "here", "read more", "click here"? |
| `ONLY_NOFOLLOW_INLINKS` | is every internal link to this page `nofollow`? |
| `ONLY_NONINDEXABLE_SOURCE_INLINKS` | does every link to it come from a page nobody may index? |

The third is the quiet one. A page linked only from `noindex` filter results is linked from a
part of the site search engines are being told to disregard, so the links exist and reach
nothing.

**3. Read the anchor findings as accessibility findings too.**

`GENERIC_ANCHOR_TEXT`'s own fix hint says it: replace it with text that describes the
destination "for both search engines and screen-reader users". A page whose links are all
"read more" is unusable in a screen reader's link list, and that argument wins internal
arguments the SEO one does not.

**4. Confirm the run before quoting a composition figure.**

```bash
seohead log-scan --run ./run
```

**5. Deliver it grouped by destination.**

```bash
seohead report-build --audit ./run/audit.json --format xlsx --out ./inlinks.xlsx
```

## What comes out

```json
{
  "check": "ONLY_NOFOLLOW_INLINKS",
  "severity": "warning",
  "target_url": "https://example.com/services/consulting",
  "message": "Every internal link to this page is nofollow",
  "fix_hint": "Add at least one ordinary, followed internal link so link equity and crawl priority reach the page."
}
```

and, from the crawl in step 1, the composition itself:

```json
{
  "check": "INLINK_BOILERPLATE_ONLY",
  "target_url": "https://example.com/services/consulting",
  "occurrences_count": 12,
  "details": {"by_position": {"footer": 11, "nav": 1}}
}
```

Twelve inlinks, zero of them editorial. That is the sentence a client understands, and it is
not derivable from a count.

And, needing neither classification nor the export:

```json
{
  "check": "FOLLOW_AND_NOFOLLOW_INLINKS",
  "severity": "notice",
  "target_url": "https://example.com/pricing"
}
```

One URL, two contradictory signals about whether it should be crawled and ranked.

## What it costs

- One crawl, with classification adding memory rather than requests.
- Steps 2, 4 and 5 read files already on disk. Nothing paid.
- The All Inlinks export is the largest artifact Screaming Frog produces; on a big site it is
  the practical limit on this chain, not the analysis.

## What it cannot answer

- **Which pages have empty anchors.** Empty anchor text is recorded per edge, but there is no
  page-level finding for the absence of anchor text — only `GENERIC_ANCHOR_TEXT` for the
  wording. Treat "no anchor text" as a stated partial rather than a clean result.
- **Why a page's `nofollow` outlinks are there.** `INTERNAL_NOFOLLOW_OUTLINKS` names every
  source page that carries an internal `nofollow` outlink and counts them. It cannot tell an
  editorial choice from `rel="sponsored"` or `rel="ugc"`: the crawl records one `nofollow`
  flag per edge, and `discovery.follow_nofollow` decides whether the crawl followed it at all.
- **Whether the anchor text is good.** "Industrial pumps" is descriptive and may still be the
  wrong description. Every check here is structural; wording is a person's judgement.
- **Anchors written by JavaScript.** The edge list is built from served HTML. See
  the [rendering scenario](rendering.md).
- **Composition on a partial crawl.** "Every inlink to this page is X" is a claim about the
  whole site; check `run.crawl_partial` before repeating it.
