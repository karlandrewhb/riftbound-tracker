#!/usr/bin/env python3
"""
Riftbound Signature PSA 10 sold-listings scraper.

Runs one fixed eBay search for completed + sold items, parses each sale,
dedupes on eBay item ID, and appends new sales to sales_history.csv.

Designed for a low-volume daily GitHub Actions run.
"""

import csv
import os
import re
import sys
import time
import random
import datetime as dt

import requests
from bs4 import BeautifulSoup

from card_resolver import resolve

# --- Config -----------------------------------------------------------------

QUERY = "riftbound signature psa 10"
SEARCH_URL = (
    "https://www.ebay.com/sch/i.html"
    "?_nkw={query}"
    "&LH_Sold=1&LH_Complete=1&_sop=13&_ipg=240"
)

# Active (unsold) listings. Uses the category filter _dcat=183454 to scope to
# trading cards, which cuts noise substantially. _sop=1 = ending soonest.
ACTIVE_QUERY = "psa 10 signature riftbound"
ACTIVE_URL = (
    "https://www.ebay.com/sch/i.html"
    "?_nkw={query}"
    "&_sacat=0&_from=R40&_oaa=1&_dcat=183454&_sop=1&_ipg=240"
)

HERE = os.path.dirname(__file__)
CSV_PATH = os.path.join(HERE, "sales_history.csv")
ACTIVE_PATH = os.path.join(HERE, "listings_active.csv")
SUPPLY_PATH = os.path.join(HERE, "listings_history.csv")

FIELDNAMES = ["item_id", "card_name", "title", "price_usd", "sold_date", "scraped_at", "url"]
ACTIVE_FIELDS = ["snapshot_date", "item_id", "card_name", "title", "price_usd",
                 "format", "bids", "first_seen", "url"]
SUPPLY_FIELDS = ["snapshot_date", "card_name", "listings", "low_ask", "bin_count",
                 "auction_count", "total_bids", "max_bids", "status"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

EXCLUDE_TERMS = [
    "lot", "bundle", "proxy", "custom", "sealed", "box", "case",
    "playset", "psa 9", "psa 8", "bgs", "cgc", "9.5", "reprint",
]

# --- Parsing helpers --------------------------------------------------------

def clean_price(raw):
    if not raw:
        return None
    m = re.search(r"[\d,]+\.\d{2}", raw)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def parse_sold_date(raw):
    if not raw:
        return None
    raw = raw.replace("Sold", "").strip()
    for fmt in ("%b %d, %Y", "%d %b %Y", "%b-%d-%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ============================================================
# Card identity: group by SET + CHARACTER.
# The character name appears in nearly every eBay title and,
# combined with the set, uniquely identifies a card. Card numbers
# are unreliable (missing, or mangled like "#3001*/298"), so they
# are NOT used for grouping.
# ============================================================

CHARACTERS = [
    "AHRI", "YASUO", "VAYNE", "IRELIA", "TEEMO", "SORAKA", "BARD", "SETT",
    "JINX", "LEONA", "VOLIBEAR", "VIKTOR", "DARIUS", "KAI'SA", "KAISA",
    "MISS FORTUNE", "LEE SIN", "LEESIN", "APHELIOS", "KARMA", "YONE",
    "MASTER YI", "DIANA", "VI", "KHA'ZIX", "KHAZIX", "RENGAR", "LEBLANC",
    "LILLIA", "VEX", "ICATHIAN RAIN",
]

CHAR_ALIASES = {"KAISA": "KAI'SA", "KHAZIX": "KHA'ZIX", "LEESIN": "LEE SIN"}

SETS = [
    ("OGN", ["OGN", "ORIGINS"]),
    ("SFD", ["SFD", "SPIRITFORGED", "SPIRIT FORGE"]),
    ("UNL", ["UNL", "UNLEASHED"]),
    ("CS",  ["S.CHINESE", "CHINESE", "CS"]),
]


def norm_character(t):
    # Longest match first so "MISS FORTUNE" beats a stray "VI".
    for c in sorted(CHARACTERS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(c) + r"\b", t):
            return CHAR_ALIASES.get(c, c)
    return ""


def norm_set(t):
    for code, aliases in SETS:
        for a in aliases:
            if re.search(r"\b" + re.escape(a), t):
                return code
    return ""


def extract_card_name(title):
    """
    Canonical card name via card_resolver (number+set, subtitle, champion, etc).

    Returns e.g. "SFD-227 Ahri Inquisitive". Falls back to the older
    set+character heuristic only when the resolver cannot identify the card,
    so ambiguous rows stay visible rather than being silently dropped.
    """
    st, num, champ, sub, method, flag = resolve(title)
    if not flag:
        return f"{st}-{num} {champ} {sub}".strip()

    # Unresolved: keep something human-readable and mark it.
    t = title.upper()
    s = norm_set(t)
    ch = norm_character(t)
    if ch:
        return f"[{flag}] {ch.title()}"
    if s:
        return f"[{flag}] {s}"
    return f"[{flag}]"


def title_is_relevant(title):
    low = title.lower()
    if "psa 10" not in low:
        return False
    if "signature" not in low:
        return False
    return not any(term in low for term in EXCLUDE_TERMS)


# --- Scrape -----------------------------------------------------------------

def fetch_page(query, url_template=SEARCH_URL, render=True):
    target = url_template.format(query=requests.utils.quote(query))

    api_key = os.environ.get("SCRAPERAPI_KEY")
    if not api_key:
        resp = requests.get(target, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        return resp.text

    # ScraperAPI with render=true: residential IP + runs eBay's JavaScript so
    # the search results actually populate.
    resp = requests.get(
        "https://api.scraperapi.com/",
        params={
            "api_key": api_key,
            "url": target,
            "country_code": "us",
            "render": "true" if render else "false",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


def _first_text(el, selectors):
    for sel in selectors:
        found = el.select_one(sel)
        if found:
            txt = found.get_text(" ", strip=True)
            if txt:
                return txt
    return ""


def parse_listings(html):
    soup = BeautifulSoup(html, "html.parser")

    items = (
        soup.select("li.s-card")
        or soup.select("ul.srp-results > li")
        or soup.select("li.s-item")
    )

    results = []
    for it in items:
        title = _first_text(it, [
            ".s-card__title", ".s-item__title", '[class*="title"]',
        ])
        if not title or title.lower().startswith("shop on ebay"):
            continue
        if not title_is_relevant(title):
            continue

        price_text = _first_text(it, [
            ".s-card__price", ".s-item__price", '[class*="price"]',
        ])

        link_el = it.select_one('a[href*="/itm/"]') or it.select_one("a[href]")
        url = link_el.get("href", "").split("?")[0] if link_el else ""
        m = re.search(r"/itm/(?:.*?/)?(\d{9,})", url)
        item_id = m.group(1) if m else (url or title)

        date_text = _first_text(it, [
            ".s-card__caption", ".s-item__caption",
            '[class*="caption"]', '[class*="sold"]',
        ])

        price = clean_price(price_text)
        if price is None:
            continue

        results.append({
            "item_id": item_id,
            "card_name": extract_card_name(title),
            "title": title,
            "price_usd": price,
            "sold_date": parse_sold_date(date_text),
            "url": url,
        })
    return results


# --- Storage ----------------------------------------------------------------

def load_existing_ids():
    if not os.path.exists(CSV_PATH):
        return set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return {row["item_id"] for row in csv.DictReader(f)}


def append_rows(rows):
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)



# --- Active listings --------------------------------------------------------

def parse_active(html):
    """Parse currently-listed (unsold) items. Same filtering as sold."""
    soup = BeautifulSoup(html, "html.parser")
    items = (
        soup.select("li.s-card")
        or soup.select("ul.srp-results > li")
        or soup.select("li.s-item")
    )

    results = []
    for it in items:
        title = _first_text(it, [
            ".s-card__title", ".s-item__title", '[class*="title"]',
        ])
        if not title or title.lower().startswith("shop on ebay"):
            continue
        if not title_is_relevant(title):
            continue

        price = clean_price(_first_text(it, [
            ".s-card__price", ".s-item__price", '[class*="price"]',
        ]))
        if price is None:
            continue

        link_el = it.select_one('a[href*="/itm/"]') or it.select_one("a[href]")
        url = link_el.get("href", "").split("?")[0] if link_el else ""
        m = re.search(r"/itm/(?:.*?/)?(\d{9,})", url)
        item_id = m.group(1) if m else (url or title)

        blob = it.get_text(" ", strip=True).lower()
        bid_m = re.search(r"(\d+)\s+bids?", blob)
        bids = int(bid_m.group(1)) if bid_m else ""
        if bid_m or "bid" in blob:
            fmt = "auction"
        elif "buy it now" in blob or "or best offer" in blob:
            fmt = "bin"
        else:
            fmt = "bin"

        results.append({
            "item_id": item_id,
            "card_name": extract_card_name(title),
            "title": title,
            "price_usd": price,
            "format": fmt,
            "bids": bids,
            "url": url,
        })
    return results


def load_first_seen():
    """item_id -> earliest snapshot_date, so we can age listings."""
    seen = {}
    if not os.path.exists(ACTIVE_PATH) or os.path.getsize(ACTIVE_PATH) == 0:
        return seen
    with open(ACTIVE_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fs = row.get("first_seen") or row.get("snapshot_date")
            iid = row.get("item_id")
            if iid and fs:
                seen[iid] = min(seen.get(iid, fs), fs)
    return seen


def write_active(rows, today):
    """Overwrite: current listings only, with first_seen carried forward."""
    prior = load_first_seen()
    with open(ACTIVE_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ACTIVE_FIELDS)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["snapshot_date"] = today
            r["first_seen"] = prior.get(r["item_id"], today)
            w.writerow({k: r.get(k, "") for k in ACTIVE_FIELDS})


def append_supply(rows, today, status):
    """Append one row per card per day: count, low ask, format split."""
    by_card = {}
    for r in rows:
        by_card.setdefault(r["card_name"], []).append(r)

    # A cleared or headerless file is treated as absent so the header gets
    # rewritten; otherwise skip if this date is already recorded.
    # Tolerate a file that was cleared by hand and left with stray blank
    # lines: strip them, and rewrite the header if the first real line is
    # not one. Otherwise skip if this date is already recorded.
    exists = False
    if os.path.exists(SUPPLY_PATH):
        with open(SUPPLY_PATH, newline="", encoding="utf-8") as f:
            raw = [ln for ln in f.read().splitlines() if ln.strip()]
        if raw and not raw[0].startswith("snapshot_date"):
            print("listings_history.csv header missing; rewriting it.")
            raw.insert(0, ",".join(SUPPLY_FIELDS))
        if raw:
            # rewrite cleanly so the stray newline cannot come back
            with open(SUPPLY_PATH, "w", newline="", encoding="utf-8") as f:
                f.write("\n".join(raw) + "\n")
            exists = True
            if any(ln.startswith(today + ",") for ln in raw[1:]):
                print(f"Supply already recorded for {today}; skipping append.")
                return

    with open(SUPPLY_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUPPLY_FIELDS)
        if not exists:
            w.writeheader()
        if not by_card:
            w.writerow({"snapshot_date": today, "card_name": "", "listings": 0,
                        "low_ask": "", "bin_count": 0, "auction_count": 0,
                        "total_bids": 0, "max_bids": 0, "status": status})
            return
        for card, rs in sorted(by_card.items()):
            # Bid counts only exist on auctions; they are the closest free
            # proxy for demand available from eBay search results.
            bids = [int(r["bids"]) for r in rs
                    if str(r.get("bids", "")).isdigit()]
            w.writerow({
                "snapshot_date": today,
                "card_name": card,
                "listings": len(rs),
                "low_ask": round(min(r["price_usd"] for r in rs), 2),
                "bin_count": sum(1 for r in rs if r["format"] == "bin"),
                "auction_count": sum(1 for r in rs if r["format"] == "auction"),
                "total_bids": sum(bids) if bids else 0,
                "max_bids": max(bids) if bids else 0,
                "status": status,
            })


def scrape_active(today):
    print(f"Scraping ACTIVE listings: {ACTIVE_QUERY}")
    time.sleep(random.uniform(2, 5))
    try:
        html = fetch_page(ACTIVE_QUERY, url_template=ACTIVE_URL)
    except Exception as e:
        print(f"ERROR: active fetch failed: {e}", file=sys.stderr)
        append_supply([], today, "fetch_failed")
        return

    rows = parse_active(html)
    print(f"Parsed {len(rows)} active listings.")
    if not rows:
        print("WARNING: 0 active listings parsed (block or layout change?).",
              file=sys.stderr)
        append_supply([], today, "zero_parsed")
        return

    write_active(rows, today)
    append_supply(rows, today, "ok")
    print(f"Wrote {len(rows)} active listings across "
          f"{len({r['card_name'] for r in rows})} cards.")


# --- Main -------------------------------------------------------------------

def main():
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Scraping: {QUERY}")

    time.sleep(random.uniform(1, 3))

    try:
        html = fetch_page(QUERY)
    except Exception as e:
        print(f"ERROR: fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    listings = parse_listings(html)
    print(f"Parsed {len(listings)} relevant sold listings.")

    if len(listings) == 0:
        print(
            "WARNING: 0 listings parsed. Either genuinely no sales, "
            "an eBay layout change, or a block. Check manually.",
            file=sys.stderr,
        )

    existing = load_existing_ids()
    new = [r for r in listings if r["item_id"] not in existing]
    for r in new:
        r["scraped_at"] = now

    if new:
        append_rows(new)
        print(f"Appended {len(new)} new sales.")
    else:
        print("No new sales since last run.")

    scrape_active(dt.date.today().isoformat())


if __name__ == "__main__":
    main()
