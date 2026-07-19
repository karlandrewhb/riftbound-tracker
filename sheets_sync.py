#!/usr/bin/env python3
"""
Push Riftbound sold-price data to Google Sheets.

Reads sales_history.csv, resolves each eBay title to a canonical card via
card_resolver, and overwrites four tabs:

  Summary   - one row per card, price stats, sorted by recent median
  Last Sold - every sale, newest first, flagged if new this scrape
  Raw       - untouched CSV dump
  Review    - rows the resolver could not identify (needs a human)

Auth: service-account JSON in GOOGLE_SA_JSON, spreadsheet id in SHEET_ID.
"""

import csv
import json
import os
import statistics
import datetime as dt

import gspread
from google.oauth2.service_account import Credentials

from card_resolver import resolve

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "sales_history.csv")
SUPPLY_PATH = os.path.join(HERE, "listings_history.csv")
ACTIVE_PATH = os.path.join(HERE, "listings_active.csv")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

RECENT_DAYS = 30


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_date(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def to_price(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# Card types kept out of Summary / Last Sold / Review.
# They remain in the Raw tab and in sales_history.csv, so nothing is lost.
EXCLUDE_TYPES = ("signature spell",)


def is_excluded(title):
    t = title.lower()
    return any(x in t for x in EXCLUDE_TYPES)


def enrich(rows):
    """Attach resolved card identity to every row, skipping excluded types."""
    out = []
    for r in rows:
        if is_excluded(r.get("title", "")):
            continue
        st, num, champ, sub, method, flag = resolve(r.get("title", ""))
        card = f"{st}-{num} {champ} {sub}".strip() if not flag else ""
        r = dict(r)
        r["_set"] = st or ""
        r["_num"] = num or ""
        r["_champ"] = champ or ""
        r["_sub"] = sub or ""
        r["_card"] = card
        r["_method"] = method
        r["_flag"] = flag
        r["_price"] = to_price(r.get("price_usd"))
        r["_date"] = to_date(r.get("sold_date"))
        out.append(r)
    return out



def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_supply(supply_rows, active_rows, summary):
    """
    Current supply per card plus how the listing count has moved.

    Reads the appended daily history for trend, and the current snapshot
    for today's detail. Cards with sales but no listings are included with
    a zero count, since 'nothing available' is itself the signal.
    """
    if not supply_rows:
        return [], ""

    dates = sorted({r["snapshot_date"] for r in supply_rows})
    latest = dates[-1]

    def as_of(target):
        """Listing count per card on the most recent date at or before target."""
        usable = [d for d in dates if d <= target]
        if not usable:
            return {}
        d = usable[-1]
        return {r["card_name"]: int(r["listings"] or 0)
                for r in supply_rows if r["snapshot_date"] == d}

    today = dt.date.fromisoformat(latest)
    now = as_of(latest)
    wk = as_of((today - dt.timedelta(days=7)).isoformat())
    mo = as_of((today - dt.timedelta(days=30)).isoformat())

    current = {r["card_name"]: r for r in supply_rows if r["snapshot_date"] == latest}

    # median asking price of what is live right now
    asks = {}
    for r in active_rows:
        try:
            asks.setdefault(r["card_name"], []).append(float(r["price_usd"]))
        except (ValueError, TypeError):
            continue

    # cards that have sold, so we can flag ones with zero supply
    sold_cards = {row[0] for row in summary}

    out = []
    for card in sorted(set(current) | sold_cards):
        r = current.get(card)
        n = int(r["listings"]) if r else 0
        a = sorted(asks.get(card, []))
        out.append([
            card,
            n,
            float(r["low_ask"]) if r and r["low_ask"] else "",
            round(statistics.median(a), 2) if a else "",
            int(r["bin_count"]) if r else 0,
            int(r["auction_count"]) if r else 0,
            int(r["total_bids"]) if r and r.get("total_bids") else 0,
            int(r["max_bids"]) if r and r.get("max_bids") else 0,
            n - wk.get(card, n),
            n - mo.get(card, n),
            r["status"] if r else "no-listings",
        ])

    # scarcest first, then most contested
    out.sort(key=lambda x: (x[1], -x[6]))
    return out, latest


def build_summary(rows):
    """Per-card stats. Flagged rows are excluded so the math stays clean."""
    cutoff = dt.date.today() - dt.timedelta(days=RECENT_DAYS)
    by_card = {}
    for r in rows:
        if r["_flag"] or r["_price"] is None:
            continue
        by_card.setdefault(r["_card"], []).append(r)

    summary = []
    for card, sales in by_card.items():
        prices = [s["_price"] for s in sales]
        recent = [s["_price"] for s in sales if s["_date"] and s["_date"] >= cutoff]
        dated = [s for s in sales if s["_date"]]
        last = max(dated, key=lambda s: s["_date"]) if dated else None
        first = sales[0]

        summary.append([
            card,
            first["_set"],
            first["_num"],
            len(prices),
            round(min(prices), 2),
            round(max(prices), 2),
            round(statistics.median(prices), 2),
            round(statistics.mean(prices), 2),
            round(statistics.median(recent), 2) if recent else "",
            len(recent),
            round(last["_price"], 2) if last else "",
            last["_date"].isoformat() if last else "",
        ])

    summary.sort(key=lambda x: (x[8] if x[8] != "" else -1, x[6]), reverse=True)
    return summary


def build_last_sold(rows, latest_batch):
    """Every sale, newest first. Flagged rows show the flag instead of a name."""
    def sort_key(r):
        return (r["_date"] or dt.date.min, r.get("scraped_at", ""))

    out = []
    for r in sorted(rows, key=sort_key, reverse=True):
        out.append([
            r.get("sold_date", ""),
            r["_card"] or f"[{r['_flag']}]",
            r["_set"],
            r["_num"],
            round(r["_price"], 2) if r["_price"] is not None else "",
            "NEW" if r.get("scraped_at") == latest_batch else "",
            r["_method"],
            r.get("scraped_at", ""),
            r.get("url", ""),
        ])
    return out


def write_tab(sh, title, header, data, banner=None):
    """Overwrite a tab. Creates it if missing."""
    body = ([banner] if banner else []) + [header] + data
    need_rows = len(body) + 20
    need_cols = max(len(header), 1)
    try:
        ws = sh.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=need_rows, cols=need_cols)
    ws.update(body, "A1")
    return ws


def main():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SA_JSON"]), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sheet_id = os.environ["SHEET_ID"]
    sh = gc.open_by_key(sheet_id)

    raw_rows = load_rows()
    rows = enrich(raw_rows)
    batches = sorted({r.get("scraped_at", "") for r in rows if r.get("scraped_at")})
    latest_batch = batches[-1] if batches else ""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    resolved = [r for r in rows if not r["_flag"]]
    flagged = [r for r in rows if r["_flag"]]

    # --- Summary ---
    summary = build_summary(rows)
    write_tab(
        sh, "Summary",
        ["Card", "Set", "No.", "Sales (all)", "Min", "Max",
         "Median (all)", "Mean (all)",
         f"Median ({RECENT_DAYS}d)", f"Sales ({RECENT_DAYS}d)",
         "Last price", "Last sold"],
        summary,
        banner=[f"Updated {stamp}  |  {len(summary)} cards  |  "
                f"{len(resolved)} sales  |  {len(flagged)} need review"],
    )

    # --- Last Sold ---
    last_sold = build_last_sold(rows, latest_batch)
    write_tab(
        sh, "Last Sold",
        ["Sold date", "Card", "Set", "No.", "Price USD",
         "New", "Match method", "Scraped at", "URL"],
        last_sold,
        banner=[f"Updated {stamp}  |  {len(last_sold)} sales  |  "
                f"latest scrape {latest_batch}"],
    )

    # --- Raw ---
    raw_header = ["item_id", "card_name", "title", "price_usd",
                  "sold_date", "scraped_at", "url"]
    write_tab(sh, "Raw", raw_header,
              [[r.get(k, "") for k in raw_header] for r in raw_rows])

    # --- Review ---
    review = [[r.get("sold_date", ""), r.get("title", ""),
               round(r["_price"], 2) if r["_price"] is not None else "",
               r["_flag"], r.get("url", "")]
              for r in flagged]
    write_tab(
        sh, "Review",
        ["Sold date", "Title", "Price USD", "Why flagged", "URL"],
        review,
        banner=[f"Updated {stamp}  |  {len(review)} rows could not be "
                f"identified automatically"],
    )

    # --- Supply ---
    supply_rows = load_csv(SUPPLY_PATH)
    active_rows = load_csv(ACTIVE_PATH)
    supply, snap = build_supply(supply_rows, active_rows, summary)
    if supply:
        write_tab(
            sh, "Supply",
            ["Card", "Listings", "Low ask", "Median ask", "BIN", "Auction",
             "Total bids", "Max bids", "7d change", "30d change", "Status"],
            supply,
            banner=[f"Updated {stamp}  |  snapshot {snap}  |  "
                    f"{sum(r[1] for r in supply)} listings across "
                    f"{sum(1 for r in supply if r[1] > 0)} cards"],
        )
        print(f"  Supply    : {len(supply)} cards, "
              f"{sum(r[1] for r in supply)} listings")
    else:
        print("  Supply    : no listings_history.csv yet, tab skipped")

    print(f"Wrote to: {sh.title}")
    print(f"  https://docs.google.com/spreadsheets/d/{sheet_id}")
    print(f"  Summary   : {len(summary)} cards")
    print(f"  Last Sold : {len(last_sold)} sales ({latest_batch} = newest)")
    print(f"  Review    : {len(review)} flagged")


if __name__ == "__main__":
    main()
