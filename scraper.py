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

# --- Config -----------------------------------------------------------------

QUERY = "riftbound signature psa 10"
SEARCH_URL = (
    "https://www.ebay.com/sch/i.html"
    "?_nkw={query}"
    "&LH_Sold=1&LH_Complete=1&_sop=13&_ipg=240"
)

CSV_PATH = os.path.join(os.path.dirname(__file__), "sales_history.csv")
FIELDNAMES = ["item_id", "card_name", "title", "price_usd", "sold_date", "scraped_at", "url"]

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


def extract_card_name(title):
    m = re.search(r"([A-Z][A-Za-z'.\- ]+?)\s*#?(\d{2,4})", title)
    if m:
        name = m.group(1).strip()
        num = m.group(2)
        name = re.sub(r"\b(riftbound|signature|foil|holo|tcg|card|the)\b", "", name, flags=re.I).strip()
        if name:
            return f"{name} #{num}"
    pre = re.split(r"\bPSA\b", title, flags=re.I)[0]
    pre = re.sub(r"\b(riftbound|signature|foil|holo|tcg|card)\b", "", pre, flags=re.I).strip()
    return pre[:40] if pre else "unknown"


def title_is_relevant(title):
    low = title.lower()
    if "psa 10" not in low:
        return False
    if "signature" not in low:
        return False
    return not any(term in low for term in EXCLUDE_TERMS)


# --- Scrape -----------------------------------------------------------------

def fetch_page(query):
    target = SEARCH_URL.format(query=requests.utils.quote(query))

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
            "render": "true",
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


if __name__ == "__main__":
    main()
