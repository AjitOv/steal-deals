#!/usr/bin/env python3
"""
Steal Deals website — search Amazon deals, view products, buy via affiliate link.

Run:    python3 app.py            (http://localhost:5001)

Config: set your Amazon Associates tag in config.json ("affiliate_tag")
        or via the AMAZON_AFFILIATE_TAG environment variable.
        Every "Buy on Amazon" click goes through /go/<asin>, which redirects
        to Amazon with your tag attached.
"""

import glob
import json
import os
import threading
import time

from flask import Flask, abort, jsonify, redirect, request, send_from_directory

import paapi
import scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
DEALS_DIR = os.path.join(BASE_DIR, "deals")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "affiliate_tag": "yourtag-21",
    "marketplace": "amazon.in",
    "search_cache_minutes": 30,
    "product_cache_minutes": 60,
    # "auto": PA-API when keys are set, scraper otherwise (and as fallback)
    # "paapi": PA-API only   |   "scrape": scraper only
    # "cache": bundled deals/ data only, never fetch (for public deploys
    #          without PA-API keys — scraping violates Associates ToS)
    "data_source": "auto",
    "paapi_access_key": "",
    "paapi_secret_key": "",
    "paapi_pages": 2,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    for env, key in (("AMAZON_AFFILIATE_TAG", "affiliate_tag"),
                     ("PAAPI_ACCESS_KEY", "paapi_access_key"),
                     ("PAAPI_SECRET_KEY", "paapi_secret_key"),
                     ("DATA_SOURCE", "data_source")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


CONFIG = load_config()
os.makedirs(CACHE_DIR, exist_ok=True)


def paapi_enabled():
    return (CONFIG["data_source"] in ("auto", "paapi")
            and CONFIG["paapi_access_key"] and CONFIG["paapi_secret_key"])


def cache_mode():
    return CONFIG["data_source"] == "cache"


def deals_index():
    """Metadata written by refresh_deals.py: keywords + updated_at."""
    try:
        with open(os.path.join(DEALS_DIR, "index.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def source_search(marketplace, q):
    """Search via PA-API when configured, falling back to the scraper."""
    if paapi_enabled():
        try:
            return paapi.search_deals(
                marketplace, q,
                partner_tag=CONFIG["affiliate_tag"],
                access_key=CONFIG["paapi_access_key"],
                secret_key=CONFIG["paapi_secret_key"],
                min_discount=0,
                pages=CONFIG["paapi_pages"],
            )
        except paapi.PaapiError as e:
            print(f"[paapi] search failed: {e}")
            if CONFIG["data_source"] == "paapi":
                return None
    if CONFIG["data_source"] == "paapi":
        return None
    return scraper.search_deals(marketplace, q, min_discount=0)


def source_product(marketplace, asin):
    """Product details via PA-API when configured, falling back to the scraper."""
    if paapi_enabled():
        try:
            return paapi.fetch_product(
                marketplace, asin,
                partner_tag=CONFIG["affiliate_tag"],
                access_key=CONFIG["paapi_access_key"],
                secret_key=CONFIG["paapi_secret_key"],
            )
        except paapi.PaapiError as e:
            print(f"[paapi] product failed: {e}")
            if CONFIG["data_source"] == "paapi":
                return None
    if CONFIG["data_source"] == "paapi":
        return None
    return scraper.fetch_product(marketplace, asin)

app = Flask(__name__, static_folder="static")


# ---- keep-alive: stop Render's free tier from idling the service ----
# Render kills free web services after ~15 min without traffic, so the next
# visitor eats a ~60s cold boot. While we're running, ping our own public URL
# (Render sets RENDER_EXTERNAL_URL automatically) every 10 minutes.
# Disable with KEEP_ALIVE=0. A GitHub Actions cron does the same from outside
# as a backstop (.github/workflows/keepalive.yml).
def _keep_alive_loop(url, interval_sec=600):
    import requests as _requests
    while True:
        time.sleep(interval_sec)
        try:
            _requests.get(url + "/api/config", timeout=30)
        except Exception as e:
            print(f"[keep-alive] ping failed: {e}")


_KEEP_ALIVE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
KEEP_ALIVE_ON = bool(_KEEP_ALIVE_URL) and os.environ.get("KEEP_ALIVE") != "0"
if KEEP_ALIVE_ON:
    threading.Thread(target=_keep_alive_loop, args=(_KEEP_ALIVE_URL,),
                     daemon=True, name="keep-alive").start()
    print(f"[keep-alive] pinging {_KEEP_ALIVE_URL} every 10 min")

# At most 2 Chrome renders at once; scrapes of the same key share one flight.
_scrape_sem = threading.Semaphore(2)
_inflight = {}
_inflight_lock = threading.Lock()


def _cache_path(kind, key, base=CACHE_DIR):
    safe = "".join(c if c.isalnum() else "_" for c in key.lower())[:80]
    return os.path.join(base, f"{kind}_{safe}.json")


def cache_get(kind, key, max_age_min):
    # cache mode: bundled deals/ first, then cache/, and stale is fine —
    # the data is only as fresh as the last refresh_deals.py push anyway
    bases = (DEALS_DIR, CACHE_DIR) if cache_mode() else (CACHE_DIR,)
    for base in bases:
        path = _cache_path(kind, key, base)
        if not os.path.exists(path):
            continue
        if not cache_mode() and time.time() - os.path.getmtime(path) > max_age_min * 60:
            continue
        try:
            with open(path) as f:
                return json.load(f)
        except (ValueError, OSError):
            continue
    return None


def cache_put(kind, key, data):
    with open(_cache_path(kind, key), "w") as f:
        json.dump(data, f)


def scrape_once(key, fn):
    """Run fn() for this key, deduping concurrent identical requests."""
    with _inflight_lock:
        event = _inflight.get(key)
        if event is None:
            event = threading.Event()
            _inflight[key] = event
            leader = True
        else:
            leader = False
    if not leader:
        event.wait(timeout=90)
        return None  # follower: caller re-reads cache
    try:
        with _scrape_sem:
            result = fn()
        return result
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        event.set()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/config")
def api_config():
    info = {
        "marketplace": CONFIG["marketplace"],
        "affiliate_tag_set": CONFIG["affiliate_tag"] != DEFAULT_CONFIG["affiliate_tag"],
        "source": "cache" if cache_mode() else
                  ("paapi" if paapi_enabled() else "scrape"),
        "keep_alive": KEEP_ALIVE_ON,
    }
    if cache_mode():
        idx = deals_index()
        info["curated"] = idx.get("keywords", [])
        info["updated_at"] = idx.get("updated_at")
    return jsonify(info)


@app.route("/api/deals")
def api_deals():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "missing q"}), 400
    if len(q) > 100:
        return jsonify({"error": "query too long"}), 400
    min_discount = max(0, min(99, request.args.get("min", 50, type=int)))
    marketplace = CONFIG["marketplace"]

    cache_key = f"{marketplace}:{q}"
    cached = cache_get("search", cache_key, CONFIG["search_cache_minutes"])
    if cached is None and cache_mode():
        return jsonify({"query": q, "count": 0, "deals": [],
                        "curated": deals_index().get("keywords", []),
                        "error": "Live search is coming soon — pick a category instead."})
    if cached is None:
        def scrape_and_cache():
            deals = source_search(marketplace, q)
            if deals is not None:
                cache_put("search", cache_key, deals)
            return deals

        cached = scrape_once(cache_key, scrape_and_cache)
        if cached is None:
            # follower woke up, or scrape failed — cache was written before
            # the in-flight event was set, so one more read settles it
            cached = cache_get("search", cache_key, CONFIG["search_cache_minutes"])
            if cached is None:
                return jsonify({"error": "Amazon didn't return results, try again"}), 502

    filtered = [d for d in cached if d["discount"] >= min_discount]
    return jsonify({"query": q, "min_discount": min_discount,
                    "count": len(filtered), "deals": filtered})


def _deal_from_bundled_searches(asin):
    for path in glob.glob(os.path.join(DEALS_DIR, "search_*.json")):
        try:
            with open(path) as f:
                items = json.load(f)
        except (ValueError, OSError):
            continue
        for d in items:
            if d.get("asin") == asin:
                return {**d, "images": [d["image"]] if d.get("image") else [],
                        "bullets": [], "availability": None}
    return None


@app.route("/api/product/<asin>")
def api_product(asin):
    if not asin.isalnum() or len(asin) > 15:
        abort(400)
    marketplace = CONFIG["marketplace"]
    cache_key = f"{marketplace}:{asin}"
    cached = cache_get("product", cache_key, CONFIG["product_cache_minutes"])
    if cached is None and cache_mode():
        # no detail file bundled — fall back to the deal card's own data
        basic = _deal_from_bundled_searches(asin)
        if basic is None:
            return jsonify({"error": "product details not available"}), 404
        return jsonify(basic)
    if cached is None:
        def scrape_and_cache():
            product = source_product(marketplace, asin)
            if product is not None:
                cache_put("product", cache_key, product)
            return product

        cached = scrape_once("p:" + cache_key, scrape_and_cache)
        if cached is None:
            cached = cache_get("product", cache_key, CONFIG["product_cache_minutes"])
            if cached is None:
                return jsonify({"error": "could not load product"}), 502
    return jsonify(cached)


@app.route("/go/<asin>")
def go(asin):
    """Affiliate redirect — the money link."""
    if not asin.isalnum() or len(asin) > 15:
        abort(400)
    url = f"https://www.{CONFIG['marketplace']}/dp/{asin}?tag={CONFIG['affiliate_tag']}"
    return redirect(url, code=302)


if __name__ == "__main__":
    tag = CONFIG["affiliate_tag"]
    if cache_mode():
        source = "bundled deals/ data (refresh with refresh_deals.py)"
    elif paapi_enabled():
        source = "Amazon PA-API (official)"
    else:
        source = "scraper (set paapi_access_key/paapi_secret_key to use the official API)"
    port = int(os.environ.get("PORT", 5001))
    print(f"Steal Deals → http://localhost:{port}")
    print(f"Marketplace: {CONFIG['marketplace']} | Affiliate tag: {tag}"
          + ("  (placeholder — set yours in config.json!)"
             if tag == DEFAULT_CONFIG["affiliate_tag"] else ""))
    print(f"Data source: {source}")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, threaded=True)
