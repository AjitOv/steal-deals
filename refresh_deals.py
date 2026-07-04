#!/usr/bin/env python3
"""
Refresh the bundled deal data served by cache-mode deploys.

Runs on a machine with Chrome (your laptop — datacenter IPs get blocked),
scrapes every curated keyword plus product details for the top deals,
writes everything to deals/, then commits and pushes so Render redeploys
with fresh data.

Usage:
    python3 refresh_deals.py                 # scrape all keywords, commit + push
    python3 refresh_deals.py --no-push       # scrape + write only (no git)
    python3 refresh_deals.py --products 6    # detail-fetch top 6 deals/keyword
    python3 refresh_deals.py Headphones Toys # only these keywords
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

import scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEALS_DIR = os.path.join(BASE_DIR, "deals")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# Keep in sync with CATEGORIES in static/index.html.
KEYWORDS = ["Electronics", "Headphones", "Smart Watches", "Mobiles", "Laptops",
            "Home & Kitchen", "Shoes", "Men's Fashion", "Women's Fashion", "Toys"]


def cache_path(kind, key):
    # same naming scheme as app.py's _cache_path
    safe = "".join(c if c.isalnum() else "_" for c in key.lower())[:80]
    return os.path.join(DEALS_DIR, f"{kind}_{safe}.json")


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def polite_sleep():
    time.sleep(3 + random.random() * 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("keywords", nargs="*", help="only refresh these keywords")
    ap.add_argument("--no-push", action="store_true", help="skip git commit + push")
    ap.add_argument("--products", type=int, default=4,
                    help="product details to fetch per keyword (default 4)")
    args = ap.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    marketplace = cfg.get("marketplace", "amazon.in")
    keywords = args.keywords or (KEYWORDS + cfg.get("refresh_keywords", []))

    chrome = scraper.find_chrome()
    if not chrome:
        sys.exit("No Chrome/Chromium found — run this on a machine with a browser.")

    os.makedirs(DEALS_DIR, exist_ok=True)
    ok, failed, seen_asins = [], [], set()

    for kw in keywords:
        print(f"[search] {kw} …", flush=True)
        deals = scraper.search_deals(marketplace, kw, min_discount=0, chrome=chrome)
        if not deals:
            print(f"[search] {kw}: FAILED (no results)")
            failed.append(kw)
            polite_sleep()
            continue
        write_json(cache_path("search", f"{marketplace}:{kw}"), deals)
        ok.append(kw)
        print(f"[search] {kw}: {len(deals)} deals")
        polite_sleep()

        for d in deals[:args.products]:
            asin = d["asin"]
            if asin in seen_asins:
                continue
            seen_asins.add(asin)
            print(f"[product] {asin} ({d['title'][:40]}…)", flush=True)
            product = scraper.fetch_product(marketplace, asin, chrome=chrome)
            if product:
                write_json(cache_path("product", f"{marketplace}:{asin}"), product)
            else:
                print(f"[product] {asin}: failed, card data will be used")
            polite_sleep()

    write_json(os.path.join(DEALS_DIR, "index.json"), {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "keywords": ok,
        "failed": failed,
        "marketplace": marketplace,
    })
    print(f"\nDone: {len(ok)} keywords ok, {len(failed)} failed, "
          f"{len(seen_asins)} product details.")

    if args.no_push:
        print("Skipping git (--no-push).")
        return
    subprocess.run(["git", "add", "deals"], cwd=BASE_DIR, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR)
    if diff.returncode == 0:
        print("No data changes — nothing to push.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(["git", "commit", "-m", f"Refresh deals {stamp}"],
                   cwd=BASE_DIR, check=True)
    subprocess.run(["git", "push"], cwd=BASE_DIR, check=True)
    print("Pushed.")

    # public-URL repos don't auto-deploy on Render — hit the deploy hook
    hook = os.environ.get("RENDER_DEPLOY_HOOK") or cfg.get("render_deploy_hook")
    if hook:
        import requests
        r = requests.get(hook, timeout=30)
        print(f"Render deploy triggered ({r.status_code}).")
    else:
        print("No render_deploy_hook in config.json — trigger the deploy "
              "manually in the Render dashboard.")


if __name__ == "__main__":
    main()
