"""
Flipkart deal scraping via headless Chrome.

Flipkart's affiliate feed API is dead (directory resolves, every feed 500s),
but affiliate links are just plain product URLs with ?affid=<id>, so we scrape
their search pages — filtered to a minimum discount — the same way scraper.py
does Amazon. Class names are obfuscated and rotate, so parsing is structural:
cards are div[data-id]; price/MRP/discount are read from the card text.
"""

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

import scraper

# search facet for "X% or more" discount (percent-encoded twice, as the site does)
_DISCOUNT_FACET = "p%5B%5D=facets.discount_range_v1%255B%255D%3D{pct}%2525%2Bor%2Bmore"

RUPEE_RE = re.compile(r"₹([\d,]+)")
OFF_RE = re.compile(r"(\d{1,2})% off")
RATING_RE = re.compile(r"(\d\.\d)\s*[|·]?\s*[\d,]+\s*Ratings", re.I)


def search_deals(keyword, affid, min_discount=50, chrome=None):
    """Scrape one Flipkart search page. Returns deal dicts (same shape as Amazon's)."""
    url = (f"https://www.flipkart.com/search?q={quote_plus(keyword)}"
           f"&{_DISCOUNT_FACET.format(pct=min_discount)}")
    html = scraper.chrome_fetch(url, chrome=chrome, budget_ms=25000)
    if not html or "data-id" not in html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    deals = []
    for card in soup.select("div[data-id]"):
        pid = card.get("data-id", "")
        link = card.select_one("a[href]")
        img = card.select_one("img[src]")
        if not (pid and link):
            continue

        text = card.get_text("|", strip=True)
        prices = [float(p.replace(",", "")) for p in RUPEE_RE.findall(text)]
        m_off = OFF_RE.search(text)
        if len(prices) < 2 or not m_off:
            continue  # no struck-through MRP -> not a real discount card
        price, mrp = prices[0], prices[1]
        if mrp <= price:
            continue

        title = text.split("|", 1)[0]
        # sponsored/label rows sometimes lead the text; title is the longest early chunk
        if len(title) < 15:
            chunks = [c for c in text.split("|") if len(c) > 15 and "₹" not in c]
            title = chunks[0] if chunks else title

        m_rating = RATING_RE.search(text)
        deals.append({
            "asin": f"FK:{pid}",  # namespaced so it can never collide with Amazon
            "title": title,
            "price": price,
            "mrp": mrp,
            "discount": int(m_off.group(1)),
            "rating": float(m_rating.group(1)) if m_rating else None,
            "reviews": None,
            "image": img["src"] if img else "",
            "url": f"https://www.flipkart.com{link['href'].split('?')[0]}?affid={affid}",
            "keyword": keyword,
            "store": "flipkart",
        })
    return deals
