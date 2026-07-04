"""
Amazon Product Advertising API 5.0 client (official, ToS-clean data source).

Implements AWS Signature v4 signing with the standard library and exposes
search_deals() / fetch_product() with the same return shapes as scraper.py,
so app.py can swap between the two sources freely.

Requires PA-API credentials (Associates account with API access):
  - access key + secret key from https://webservices.amazon.in/paapi5/... console
  - your Associates partner tag (e.g. "yourtag-21")

Rate limits start at 1 request/sec and 8640/day — app.py's disk cache keeps
usage well under that.
"""

import datetime
import hashlib
import hmac
import json

import requests

# marketplace domain -> (api host, aws region, marketplace id)
MARKETPLACES = {
    "amazon.in": ("webservices.amazon.in", "eu-west-1", "www.amazon.in"),
    "amazon.com": ("webservices.amazon.com", "us-east-1", "www.amazon.com"),
    "amazon.co.uk": ("webservices.amazon.co.uk", "eu-west-1", "www.amazon.co.uk"),
    "amazon.de": ("webservices.amazon.de", "eu-west-1", "www.amazon.de"),
    "amazon.fr": ("webservices.amazon.fr", "eu-west-1", "www.amazon.fr"),
    "amazon.it": ("webservices.amazon.it", "eu-west-1", "www.amazon.it"),
    "amazon.es": ("webservices.amazon.es", "eu-west-1", "www.amazon.es"),
    "amazon.ca": ("webservices.amazon.ca", "us-east-1", "www.amazon.ca"),
    "amazon.com.au": ("webservices.amazon.com.au", "us-west-2", "www.amazon.com.au"),
    "amazon.co.jp": ("webservices.amazon.co.jp", "us-west-2", "www.amazon.co.jp"),
}

SERVICE = "ProductAdvertisingAPI"

SEARCH_RESOURCES = [
    "Images.Primary.Large",
    "ItemInfo.Title",
    "Offers.Listings.Price",
    "Offers.Listings.SavingBasis",
    "CustomerReviews.Count",
    "CustomerReviews.StarRating",
]

PRODUCT_RESOURCES = SEARCH_RESOURCES + [
    "Images.Variants.Large",
    "ItemInfo.Features",
    "Offers.Listings.Availability.Message",
]


class PaapiError(Exception):
    pass


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _sign_and_post(host, region, target, payload, access_key, secret_key, timeout=15):
    """POST a signed PA-API request. `target` e.g. 'SearchItems'."""
    body = json.dumps(payload)
    amz_target = f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{target}"
    path = f"/paapi5/{target.lower()}"

    now = datetime.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    # -- canonical request --
    headers_to_sign = {
        "content-encoding": "amz-1.0",
        "content-type": "application/json; charset=utf-8",
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-target": amz_target,
    }
    signed_headers = ";".join(headers_to_sign)
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in headers_to_sign.items())
    payload_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical_request = "\n".join([
        "POST", path, "", canonical_headers, signed_headers, payload_hash,
    ])

    # -- string to sign --
    credential_scope = f"{date_stamp}/{region}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    # -- signature --
    k = _hmac(("AWS4" + secret_key).encode(), date_stamp)
    k = _hmac(k, region)
    k = _hmac(k, SERVICE)
    k = _hmac(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()

    headers = {
        "Content-Encoding": "amz-1.0",
        "Content-Type": "application/json; charset=utf-8",
        "X-Amz-Date": amz_date,
        "X-Amz-Target": amz_target,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }

    resp = requests.post(f"https://{host}{path}", data=body,
                         headers=headers, timeout=timeout)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or "Errors" in data:
        errs = data.get("Errors") or [{"Code": resp.status_code, "Message": resp.text[:200]}]
        raise PaapiError("; ".join(f"{e.get('Code')}: {e.get('Message')}" for e in errs))
    return data


def _parse_item(item, marketplace):
    """Map a PA-API item to the deal dict shape used by the app."""
    listings = (item.get("Offers") or {}).get("Listings") or [{}]
    listing = listings[0]
    price = (listing.get("Price") or {}).get("Amount")
    saving_basis = (listing.get("SavingBasis") or {}).get("Amount")
    savings_pct = ((listing.get("Price") or {}).get("Savings") or {}).get("Percentage")

    discount = savings_pct
    if discount is None and price and saving_basis and saving_basis > price:
        discount = round((1 - price / saving_basis) * 100)

    reviews_info = item.get("CustomerReviews") or {}
    rating = (reviews_info.get("StarRating") or {}).get("Value")
    reviews = reviews_info.get("Count")

    title = ((item.get("ItemInfo") or {}).get("Title") or {}).get("DisplayValue")
    image = (((item.get("Images") or {}).get("Primary") or {}).get("Large") or {}).get("URL", "")

    return {
        "asin": item.get("ASIN"),
        "title": title,
        "price": price,
        "mrp": saving_basis,
        "discount": discount,
        "rating": float(rating) if rating is not None else None,
        "reviews": reviews,
        "image": image,
        "url": item.get("DetailPageURL") or f"https://www.{marketplace}/dp/{item.get('ASIN')}",
    }


def search_deals(marketplace, keyword, partner_tag, access_key, secret_key,
                 min_discount=0, pages=2):
    """SearchItems across up to `pages` pages; returns deals sorted best-first."""
    if marketplace not in MARKETPLACES:
        raise PaapiError(f"unsupported marketplace {marketplace}")
    host, region, marketplace_id = MARKETPLACES[marketplace]

    deals, seen = [], set()
    for page in range(1, pages + 1):
        payload = {
            "Keywords": keyword,
            "PartnerTag": partner_tag,
            "PartnerType": "Associates",
            "Marketplace": marketplace_id,
            "ItemCount": 10,
            "ItemPage": page,
            "Resources": SEARCH_RESOURCES,
        }
        if min_discount > 0:
            payload["MinSavingPercent"] = min_discount
        data = _sign_and_post(host, region, "SearchItems", payload,
                              access_key, secret_key)
        items = (data.get("SearchResult") or {}).get("Items") or []
        for raw in items:
            d = _parse_item(raw, marketplace)
            if not d["asin"] or d["asin"] in seen:
                continue
            if not d["price"] or not d["mrp"] or not d["discount"]:
                continue
            d["keyword"] = keyword
            seen.add(d["asin"])
            deals.append(d)
        if len(items) < 10:
            break

    deals = [d for d in deals if d["discount"] >= min_discount]
    deals.sort(key=lambda d: d["discount"], reverse=True)
    return deals


def fetch_product(marketplace, asin, partner_tag, access_key, secret_key):
    """GetItems for one ASIN; returns the product dict shape used by the app."""
    if marketplace not in MARKETPLACES:
        raise PaapiError(f"unsupported marketplace {marketplace}")
    host, region, marketplace_id = MARKETPLACES[marketplace]

    payload = {
        "ItemIds": [asin],
        "PartnerTag": partner_tag,
        "PartnerType": "Associates",
        "Marketplace": marketplace_id,
        "Resources": PRODUCT_RESOURCES,
    }
    data = _sign_and_post(host, region, "GetItems", payload, access_key, secret_key)
    items = (data.get("ItemsResult") or {}).get("Items") or []
    if not items:
        return None
    item = items[0]
    d = _parse_item(item, marketplace)

    listings = (item.get("Offers") or {}).get("Listings") or [{}]
    availability = ((listings[0].get("Availability") or {}).get("Message"))
    bullets = (((item.get("ItemInfo") or {}).get("Features") or {})
               .get("DisplayValues") or [])[:8]

    images = [d["image"]] if d["image"] else []
    for var in ((item.get("Images") or {}).get("Variants") or [])[:5]:
        url = (var.get("Large") or {}).get("URL")
        if url and url not in images:
            images.append(url)

    d.update({
        "bullets": bullets,
        "images": images,
        "availability": availability,
    })
    d.pop("keyword", None)
    return d
