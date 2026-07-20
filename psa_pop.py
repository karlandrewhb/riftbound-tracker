"""
Fetch PSA population data for the tracked Riftbound signature cards.

Uses the PSA Public API (https://api.psacard.com/publicapi), which returns
JSON directly - no scraping, no ScraperAPI credits.

Requires a PSA_TOKEN environment variable (generate one from the PSA API
docs page while logged in).

Writes pop_history.csv, appending one row per card per run so population
growth over time is visible. Pops move slowly, so weekly is plenty.
"""
import csv
import datetime as dt
import os
import sys
import time

import requests

HERE = os.path.dirname(__file__)
POP_PATH = os.path.join(HERE, "pop_history.csv")
POP_FIELDS = ["snapshot_date", "card_name", "spec_id", "pop_10", "pop_9",
              "total", "gem_rate", "status"]

API = "https://api.psacard.com/publicapi/pop/GetPSASpecPopulation/{spec_id}"
TOKEN = os.environ.get("PSA_TOKEN", "")

# Canonical card name -> PSA specID.
# Fill these in as you find them; the script skips any left as None and
# reports which are missing, so a partial map still runs.
SPEC_IDS = {
    # --- OGN Origins (set 321586) ---
    "OGN-299 Kai'Sa Daughter of the Void": 14804169,
    "OGN-300 Volibear Relentless Storm": 14804170,
    "OGN-301 Jinx Loose Cannon": 14804171,
    "OGN-302 Darius Hand of Noxus": 14804172,
    "OGN-303 Ahri Nine-Tailed Fox": 14804173,
    "OGN-304 Lee Sin Blind Monk": 14804174,
    "OGN-305 Yasuo Unforgiven": 14804175,
    "OGN-306 Leona Radiant Dawn": 14804176,
    "OGN-307 Teemo Swift Scout": 14804177,
    "OGN-308 Viktor Herald of the Arcane": 14804178,
    "OGN-309 Miss Fortune Bounty Hunter": 14804179,
    "OGN-310 Sett The Boss": 14804180,
    # --- SFD Spiritforged (set 330812) ---
    "SFD-223 Vayne Hunter": 15332537,
    "SFD-224 Aphelios Exalted": 15332538,
    "SFD-225 Irelia Fervent": 15332539,
    "SFD-227 Ahri Inquisitive": 15332540,
    "SFD-228 Bard Mercurial": 15332541,
    "SFD-230 Teemo Strategist": 15332542,
    "SFD-232 Sett Brawler": 15332543,
    "SFD-233 Yone Blademaster": 15332544,
    "SFD-235 Yasuo Windrider": 15332545,
    "SFD-236 Darius Executioner": 15332546,
    "SFD-237 Karma Channeler": 15332547,
    "SFD-239 Soraka Wanderer": 15332548,
    # --- UNL Unleashed (set 338987) ---
    "UNL-226 Jhin Virtuoso": 15951352,
    "UNL-227 Rengar Pridestalker": 15951353,
    "UNL-228 Pyke Bloodharbor Ripper": 15951354,
    "UNL-229 Vi Piltover Enforcer": 15951355,
    "UNL-230 Lillia Bashful Bloom": 15951356,
    "UNL-231 Master Yi Wuju Master": 15951357,
    "UNL-232 Vex Gloomist": 15951358,
    "UNL-233 Ivern Green Father": 15951359,
    "UNL-234 Diana Scorn of the Moon": 15951360,
    "UNL-235 Leblanc Deceiver": 15951361,
    "UNL-236 Kha'Zix Voidreaver": 15951362,
    "UNL-237 Poppy Keeper of the Hammer": 15951363,
}


def fetch_pop(spec_id, attempts=4):
    """
    Return (pop10, pop9, total, status) for one specID.

    PSA rate-limits with HTTP 429. Back off and retry rather than burning
    the whole run: 5s, 15s, 45s. If it still fails the caller stops early,
    since a persistent 429 usually means a quota rather than a burst limit.
    """
    delay = 5
    for attempt in range(1, attempts + 1):
        r = requests.get(
            API.format(spec_id=spec_id),
            headers={"authorization": f"bearer {TOKEN}"},
            timeout=30,
        )

        if r.status_code == 429:
            if attempt == attempts:
                return None, None, None, "http-429"
            wait = int(r.headers.get("Retry-After") or delay)
            print(f"      rate limited, waiting {wait}s "
                  f"(attempt {attempt}/{attempts})")
            time.sleep(wait)
            delay *= 3
            continue

        if r.status_code == 204:
            return None, None, None, "no-data"
        if r.status_code >= 400:
            return None, None, None, f"http-{r.status_code}"

        data = r.json()
        pop = data.get("PSAPop") or {}
        if not pop:
            return None, None, None, "empty"
        return (pop.get("Grade10", 0), pop.get("Grade9", 0),
                pop.get("Total", 0), "ok")

    return None, None, None, "http-429"


def already_recorded(today):
    """True if this date is already in the file, so re-runs don't duplicate."""
    if not os.path.exists(POP_PATH) or os.path.getsize(POP_PATH) == 0:
        return False
    with open(POP_PATH, newline="", encoding="utf-8") as f:
        rows = [ln for ln in f.read().splitlines() if ln.strip()]
    return any(ln.startswith(today + ",") for ln in rows[1:])


def main():
    if not TOKEN:
        print("ERROR: PSA_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    today = dt.date.today().isoformat()
    if already_recorded(today):
        print(f"Pop already recorded for {today}; nothing to do.")
        return

    mapped = {k: v for k, v in SPEC_IDS.items() if v}
    missing = [k for k, v in SPEC_IDS.items() if not v]
    print(f"{len(mapped)} cards mapped, {len(missing)} awaiting a specID.")
    if not mapped:
        print("No specIDs filled in yet; exiting without writing.")
        return

    out = []
    consecutive_429 = 0
    for card, spec_id in sorted(mapped.items()):
        try:
            p10, p9, total, status = fetch_pop(spec_id)
        except Exception as e:
            print(f"  {card}: FAILED {e}", file=sys.stderr)
            p10 = p9 = total = None
            status = "error"

        if status == "http-429":
            consecutive_429 += 1
            if consecutive_429 >= 3:
                print("\nThree cards rate-limited in a row - stopping early "
                      "so the rest can be retried on the next run.",
                      file=sys.stderr)
                break
        else:
            consecutive_429 = 0

        gem = round(p10 / total, 4) if (p10 and total) else ""
        out.append({
            "snapshot_date": today,
            "card_name": card,
            "spec_id": spec_id,
            "pop_10": p10 if p10 is not None else "",
            "pop_9": p9 if p9 is not None else "",
            "total": total if total is not None else "",
            "gem_rate": gem,
            "status": status,
        })
        print(f"  {card:<38} pop10={p10} total={total} [{status}]")
        time.sleep(2)  # be polite to the API

    ok_rows = [r for r in out if r["status"] == "ok"]
    if not ok_rows:
        print("No successful lookups; nothing written. "
              "Likely a rate limit or quota - try again later.",
              file=sys.stderr)
        sys.exit(1)

    # only persist rows that actually returned data, so failed lookups
    # do not pollute the history with blanks
    out = ok_rows
    exists = os.path.exists(POP_PATH) and os.path.getsize(POP_PATH) > 0
    with open(POP_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=POP_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(out)

    ok = sum(1 for r in out if r["status"] == "ok")
    print(f"Wrote {len(out)} rows ({ok} successful) to pop_history.csv")
    if missing:
        print(f"Still need specIDs for: {', '.join(missing[:5])}"
              f"{' ...' if len(missing) > 5 else ''}")


if __name__ == "__main__":
    main()
