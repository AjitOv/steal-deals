#!/usr/bin/env python3
"""
Generate + auto-post the daily deals message from the bundled deals/ data.

WhatsApp : no safe posting API exists — the message is copied to your
           clipboard; paste it into your WhatsApp channel.
Telegram : posted automatically via the official Bot API when
           telegram_bot_token + telegram_channel are set in config.json.
Pinterest: top deals pinned automatically when pinterest_access_token +
           pinterest_board_id are set in config.json.

Usage:
    python3 share_deals.py            # print + copy to clipboard only
    python3 share_deals.py --post     # also post to Telegram / Pinterest
    python3 share_deals.py --top 10   # number of deals in the message
"""

import argparse
import glob
import json
import os
import subprocess
import sys

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEALS_DIR = os.path.join(BASE_DIR, "deals")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SITE = "https://steal-deals.onrender.com"

EMOJI = {"electronics": "🔌", "headphones": "🎧", "smart watches": "⌚",
         "mobiles": "📱", "laptops": "💻", "home & kitchen": "🏠",
         "shoes": "👟", "men's fashion": "👕", "women's fashion": "👗",
         "toys": "🧸"}


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pick_deals(top_n):
    """Trustworthy picks, enriched from bundled product-detail pages.

    Search cards often carry seller-inflated MRPs and no ratings; the
    product page has the honest numbers. Where a product_*.json exists,
    its price/MRP/discount/rating replace the card's.
    """
    deals = {}
    for path in glob.glob(os.path.join(DEALS_DIR, "search_*.json")):
        for d in _load_json(path) or []:
            deals.setdefault(d["asin"], d)
    for path in glob.glob(os.path.join(DEALS_DIR, "product_*.json")):
        p = _load_json(path)
        if p and p.get("asin") in deals and p.get("price") and p.get("discount"):
            deals[p["asin"]].update({k: p[k] for k in
                                     ("price", "mrp", "discount", "rating", "reviews")
                                     if p.get(k) is not None})
            deals[p["asin"]]["verified"] = True

    def score(d):
        return (bool(d.get("verified")), (d.get("rating") or 0) >= 4.0,
                d["discount"])

    sane = [d for d in deals.values()
            if 40 <= d["discount"] <= 90 and (d.get("rating") or 5) >= 3.8]
    sane.sort(key=score, reverse=True)

    out, per_kw = [], {}
    for d in sane:  # variety: max 2 per source keyword
        kw = d.get("keyword", "")
        if per_kw.get(kw, 0) < 2:
            per_kw[kw] = per_kw.get(kw, 0) + 1
            out.append(d)
        if len(out) == top_n:
            break
    return out


def rup(n):
    return "₹" + f"{round(n):,}"


def line(d):
    emoji = EMOJI.get(d.get("keyword", "").lower(), "🛒")
    title = d["title"][:55].rstrip() + ("…" if len(d["title"]) > 55 else "")
    stars = f" ★{d['rating']:.1f}" if d.get("rating") else ""
    return (f"{emoji} {title}\n"
            f"   {rup(d['price'])} (was {rup(d['mrp'])}, -{d['discount']}%){stars}\n"
            f"   {SITE}/go/{d['asin']}")


def pick_flipkart(top_n=3):
    """Top Flipkart deals from the scraped fksearch_*.json caches."""
    seen, deals = set(), []
    for path in glob.glob(os.path.join(DEALS_DIR, "fksearch_*.json")):
        for d in _load_json(path) or []:
            if d["asin"] not in seen and 50 <= d["discount"] <= 90:
                seen.add(d["asin"])
                deals.append(d)
    deals.sort(key=lambda d: d["discount"], reverse=True)
    out, per_kw = [], {}
    for d in deals:  # variety: max 1 per source keyword
        kw = d.get("keyword", "")
        if per_kw.get(kw, 0) < 1:
            per_kw[kw] = 1
            out.append(d)
        if len(out) == top_n:
            break
    return out


def fk_line(d):
    title = d["title"][:55].rstrip() + ("…" if len(d["title"]) > 55 else "")
    return (f"🔷 {title}\n"
            f"   {rup(d['price'])} (was {rup(d['mrp'])}, -{d['discount']}%)\n"
            f"   {d['url']}")


def build_message(deals, fk_deals=None):
    body = "\n\n".join(line(d) for d in deals)
    fk_body = ""
    if fk_deals:
        fk_body = ("\n\n— FLIPKART STEALS —\n\n"
                   + "\n\n".join(fk_line(d) for d in fk_deals))
    return (f"🔥 Today's steals — hand-picked, honest discounts:\n\n{body}{fk_body}"
            f"\n\nAll deals: {SITE}\n(affiliate links — costs you nothing extra)")


def copy_clipboard(text):
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode(), check=False)
        return True
    return False


def post_telegram(cfg, text):
    token, channel = cfg.get("telegram_bot_token"), cfg.get("telegram_channel")
    if not (token and channel):
        print("[telegram] skipped — set telegram_bot_token + telegram_channel "
              "in config.json (see README)")
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": channel, "text": text,
                            "disable_web_page_preview": True}, timeout=30)
    ok = r.status_code == 200 and r.json().get("ok")
    print(f"[telegram] {'posted to ' + channel if ok else 'FAILED: ' + r.text[:200]}")


def post_pinterest(cfg, deals):
    token, board = cfg.get("pinterest_access_token"), cfg.get("pinterest_board_id")
    if not (token and board):
        print("[pinterest] skipped — set pinterest_access_token + "
              "pinterest_board_id in config.json (see README)")
        return
    for d in deals[:3]:  # pin the top few, not the whole list
        if not d.get("image"):
            continue
        r = requests.post(
            "https://api.pinterest.com/v5/pins",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "board_id": board,
                "title": f"{d['title'][:90]} — {d['discount']}% off",
                "description": (f"{rup(d['price'])} instead of {rup(d['mrp'])}. "
                                f"Hand-picked Amazon India steal. #deals #amazonfinds"),
                "link": f"{SITE}/go/{d['asin']}",
                "media_source": {"source_type": "image_url", "url": d["image"]},
            }, timeout=30)
        status = "pinned" if r.status_code in (200, 201) else f"FAILED {r.status_code}: {r.text[:150]}"
        print(f"[pinterest] {d['asin']}: {status}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--post", action="store_true",
                    help="post to Telegram/Pinterest (needs tokens in config.json)")
    ap.add_argument("--top", type=int, default=8, help="deals per message (default 8)")
    args = ap.parse_args()

    cfg = load_config()
    deals = pick_deals(args.top)
    if not deals:
        sys.exit("No deals found — run refresh_deals.py first.")
    msg = build_message(deals, pick_flipkart())

    print(msg)
    print("\n" + "=" * 60)
    if copy_clipboard(msg):
        print("Copied to clipboard — paste into your WhatsApp channel.")
    if args.post:
        post_telegram(cfg, msg)
        post_pinterest(cfg, deals)
    else:
        print("Dry run (no posting). Add --post to send to Telegram/Pinterest.")


if __name__ == "__main__":
    main()
