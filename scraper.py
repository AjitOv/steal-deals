"""
Core Amazon scraping via headless Chrome.

Shared by the CLI (steal_deals.py) and the website (app.py).
Renders pages in the user's installed Chrome so Amazon's JS challenge
passes like a normal browser visit. Personal-use: low volume, throttled.
"""

import json
import os
import random
import re
import shutil
import subprocess
import time
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

PRICE_RE = re.compile(r"[\d,]+(?:\.\d+)?")

# Amazon.in search filter node for "50% Off or more" (percent-off-with-tax).
PCT_OFF_NODE_IN = "p_n_pct-off-with-tax:2665401031"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def parse_price(text):
    """'₹1,299.00' -> 1299.0, returns None if no number found."""
    if not text:
        return None
    m = PRICE_RE.search(text)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def chrome_fetch(url, chrome=None, budget_ms=20000, timeout=60):
    """Render a URL in headless Chrome and return the DOM HTML, or None."""
    chrome = chrome or find_chrome()
    if not chrome:
        raise RuntimeError("No Chrome/Chromium/Edge/Brave found on this machine.")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        f"--virtual-time-budget={budget_ms}",
        "--timeout=30000",
        "--dump-dom",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return result.stdout or None


def fetch_search_page(marketplace, keyword, chrome=None):
    """Fetch a search results page, retrying once. Returns HTML or None."""
    url = f"https://www.{marketplace}/s?k={quote_plus(keyword)}"
    if marketplace == "amazon.in":
        url += f"&rh={quote_plus(PCT_OFF_NODE_IN)}"
    for attempt in range(2):
        page = chrome_fetch(url, chrome=chrome)
        if page and "s-search-result" in page:
            return page
        if attempt == 0:
            time.sleep(4 + random.random() * 3)
    return None


def extract_deals(page_html, marketplace, keyword):
    """Parse search results into deal dicts (only items with a real discount)."""
    soup = BeautifulSoup(page_html, "html.parser")
    deals = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        asin = card.get("data-asin", "")
        if not asin:
            continue

        title_el = card.select_one("h2 span")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            continue

        price_el = card.select_one(".a-price:not(.a-text-price) .a-offscreen")
        mrp_el = card.select_one(".a-price.a-text-price .a-offscreen")
        price = parse_price(price_el.get_text() if price_el else None)
        mrp = parse_price(mrp_el.get_text() if mrp_el else None)
        if not price or not mrp or mrp <= price:
            continue

        discount = round((1 - price / mrp) * 100)

        img_el = card.select_one("img.s-image")
        # star icon variants: -small/-mini seen across layouts; aria-label as fallback
        rating_el = card.select_one(
            "i.a-icon-star-small span.a-icon-alt, i.a-icon-star span.a-icon-alt, "
            "i.a-icon-star-mini span.a-icon-alt")
        rating_text = rating_el.get_text(strip=True) if rating_el else None
        if not rating_text:
            aria_el = card.select_one('[aria-label*="out of 5 stars"]')
            rating_text = aria_el.get("aria-label") if aria_el else None
        rating = None
        if rating_text:
            m = re.match(r"([\d.]+)", rating_text)
            rating = float(m.group(1)) if m else None

        # count lives in aria-label ("2,300 ratings"); visible text is abbreviated ("(2.3K)")
        reviews = None
        for el in card.select("a[aria-label], span.a-size-base.s-underline-text"):
            source = el.get("aria-label") or el.get_text(strip=True)
            m = re.fullmatch(r"([\d,]+)\s*ratings?", source.strip())
            if m:
                reviews = int(m.group(1).replace(",", ""))
                break

        deals.append({
            "asin": asin,
            "title": title,
            "price": price,
            "mrp": mrp,
            "discount": discount,
            "rating": rating,
            "reviews": reviews,
            "image": img_el["src"] if img_el and img_el.has_attr("src") else "",
            "url": f"https://www.{marketplace}/dp/{asin}",
            "keyword": keyword,
        })
    return deals


def search_deals(marketplace, keyword, min_discount=0, chrome=None):
    """Search one keyword and return deals >= min_discount, best first."""
    page = fetch_search_page(marketplace, keyword, chrome=chrome)
    if page is None:
        return None
    deals = [d for d in extract_deals(page, marketplace, keyword)
             if d["discount"] >= min_discount]
    deals.sort(key=lambda d: d["discount"], reverse=True)
    return deals


def fetch_product(marketplace, asin, chrome=None):
    """Fetch a product detail page and extract rich info. Returns dict or None."""
    url = f"https://www.{marketplace}/dp/{asin}"
    page = chrome_fetch(url, chrome=chrome, budget_ms=25000)
    if not page or "productTitle" not in page:
        return None
    soup = BeautifulSoup(page, "html.parser")

    def text_of(sel):
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else None

    title = text_of("#productTitle")
    price = parse_price(text_of(
        "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen"
    ) or text_of(".a-price:not(.a-text-price) .a-offscreen"))
    mrp = parse_price(text_of(".basisPrice .a-offscreen")
                      or text_of(".a-price.a-text-price .a-offscreen"))

    rating = None
    rating_el = soup.select_one("#acrPopover")
    if rating_el and rating_el.has_attr("title"):
        m = re.match(r"([\d.]+)", rating_el["title"])
        rating = float(m.group(1)) if m else None

    reviews = None
    reviews_txt = text_of("#acrCustomerReviewText")
    if reviews_txt:
        m = PRICE_RE.search(reviews_txt)
        reviews = int(m.group(0).replace(",", "")) if m else None

    bullets = [li.get_text(" ", strip=True)
               for li in soup.select("#feature-bullets li span.a-list-item")][:8]

    images = []
    img_el = soup.select_one("#landingImage")
    if img_el:
        if img_el.has_attr("data-a-dynamic-image"):
            try:
                images = list(json.loads(img_el["data-a-dynamic-image"]).keys())[:6]
            except (ValueError, TypeError):
                pass
        if not images and img_el.has_attr("src"):
            images = [img_el["src"]]

    availability = text_of("#availability span")

    discount = None
    if price and mrp and mrp > price:
        discount = round((1 - price / mrp) * 100)

    return {
        "asin": asin,
        "title": title,
        "price": price,
        "mrp": mrp,
        "discount": discount,
        "rating": rating,
        "reviews": reviews,
        "bullets": bullets,
        "images": images,
        "availability": availability,
        "url": url,
    }
