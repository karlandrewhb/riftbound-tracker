"""
Fetch PSA population data for the tracked Riftbound signature cards.

Uses the same endpoint the PSA pop report page calls for its own table
(POST /Pop/GetSetItems), which returns every card in a set as JSON in one
request. No API token, no per-card quota - three requests covers all 36
signatures.

Endpoint discovered via github.com/ChrisMuir/psa-scrape.

Writes pop_history.csv, appending one row per card per run. Population
moves slowly, so weekly is plenty; the date guard makes re-runs a no-op.
"""
import csv
import datetime as dt
import os
import sys
import time

import requests

from card_resolver import SETS

HERE = os.path.dirname(__file__)
POP_PATH = os.path.join(HERE, "pop_history.csv")
POP_FIELDS = ["snapshot_date", "card_name", "spec_id", "card_number",
              "variety", "pop_10", "pop_9", "total", "gem_rate"]

POP_URL = "https://www.psacard.com/Pop/GetSetItems"
CATEGORY_ID = "20019"

# PSA heading IDs for the three Riftbound sets (from the pop report URLs).
PSA_SETS = {
    "OGN": 321586,
    "SFD": 330812,
    "UNL": 338987,
}

# Only signature cards are tracked; PSA marks them with an asterisk in the
# card number, matching the convention used across the rest of this project.
SIGNATURE_MARKER = "*"


def fetch_set(heading_id, session):
    """POST for one set, returning the raw card records."""
    r = session.post(
        POP_URL,
        data={
            "headingID": str(heading_id),
            "categoryID": CATEGORY_ID,
            "draw": 1,
            "start": 0,
            "length": 500,
            "isPSADNA": "false",
        },
        headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.psacard.com/pop/",
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def to_int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def canonical_name(set_code, card_number):
    """Map a PSA card number back to the project's canonical card name."""
    try:
        num = int(str(card_number).replace("*", "").strip())
    except ValueError:
        return None
    entry = SETS.get(set_code, {}).get(num)
    if not entry:
        return None
    champ, sub = entry
    return f"{set_code}-{num} {champ} {sub}".strip()


def already_recorded(today):
    if not os.path.exists(POP_PATH) or os.path.getsize(POP_PATH) == 0:
        return False
    with open(POP_PATH, newline="", encoding="utf-8") as f:
        rows = [ln for ln in f.read().splitlines() if ln.strip()]
    return any(ln.startswith(today + ",") for ln in rows[1:])


def main():
    today = dt.date.today().isoformat()
    if already_recorded(today):
        print(f"Pop already recorded for {today}; nothing to do.")
        return

    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(max_retries=3))

    out = []
    for set_code, heading_id in PSA_SETS.items():
        try:
            cards = fetch_set(heading_id, sess)
        except Exception as e:
            print(f"{set_code}: FAILED ({e})", file=sys.stderr)
            continue

        matched = 0
        for c in cards:
            number = str(c.get("CardNumber") or "").strip()
            if SIGNATURE_MARKER not in number:
                continue  # skip non-signature versions

            name = canonical_name(set_code, number)
            if not name:
                continue  # a signature we do not track (e.g. spells)

            p10 = to_int(c.get("Grade10"))
            p9 = to_int(c.get("Grade9"))
            total = to_int(c.get("Total"))
            out.append({
                "snapshot_date": today,
                "card_name": name,
                "spec_id": c.get("SpecID", ""),
                "card_number": number,
                "variety": (c.get("Variety") or "").strip(),
                "pop_10": p10,
                "pop_9": p9,
                "total": total,
                "gem_rate": round(p10 / total, 4) if total else "",
            })
            matched += 1

        print(f"{set_code}: {len(cards)} cards in set, "
              f"{matched} signatures matched")
        time.sleep(3)

    if not out:
        print("No population rows collected; nothing written.", file=sys.stderr)
        return

    exists = os.path.exists(POP_PATH) and os.path.getsize(POP_PATH) > 0
    with open(POP_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=POP_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(sorted(out, key=lambda r: r["card_name"]))

    print(f"\nWrote {len(out)} rows to pop_history.csv")
    for r in sorted(out, key=lambda r: -r["pop_10"])[:5]:
        print(f"  {r['card_name']:<38} pop10={r['pop_10']:>4} "
              f"total={r['total']:>4} gem={r['gem_rate']}")


if __name__ == "__main__":
    main()
