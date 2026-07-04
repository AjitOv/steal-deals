#!/usr/bin/env python3
"""
Steal Deals CLI — find heavily-discounted products on Amazon.

Scrapes Amazon search results (with Amazon's own "% off" filter applied where
supported), keeps only items at or above a discount threshold, and writes a
sortable HTML dashboard plus a console summary.

Usage:
    python3 steal_deals.py                          # default keywords, 50%+ off
    python3 steal_deals.py -k "headphones" -k "smart watch" --min-discount 60
    python3 steal_deals.py --marketplace amazon.com --out deals.html

For the full website (search UI, product pages, affiliate links) run app.py.
Personal-use tool: keep request volume low (one page per keyword, throttled).
"""

import argparse
import html
import random
import sys
import time
from datetime import datetime

from scraper import find_chrome, search_deals

DEFAULT_KEYWORDS = [
    "electronics deals",
    "headphones",
    "smart watch",
    "home appliances",
    "shoes",
]


def currency_symbol(marketplace):
    return "₹" if marketplace.endswith(".in") else "$"


def render_html(deals, marketplace, min_discount, out_path):
    sym = currency_symbol(marketplace)
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    cards = []
    for d in deals:
        rating_txt = f"★ {d['rating']:.1f}" if d.get("rating") else ""
        cards.append(f"""
        <a class="card" href="{html.escape(d['url'])}" target="_blank" rel="noopener">
          <div class="badge">-{d['discount']}%</div>
          <div class="imgwrap"><img src="{html.escape(d['image'])}" alt="" loading="lazy"></div>
          <div class="body">
            <div class="title">{html.escape(d['title'][:120])}</div>
            <div class="meta">
              <span class="price">{sym}{d['price']:,.0f}</span>
              <span class="mrp">{sym}{d['mrp']:,.0f}</span>
              <span class="rating">{rating_txt}</span>
            </div>
            <div class="kw">{html.escape(d['keyword'])}</div>
          </div>
        </a>""")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Steal Deals — {marketplace}</title>
<style>
  :root {{ --bg:#0f1115; --card:#181b22; --text:#e8eaf0; --muted:#8a90a0; --accent:#ff4d4d; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font:15px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif; padding:24px; }}
  header {{ max-width:1200px; margin:0 auto 20px; display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:8px; }}
  h1 {{ font-size:22px; }} h1 em {{ color:var(--accent); font-style:normal; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .grid {{ max-width:1200px; margin:0 auto; display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:14px; }}
  .card {{ position:relative; background:var(--card); border-radius:12px; overflow:hidden; text-decoration:none; color:inherit; border:1px solid #232733; transition:transform .12s ease, border-color .12s ease; display:flex; flex-direction:column; }}
  .card:hover {{ transform:translateY(-3px); border-color:#3a4152; }}
  .badge {{ position:absolute; top:10px; left:10px; background:var(--accent); color:#fff; font-weight:700; font-size:13px; padding:3px 8px; border-radius:6px; }}
  .imgwrap {{ background:#fff; height:180px; display:flex; align-items:center; justify-content:center; }}
  .imgwrap img {{ max-height:160px; max-width:90%; object-fit:contain; }}
  .body {{ padding:12px; display:flex; flex-direction:column; gap:6px; flex:1; }}
  .title {{ font-size:13.5px; line-height:1.35; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }}
  .meta {{ display:flex; align-items:baseline; gap:8px; margin-top:auto; }}
  .price {{ font-size:17px; font-weight:700; }}
  .mrp {{ color:var(--muted); text-decoration:line-through; font-size:12.5px; }}
  .rating {{ margin-left:auto; color:#f5b942; font-size:12.5px; }}
  .kw {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.4px; }}
  .empty {{ max-width:600px; margin:60px auto; text-align:center; color:var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>🔥 Steal Deals <em>≥{min_discount}% off</em> — {marketplace}</h1>
  <div class="sub">{len(deals)} deals · updated {now}</div>
</header>
{'<div class="grid">' + ''.join(cards) + '</div>' if deals else '<div class="empty">No deals matched. Try lowering --min-discount or different keywords.</div>'}
</body>
</html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description="Find steal deals on Amazon")
    ap.add_argument("-k", "--keyword", action="append", dest="keywords",
                    help="search keyword (repeatable); defaults to a preset list")
    ap.add_argument("--min-discount", type=int, default=50,
                    help="minimum discount %% to qualify as a steal (default 50)")
    ap.add_argument("--marketplace", default="amazon.in",
                    help="amazon domain, e.g. amazon.in or amazon.com (default amazon.in)")
    ap.add_argument("--out", default="steal_deals.html", help="output HTML file")
    ap.add_argument("--max-per-keyword", type=int, default=20,
                    help="cap results per keyword (default 20)")
    args = ap.parse_args()

    keywords = args.keywords or DEFAULT_KEYWORDS
    chrome = find_chrome()
    if not chrome:
        print("Error: no Chrome/Chromium/Edge/Brave found. Install one to use this tool.")
        return 2
    all_deals, seen = [], set()

    print(f"Hunting steals on {args.marketplace} (≥{args.min_discount}% off)...")
    for kw in keywords:
        print(f"• {kw}")
        deals = search_deals(args.marketplace, kw,
                             min_discount=args.min_discount, chrome=chrome)
        if deals is None:
            print("  ! giving up on this keyword (page didn't load)")
            continue
        deals = [d for d in deals if d["asin"] not in seen][:args.max_per_keyword]
        seen.update(d["asin"] for d in deals)
        all_deals.extend(deals)
        print(f"  {len(deals)} steals found")
        time.sleep(2 + random.random() * 2)  # be polite between keywords

    all_deals.sort(key=lambda d: d["discount"], reverse=True)
    render_html(all_deals, args.marketplace, args.min_discount, args.out)

    sym = currency_symbol(args.marketplace)
    print(f"\n{'='*72}")
    print(f"TOP STEALS ({len(all_deals)} total)")
    print(f"{'='*72}")
    for d in all_deals[:15]:
        print(f"  -{d['discount']:>3}%  {sym}{d['price']:>9,.0f}  (was {sym}{d['mrp']:,.0f})  {d['title'][:60]}")
    print(f"\nDashboard written to: {args.out}")
    return 0 if all_deals else 1


if __name__ == "__main__":
    sys.exit(main())
