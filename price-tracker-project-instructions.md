# Price Tracker - Claude Project Instructions

## What this project is

A Python-based price monitoring pipeline for Canadian Apple hardware and 5K monitors. It scrapes verified Canadian retailers twice daily, stores price history in SQLite, and sends macOS notifications on new all-time lows. Optional Notion logging. No cloud services. No paid APIs. Runs on Mac Mini (cron) with a planned migration to a headless Lenovo Ubuntu server in Q1 2027.

**Repo location:** `~/Documents/price-tracker/` (Mac Mini)

---

## File map

| File | Purpose |
|---|---|
| `tracker.py` | Main engine: scrapes all retailers, writes to SQLite, fires macOS notifications |
| `products.json` | Watchlist of product-retailer pairs (69 entries as of July 2026) |
| `add_product.py` | CLI tool to add/remove/list products without editing JSON manually |
| `price_history.db` | Auto-created SQLite: `price_records` (current lows) + `price_history` (every run) |
| `.env` | Optional `NOTION_TOKEN` and `NOTION_DATABASE_ID` (copy from `.env.example`) |
| `requirements.txt` | Python dependencies (`pip install -r requirements.txt`) |
| `tracker.log` | Auto-created run log |
| `cron.log` | Auto-created cron output |

---

## Verified retailers and their seller verification logic

This is the most important rule in the project. A price must never be recorded from a third-party marketplace seller. Each retailer has explicit verification:

| Key | Verification required before recording price |
|---|---|
| `bestbuy_ca` | **Paused** - not dispatched. Page must contain "Sold and shipped by Best Buy" when re-enabled. |
| `amazon_ca` | Buy-box must show "Sold by Amazon.ca" AND "Ships from Amazon.ca" |
| `newegg_ca` | Must be sold by Newegg Canada directly, not a Newegg Marketplace seller |
| `apple_ca` | Official Apple Store CA - no verification needed |
| `apple_refurb_ca` | Apple Certified Refurbished CA - all listings are Apple-certified |
| `canada_computers` | Direct retailer - no verification needed |
| `memory_express` | Direct retailer - no verification needed |
| `staples_ca` | Direct retailer - no verification needed |
| `asus_ca` | Direct retailer, official ASUS Canada store, no marketplace filtering needed |

Never bypass, soften, or remove seller verification logic. If a scraper cannot confirm the seller, it must return `None`.

---

## Products tracked (July 2026)

**Mac hardware**
- Mac Studio M4 Max / M3 Ultra: Apple (new + refurb), Canada Computers, Memory Express, Newegg, Staples
- Mac Mini M4 / M4 Pro: Apple (new + refurb), Canada Computers, Memory Express, Newegg, Staples
- MacBook Air 13-inch M3/M4/M5: Apple (new + refurb), Canada Computers, Memory Express, Newegg, Staples
- MacBook Pro 14-inch M4/M4 Pro/M5/M5 Pro: Apple (new + refurb), Canada Computers, Staples (M5 Pro launched March 11 2026, from $2,999 CAD)

**5K monitors**
- ASUS ProArt PA27JCV: Amazon, Newegg, ASUS Canada
- ASUS ROG Strix XG27JCG (27-inch 5K 180Hz HDR600): Amazon, Canada Computers, Newegg, ASUS Canada
- LG UltraFine 5K 27MD5KL: Amazon
- BenQ MA270S: Amazon
- BenQ PD2730S: Amazon, Newegg

Total: 69 product-retailer entries

---

## Known scraper issues (July 2026)

**Working**
- `apple_ca`
- `apple_refurb_ca` (timeout fix applied)
- `newegg_ca` (monitors working)
- `asus_ca` (new)

**Paused (not dispatched)**
- `bestbuy_ca` (bot detection, zero yield; removed from watchlist, scraper code retained)

**Not working**
- `amazon_ca` (bot detection)
- `canada_computers` (URL/JS issues)
- `memory_express` (Cloudflare)
- `staples_ca` (JS not rendering)

Best Buy CA is paused (removed from `products.json` and `RETAILER_MAP`) due to bot detection and zero successful checks. Amazon CA is intentionally not being pursued further due to bot detection. Check those retailers manually when notified by alerts from other retailers.

---

## Standing rules (non-negotiable)

1. **No em dashes** in code, comments, strings, or output. Use "-" or rephrase.
2. **Every scraper in try/except** - one broken page must never crash the full run.
3. **`page.wait_for_timeout(2000)` after every `page.goto()`** - pages render JS dynamically.
4. **`parse_price()` is the single price extraction utility** - never duplicate price parsing logic.
5. **No target_price field** - alerts fire on new all-time lows only, not against a user-set threshold.
6. **All prices are CAD.**
7. **No fake or placeholder prices** - if a price cannot be found, return `None` and log it.

---

## How alerts work

- On every run, each product's scraped price is compared against `price_records` in SQLite.
- If the price is lower than the stored low (or it is the first time a product is seen), a macOS notification fires and the product URL opens in your browser.
- Optional: set `NOTION_TOKEN` and `NOTION_DATABASE_ID` in `.env` to log each check to a Notion database. If unset, Notion logging is skipped silently.

---

## How to add a product (without editing JSON manually)

```bash
python add_product.py              # Interactive
python add_product.py --list       # See all tracked products
python add_product.py --remove product_id_here
python add_product.py --id dell_u2725d_amazon --name "Dell U2725D 5K - Amazon CA" --retailer amazon_ca --url "https://..."
```

---

## Cron schedule

**Mac Mini (current):**
```
00 07,19 * * * /usr/bin/python3 /Users/jainish/price-tracker/tracker.py >> /Users/jainish/price-tracker/cron.log 2>&1
```

**Lenovo Ubuntu server (planned Q1 2027):**
```
00 07,19 * * * /usr/bin/python3 /home/jainish/price-tracker/tracker.py >> /home/jainish/price-tracker/cron.log 2>&1
```

---

## Planned extensions (not yet built)

- Notion database integration: write price records to a Notion database after each run, reusing the same API pattern as jd-pipeline's Role Finder database.
- Local HTML dashboard: read from SQLite and render a price history table (low priority until Lenovo server is up).

---

## What to do when a scraper breaks

CSS selectors go stale when retailers update their markup. When a scraper returns `None` unexpectedly:

1. Open the product URL in a browser.
2. Inspect the price element and the seller info element.
3. Update the CSS selectors in the relevant `scrape_*` function in `tracker.py`.
4. Re-run manually with `python tracker.py` to verify.

For `bestbuy_ca`, the scraper is paused (not dispatched) due to bot detection and zero yield; code is retained for possible re-enable. For `amazon_ca`, bot detection is the current blocker. Do not invest further effort unless the retailer changes their anti-bot posture.

The Cursor prompt in README.md has the full context needed to diagnose this efficiently.

---

## Cost

$0/month. No external APIs. No cloud services. Runs on existing Mac Mini hardware under Cursor Pro (active through May 2027).
