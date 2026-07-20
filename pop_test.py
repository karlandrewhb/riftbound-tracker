"""
Throwaway: fetch one PSA pop page and report what the HTML actually contains.
Run once, paste the output, then delete this file.

Usage in GitHub Actions: add a temporary workflow step, or run locally with
    SCRAPERAPI_KEY=xxx python pop_test.py
"""
import os
import re
import sys

import requests
from bs4 import BeautifulSoup

SETS = {
    "OGN": "https://www.psacard.com/pop/tcg-cards/2025/riftbound-league-legends-origins/321586",
    "SFD": "https://www.psacard.com/pop/tcg-cards/2026/riftbound-league-legends-sfd-spiritforged/330812",
    "UNL": "https://www.psacard.com/pop/tcg-cards/2026/riftbound-league-legends-unl-unleashed/338987",
}
KEY = os.environ.get("SCRAPERAPI_KEY", "")


def fetch(url, render):
    if not KEY:
        print("No SCRAPERAPI_KEY set.", file=sys.stderr)
        sys.exit(1)
    r = requests.get(
        "https://api.scraperapi.com/",
        params={
            "api_key": KEY,
            "url": url,
            "country_code": "us",
            "render": "true" if render else "false",
        },
        timeout=180,
    )
    r.raise_for_status()
    return r.text


# First: does SFD parse without JS? That decides the credit cost.
probe_url = SETS["SFD"]
for render in (False, True):
    label = "render=true" if render else "render=false"
    print(f"\n{'=' * 60}\nSFD with {label}\n{'=' * 60}")
    try:
        html = fetch(probe_url, render)
    except Exception as e:
        print(f"FAILED: {e}")
        continue

    print(f"bytes: {len(html):,}")
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"tables found: {len(tables)}")
    for probe in ("Karma", "Channeler", "Signature Overnumber"):
        print(f"  contains {probe!r}: {probe in html}")

    for t in tables:
        rows = t.find_all("tr")
        if len(rows) < 2:
            continue
        print(f"\n  table with {len(rows)} rows; first 4 rows:")
        for tr in rows[:4]:
            cells = [c.get_text(" ", strip=True)[:20]
                     for c in tr.find_all(["th", "td"])]
            print(f"    {cells}")
        break

    ids = set(re.findall(r"/pop/[^\"']*?/(\d{5,7})", html))
    print(f"\n  numeric IDs in links: {sorted(ids)[:12]}")

    if render:
        with open("pop_sample.html", "w", encoding="utf-8") as f:
            f.write(html[:200000])
        print("  wrote first 200KB to pop_sample.html")

# Second: confirm the other two set URLs are valid at all.
print(f"\n{'=' * 60}\nURL check for OGN and UNL (render=false)\n{'=' * 60}")
for name in ("OGN", "UNL"):
    try:
        html = fetch(SETS[name], False)
        soup = BeautifulSoup(html, "html.parser")
        title = soup.find("h1")
        print(f"{name}: {len(html):,} bytes | h1={title.get_text(strip=True)[:60] if title else 'none'!r}")
    except Exception as e:
        print(f"{name}: FAILED {e}")
