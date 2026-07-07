# price-tracker

Canadian price tracker for Mac Studio, MacBook Air, MacBook Pro, and 5K monitors.
Verified retailers only. No Facebook Marketplace, Kijiji, Craigslist, or third-party scalpers.

## Verified Sources

| Retailer | Key | Seller Verification |
|---|---|---|
| Apple Store CA | `apple_ca` | Official listings only |
| Apple Certified Refurb CA | `apple_refurb_ca` | All listings Apple-certified |
| Best Buy CA | `bestbuy_ca` | **Paused** - bot detection, zero yield; scraper code retained for possible re-enable |
| Amazon CA | `amazon_ca` | "Ships from Amazon.ca / Sold by Amazon.ca" only |
| Canada Computers | `canada_computers` | Direct retailer |
| Memory Express | `memory_express` | Direct retailer |
| Newegg CA | `newegg_ca` | Sold by Newegg Canada directly only |
| Staples CA | `staples_ca` | Direct retailer |
| ASUS Canada | `asus_ca` | Direct retailer, official ASUS Canada store, no marketplace filtering needed |

## Products Tracked (69 entries)

| Product | Retailers |
|---|---|
| Mac Studio M4 Max / M3 Ultra | Apple (new + refurb), Canada Computers, Memory Express, Newegg, Staples |
| Mac Mini M4 / M4 Pro | Apple (new + refurb), Canada Computers, Memory Express, Newegg, Staples |
| MacBook Air 13-inch M3/M4/M5 | Apple (new + refurb), Canada Computers, Memory Express, Newegg, Staples |
| MacBook Pro 14-inch M4/M4 Pro/M5/M5 Pro | Apple (new + refurb), Canada Computers, Staples |
| ASUS ProArt PA27JCV 5K | Amazon, Newegg, ASUS Canada |
| ASUS ROG Strix 5K XG27JCG | Amazon, Canada Computers, Newegg, ASUS Canada |
| LG UltraFine 5K 27MD5KL | Amazon |
| BenQ MA270S 5K | Amazon |
| BenQ PD2730S 5K | Amazon, Newegg |

## Known Scraper Status (July 2026)

**Working**
- `apple_ca`
- `apple_refurb_ca` (timeout fix applied)
- `newegg_ca` (monitors working)
- `asus_ca` (new)

**Paused (not dispatched)**
- `bestbuy_ca` (bot detection, zero yield after category-page migration; scraper code kept for possible re-enable)

**Not working**
- `amazon_ca` (bot detection)
- `canada_computers` (URL/JS issues)
- `memory_express` (Cloudflare)
- `staples_ca` (JS not rendering)

Best Buy CA is paused (removed from `products.json` and `RETAILER_MAP`) due to bot detection and zero successful checks. Amazon CA is intentionally not being pursued further due to bot detection. Check those retailers manually when notified by alerts from other retailers.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Optional: add NOTION_TOKEN and NOTION_DATABASE_ID to .env for Notion logging
```

## Run Manually

```bash
python tracker.py
```

## Add / Remove Products (without editing JSON manually)

```bash
# Interactive: guided prompts
python add_product.py

# List all tracked products
python add_product.py --list

# Non-interactive: all flags
python add_product.py \
  --id dell_u2725d_amazon \
  --name "Dell UltraSharp U2725D 5K - Amazon CA" \
  --retailer amazon_ca \
  --url "https://www.amazon.ca/..." \
  --notes "27-inch 5K IPS USB-C"

# Remove a product
python add_product.py --remove dell_u2725d_amazon
```

Valid retailer keys: `amazon_ca`, `apple_ca`, `apple_refurb_ca`, `canada_computers`, `memory_express`, `newegg_ca`, `staples_ca`, `asus_ca` (`bestbuy_ca` paused, not addable via CLI)

## Schedule (cron)

```bash
crontab -e
```

Add to run at 7 AM and 7 PM daily (replace `/path/to/price-tracker` with your clone):

```
00 07,19 * * * /usr/bin/python3 /path/to/price-tracker/tracker.py >> /path/to/price-tracker/cron.log 2>&1
```

If the project path contains spaces, escape them in the crontab line (backslash before each space).

---

## Cursor Prompt

Paste this into Cursor Composer (Cmd+I) at the start of any session working on this project:

```
You are helping me maintain and extend price-tracker, a Python price monitoring tool for Canadian Apple/monitor retailers.

PROJECT CONTEXT:
- tracker.py: main script. Playwright (headless Chromium) scrapes product pages, SQLite stores price history, macOS notifications on new all-time lows, optional Notion logging.
- products.json: watchlist of 69 product-retailer pairs. No target_price field - alerts fire on any new all-time low only.
- add_product.py: CLI tool to interactively add/remove/list products without editing JSON manually.
- price_history.db: auto-created SQLite with two tables: price_records (current low per product) and price_history (every run logged).
- .env: holds optional NOTION_TOKEN and NOTION_DATABASE_ID. Copy from .env.example. Never commit .env.
- Project path: clone directory (e.g. `~/price-tracker`)

VERIFIED RETAILERS AND THEIR VERIFICATION LOGIC:
- bestbuy_ca: PAUSED - not dispatched (bot detection, zero yield; scraper code retained)
- amazon_ca: only record price if buy-box shows "Sold by Amazon.ca" and "Ships from Amazon.ca"
- newegg_ca: only record price if sold by Newegg Canada directly (not Newegg marketplace sellers)
- apple_ca, apple_refurb_ca, canada_computers, memory_express, staples_ca, asus_ca: direct retailers, no marketplace filtering needed

SCRAPER STATUS:
- Working: apple_ca, apple_refurb_ca, newegg_ca, asus_ca
- Paused: bestbuy_ca (removed from watchlist; bot detection)
- Not working: amazon_ca (bot detection, not being pursued further), canada_computers, memory_express, staples_ca

CODING RULES:
- No em dashes anywhere in code or comments
- Every scraper must be wrapped in try/except so one failure does not crash the full run
- Use page.wait_for_timeout(2000) after every page.goto() to allow JS to render
- parse_price() is the single shared price extraction utility - never duplicate it
- All prices are CAD

CURRENT TASK:
[Describe what you need - e.g. "The Newegg scraper is returning None - here is the log output: ..." or "Add a Memory Express scraper for this URL: ..."]
```

---

## File Structure

```
Price Tracker/
  tracker.py          # Main scraper and alert engine
  add_product.py      # CLI tool to manage products.json
  products.json       # Watchlist (69 entries)
  requirements.txt    # Python dependencies
  .env                # Your secrets (never share or upload)
  .env.example        # Template
  .cursorignore       # Keeps .env and logs out of Cursor indexing
  price_history.db    # Auto-created SQLite DB
  tracker.log         # Auto-created run log
  cron.log            # Auto-created cron output log
  LICENSE             # MIT license
  SECURITY.md         # Secret-handling and pre-public audit commands
```

## Making this repository public

1. **Audit for secrets** - run the commands in [SECURITY.md](SECURITY.md). The quick check:

   ```bash
   git log --all --oneline -- .env          # must be empty
   git check-ignore -v .env                 # must show .gitignore
   git log -p --all | grep 'NOTION_TOKEN='  # only placeholder lines in .env.example
   ```

2. **Review committed files** on GitHub - confirm no `.env`, database, or logs.

3. **Add/keep `LICENSE`** - MIT license is included for open distribution.

4. **GitHub Settings** - General - Danger Zone - Change visibility - Public.

Keep `.env` local forever. Rotate Notion tokens if they were ever committed.

---
