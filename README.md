# Riftbound Signature PSA 10 · Sold Tracker

Scrapes one fixed eBay search (`riftbound signature psa 10`, sold + completed)
once a day via GitHub Actions, appends new sales to `sales_history.csv`, and
charts price-over-time auto-split by card in `dashboard.html`.

No API keys. No paid services. Your Mac can be off.

## How it works

```
scraper.py  --(daily cron on GitHub)-->  sales_history.csv  -->  dashboard.html
```

- One page hit per day = very low volume, safe for scraping.
- Dedupes on eBay item ID, so re-runs never double-count a sale.
- Logs a warning if a run returns 0 listings (possible layout change or block).

## Setup (one time)

1. Create a new GitHub repo and push these files.
2. Go to **Settings → Actions → General → Workflow permissions** and select
   **Read and write permissions** (lets the workflow commit data back).
3. That's it. The scrape runs daily at 09:00 UTC. Trigger it manually anytime
   from the **Actions** tab → *Daily Riftbound scrape* → *Run workflow*.

## Viewing the dashboard

Enable **GitHub Pages** (Settings → Pages → deploy from `main` branch) and open
`https://<you>.github.io/<repo>/dashboard.html`. It reads the CSV directly.

Or just open `dashboard.html` locally after pulling the latest CSV.

## Tuning

- **Schedule:** edit the `cron` line in `.github/workflows/scrape.yml`.
- **Filtering:** `EXCLUDE_TERMS` in `scraper.py` drops lots, sealed, wrong grades.
- **If parsing breaks:** eBay changed its markup. The CSS selectors in
  `parse_listings()` are the thing to update.

## Known limits

- eBay's sold-date markup is less stable than price/title; the scraper falls
  back to scrape-date when it can't parse a sold date.
- Card-name extraction is heuristic (name + number from the title). Messy
  titles may land in an `unknown` bucket you can clean up later.
