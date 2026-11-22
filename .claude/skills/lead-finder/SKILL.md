---
name: lead-finder
description: Find leads to sell one service (AI photos/video, SEO, or web design) to LOCAL businesses in one niche and one city. Pulls Google Maps listings, checks ONE gap signal per service that doubles as the opening line, finds the owner name and email from the website domain, and outputs a CSV with a one-line personal hook. Use this whenever the user says find leads, find clients, prospect, lead list, "dentists in Lyon", "restaurants in Austin who need photos/SEO/a new site", or wants local businesses to pitch a service to, even if he does not say "lead finder". Narrow on purpose - it finds and qualifies leads only. It does not write outreach emails, build sequences, or send anything.
---

# lead-finder

One service + one niche + one city in, a CSV of qualified local businesses out. Each row has the gap that makes the pitch personal, and a one-line hook built from that gap.

Why it is narrow: a lead list is only useful if every row already contains a reason to write. A raw contact list without a gap is the thing Apollo and Clay already sell. Do not grow this into email writing, sequences or sending.

## Input

Ask for nothing if the request already has all three. Otherwise infer sensible defaults and say what you assumed.
- service: `web` (web design), `seo`, or `photos` (AI photos/video)
- niche: e.g. dentists, restaurants, salons
- city: e.g. "Lyon, France", "Austin, TX" (include state or country so Maps does not guess)

## The one gap signal per service

| Service | Gap (also the opening line) | How it is checked |
|---|---|---|
| web | No website (or only a Facebook/Yelp page), website broken, mobile load over 4s (strong), 2.5 to 4s or no https (soft) | Mobile page audit with timings, plus a free plain fetch to confirm "broken" |
| seo | Not in the top 3 of the map pack for "niche in city", and far fewer reviews than the top 3 | Position in the Maps search itself, reviews vs top-3 average |
| photos | No photos on the Google listing, or Instagram on the website last posted over 90 days ago (strong); thin photo group and no video (soft) | Place lookup, Instagram handle scraped from the homepage, latest post date |

Strong gaps are the ones to pitch first. Soft gaps are still real but weaker; keep them in the file, flagged, so the user can filter.

## Route A: with treg (everything automatic)

Needs a treg.to account and `$TREG_API_TOKEN` exported in your shell (get a token at https://treg.to). Run the helper:

```bash
export TREG_API_TOKEN=your_token
python3 ~/.claude/skills/lead-finder/scripts/lead_finder.py \
  --service web --niche dentists --city "Austin, TX" --max 40 --budget 3.00 \
  --out /path/leads.csv --cost-log /path/costs.csv
```

What it does and what each step costs (real numbers from the 2026-10-08 test):
1. Find: `litescrape.google.serp.maps`, 20 listings per call, $0.00015 per call.
2. Qualify: web uses `dataforseo.web.page.audit` mobile render, about $0.0051 per site. Photos uses the Maps place lookup ($0.00015) and `treg.instagram.user.posts` (about $0.002). SEO costs nothing extra, the rank is already in step 1.
3. Contact (qualified leads with a real website only): `tomba.companies.emails.list` about $0.0089 per hit, free on a miss, then `treg.people.email.verify` capped at $0.01 (usually $0 via contactout). Owner is picked by title (owner, founder, dentist, chef, etc.); otherwise any named person; otherwise the generic address, flagged `generic`.
4. Output: `leads.csv` (qualified only), `leads-all.csv` (everything found, with the gap column empty for unqualified), the cost log, and a funnel JSON on stdout.

The script enforces `--budget` and logs `X-Treg-Cost-Micro` for every call. A 503 `treg_saturated` is retried (unbilled). Check `treg balance` first if the run is large. 40 listings costs roughly $0.30 to $0.35 in total.

CSV columns: business, address, phone, website, rating, reviews, map_rank, gap_level, gap, hook, owner_name, owner_title, email, email_kind (owner / person / generic), email_verified.

After the run, read the CSV before handing it over. Drop anything that looks wrong (a chain, a closed business, a site that actually works). Tell the user the funnel numbers and the spend. Never send or contact anyone.

## Route B: without treg (manual and free)

Same flow, slower:
1. Find: open Google Maps in the browser, search "niche in city", scroll the list. Copy name, address, website, rating and review count, and note the order (that order is the SEO rank).
2. Qualify with the single gap:
   - web: open the site on a phone or Chrome DevTools mobile mode; run it through pagespeed.web.dev (free) and note the LCP; no site or only Facebook is the gap.
   - seo: rank beyond 3 and review count well below the top 3.
   - photos: open the listing's Photos tab, count and look for a Videos tab; check the Instagram link on their site for the last post date.
3. Contact: read the website's contact, about or team page for the owner name; an address on the site's domain is best. If none, try `first@domain` patterns and check with a free verifier (e.g. the free tier of Hunter or MillionVerifier) before trusting.
4. Write the CSV with the same columns and a one-line hook that states the gap plainly.

## Writing the hook

One sentence, built from the gap, factual and specific, no flattery and no pitch. Example: "I noticed that ATX Family Dental's site takes about 5 seconds to show its main content on a phone." The script generates these from templates; improve any that read awkwardly before delivery. No em dashes.

## Known limits (say them when relevant)

- Map rank comes from an un-geolocated search for the city; a rank is a good signal, not an exact one.
- Mobile timing is measured from a data-center crawler, so treat it as indicative, not lab-grade.
- Owner emails come from a public-web index. Roughly a third to half of small businesses return nothing or only `info@`. Say so rather than inventing an address.
- Photo counts are not exposed by the Maps endpoint, so the photos signal leans on Instagram staleness and "no photos"; the soft photo flag is a weak heuristic.
