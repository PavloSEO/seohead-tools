# Schema.org @graph Templates by Page Type

The generator (`seohead/checks/schema_build.py`) builds a connected graph. Principle:
**a property is added only when the page provides the corresponding fact**. The examples
below show what a complete graph looks like and what it supports for each type. The `@id`
names are fixed: `#organization`, `#website`, `#webpage`, and `#breadcrumb`.

## Skeleton (shared by all types)

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {"@type": "Organization", "@id": "#organization", "url": "<origin>", "name": "...",
     "logo": "...", "sameAs": ["..."], "telephone": "...", "address": "..."},
    {"@type": "WebSite", "@id": "#website", "url": "<origin>", "name": "...",
     "publisher": {"@id": "#organization"}},
    {"@type": "<page type>", "@id": "#webpage", "url": "<canonical>",
     "name": "...", "description": "...", "image": "...",
     "isPartOf": {"@id": "#website"}, "breadcrumb": {"@id": "#breadcrumb"}},
    {"@type": "BreadcrumbList", "@id": "#breadcrumb", "itemListElement": [...]}
  ]
}
```

Connectivity: `WebSite.publisher → Organization`, `WebPage.isPartOf → WebSite`, and
`WebPage.breadcrumb → BreadcrumbList`. This is a graph, not a collection of separate
blocks, so the validator reports `is_graph: true`.

**What is omitted when no fact is available:** the entire `Organization` node (no
name/logo → no node), `breadcrumb` (no breadcrumbs → no node and no property on the
page), and each of `logo`, `sameAs`, `telephone`, and `address` individually.

## Type-specific behavior

### Article (NewsArticle and BlogPosting are normalized to Article)
- `headline` **instead of** `name` (a Google rich-result requirement).
- `datePublished` (from `article:published_time`) and `dateModified` when available.
- `author`: a `Person` with a `url` from `rel=author`; otherwise, do not invent one.
- `publisher`: `{"@id": "#organization"}` (required for the Article rich result).
- `image` from `og:image`.

### Product
- `name`, `image`, and `description`.
- `brand`: `{"@type": "Brand", "name": <organization name>}` when an Organization is
  available.
- `offers`: `{"@type": "Offer", "price", "priceCurrency"}`—**only when a price was
  extracted**.
- `aggregateRating`: `{"@type": "AggregateRating", "ratingValue", "reviewCount|ratingCount"}`—
  only when a rating is available.

### Service
- `name` and `description`.
- `provider`: `{"@id": "#organization"}`.
- `serviceType`: from the H1 (for example, "SEO Services").
- `offers` when a price is available (the same logic as for Product).

### LocalBusiness
- `name`, `address`, and `telephone` from organization microdata.
- `aggregateRating` when available.
- Do not confuse it with Product: LocalBusiness describes a **location**, not a product.

### FAQPage
- A `{"@type": "FAQPage"}` skeleton **without** `mainEntity`. The tool does not extract
  real questions and answers; an honest skeleton is better than invented questions. The
  validator will report `missing_required: ["mainEntity"]`, which is expected.
  **Important:** Google no longer provides FAQPage rich results; the markup remains useful
  for AI content extraction (GEO/AEO).

### Other types (Recipe, Event, VideoObject, Course, JobPosting, WebPage)
- Base `name`, `description`, and `image` properties from shared fields. The tool does not
  invent type-specific required properties (`Event.startDate`,
  `Recipe.recipeIngredient`, and so on); the validator reports them honestly in
  `missing_required`.

## Honest-markup rules (do not violate them)

1. **Visible content only.** If a person cannot see it on the page, it does not belong in
   the graph. Marking up hidden content is a violation and may lead to penalties.
2. **Connectivity through `@id`.** Connect entities instead of leaving them as islands.
3. **Do not treat the rich result as the only goal.** Vocabulary-valid JSON-LD may not
   produce a rich result, while markup that produces a rich result may contain a
   deprecated term. These are two separate layers.
4. **FAQPage/HowTo**—recommend them for AI extraction, not for rich results.

## How to eliminate dangling `@id` references

The generator uses fixed `@id` values, so everything is connected. If you edit the graph
manually, ensure that every `{"@id": "..."}` used as a value has a node definition with
the same `@id`. `schema-check` detects dangling references through `entities[].errors`
("reference to @id X, which is not defined in the graph").
