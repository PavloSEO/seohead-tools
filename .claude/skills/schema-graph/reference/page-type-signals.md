# How the Classifier Determines Page Type

`seohead/checks/page_type.py` collects weighted signals and selects the type with the
highest score. This is not a neural network; it is a transparent feature table, and every
score is visible in `signals[]`.

## Signal strength (descending by weight)

| Weight | Signal | Points toward |
|---|---|---|
| **5.0** | A content-type `@type` is already present in JSON-LD | almost conclusive → `high` |
| 3.0 | `og:type=article\|product\|...` | the OG type |
| 2.0 | The path matches a pattern (`/blog/`, `/product/`, `/uslugi/`) | Latin and Cyrillic patterns |
| 2.0 | A price appears on the page (microdata **or** regex heuristic) | Product |
| 2.0 | Localized service terms or English `service` in the H1 | Service |
| 2.0 | An article date plus long text (>500 words) | Article |
| 1.0 | `aggregateRating` (used for both products and businesses) | Product **and** LocalBusiness equally |
| 1.0 | An article date without long text | Article (weak) |

## Confidence thresholds

- **high**—the top score is ≥5 (usually an already marked-up type or a strong combination).
- **mid**—the top score is ≥3 and has a margin over the competing types.
- **low**—the signal is weak or the race is close. The classifier returns
  `alternatives[]` and a `note`: "uncertain between X and Y—check the context." **Do not
  present this type as a fact.**

## Path patterns (Latin and Cyrillic)

| Type | Paths |
|---|---|
| Article | `/blog/`, `/article`, `/news/`, `/post/`, `/statya/`, `/novost...` |
| Product | `/product`, `/item/`, `/shop/`, `/catalog/`, `/tovar...`, `/p/<digit>`, `/goods` |
| Service | `/service`, `/uslug...`, `/reshen...`, `/solutions` |
| LocalBusiness | `/contact`, `/kontakt`, `/about`, `/o-kompanii`, `/address` |
| Event | `/event`, `/meropriyat...`, `/afisha` |
| Recipe | `/recipe`, `/recept` |
| FAQPage | `/faq`, `/vopros` |
| Course | `/course`, `/kurs` |
| JobPosting | `/job`, `/vacanc...`, `/career`, `/rabota` |

## Content types versus supporting types

Content types (classified): Article/NewsArticle/BlogPosting, Product/Offer, Service,
LocalBusiness/Store/Restaurant, Event, Recipe, FAQPage, HowTo, VideoObject, Course,
JobPosting, Review, and Question.

Supporting types (`WebPage`, `Organization`, `BreadcrumbList`, and `WebSite`) do **not**
identify the content; they appear almost everywhere. The classifier ignores them as
page-type signals.

## When to trust the result and when to verify it

- `high` → the graph can be built automatically.
- `mid` → build the graph, but show the user `signals` and `alternatives`.
- `low` → **always** show the `note` and recommend `--type`. The design principle is
  that the skill must say "uncertain" honestly instead of guessing. Product versus
  Service is the hardest distinction: a price, localized service wording in the H1,
  and a `/services/` path can produce a close race.

## Labeled heuristics

Price and rating values from **microdata** (`itemprop=price`, `ratingValue`) are facts,
with `heuristic=False`. The same values extracted from **text** with a regex—localized
Localized text fixtures equivalent to `1290 RUB` and `4.5 out of 5` are heuristics, with
`heuristic=True` and `source=text`. A heuristic may be recommended, but it must be
labeled as uncertain.
