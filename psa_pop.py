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
    "OGN-299 Kai'Sa Daughter of the Void": None,
    "OGN-300 Volibear Relentless Storm": None,
    "OGN-301 Jinx Loose Cannon": None,
    "OGN-302 Darius Hand of Noxus": None,
    "OGN-303 Ahri Nine-Tailed Fox": None,
    "OGN-304 Lee Sin Blind Monk": None,
    "OGN-305 Yasuo Unforgiven": None,
    "OGN-306 Leona Radiant Dawn": None,
    "OGN-307 Teemo Swift Scout": None,
    "OGN-308 Viktor Herald of the Arcane": None,
    "OGN-309 Miss Fortune Bounty Hunter": None,
    "OGN-310 Sett The Boss": None,
    # --- SFD Spiritforged (set 330812) ---
    "SFD-223 Vayne Hunter": None,
    "SFD-224 Aphelios Exalted": None,
    "SFD-225 Irelia Fervent": None,
    "SFD-227 Ahri Inquisitive": None,
    "SFD-228 Bard Mercurial": None,
    "SFD-230 Teemo Strategist": None,
    "SFD-232 Sett Brawler": None,
    "SFD-233 Yone Blademaster": None,
    "SFD-235 Yasuo Windrider": None,
    "SFD-236 Darius Executioner": None,
    "SFD-237 Karma Channeler": 15332547,
    "SFD-239 Soraka Wanderer": None,
    # --- UNL Unleashed (set 338987) ---
    "UNL-226 Jhin Virtuoso": None,
    "UNL-227 Rengar Pridestalker": None,
    "UNL-229 Vi Piltover Enforcer": None,
    "UNL-230 Lillia Bashful Bloom": None,
    "UNL-231 Master Yi Wuju Master": None,
    "UNL-232 Vex Gloomist": None,
    "UNL-234 Diana Scorn of the Moon": None,
    "UNL-235 Leblanc Deceiver": None,
    "UNL-236 Kha'Zix Voidreaver": None,
    "UNL-237 Poppy Keeper of the Hammer": None,
}


def fetch_pop(spec_id):
    """Return (pop10, pop9, total, status) for one specID."""
    r = requests.get(
        API.format(spec_id=spec_id),
        headers={"authorization": f"bearer {TOKEN}"},
        timeout=30,
    )
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
    for card, spec_id in sorted(mapped.items()):
        try:
            p10, p9, total, status = fetch_pop(spec_id)
        except Exception as e:
            print(f"  {card}: FAILED {e}", file=sys.stderr)
            p10 = p9 = total = None
            status = "error"

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
        time.sleep(0.5)  # be polite to the API

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
