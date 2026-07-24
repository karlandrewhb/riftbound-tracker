"""
Build a static dashboard from the tracker CSVs.

Writes docs/index.html, which GitHub Pages can serve directly. All data is
embedded as JSON in the page, so there is no fetch at runtime and the site
works offline or from a file:// URL.

Run after the scraper and pop fetch, before or after the sheet sync.
"""
import csv
import datetime as dt
import json
import os
import statistics

import card_resolver as cr

HERE = os.path.dirname(__file__)
DOCS = os.path.join(HERE, "docs")
OUT = os.path.join(DOCS, "index.html")

SALES = os.path.join(HERE, "sales_history.csv")
SUPPLY = os.path.join(HERE, "listings_history.csv")
ACTIVE = os.path.join(HERE, "listings_active.csv")
POP = os.path.join(HERE, "pop_history.csv")

FEMALE = {"Kai'Sa", "Jinx", "Ahri", "Leona", "Miss Fortune", "Vayne",
          "Irelia", "Karma", "Soraka", "Lillia", "Vex", "Diana", "Leblanc",
          "Poppy", "Vi"}


def read(path, required=()):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if rows and required and any(c not in rows[0] for c in required):
        return []
    return rows


def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_sales():
    """Resolved, dated sales only - the flagged ones would distort charts."""
    out = []
    for r in read(SALES, ("title", "price_usd", "sold_date")):
        st, n, champ, sub, method, flag = cr.resolve(r["title"])
        if flag or "spell" in r["title"].lower():
            continue
        price = num(r["price_usd"])
        if price is None:
            continue
        try:
            date = dt.date.fromisoformat(r["sold_date"].strip())
        except (ValueError, AttributeError):
            continue
        out.append({
            "card": f"{st}-{n} {champ} {sub}".strip(),
            "set": st,
            "num": n,
            "champ": champ,
            "date": date.isoformat(),
            "price": round(price, 2),
            "title": r["title"].replace(" Opens in a new window or tab", ""),
            "url": r.get("url", ""),
        })
    out.sort(key=lambda x: x["date"])
    return out


def latest_snapshot(rows, key="snapshot_date"):
    dates = [r[key] for r in rows if r.get(key)]
    return max(dates) if dates else None


def build():
    sales = load_sales()
    pop_rows = read(POP, ("snapshot_date", "card_name", "pop_10"))
    supply_rows = read(SUPPLY, ("snapshot_date", "card_name", "listings"))
    active_rows = read(ACTIVE, ("card_name", "price_usd"))

    pop_date = latest_snapshot(pop_rows)
    pop = {r["card_name"]: {
        "pop10": int(num(r.get("pop_10"), 0)),
        "total": int(num(r.get("total"), 0)),
        "gem": num(r.get("gem_rate")),
    } for r in pop_rows if r.get("snapshot_date") == pop_date}

    sup_date = latest_snapshot(supply_rows)
    supply = {r["card_name"]: {
        "listings": int(num(r.get("listings"), 0)),
        "low": num(r.get("low_ask")),
        "bin": int(num(r.get("bin_count"), 0)),
        "auction": int(num(r.get("auction_count"), 0)),
        "bids": int(num(r.get("total_bids"), 0)),
        "maxbids": int(num(r.get("max_bids"), 0)),
    } for r in supply_rows if r.get("snapshot_date") == sup_date}

    # supply trend: listing count per card per date, for sparkline context
    trend = {}
    for r in supply_rows:
        d, c = r.get("snapshot_date"), r.get("card_name")
        if not d or not c:
            continue
        trend.setdefault(c, {})[d] = int(num(r.get("listings"), 0))

    # per-card aggregates
    by_card = {}
    for s in sales:
        by_card.setdefault(s["card"], []).append(s)

    today = dt.date.today()
    cutoff30 = (today - dt.timedelta(days=30)).isoformat()
    cards = []
    for card, ss in by_card.items():
        prices = [x["price"] for x in ss]
        recent = [x["price"] for x in ss if x["date"] >= cutoff30]
        p = pop.get(card, {})
        med = statistics.median(prices)
        cards.append({
            "card": card,
            "set": ss[0]["set"],
            "num": ss[0]["num"],
            "champ": ss[0]["champ"],
            "female": ss[0]["champ"] in FEMALE,
            "n": len(prices),
            "min": round(min(prices), 2),
            "max": round(max(prices), 2),
            "median": round(med, 2),
            "med30": round(statistics.median(recent), 2) if recent else None,
            "n30": len(recent),
            "last": ss[-1]["price"],
            "lastDate": ss[-1]["date"],
            "pop10": p.get("pop10"),
            "total": p.get("total"),
            "gem": p.get("gem"),
            "perPop": round(med / p["pop10"], 2) if p.get("pop10") else None,
            "listings": supply.get(card, {}).get("listings", 0),
            "lowAsk": supply.get(card, {}).get("low"),
            "bids": supply.get(card, {}).get("bids", 0),
            "maxBids": supply.get(card, {}).get("maxbids", 0),
            "binCount": supply.get(card, {}).get("bin", 0),
            "aucCount": supply.get(card, {}).get("auction", 0),
        })
    cards.sort(key=lambda c: -c["median"])

    # cards with pop but no sales still matter - they are the untraded ones
    for name, p in pop.items():
        if name not in by_card:
            st = name.split("-")[0]
            cards.append({
                "card": name, "set": st, "num": int(name.split("-")[1].split()[0]),
                "champ": name.split(" ", 1)[1] if " " in name else name,
                "female": any(f in name for f in FEMALE),
                "n": 0, "min": None, "max": None, "median": None,
                "med30": None, "n30": 0, "last": None, "lastDate": None,
                "pop10": p["pop10"], "total": p["total"], "gem": p["gem"],
                "perPop": None,
                "listings": supply.get(name, {}).get("listings", 0),
                "lowAsk": supply.get(name, {}).get("low"),
                "bids": 0, "maxBids": 0, "binCount": 0, "aucCount": 0,
            })

    active = [{
        "card": r["card_name"],
        "price": num(r["price_usd"]),
        "format": r.get("format", ""),
        "bids": int(num(r.get("bids"), 0)),
        "firstSeen": r.get("first_seen", ""),
        "title": r.get("title", "").replace(" Opens in a new window or tab", ""),
        "url": r.get("url", ""),
    } for r in active_rows]
    active.sort(key=lambda x: -(x["price"] or 0))

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sales": sales,
        "cards": cards,
        "active": active,
        "trend": trend,
        "popDate": pop_date,
        "supplyDate": sup_date,
    }

    os.makedirs(DOCS, exist_ok=True)
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Built {OUT}")
    print(f"  {len(sales)} sales, {len(cards)} cards, "
          f"{len(active)} active listings")
    if pop_date:
        print(f"  pop snapshot {pop_date}, supply snapshot {sup_date}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scuttle's Cove — Riftbound signature tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#0B0E11;        /* deep hull */
  --ink-2:#12171C;      /* raised panel */
  --line:#1E262E;       /* hairline */
  --dim:#5A6B77;        /* muted label */
  --text:#C8D4DC;       /* body */
  --bright:#F2F6F8;     /* headline */
  --tide:#4FD1C5;       /* the accent: shallow-water teal */
  --tide-dim:#2A6E68;
  --warn:#E8A33D;
  --down:#D9556B;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--ink);
  color:var(--text);
  font:400 14px/1.55 'IBM Plex Mono',ui-monospace,monospace;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--tide);text-decoration:none}
a:hover{text-decoration:underline}

.wrap{max-width:1400px;margin:0 auto;padding:0 20px 80px}

/* ---- masthead ---- */
header{
  border-bottom:1px solid var(--line);
  padding:34px 0 22px;margin-bottom:26px;
}
h1{
  font:400 46px/1 'Instrument Serif',Georgia,serif;
  color:var(--bright);margin:0 0 6px;letter-spacing:-.01em;
}
h1 em{font-style:italic;color:var(--tide)}
.sub{color:var(--dim);font-size:13px;letter-spacing:.04em}
.stats{display:flex;gap:28px;flex-wrap:wrap;margin-top:18px}
.stat b{
  display:block;font-size:26px;color:var(--bright);
  font-weight:500;letter-spacing:-.02em;
}
.stat span{font-size:10.5px;color:var(--dim);letter-spacing:.12em;text-transform:uppercase}

/* ---- tabs ---- */
nav{display:flex;gap:2px;margin-bottom:22px;flex-wrap:wrap}
nav button{
  background:none;border:1px solid transparent;border-bottom-color:var(--line);
  color:var(--dim);font:500 12px/1 'IBM Plex Mono',monospace;
  letter-spacing:.1em;text-transform:uppercase;
  padding:12px 18px;cursor:pointer;transition:.15s;
}
nav button:hover{color:var(--text)}
nav button[aria-selected="true"]{
  color:var(--ink);background:var(--tide);border-color:var(--tide);
}
nav button:focus-visible{outline:2px solid var(--tide);outline-offset:2px}

section{display:none}
section.on{display:block}

/* ---- chart ---- */
.chartbox{
  background:var(--ink-2);border:1px solid var(--line);
  padding:18px 18px 8px;margin-bottom:18px;position:relative;
}
.chartbox svg{display:block;width:100%;height:auto;overflow:visible}
.ctrl{
  display:flex;gap:14px;align-items:center;flex-wrap:wrap;
  margin-bottom:14px;font-size:12px;color:var(--dim);
}
.ctrl label{display:flex;align-items:center;gap:6px;cursor:pointer;letter-spacing:.06em}
.ctrl select,.ctrl input[type=search]{
  background:var(--ink);border:1px solid var(--line);color:var(--text);
  font:400 12px 'IBM Plex Mono',monospace;padding:6px 9px;
}
.ctrl select:focus,.ctrl input:focus{outline:1px solid var(--tide)}
.pill{
  border:1px solid var(--line);background:none;color:var(--dim);
  font:500 10px 'IBM Plex Mono',monospace;letter-spacing:.1em;
  text-transform:uppercase;padding:6px 11px;cursor:pointer;
}
.pill[aria-pressed="true"]{background:var(--tide);color:var(--ink);border-color:var(--tide)}

.legend{
  display:flex;flex-wrap:wrap;gap:4px;margin-top:12px;
  max-height:132px;overflow-y:auto;padding-top:12px;
  border-top:1px solid var(--line);
}
.legend button{
  background:none;border:1px solid var(--line);color:var(--dim);
  font:400 11px 'IBM Plex Mono',monospace;padding:5px 9px;cursor:pointer;
  display:flex;align-items:center;gap:5px;
}
.legend button[aria-pressed="true"]{color:var(--bright);border-color:var(--dim)}
.legend i{width:8px;height:8px;border-radius:50%;display:block}

.tip{
  position:absolute;pointer-events:none;z-index:5;
  background:var(--ink);border:1px solid var(--tide);
  padding:8px 11px;font-size:11px;color:var(--bright);
  white-space:nowrap;opacity:0;transition:opacity .1s;
}
.tip b{color:var(--tide)}

/* ---- tables ---- */
.tablewrap{overflow-x:auto;border:1px solid var(--line);background:var(--ink-2)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{
  position:sticky;top:0;background:var(--ink-2);z-index:1;
  color:var(--dim);font-weight:500;font-size:11px;letter-spacing:.09em;
  text-transform:uppercase;text-align:right;padding:12px 14px;
  border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap;
}
th:first-child,td:first-child{text-align:left}
th:hover{color:var(--text)}
th[data-dir]::after{content:" ↓";color:var(--tide)}
th[data-dir="asc"]::after{content:" ↑";color:var(--tide)}
td{padding:10px 14px;text-align:right;border-bottom:1px solid rgba(30,38,46,.5);white-space:nowrap}
tbody tr:hover{background:rgba(79,209,197,.04)}
.card-cell{color:var(--bright)}
.muted{color:var(--dim)}
.up{color:var(--tide)}
.dn{color:var(--down)}
.warn{color:var(--warn)}
.tag{
  font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  border:1px solid var(--line);padding:1px 5px;color:var(--dim);
}
.bar{
  display:inline-block;height:3px;background:var(--tide-dim);
  vertical-align:middle;margin-left:6px;
}
.empty{padding:40px;text-align:center;color:var(--dim)}
footer{
  margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11px;display:flex;justify-content:space-between;
  flex-wrap:wrap;gap:10px;
}
@media (max-width:640px){
  h1{font-size:28px}
  .stats{gap:18px}
  .stat b{font-size:17px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Scuttle's <em>Cove</em></h1>
  <div class="sub" id="sub"></div>
  <div class="stats" id="stats"></div>
</header>

<nav role="tablist">
  <button role="tab" aria-selected="true" data-tab="chart">Price history</button>
  <button role="tab" aria-selected="false" data-tab="sold">Last sold</button>
  <button role="tab" aria-selected="false" data-tab="summary">Summary</button>
  <button role="tab" aria-selected="false" data-tab="supply">Supply</button>
</nav>

<section id="chart" class="on">
  <div class="ctrl">
    <label>Scale
      <select id="scale">
        <option value="log">Logarithmic</option>
        <option value="lin">Linear</option>
      </select>
    </label>
    <label>Set
      <select id="setfilter">
        <option value="">All sets</option>
        <option value="OGN">OGN</option>
        <option value="SFD">SFD</option>
        <option value="UNL">UNL</option>
      </select>
    </label>
    <button class="pill" id="topOnly" aria-pressed="true">Top 8 only</button>
    <button class="pill" id="allOn" aria-pressed="false">Show all</button>
    <span class="muted" id="chartnote"></span>
  </div>
  <div class="chartbox">
    <div class="tip" id="tip"></div>
    <svg id="svg" viewBox="0 0 1000 460" preserveAspectRatio="xMidYMid meet"></svg>
    <div class="legend" id="legend"></div>
  </div>
</section>

<section id="sold">
  <div class="ctrl">
    <input type="search" id="soldsearch" placeholder="Filter by card or champion…" style="min-width:260px">
    <span class="muted" id="soldcount"></span>
  </div>
  <div class="tablewrap"><table id="soldtable"></table></div>
</section>

<section id="summary">
  <div class="ctrl"><span class="muted">Median price, population, and what one PSA 10 costs per graded copy.</span></div>
  <div class="tablewrap"><table id="sumtable"></table></div>
</section>

<section id="supply">
  <div class="ctrl"><span class="muted" id="supnote"></span></div>
  <div class="tablewrap"><table id="suptable"></table></div>
  <div style="margin-top:26px" class="ctrl"><span class="muted">Every listing currently on eBay.</span></div>
  <div class="tablewrap"><table id="acttable"></table></div>
</section>

<footer>
  <span id="gen"></span>
  <span>Data: eBay sold + active listings, PSA population report</span>
</footer>
</div>

<script>
const D = __DATA__;

/* ---------- helpers ---------- */
const fmt = n => n==null||n===""? "—" : "$"+Math.round(n).toLocaleString();
const pct = n => n==null? "—" : (n*100).toFixed(1)+"%";
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

/* Colour by set, lightness by rank - so related cards read as a family. */
const SETHUE = {OGN:172, SFD:196, UNL:150};
const traded = D.cards.filter(c=>c.n>0);
const colorOf = {};
traded.forEach((c,i)=>{
  const h = SETHUE[c.set] ?? 180;
  const l = 42 + (i % 7) * 6;
  colorOf[c.card] = `hsl(${h} ${c.female?62:38}% ${l}%)`;
});

/* ---------- header ---------- */
document.getElementById("sub").textContent =
  `Riftbound signature cards · PSA 10 · ${D.sales.length} recorded sales`;
document.getElementById("gen").textContent = "Built "+D.generated;

const totalVal = D.sales.reduce((a,s)=>a+s.price,0);
const stats = [
  [D.sales.length, "sales tracked"],
  [traded.length, "cards traded"],
  [fmt(totalVal), "total volume"],
  [D.active.length, "listed now"],
  [D.popDate ? D.cards.filter(c=>c.pop10).length : 0, "with pop data"],
];
document.getElementById("stats").innerHTML = stats
  .map(([v,l])=>`<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");

/* ---------- tabs ---------- */
document.querySelectorAll("nav button").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll("nav button").forEach(x=>x.setAttribute("aria-selected","false"));
    b.setAttribute("aria-selected","true");
    document.querySelectorAll("section").forEach(s=>s.classList.remove("on"));
    document.getElementById(b.dataset.tab).classList.add("on");
    if(b.dataset.tab==="chart") draw();
  };
});

/* ---------- chart ---------- */
const svg = document.getElementById("svg");
const tip = document.getElementById("tip");
let selected = new Set(traded.slice(0,8).map(c=>c.card));
let scale = "log", setFilter = "";

function visibleCards(){
  return traded.filter(c=>
    (!setFilter || c.set===setFilter) && selected.has(c.card));
}

function draw(){
  const cards = visibleCards();
  const W=1000,H=460,L=64,R=16,T=16,B=34;
  svg.innerHTML="";
  const note = document.getElementById("chartnote");

  if(!cards.length){
    note.textContent = "No cards selected.";
    svg.innerHTML = `<text x="500" y="230" fill="#5A6B77" font-size="13"
      text-anchor="middle" font-family="IBM Plex Mono">Select a card below</text>`;
    return;
  }
  note.textContent = `${cards.length} card${cards.length>1?"s":""} shown`;

  const names = new Set(cards.map(c=>c.card));
  const pts = D.sales.filter(s=>names.has(s.card));
  const xs = pts.map(s=>+new Date(s.date));
  const ys = pts.map(s=>s.price);
  const x0=Math.min(...xs), x1=Math.max(...xs);
  let y0=Math.min(...ys), y1=Math.max(...ys);
  if(scale==="log"){ y0=Math.max(50,y0*0.85); y1*=1.15; }
  else { y0=0; y1*=1.08; }

  const X = t => L + (W-L-R) * ((t-x0)/((x1-x0)||1));
  const Y = v => {
    if(scale==="log"){
      const a=Math.log(y0), b=Math.log(y1);
      return T + (H-T-B) * (1-(Math.log(Math.max(v,y0))-a)/((b-a)||1));
    }
    return T + (H-T-B) * (1-(v-y0)/((y1-y0)||1));
  };

  const ns="http://www.w3.org/2000/svg";
  const el=(t,a)=>{const e=document.createElementNS(ns,t);
    for(const k in a) e.setAttribute(k,a[k]); return e;};

  /* gridlines + y labels */
  const ticks = scale==="log"
    ? [100,250,500,1000,2500,5000,10000,20000].filter(v=>v>=y0&&v<=y1)
    : Array.from({length:6},(_,i)=>y0+(y1-y0)*i/5);
  ticks.forEach(v=>{
    svg.appendChild(el("line",{x1:L,x2:W-R,y1:Y(v),y2:Y(v),
      stroke:"#1E262E","stroke-width":1}));
    const t=el("text",{x:L-9,y:Y(v)+4,fill:"#5A6B77","font-size":11.5,
      "text-anchor":"end","font-family":"IBM Plex Mono"});
    t.textContent=fmt(v); svg.appendChild(t);
  });

  /* x labels: month boundaries */
  const seen=new Set();
  D.sales.forEach(s=>{
    const m=s.date.slice(0,7);
    if(seen.has(m)) return; seen.add(m);
    const t=+new Date(s.date);
    if(t<x0||t>x1) return;
    svg.appendChild(el("line",{x1:X(t),x2:X(t),y1:T,y2:H-B,
      stroke:"#1E262E","stroke-width":1,"stroke-dasharray":"2 4"}));
    const lab=el("text",{x:X(t),y:H-B+18,fill:"#5A6B77","font-size":11.5,
      "text-anchor":"middle","font-family":"IBM Plex Mono"});
    lab.textContent=new Date(s.date).toLocaleDateString("en",
      {month:"short",year:"2-digit"});
    svg.appendChild(lab);
  });

  /* one path per card, dots on each sale */
  cards.forEach(c=>{
    const ss=D.sales.filter(s=>s.card===c.card);
    if(!ss.length) return;
    const col=colorOf[c.card];
    const d=ss.map((s,i)=>(i?"L":"M")+X(+new Date(s.date)).toFixed(1)+" "+Y(s.price).toFixed(1)).join(" ");
    svg.appendChild(el("path",{d,fill:"none",stroke:col,"stroke-width":1.6,
      "stroke-linejoin":"round","stroke-opacity":.85}));
    ss.forEach(s=>{
      const dot=el("circle",{cx:X(+new Date(s.date)),cy:Y(s.price),r:3,
        fill:col,"fill-opacity":.9,style:"cursor:pointer"});
      dot.addEventListener("mouseenter",ev=>{
        const box=svg.getBoundingClientRect();
        tip.innerHTML=`<b>${esc(c.card)}</b><br>${s.date} · ${fmt(s.price)}`;
        tip.style.opacity=1;
        tip.style.left=Math.min(ev.clientX-box.left+12, box.width-200)+"px";
        tip.style.top=(ev.clientY-box.top-46)+"px";
      });
      dot.addEventListener("mouseleave",()=>tip.style.opacity=0);
      svg.appendChild(dot);
    });
  });
}

/* legend */
function buildLegend(){
  const box=document.getElementById("legend");
  box.innerHTML="";
  traded.filter(c=>!setFilter||c.set===setFilter).forEach(c=>{
    const b=document.createElement("button");
    b.setAttribute("aria-pressed", selected.has(c.card));
    b.innerHTML=`<i style="background:${colorOf[c.card]}"></i>${esc(c.card.split(" ")[0])} ${esc(c.champ)}`;
    b.onclick=()=>{
      selected.has(c.card)? selected.delete(c.card) : selected.add(c.card);
      b.setAttribute("aria-pressed", selected.has(c.card));
      draw();
    };
    box.appendChild(b);
  });
}

document.getElementById("scale").onchange=e=>{scale=e.target.value;draw()};
document.getElementById("setfilter").onchange=e=>{
  setFilter=e.target.value; buildLegend(); draw();
};
document.getElementById("topOnly").onclick=()=>{
  selected=new Set(traded.filter(c=>!setFilter||c.set===setFilter).slice(0,8).map(c=>c.card));
  buildLegend(); draw();
};
document.getElementById("allOn").onclick=()=>{
  selected=new Set(traded.filter(c=>!setFilter||c.set===setFilter).map(c=>c.card));
  buildLegend(); draw();
};

/* ---------- sortable tables ---------- */
function table(el, cols, rows, initialSort){
  let dir={}, key=initialSort;
  function render(){
    const sorted=[...rows].sort((a,b)=>{
      const va=a[key], vb=b[key];
      if(va==null) return 1; if(vb==null) return -1;
      const s = typeof va==="number" ? vb-va : String(va).localeCompare(String(vb));
      return dir[key]==="asc" ? -s : s;
    });
    el.innerHTML =
      "<thead><tr>"+cols.map(c=>
        `<th data-k="${c.k}"${key===c.k?` data-dir="${dir[c.k]||"desc"}"`:""}>${c.t}</th>`
      ).join("")+"</tr></thead><tbody>"+
      (sorted.length? sorted.map(r=>"<tr>"+cols.map(c=>
        `<td class="${c.cls?c.cls(r):""}">${c.f(r)}</td>`).join("")+"</tr>").join("")
        : `<tr><td colspan="${cols.length}" class="empty">Nothing here yet</td></tr>`)+
      "</tbody>";
    el.querySelectorAll("th").forEach(th=>{
      th.onclick=()=>{
        const k=th.dataset.k;
        dir[k]= key===k && dir[k]!=="asc" ? "asc":"desc";
        key=k; render();
      };
    });
  }
  render();
  return {render:()=>render(), setRows:r=>{rows=r;render()}};
}

/* last sold */
const soldRows = D.sales.slice().reverse();
const soldCols = [
  {k:"date",t:"Sold",f:r=>r.date},
  {k:"card",t:"Card",f:r=>`<span class="card-cell">${esc(r.card)}</span>`},
  {k:"set",t:"Set",f:r=>`<span class="tag">${r.set}</span>`},
  {k:"price",t:"Price",f:r=>fmt(r.price),cls:()=>"up"},
  {k:"title",t:"Listing",f:r=>r.url
    ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title.slice(0,58))}…</a>`
    : `<span class="muted">${esc(r.title.slice(0,58))}</span>`},
];
const soldT = table(document.getElementById("soldtable"), soldCols, soldRows, "date");
document.getElementById("soldcount").textContent = `${soldRows.length} sales`;
document.getElementById("soldsearch").oninput=e=>{
  const q=e.target.value.toLowerCase();
  const f=soldRows.filter(r=>r.card.toLowerCase().includes(q)||r.title.toLowerCase().includes(q));
  soldT.setRows(f);
  document.getElementById("soldcount").textContent=`${f.length} of ${soldRows.length} sales`;
};

/* summary */
table(document.getElementById("sumtable"), [
  {k:"card",t:"Card",f:r=>`<span class="card-cell">${esc(r.card)}</span>`},
  {k:"n",t:"Sales",f:r=>r.n||"—"},
  {k:"median",t:"Median",f:r=>fmt(r.median)},
  {k:"med30",t:"Median 30d",f:r=>fmt(r.med30),
   cls:r=>r.med30&&r.median&&r.med30>r.median?"up":r.med30?"dn":"muted"},
  {k:"last",t:"Last",f:r=>fmt(r.last)},
  {k:"min",t:"Low",f:r=>fmt(r.min),cls:()=>"muted"},
  {k:"max",t:"High",f:r=>fmt(r.max),cls:()=>"muted"},
  {k:"pop10",t:"PSA 10 pop",f:r=>r.pop10??"—"},
  {k:"total",t:"Graded",f:r=>r.total??"—",cls:()=>"muted"},
  {k:"gem",t:"Gem rate",f:r=>pct(r.gem),cls:r=>r.gem&&r.gem<.85?"warn":"muted"},
  {k:"perPop",t:"$ / pop",f:r=>r.perPop?"$"+r.perPop.toFixed(2):"—"},
], D.cards, "median");

/* supply */
document.getElementById("supnote").textContent =
  D.supplyDate ? `Snapshot ${D.supplyDate} · ${D.active.length} listings live`
               : "No supply snapshot yet";
const maxL = Math.max(1,...D.cards.map(c=>c.listings||0));
table(document.getElementById("suptable"), [
  {k:"card",t:"Card",f:r=>`<span class="card-cell">${esc(r.card)}</span>`},
  {k:"listings",t:"Listed",f:r=>r.listings
    ? `${r.listings}<span class="bar" style="width:${(r.listings/maxL*38).toFixed(0)}px"></span>`
    : `<span class="muted">0</span>`},
  {k:"lowAsk",t:"Low ask",f:r=>fmt(r.lowAsk)},
  {k:"median",t:"Median sold",f:r=>fmt(r.median),cls:()=>"muted"},
  {k:"binCount",t:"BIN",f:r=>r.binCount||"—",cls:()=>"muted"},
  {k:"aucCount",t:"Auction",f:r=>r.aucCount||"—",cls:()=>"muted"},
  {k:"bids",t:"Total bids",f:r=>r.bids||"—",cls:r=>r.bids>20?"up":""},
  {k:"maxBids",t:"Max bids",f:r=>r.maxBids||"—",cls:r=>r.maxBids>20?"up":""},
], D.cards, "listings");

table(document.getElementById("acttable"), [
  {k:"card",t:"Card",f:r=>`<span class="card-cell">${esc(r.card)}</span>`},
  {k:"price",t:"Price",f:r=>fmt(r.price)},
  {k:"format",t:"Format",f:r=>`<span class="tag">${esc(r.format)}</span>`},
  {k:"bids",t:"Bids",f:r=>r.bids||"—",cls:r=>r.bids>20?"up":""},
  {k:"firstSeen",t:"First seen",f:r=>r.firstSeen||"—",cls:()=>"muted"},
  {k:"title",t:"Listing",f:r=>r.url
    ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title.slice(0,52))}…</a>`
    : esc(r.title.slice(0,52))},
], D.active, "price");

buildLegend();
draw();
window.addEventListener("resize", draw);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
