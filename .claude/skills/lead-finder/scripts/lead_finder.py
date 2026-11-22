#!/usr/bin/env python3
"""lead-finder: Google Maps listings -> one gap signal -> owner email -> CSV.

Runs the whole flow through treg.to (stdlib only). Every call's cost is read from the
X-Treg-Cost-Micro response header, logged, and checked against a hard budget.

Usage:
  export TREG_API_TOKEN=your_token   # from https://treg.to
  python3 lead_finder.py --service web --niche dentists --city "Austin, TX" --max 40 \
      --budget 3.00 --out leads.csv --cost-log costs.csv

--service  web | seo | photos
--no-contacts   stop after qualification (no email lookup)
"""
import argparse, csv, json, os, re, statistics, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://treg.to/call/"
TOKEN = os.environ.get("TREG_API_TOKEN", "")

# Matched against the HOSTNAME only (suffix match). Substring matching once flagged "dentisttx.com" as "x.com".
SOCIAL = ("facebook.com", "instagram.com", "yelp.com", "linktr.ee", "tiktok.com", "youtube.com", "google.com",
          "business.site", "wixsite.com", "square.site", "x.com", "twitter.com", "booksy.com", "vagaro.com",
          "tripadvisor.com", "ubereats.com", "doordash.com", "opentable.com", "linkedin.com")
OWNER_WORDS = re.compile(r"owner|founder|president|ceo|principal|partner|proprietor|director|"
                         r"managing|general manager|chef|dds|dmd|dentist|doctor|broker", re.I)

spent_micro = 0
cost_rows = []
BUDGET_MICRO = 3_000_000


def log_cost(step, endpoint, micro, note=""):
    global spent_micro
    spent_micro += micro
    cost_rows.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step,
                      "endpoint": endpoint, "cost_micro": micro, "cost_usd": round(micro / 1e6, 6), "note": note})


def treg(step, endpoint, params=None, body=None, headers=None, tries=3):
    """One treg call. Returns parsed JSON or None. Logs cost. Retries 503 (unbilled)."""
    if spent_micro >= BUDGET_MICRO:
        raise SystemExit(f"budget reached (${spent_micro/1e6:.4f}); stopping")
    url = BASE + endpoint + ("?" + urllib.parse.urlencode(params) if params else "")
    h = {"X-Treg-Token": TOKEN, "User-Agent": "lead-finder/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["content-type"] = "application/json"
    h.update(headers or {})
    for i in range(tries):
        req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                micro = int(r.headers.get("X-Treg-Cost-Micro", "0") or 0)
                log_cost(step, endpoint, micro)
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            micro = int(e.headers.get("X-Treg-Cost-Micro", "0") or 0)
            if micro:
                log_cost(step, endpoint, micro, f"http {e.code}")
            if e.code == 503 and i < tries - 1:
                time.sleep(2 * (i + 1)); continue
            cost_rows.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step,
                              "endpoint": endpoint, "cost_micro": 0, "cost_usd": 0, "note": f"failed http {e.code}"})
            return None
        except Exception as e:  # network
            if i < tries - 1:
                time.sleep(2); continue
            cost_rows.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step,
                              "endpoint": endpoint, "cost_micro": 0, "cost_usd": 0, "note": f"failed {e}"})
            return None


def domain_of(url):
    if not url:
        return ""
    host = urllib.parse.urlparse(url if "//" in url else "//" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_real_site(url):
    if not url:
        return False
    host = domain_of(url)
    return not any(host == s or host.endswith("." + s) for s in SOCIAL)


# ---------- step 1: find ----------
def find(niche, city, want):
    out, start = [], 0
    while len(out) < want:
        p = {"q": f"{niche} in {city}", "type": "search", "start": start}
        d = treg("find", "litescrape.google.serp.maps", p)
        if not d or not d.get("local_results"):
            break
        for r in d["local_results"]:
            r["_rank"] = len(out) + 1
            out.append(r)
        if not (d.get("pagination") or {}).get("next"):
            break
        start += 20
    return out[:want]


# ---------- step 2: qualify ----------
def audit_site(url):
    d = treg("qualify-web", "dataforseo.web.page.audit",
             body=[{"url": url, "enable_browser_rendering": True, "browser_preset": "mobile"}])
    try:
        it = d["tasks"][0]["result"][0]["items"][0]
        return {"status": it.get("status_code"), "lcp": (it.get("page_timing") or {}).get("largest_contentful_paint"),
                "https": (it.get("checks") or {}).get("is_https"), "broken": (it.get("checks") or {}).get("is_broken")}
    except Exception:
        return {"status": None, "lcp": None, "https": None, "broken": True}


def plain_status(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def qualify_web(b):
    site = b.get("website")
    if not is_real_site(site):
        why = "only a social/listing page" if site else "no website"
        return "strong", f"{why} on its Google listing", f"{b['title']} has {why.replace('only a','just a')} linked from Google"
    a = audit_site(site)
    b["_audit"] = a
    if a["broken"] or a["status"] is None or (a["status"] and a["status"] >= 400):
        # The crawler can be blocked (403) or time out on a healthy site, so confirm with our own free fetch.
        code = plain_status(site)
        if code is None or code in (404, 410) or code >= 500:
            return "strong", f"website fails to load (status {code or 'no response'})", \
                f"the website linked from {b['title']}'s Google listing doesn't load properly"
        if a["lcp"] is None:
            return None, "", ""  # loads fine for a person, no timing data: not a gap we can prove
    if a["lcp"] and a["lcp"] > 4000:
        return "strong", f"mobile load is slow (largest content paint {a['lcp']/1000:.1f}s, over Google's 4s poor line)", \
            f"{b['title']}'s site takes about {a['lcp']/1000:.0f} seconds to show its main content on a phone"
    if a["https"] is False:
        return "strong", "site not served over https (browsers warn visitors)", \
            f"{b['title']}'s site still loads without https, so browsers show a 'not secure' warning"
    if a["lcp"] and a["lcp"] > 2500:
        return "soft", f"mobile load is borderline (largest content paint {a['lcp']/1000:.1f}s, Google wants under 2.5s)", \
            f"{b['title']}'s site takes {a['lcp']/1000:.1f} seconds to show its main content on a phone, over Google's 2.5 second target"
    return None, "", ""


def qualify_seo(b, top3_avg):
    rank, rev = b["_rank"], b.get("reviews") or 0
    if rank > 3:
        gap = f"ranks #{rank} for '{b['_q']}', outside the top 3 map pack; {rev} reviews vs {top3_avg:.0f} average for the top 3"
        return ("strong" if rev < top3_avg * 0.5 else "soft"), gap, \
            f"{b['title']} shows up at #{rank} on Google Maps for '{b['_q']}', behind three competitors with about {top3_avg:.0f} reviews each"
    return None, "", ""


def fetch_html(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(400000).decode("utf-8", "ignore")
    except Exception:
        return ""


def qualify_photos(b):
    d = treg("qualify-photos", "litescrape.google.serp.maps", {"place_id": b["place_id"]})
    pr = (d or {}).get("place_results") or {}
    tabs = [i.get("title", "").lower() for i in pr.get("images", [])]
    has_video = any("video" in t for t in tabs)
    if not pr.get("thumbnail"):
        return "strong", "no photos on the Google listing", f"{b['title']}'s Google listing has no photos at all"
    ig = None
    if is_real_site(b.get("website")):
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]{2,30})", fetch_html(b["website"]))
        if m and m.group(1).lower() not in ("p", "reel", "explore", "accounts"):
            ig = m.group(1)
    if ig:
        d = treg("qualify-photos-ig", "treg.instagram.user.posts", body={"username": ig},
                 headers={"X-Treg-Route-Max-Cost": "0.01"})
        posts = ((d or {}).get("output") or {}).get("posts") or []
        ts = [p.get("taken_at") for p in posts if p.get("taken_at")]
        if ts:
            days = (time.time() - max(ts)) / 86400
            if days > 90:
                return "strong", f"Instagram @{ig} last posted {int(days)} days ago", \
                    f"{b['title']}'s Instagram has been quiet for about {int(days/30)} months"
    if len(tabs) <= 1 and not has_video:
        return "soft", "Google listing shows only one photo group and no video", \
            f"{b['title']}'s Google listing has a thin photo section and no video"
    return None, "", ""


# ---------- step 3: contact ----------
def pick_owner(emails):
    people = [e for e in emails if e.get("type") == "personal" and (e.get("full_name") or "").strip()]
    owners = [e for e in people if OWNER_WORDS.search(e.get("position") or "")]
    ranked = sorted(owners or people, key=lambda e: -(e.get("score") or 0))
    if ranked:
        return ranked[0], "owner" if owners else "person"
    gen = sorted([e for e in emails if e.get("type") == "generic"], key=lambda e: -(e.get("score") or 0))
    return (gen[0], "generic") if gen else (None, "")


def contact(b):
    dom = domain_of(b.get("website"))
    d = treg("contact", "tomba.companies.emails.list", {"domain": dom, "limit": 10})
    emails = (((d or {}).get("data") or {}).get("emails")) or []
    e, kind = pick_owner(emails)
    if not e:
        return {"owner_name": "", "owner_title": "", "email": "", "email_kind": "", "verified": ""}
    v = treg("verify", "treg.people.email.verify", body={"email": e["email"]}, headers={"X-Treg-Route-Max-Cost": "0.01"})
    st = ((v or {}).get("output") or {}).get("status", "unknown")
    return {"owner_name": (e.get("full_name") or "").strip(), "owner_title": e.get("position") or "",
            "email": e["email"], "email_kind": kind, "verified": "yes" if st == "valid" else f"no ({st})"}


# ---------- main ----------
def main():
    global BUDGET_MICRO
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", required=True, choices=["web", "seo", "photos"])
    ap.add_argument("--niche", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--out", default="leads.csv")
    ap.add_argument("--cost-log", default="costs.csv")
    ap.add_argument("--no-contacts", action="store_true")
    a = ap.parse_args()
    BUDGET_MICRO = int(a.budget * 1e6)
    if not TOKEN:
        sys.exit("TREG_API_TOKEN missing. Get a token at https://treg.to and export it.")

    biz = find(a.niche, a.city, a.max)
    q = f"{a.niche} in {a.city}"
    for b in biz:
        b["_q"] = q
    print(f"found {len(biz)} listings (${spent_micro/1e6:.4f})", file=sys.stderr)
    top3 = [x.get("reviews") or 0 for x in biz[:3]]
    top3_avg = statistics.mean(top3) if top3 else 0

    rows, stats = [], {"found": len(biz), "with_website": 0, "qualified": 0, "strong": 0, "with_email": 0,
                       "with_owner_name": 0, "verified_email": 0, "verified_owner": 0}
    for b in biz:
        if is_real_site(b.get("website")):
            stats["with_website"] += 1
        if a.service == "web":
            lvl, gap, hook = qualify_web(b)
        elif a.service == "seo":
            lvl, gap, hook = qualify_seo(b, top3_avg)
        else:
            lvl, gap, hook = qualify_photos(b)
        row = {"business": b["title"], "address": b.get("address", ""), "phone": b.get("phone", ""),
               "website": b.get("website", ""), "rating": b.get("rating", ""), "reviews": b.get("reviews", ""),
               "map_rank": b["_rank"], "gap_level": lvl or "", "gap": gap, "hook": "",
               "owner_name": "", "owner_title": "", "email": "", "email_kind": "", "email_verified": ""}
        if lvl:
            stats["qualified"] += 1
            stats["strong"] += lvl == "strong"
            row["hook"] = "I noticed that " + hook
            if not a.no_contacts and is_real_site(b.get("website")):
                c = contact(b)
                row.update({k: c[k] for k in ("owner_name", "owner_title", "email", "email_kind")})
                row["email_verified"] = c["verified"]
                if c["email"]:
                    stats["with_email"] += 1
                    stats["with_owner_name"] += bool(c["owner_name"])
                    stats["verified_email"] += c["verified"] == "yes"
                    stats["verified_owner"] += c["verified"] == "yes" and bool(c["owner_name"])
        rows.append(row)
        print(f"  {b['_rank']:>2} {b['title'][:34]:34} {lvl or '-':7} {gap[:60]}", file=sys.stderr)

    qual = [r for r in rows if r["gap_level"]]
    cols = list(rows[0].keys()) if rows else []
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(qual)
    with open(a.out.replace(".csv", "-all.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)
    with open(a.cost_log, "w", newline="") as f:
        w = csv.DictWriter(f, ["ts", "step", "endpoint", "cost_micro", "cost_usd", "note"])
        w.writeheader(); w.writerows(cost_rows)
    stats["total_spend_usd"] = round(spent_micro / 1e6, 4)
    by_step = {}
    for c in cost_rows:
        by_step[c["step"]] = by_step.get(c["step"], 0) + c["cost_micro"]
    stats["spend_by_step_usd"] = {k: round(v / 1e6, 4) for k, v in by_step.items()}
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
