#!/usr/bin/env python3
"""
Push Riftbound sold-price summary to Google Sheets.

Reads sales_history.csv, aggregates per card_name, and overwrites two tabs
in the target spreadsheet:
  - "Summary" : one row per card with price stats
  - "Raw"     : full sales_history dump (for reference/pivots)

Auth: service-account JSON in env var GOOGLE_SA_JSON, spreadsheet id in
env var SHEET_ID.
"""

import csv
import json
import os
import statistics
import datetime as dt

import gspread
from google.oauth2.service_account import Credentials

CSV_PATH = os.path.join(os.path.dirname(__file__), "sales_history.csv")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Only sales on/after this many days back count toward the "recent" stats.
RECENT_DAYS = 30


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_date(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def build_summary(rows):
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=RECENT_DAYS)

    by_card = {}
    for r in rows:
        name = r["card_name"]
        try:
            price = float(r["price_usd"])
        except (ValueError, TypeError):
            continue
        d = to_date(r["sold_date"])
        by_card.setdefault(name, []).append((price, d))

    summary = []
    for name, sales in sorted(by_card.items()):
        prices = [p for p, _ in sales]
        recent = [p for p, d in sales if d and d >= cutoff]
        dated = [(p, d) for p, d in sales if d]
        last_price = ""
        last_date = ""
        if dated:
            last_p, last_d = max(dated, key=lambda x: x[1])
            last_price = round(last_p, 2)
            last_date = last_d.isoformat()

        summary.append([
            name,
            len(prices),
            round(min(prices), 2),
            round(max(prices), 2),
            round(statistics.median(prices), 2),
            round(statistics.mean(prices), 2),
            round(statistics.median(recent), 2) if recent else "",
            len(recent),
            last_price,
            last_date,
        ])

    # Sort by recent-median desc, then all-time median, so hot cards float up.
    summary.sort(key=lambda x: (x[6] if x[6] != "" else -1, x[4]), reverse=True)
    return summary


def main():
    sa_json = os.environ["GOOGLE_SA_JSON"]
    sheet_id = os.environ["SHEET_ID"]

    creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    rows = load_rows()
    summary = build_summary(rows)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    header = [
        "Card", "Sales (all)", "Min", "Max", "Median (all)", "Mean (all)",
        f"Median ({RECENT_DAYS}d)", f"Sales ({RECENT_DAYS}d)",
        "Last price", "Last sold",
    ]

    # --- Summary tab ---
    try:
        ws = sh.worksheet("Summary")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Summary", rows=len(summary) + 10, cols=len(header))

    ws.update([[f"Updated {stamp}"], header] + summary, "A1")

    # --- Raw tab ---
    raw_header = ["item_id", "card_name", "title", "price_usd", "sold_date", "scraped_at", "url"]
    raw = [[r.get(k, "") for k in raw_header] for r in rows]
    try:
        rws = sh.worksheet("Raw")
        rws.clear()
    except gspread.WorksheetNotFound:
        rws = sh.add_worksheet(title="Raw", rows=len(raw) + 10, cols=len(raw_header))
    rws.update([raw_header] + raw, "A1")

    print(f"Synced {len(summary)} cards / {len(raw)} sales to sheet.")


if __name__ == "__main__":
    main()
