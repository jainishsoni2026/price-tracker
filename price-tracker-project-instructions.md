# Price Tracker - Maintainer Notes (Cursor)

Internal context for AI-assisted development. For setup, product list, and usage, see [README.md](README.md).

---

## File map

| File | Purpose |
|---|---|
| `tracker.py` | Scrapers, SQLite, macOS notifications, optional Notion logging |
| `products.json` | Watchlist (69 entries) |
| `add_product.py` | CLI to add/remove/list products |
| `test_refurb.py` | Unit + Playwright tests for `apple_refurb_ca` |
| `requirements.txt` | `pip install -r requirements.txt` |
| `.env` | Optional `NOTION_TOKEN`, `NOTION_DATABASE_ID` (never commit) |
| `SECURITY.md` | Secret audit commands |

---

## Seller verification (do not weaken)

| Key | Rule |
|---|---|
| `amazon_ca` | Buy-box: sold by and ships from Amazon.ca |
| `newegg_ca` | Sold by Newegg Canada only, not marketplace |
| `bestbuy_ca` | **Paused** - not in `RETAILER_MAP`; code retained |
| Direct retailers (`apple_ca`, `apple_refurb_ca`, `canada_computers`, `memory_express`, `staples_ca`, `asus_ca`) | No marketplace filter |

If seller cannot be confirmed, return `None`.

---

## Scraper status (July 2026)

| Status | Retailers |
|---|---|
| Working | `apple_ca`, `apple_refurb_ca`, `newegg_ca`, `asus_ca`, `canada_computers` |
| Paused | `bestbuy_ca` (not dispatched) |
| Not working | `amazon_ca`, `memory_express`, `staples_ca` |

`canada_computers`: migrated off dead Magento URLs; search-result pre-filter and 30s deadline enforced. Verify on live cron runs.

---

## Coding rules

1. No em dashes in code, comments, or docs.
2. Every scraper in try/except - one failure must not crash the run.
3. `page.wait_for_timeout(2000)` after `page.goto()` unless a scraper documents otherwise.
4. `parse_price()` is the only price parser.
5. All prices are CAD. No `target_price` field - alerts on new all-time lows only.
6. Return `None` when price or seller cannot be verified.

---

## Alerts and Notion

- New all-time low: macOS notification via `osascript`, then opens product URL.
- Optional Notion logging when `NOTION_TOKEN` and `NOTION_DATABASE_ID` are set in `.env`.
- `extract_ram_storage()` populates RAM/Storage on Notion when tile titles include specs.

---

## When a scraper breaks

1. Open the product URL in a browser.
2. Inspect price and seller DOM (or network JSON).
3. Update selectors in the relevant `scrape_*` function in `tracker.py`.
4. Re-run `python tracker.py` or `python test_refurb.py` for refurb.

Do not invest in `bestbuy_ca` or `amazon_ca` unless anti-bot posture changes.

---

## Cron (example)

```
00 07,19 * * * /usr/bin/python3 /path/to/price-tracker/tracker.py >> /path/to/price-tracker/cron.log 2>&1
```

The Cursor prompt block in README.md has the full session context for Composer.
