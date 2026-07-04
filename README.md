# 🔥 Steal Deals

A deal-hunting website for Amazon: search any product, browse heavily-discounted "steals", open a full product view (gallery, rating, features, stock), and buy on Amazon through **your affiliate link**.

Two data sources, switched automatically:

1. **Amazon Product Advertising API (PA-API 5.0)** — the official, ToS-clean source. Used as soon as you add your API keys (see below). Implemented in [paapi.py](paapi.py) with stdlib AWS SigV4 signing — no SDK needed.
2. **Headless-Chrome scraper** — zero-setup fallback so the site works today, before your API access is approved. Personal-use volume only.

Dependencies: `flask`, `beautifulsoup4`, `requests`.

## Run the website

```bash
python3 app.py
# → http://localhost:5001
```

Copy `config.example.json` to `config.json` and set your affiliate tag (config.json is gitignored so your tag stays out of the repo).

## Production deploy (Render)

Production runs in **cache mode** (`DATA_SOURCE=cache`): it serves the deal data bundled in `deals/` and never scrapes — scraping from a public site violates Amazon Associates ToS and datacenter IPs are blocked anyway. [render.yaml](render.yaml) defines the whole service; in the Render dashboard use **New → Blueprint** and pick this repo.

Refresh the live data from a machine with Chrome (your laptop):

```bash
python3 refresh_deals.py          # scrape all categories, commit + push → Render redeploys
python3 refresh_deals.py --no-push  # dry run, write deals/ only
```

Once you have PA-API keys (3 qualifying sales), set `PAAPI_ACCESS_KEY`, `PAAPI_SECRET_KEY` and change `DATA_SOURCE` to `auto` in the Render dashboard — the site switches to live official data, no code change needed.

## Daily promotion — share_deals.py

Generates the day's top-deals message (rating-filtered, honest product-page MRPs, affiliate `/go/` links) and distributes it:

```bash
python3 share_deals.py          # print + copy to clipboard (paste into WhatsApp channel)
python3 share_deals.py --post   # also auto-post to Telegram + Pinterest
```

- **WhatsApp**: no official posting API — the message lands in your clipboard, paste it into your channel. (Don't use unofficial WhatsApp automation libraries; they get numbers banned.)
- **Telegram**: create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`), create a channel, add the bot as admin, then set `telegram_bot_token` and `telegram_channel` (e.g. `"@YourChannel"`) in config.json.
- **Pinterest**: create an app at [developers.pinterest.com](https://developers.pinterest.com), generate a token with `pins:write`, set `pinterest_access_token` and `pinterest_board_id` in config.json. Top 3 deals get pinned with product images.

Daily routine: `python3 refresh_deals.py && python3 share_deals.py --post` → fresh data on the site + posts everywhere.

## Set your affiliate tag (important!)

Every "Buy on Amazon" button goes through `/go/<asin>`, which 302-redirects to Amazon with your Associates tag attached. Put your tag in [config.json](config.json):

```json
{ "affiliate_tag": "YOURTAG-21", "marketplace": "amazon.in" }
```

or set the `AMAZON_AFFILIATE_TAG` environment variable. Until you do, the placeholder `yourtag-21` is used and **you earn nothing**. Get a tag at https://affiliate-program.amazon.in (or .com for the US programme).

## Enable the official Amazon API (recommended)

1. Join Amazon Associates and make **3 qualifying sales** (Amazon's requirement for API access).
2. In Associates Central → Tools → **Product Advertising API**, request access and generate your **Access Key** and **Secret Key**.
3. Add them to `config.json`:

```json
{
  "paapi_access_key": "AKIA...",
  "paapi_secret_key": "...",
  "affiliate_tag": "YOURTAG-21"
}
```

(or set `PAAPI_ACCESS_KEY` / `PAAPI_SECRET_KEY` env vars). Restart the app — the startup banner and `/api/config` will show `Data source: Amazon PA-API (official)`.

`data_source` in config controls the behaviour: `"auto"` (default — PA-API when keys work, scraper as fallback), `"paapi"` (API only, no scraping ever), `"scrape"` (scraper only). PA-API extras: search results include real ratings/review counts, `MinSavingPercent` filtering happens server-side at Amazon, and `paapi_pages` controls how many 10-item pages are fetched per search (default 2). New accounts get 1 request/sec, 8640/day — the disk cache keeps you far below that.

## Features

- **Search** any keyword, or use the category chips (Electronics, Headphones, Mobiles, …)
- **Filters**: minimum discount (30/50/70/80%+) and sorting (discount, price, rating) — instant, no re-scrape
- **Product view**: click a card for the full detail modal — image gallery, live price vs MRP, rating & review count, availability, feature bullets (fetched from the actual product page, so the MRP there is the honest one)
- **Buy on Amazon**: opens the product on Amazon with your affiliate tag
- **Caching**: searches cached 30 min, products 60 min (`cache/` folder), so repeat visits are instant; at most 2 Chrome renders run at once and identical concurrent searches share one scrape

## Architecture

| File | Role |
|------|------|
| [app.py](app.py) | Flask server — `/api/deals`, `/api/product/<asin>`, `/go/<asin>` affiliate redirect, disk cache, source switching |
| [paapi.py](paapi.py) | Official PA-API 5.0 client — SigV4 signing, SearchItems & GetItems |
| [scraper.py](scraper.py) | Fallback scraping — headless-Chrome fetch, search & product-page parsers |
| [static/index.html](static/index.html) | Frontend SPA — search, chips, filters, deal grid, product modal |
| [steal_deals.py](steal_deals.py) | CLI version — writes a static `steal_deals.html` dashboard |
| [config.json](config.json) | Affiliate tag, marketplace, cache TTLs |

## Notes & limits

- In scraper mode, the first search for a keyword takes ~15–25s (a real Chrome render); with PA-API it's ~1s. Both are cached after that.
- In scraper mode, search-result "MRP" is often inflated by sellers — the product detail view shows the product page's own numbers, which are usually saner. PA-API prices come straight from Amazon's systems.
- Don't publish the site while it's in scraper mode — enable PA-API first (`"data_source": "paapi"` guarantees no scraping).
- Amazon Associates rules require disclosure — the footer includes the standard "As an Amazon Associate…" line. Keep it if you publish this.
