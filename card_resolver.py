#!/usr/bin/env python3
"""
Riftbound canonical card resolver.

resolve(title) -> (set_code, number, champion, subtitle, method, flag)

Resolution tiers, most reliable first:
  number+set      card number validated against a set named in the title
  number-unique   number belongs to exactly one set overall
  number+champion number ambiguous across sets, champion name disambiguates
  name+set        set named, champion matched by name
  subtitle        champion + subtitle matched (e.g. "Ahri - Inquisitive")
  champion-unique champion exists in only one set
  champion-only   champion found, set genuinely unknown  -> flag
  unresolved      no match                                -> flag

Rows with a non-empty flag should be excluded from price statistics
and surfaced for manual review.
"""
# Canonical Riftbound signature card list.
# number -> (champion, subtitle)
OGN = {
299:("Kai'Sa","Daughter of the Void"),300:("Volibear","Relentless Storm"),
301:("Jinx","Loose Cannon"),302:("Darius","Hand of Noxus"),
303:("Ahri","Nine-Tailed Fox"),304:("Lee Sin","Blind Monk"),
305:("Yasuo","Unforgiven"),306:("Leona","Radiant Dawn"),
307:("Teemo","Swift Scout"),308:("Viktor","Herald of the Arcane"),
309:("Miss Fortune","Bounty Hunter"),310:("Sett","The Boss"),
}
SFD = {
223:("Vayne","Hunter"),224:("Aphelios","Exalted"),225:("Irelia","Fervent"),
227:("Ahri","Inquisitive"),228:("Bard","Mercurial"),230:("Teemo","Strategist"),
232:("Sett","Brawler"),233:("Yone","Blademaster"),235:("Yasuo","Windrider"),
236:("Darius","Executioner"),237:("Karma","Channeler"),239:("Soraka","Wanderer"),
}
UNL = {
226:("Jhin","Virtuoso"),227:("Rengar","Pridestalker"),228:("Pyke","Bloodharbor Ripper"),
229:("Vi","Piltover Enforcer"),230:("Lillia","Bashful Bloom"),231:("Master Yi","Wuju Master"),
232:("Vex","Gloomist"),233:("Ivern","Green Father"),234:("Diana","Scorn of the Moon"),
235:("Leblanc","Deceiver"),236:("Kha'Zix","Voidreaver"),237:("Poppy","Keeper of the Hammer"),
}
ULTIMATES = {("UNL",238):("Baron Nashor","")}   # no asterisk: ultimate, not signature
SETS = {"OGN":OGN,"SFD":SFD,"UNL":UNL}
SET_TOTAL = {"OGN": 298, "SFD": 221, "UNL": 219}   # confirmed by card owner
import re

YEARS = {2023,2024,2025,2026,2027}
DENOM = re.compile(r'(\d{2,4})\s*\*?\s*/\s*(\d{2,4})')   # 223*/221 -> card 223
NUM   = re.compile(r'(\d{2,4})')

SET_HINTS = [
    ("SFD", ("spiritforged","spirit forged","sfd")),
    ("UNL", ("unleashed","unl")),
    ("OGN", ("origins","ogn")),
]

def detect_set(title):
    t = title.lower()
    hits = [code for code, keys in SET_HINTS if any(k in t for k in keys)]
    return hits[0] if len(set(hits)) == 1 else None

def numbers_in(title):
    nums, rest = [], title
    for m in DENOM.finditer(title):
        nums.append(int(m.group(1)))          # numerator only
    rest = DENOM.sub(" ", title)
    nums += [int(x) for x in NUM.findall(rest)]
    return [n for n in nums if n not in YEARS and n != 10]

def resolve(title):
    """Return (set_code, number, champion, subtitle, method, flag)."""
    st = detect_set(title)
    nums = numbers_in(title)

    # Tier 1: number + set context
    if st:
        cand = sorted({n for n in nums if n in SETS[st]})
        if len(cand) == 1:
            ch, sub = SETS[st][cand[0]]
            return st, cand[0], ch, sub, "number+set", ""
        if (st, ) and any(n == 238 for n in nums) and st == "UNL":
            return "UNL", 238, "Baron Nashor", "", "ultimate", "ultimate-not-signature"

    # Tier 1b: number unique across ALL sets (no set hint needed)
    owners = {}
    for code, d in SETS.items():
        for n in nums:
            if n in d: owners.setdefault(n, []).append(code)
    unique = [(n, c[0]) for n, c in owners.items() if len(c) == 1]
    if len(unique) == 1:
        n, code = unique[0]
        ch, sub = SETS[code][n]
        return code, n, ch, sub, "number-unique", ""

    # Tier 1c: number + champion name agree (disambiguates cross-set collisions)
    tl = title.lower().replace("'","")
    pairs = []
    for code, d in SETS.items():
        for n in nums:
            if n in d:
                ch, sub = d[n]
                if ch.lower().replace("'","") in tl:
                    pairs.append((code, n, ch, sub))
    if len({(c,n) for c,n,_,_ in pairs}) == 1:
        code, n, ch, sub = pairs[0]
        return code, n, ch, sub, "number+champion", ""

    # Tier 2: set + champion (and subtitle) by name
    t = title.lower()
    if st:
        matches = []
        for n, (ch, sub) in SETS[st].items():
            if ch.lower().replace("'","") in t.replace("'",""):
                matches.append((n, ch, sub))
        if len(matches) == 1:
            n, ch, sub = matches[0]
            return st, n, ch, sub, "name+set", ""
        if len(matches) > 1:
            return st, None, None, None, "ambiguous", "multiple-champions"

    # Tier 3: subtitle alone is often unique across sets
    for code, d in SETS.items():
        for n, (ch, sub) in d.items():
            if sub and sub.lower() in t and ch.lower().replace("'","") in t.replace("'",""):
                return code, n, ch, sub, "subtitle", ""

    # Tier 4: champion named but set unknown
    champs = set()
    for code, d in SETS.items():
        for n, (ch, sub) in d.items():
            if ch.lower().replace("'","") in t.replace("'",""):
                champs.add(ch)
    if len(champs) == 1:
        ch = champs.pop()
        owners = [(code, n, s) for code, d in SETS.items()
                  for n, (c, s) in d.items()
                  if c.lower().replace("'","") == ch.lower().replace("'","")]
        if len(owners) == 1:
            code, n, sub = owners[0]
            return code, n, ch, sub, "champion-unique", ""
        return None, None, ch, None, "champion-only", "set-ambiguous"

    return None, None, None, None, "unresolved", "no-match"
