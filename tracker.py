"""
price_tracker.py
Canadian Apple/Monitor Price Tracker
Targets: Mac Studio M4 Max/M3 Ultra, Mac Mini M4/M4 Pro, MacBook Air 13-inch M3/M4/M5, MacBook Pro 14-inch M4/M4 Pro/M5/M5 Pro, 5K Monitors
Verified retailers only: Apple CA, Apple Refurb CA, Best Buy CA, Amazon CA, Canada Computers, Memory Express, Newegg CA, Staples CA
69 product-retailer entries tracked as of July 2026

Setup:
    pip install playwright python-dotenv
    playwright install chromium

Run:
    python tracker.py

Cron (Mac Mini - twice daily at 7am and 7pm):
    # In Terminal only: use a backslash before the space in the directory name
    00 07,19 * * * /usr/bin/python3 /Users/jainishsoni/Documents/ClaudeProjects_Documents/Price Tracker/tracker.py >> /Users/jainishsoni/Documents/ClaudeProjects_Documents/Price Tracker/cron.log 2>&1

Cron (Lenovo Ubuntu server - planned Q1 2027):
    00 07,19 * * * /usr/bin/python3 /home/jainish/price-tracker/tracker.py >> /home/jainish/price-tracker/cron.log 2>&1
"""

import json
import sqlite3
import subprocess
import logging
import os
import re
import random
import requests
import time
from html.parser import HTMLParser
from datetime import datetime
from typing import NamedTuple
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

load_dotenv()

# -------------------------
# CONFIG
# -------------------------

PRODUCTS_FILE = "products.json"
DB_FILE = "price_history.db"
LOG_FILE = "tracker.log"

_file_handler = logging.FileHandler(LOG_FILE)
_file_handler.setLevel(logging.DEBUG)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[_file_handler, _console_handler],
)
log = logging.getLogger(__name__)


class ScrapeResult(NamedTuple):
    price: float | None
    matched_text: str | None = None


RAM_STORAGE_NAME_FALLBACK_RETAILERS = frozenset({"newegg_ca", "asus_ca"})


# -------------------------
# DATABASE
# -------------------------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_records (
            product_id TEXT PRIMARY KEY,
            product_name TEXT,
            retailer TEXT,
            lowest_price REAL,
            last_price REAL,
            last_checked TEXT,
            url TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            price REAL,
            retailer TEXT,
            checked_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_stored_low(product_id: str) -> float | None:
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT lowest_price FROM price_records WHERE product_id = ?",
        (product_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def update_price_record(product: dict, price: float):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_FILE)

    existing = conn.execute(
        "SELECT lowest_price FROM price_records WHERE product_id = ?",
        (product["id"],)
    ).fetchone()

    if existing:
        new_low = min(existing[0], price)
        conn.execute("""
            UPDATE price_records
            SET last_price = ?, lowest_price = ?, last_checked = ?
            WHERE product_id = ?
        """, (price, new_low, now, product["id"]))
    else:
        conn.execute("""
            INSERT INTO price_records (product_id, product_name, retailer, lowest_price, last_price, last_checked, url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (product["id"], product["name"], product["retailer"], price, price, now, product["url"]))

    conn.execute("""
        INSERT INTO price_history (product_id, price, retailer, checked_at)
        VALUES (?, ?, ?, ?)
    """, (product["id"], price, product["retailer"], now))

    conn.commit()
    conn.close()


# -------------------------
# NOTIFICATIONS
# -------------------------

def send_notification(product_name: str, price: float, previous_low: float | None, url: str, retailer: str):
    retailer_label = retailer.replace("_", " ").title()
    if previous_low is None or previous_low == price:
        price_line = f"First recorded: ${price:,.2f} CAD"
    else:
        price_line = f"${price:,.2f} CAD (was ${previous_low:,.2f} CAD) at {retailer_label}"

    message = f"{product_name}: {price_line}\n{url}"

    script = (
        f"display notification {json.dumps(message)} "
        f'with title "Price Drop Alert" '
        f"subtitle {json.dumps(retailer_label)}"
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning(
                f"Notification failed for {product_name}: "
                f"{result.stderr.strip() or 'osascript returned non-zero exit code'}"
            )
        else:
            log.info(f"Notification sent for {product_name}")
    except Exception as e:
        log.warning(f"Notification failed for {product_name}: {e}")

    try:
        subprocess.run(["open", url], check=False)
    except Exception as e:
        log.warning(f"Could not open URL for {product_name}: {e}")


# -------------------------
# NOTION
# -------------------------

_notion_debug_logged = False


def log_to_notion(
    product_name: str,
    retailer: str,
    price: float,
    is_new_low: bool,
    checked_at: str,
    url: str,
    ram_gb: int | None = None,
    storage_gb: int | None = None,
):
    global _notion_debug_logged

    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_database_id = os.getenv("NOTION_DATABASE_ID", "")

    if not notion_token or not notion_database_id:
        log.warning("Notion credentials not configured. Skipping Notion log.")
        return

    checked_at_str = datetime.fromisoformat(str(checked_at)).astimezone().isoformat()

    try:
        properties = {
            "Product": {"title": [{"text": {"content": product_name}}]},
            "Retailer": {"rich_text": [{"text": {"content": retailer}}]},
            "Price (CAD)": {"number": price},
            "Is New Low": {"checkbox": is_new_low},
            "Checked At": {"date": {"start": checked_at_str}},
            "URL": {"url": url},
        }
        if ram_gb is not None:
            properties["RAM (GB)"] = {"number": ram_gb}
        if storage_gb is not None:
            properties["Storage (GB)"] = {"number": storage_gb}
        payload = {
            "parent": {"database_id": notion_database_id},
            "properties": properties,
        }
        if not _notion_debug_logged:
            log.info(f"Notion payload (first call): {json.dumps(payload)}")
            _notion_debug_logged = True
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        log.info(f"Notion log created for {product_name}")
    except Exception as e:
        log.error(f"Notion log failed for {product_name}: {e}")


# -------------------------
# UTILITIES
# -------------------------

def parse_price(text: str) -> float | None:
    """Extract a numeric price from messy price strings like '$1,299.00 CAD'"""
    if not text:
        return None
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if match:
        try:
            val = float(match.group().replace(",", ""))
            if val > 10:
                return val
        except ValueError:
            pass
    return None


def extract_ram_storage(title_or_tile_text: str) -> tuple[int | None, int | None]:
    """Extract RAM and storage GB from Apple-style spec strings in titles or tile text."""
    if not title_or_tile_text:
        return None, None

    text = (
        title_or_tile_text.replace("\u2011", "-")
        .replace("\xa0", " ")
        .replace(",", " ")
    )

    ram = None
    storage = None

    ram_match = re.search(r"(\d+)\s*GB\s*memory", text, re.I)
    if ram_match:
        ram = int(ram_match.group(1))

    storage_match = re.search(r"(\d+)\s*GB\s*storage", text, re.I)
    if storage_match:
        storage = int(storage_match.group(1))
    else:
        storage_tb_match = re.search(r"(\d+)\s*TB\s*storage", text, re.I)
        if storage_tb_match:
            storage = int(storage_tb_match.group(1)) * 1024

    return ram, storage


def _product_name_ram_storage_fallback_ok(product_name: str, retailer: str) -> bool:
    if retailer in RAM_STORAGE_NAME_FALLBACK_RETAILERS:
        return True
    name_l = product_name.lower()
    monitor_hints = (
        "monitor", "5k", "proart", "ultrafine", "rog strix",
        "benq", "pa27jcv", "xg27jcg", "27md5kl", "ma270s", "pd2730s",
    )
    return any(hint in name_l for hint in monitor_hints)


def resolve_ram_storage_for_notion(
    product: dict,
    retailer: str,
    matched_text: str | None,
) -> tuple[int | None, int | None]:
    ram_gb, storage_gb = extract_ram_storage(matched_text or "")
    if ram_gb is not None or storage_gb is not None:
        return ram_gb, storage_gb
    if _product_name_ram_storage_fallback_ok(product["name"], retailer):
        return extract_ram_storage(product["name"])
    return None, None


def validate_price_sanity(product_name: str, price: float) -> bool:
    name = product_name.lower()

    if "mac studio m4 max" in name:
        if price < 2699:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Studio M4 Max minimum $2,699)"
            )
            return False
    if "mac studio m3 ultra" in name:
        if price < 3999:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Studio M3 Ultra minimum $3,999)"
            )
            return False
    if "mac mini m4 pro" in name:
        if price < 1599:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Mini M4 Pro minimum $1,599)"
            )
            return False
    elif "mac mini m4" in name:
        if price < 1099 or price > 1500:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Mini M4 expected $1,099 to $1,500)"
            )
            return False

    if "mac studio" in name:
        if price < 2500:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Studio minimum $2,500)"
            )
            return False
    if "mac mini" in name:
        if price < 800:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Mini minimum $800)"
            )
            return False
    if "mac pro" in name:
        if price < 5000:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(Mac Pro minimum $5,000)"
            )
            return False
    if "macbook" in name:
        if price < 800:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(MacBook minimum $800)"
            )
            return False

    monitor_hints = (
        "monitor", "5k", "proart", "ultrafine", "rog strix",
        "benq", "pa27jcv", "xg27jcg", "27md5kl", "ma270s", "pd2730s",
    )
    if any(hint in name for hint in monitor_hints):
        if price < 200:
            log.warning(
                f"Price sanity check rejected ${price:,.2f} for {product_name} "
                f"(monitor minimum $200)"
            )
            return False
    return True


PRICE_KEYS = {
    "saleprice", "regularprice", "currentprice", "customerprice",
    "finalprice", "listprice", "price", "unitprice", "displayprice",
    "currentpriceraw", "raw_amount",
}
TITLE_KEYS = {"name", "title", "productname", "shortdescription", "description"}
SELLER_KEYS = {"soldby", "sellername", "seller", "vendor", "merchantname"}


def _coerce_price(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if value > 10 else None
    if isinstance(value, str):
        return parse_price(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "raw_amount", "price"):
            if key in value:
                return _coerce_price(value[key])
    return None


def _extract_offers_from_json(obj, offers: list | None = None) -> list[dict]:
    if offers is None:
        offers = []

    if isinstance(obj, dict):
        keys_lower = {k.lower(): k for k in obj.keys()}
        price = None
        for pk in PRICE_KEYS:
            if pk in keys_lower:
                price = _coerce_price(obj[keys_lower[pk]])
                if price:
                    break

        title = ""
        for tk in TITLE_KEYS:
            if tk in keys_lower and obj[keys_lower[tk]]:
                title = str(obj[keys_lower[tk]])
                break

        seller = ""
        for sk in SELLER_KEYS:
            if sk in keys_lower and obj[keys_lower[sk]]:
                seller = str(obj[keys_lower[sk]])
                break

        marketplace = obj.get("isMarketplace", obj.get("is_marketplace", obj.get("marketplace")))

        if price:
            offers.append({
                "price": price,
                "title": title,
                "seller": seller,
                "marketplace": marketplace,
                "raw": obj,
            })

        for value in obj.values():
            _extract_offers_from_json(value, offers)

    elif isinstance(obj, list):
        for item in obj:
            _extract_offers_from_json(item, offers)

    return offers


def _collect_offers_from_page(page, captured: list[tuple[str, object]]) -> list[dict]:
    offers = []
    for _, data in captured:
        _extract_offers_from_json(data, offers)

    try:
        scripts = page.locator('script[type="application/ld+json"]').all()
        for script in scripts:
            text = script.text_content()
            if not text:
                continue
            data = json.loads(text)
            _extract_offers_from_json(data, offers)
    except Exception:
        pass

    return offers


def _offer_blob(offer: dict) -> str:
    return json.dumps(offer.get("raw", {}), default=str).lower() + " " + offer.get("seller", "").lower()


CATEGORY_WORDS = {"5k", "cad", "monitor", "display"}
OPTIONAL_MARKETING_WORDS = {"strix", "ultrafine", "proart"}
CHIP_PATTERNS = ("m3 ultra", "m4 pro", "m5 pro", "m4 max", "m5 max", "m3", "m4", "m5")
CC_SCRAPER_TIMEOUT_MS = 30000
CC_PRODUCT_LINK_RE = re.compile(r"/en/[^/]+/\d+/[^?#]+\.html", re.I)
ME_PRODUCT_LINK_RE = re.compile(r"/Products/MX\d+/?$", re.I)
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": HTTP_USER_AGENT,
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
BESTBUY_STEALTH_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)
CC_LINK_SELECTORS = [
    ".product a[href*='.html']",
    "article a[href*='.html']",
    "[class*='product'] a[href*='.html']",
    'a[href*="/en/"][href*=".html"]',
]
ME_LINK_SELECTORS = [
    'a[href*="/Products/MX"]',
    ".product-title a",
    "h3.product-name a",
]
RETAILER_BASE_URLS = {
    "bestbuy_ca": "https://www.bestbuy.ca",
    "amazon_ca": "https://www.amazon.ca",
    "canada_computers": "https://www.canadacomputers.com",
    "memory_express": "https://www.memoryexpress.com",
    "newegg_ca": "https://www.newegg.ca",
    "staples_ca": "https://www.staples.ca",
}
RETAILER_PRODUCT_LINKS = {
    "bestbuy_ca": 'a[href*="/en-ca/product/"]',
    "amazon_ca": 'div[data-component-type="s-search-result"] h2 a, div.s-result-item h2 a',
    "canada_computers": (
        '.product a[href*=".html"], '
        'article a[href*=".html"], '
        '[class*="product"] a[href*=".html"], '
        'a[href*="/en/"][href*=".html"]'
    ),
    "memory_express": (
        '.product-grid a[href*="/Products/MX"], '
        '.SearchResults .product a[href*="/Products/MX"], '
        'a[href*="/Products/MX"]'
    ),
    "newegg_ca": "a.item-title, a.item-image",
    "staples_ca": 'a[href*="/products/"], a.product_block_link',
}
RETAILER_LINK_PATTERNS = {
    "canada_computers": CC_PRODUCT_LINK_RE,
    "memory_express": ME_PRODUCT_LINK_RE,
}
RETAILER_TITLE_SELECTORS = {
    "bestbuy_ca": "h1[data-automation='product-name'], h1",
    "amazon_ca": "#productTitle, h1",
    "canada_computers": "h1, h1.page-title span, h1.product-name",
    "memory_express": "h1.ProductTitle, h1",
    "newegg_ca": "h1.product-title, #grpDescrip_h span, h1",
    "staples_ca": "h1.product-name, h1",
}


def _normalize_match_text(text: str) -> str:
    """Lowercase and collapse whitespace for title comparisons."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().lower()


def _normalize_size_term(term: str) -> str:
    """Convert screen sizes like 13-inch to bare 13 for retailer title matching."""
    term_l = term.lower().strip()
    size_match = re.fullmatch(r"(\d{2})(?:\s*-?\s*inch)?", term_l)
    if size_match:
        return size_match.group(1)
    return term_l


def _term_in_title(term: str, title_l: str) -> bool:
    """Match a key term against a normalized lowercase title string."""
    term = _normalize_size_term(term)
    if term in ("m3", "m4", "m5"):
        if term not in title_l:
            return False
        return f"{term} pro" not in title_l and f"{term} max" not in title_l
    if re.fullmatch(r"\d{2}", term):
        return bool(
            re.search(
                rf"\b{re.escape(term)}(?:\.\d+)?(?:\"|-?\s*inch)?\b",
                title_l,
            )
        )
    words = term.split()
    if len(words) > 1:
        pattern = r"\b" + r"\s+".join(re.escape(word) for word in words) + r"\b"
        return bool(re.search(pattern, title_l))
    return term in title_l


def extract_key_terms(product_name: str) -> list[str]:
    """Extract required match terms from a products.json display name."""
    base = product_name.split(" - ")[0].strip()
    base_l = base.lower()

    terms: list[str] = []

    for chip in CHIP_PATTERNS:
        if chip in base_l:
            terms.append(chip)
            break

    size_match = re.search(r"(\d{2})\s*-?\s*inch", base_l)
    if size_match:
        terms.append(size_match.group(1))

    if "macbook air" in base_l:
        terms.append("macbook air")
    elif "macbook pro" in base_l:
        terms.append("macbook pro")
    elif "mac studio" in base_l:
        terms.append("mac studio")
    elif "mac mini" in base_l:
        terms.append("mac mini")

    cleaned = base_l
    for chip in CHIP_PATTERNS:
        cleaned = cleaned.replace(chip, " ")
    cleaned = re.sub(r"\d{2}\s*-?\s*inch", " ", cleaned)
    for word in CATEGORY_WORDS:
        cleaned = re.sub(rf"\b{re.escape(word)}\b", " ", cleaned)

    for token in re.findall(r"[a-z0-9]+", cleaned):
        if token in CATEGORY_WORDS:
            continue
        if token in OPTIONAL_MARKETING_WORDS:
            continue
        if token in ("inch", "macbook", "air", "pro", "studio", "mini", "apple", "mac"):
            continue
        if re.fullmatch(r"m\d+", token):
            continue
        if len(token) >= 2:
            terms.append(token)

    seen = set()
    unique: list[str] = []
    for term in terms:
        normalized = _normalize_size_term(term.lower())
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def key_terms_match_title(key_terms: list[str], page_title: str) -> bool:
    if not key_terms:
        return True
    if not page_title:
        return False

    title_l = _normalize_match_text(page_title)
    return all(_term_in_title(term, title_l) for term in key_terms)


def _read_page_product_title(page, retailer: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    selectors = RETAILER_TITLE_SELECTORS.get(retailer, "h1")
    for selector in selectors.split(", "):
        try:
            el = page.locator(selector.strip()).first
            text = el.text_content(timeout=3000)
            if text and text.strip():
                cleaned = text.strip()
                if cleaned not in seen:
                    seen.add(cleaned)
                    parts.append(cleaned)
        except Exception:
            continue
    try:
        doc_title = page.title()
        if doc_title:
            cleaned = doc_title.strip()
            if cleaned not in seen:
                parts.append(cleaned)
    except Exception:
        pass
    return " ".join(parts)


def _verify_page_title(page, product: dict, retailer: str) -> bool:
    page_title = _read_page_product_title(page, retailer)
    log.info(f"[{retailer}] Found page title: {page_title}")

    key_terms = extract_key_terms(product["name"])
    if not key_terms_match_title(key_terms, page_title):
        log.warning(
            f"[{retailer}] Title mismatch for {product['name']}: "
            f"required {key_terms}, found '{page_title}'"
        )
        return False
    return True


def _is_search_url(url: str) -> bool:
    url_l = url.lower()
    search_hints = (
        "/s?", "search", "catalogsearch", "keywords=", "query=",
        "/en-ca/search", "/Products/Search", "SearchResult",
    )
    return any(hint in url_l for hint in search_hints)


def _is_bestbuy_listing_url(url: str) -> bool:
    """Best Buy category/shop pages list products but are not search URLs."""
    url_l = url.lower()
    return (
        "bestbuy.ca" in url_l
        and "/en-ca/product/" not in url_l
        and ("/category/" in url_l or "/shop/" in url_l)
    )


def _is_product_href(retailer: str, href: str) -> bool:
    pattern = RETAILER_LINK_PATTERNS.get(retailer)
    if not pattern:
        return True
    path = urlparse(href).path if "://" in href else href
    return bool(pattern.search(path))


def _cc_remaining_ms(deadline: float, default_ms: int) -> int:
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        return 0
    return min(default_ms, remaining)


def _cc_wait_ms(page, deadline: float, desired_ms: int) -> None:
    if time.monotonic() >= deadline:
        return
    wait_ms = _cc_remaining_ms(deadline, desired_ms)
    if wait_ms > 0:
        page.wait_for_timeout(wait_ms)


def _cc_title_from_url(url: str) -> str:
    slug = urlparse(url).path.rsplit("/", 1)[-1]
    slug = re.sub(r"\.html.*$", "", slug, flags=re.I)
    return slug.replace("-", " ").strip()


def _cc_candidate_title(anchor) -> str:
    title = (anchor.text_content() or "").strip()
    title = re.sub(r"\s+", " ", title)
    if len(title) >= 8 and not title.lower().startswith("online"):
        return title

    for xpath in (
        "xpath=ancestor::article[1]",
        "xpath=ancestor::*[contains(@class,'product')][1]",
    ):
        try:
            parent = anchor.locator(xpath).first
            if parent.count() == 0:
                continue
            parent_text = (parent.text_content() or "").strip()
            parent_text = re.sub(r"\s+", " ", parent_text)
            if len(parent_text) >= 8:
                return parent_text
        except Exception:
            continue
    return title


def _collect_cc_search_candidates(page, base_url: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for selector in CC_LINK_SELECTORS:
        try:
            anchors = page.locator(selector).all()
            for anchor in anchors[:50]:
                href = anchor.get_attribute("href")
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                full_url = urljoin(base_url, href.split("?")[0])
                if full_url in seen_urls:
                    continue
                if not _is_product_href("canada_computers", full_url):
                    continue
                seen_urls.add(full_url)
                title = _cc_candidate_title(anchor) or _cc_title_from_url(full_url)
                candidates.append((full_url, title))
        except Exception:
            continue

    if not candidates:
        for url in _collect_links_from_html(page, "canada_computers", base_url):
            clean_url = url.split("?")[0]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)
            candidates.append((clean_url, _cc_title_from_url(clean_url)))

    return candidates


def _prefilter_cc_candidates(
    candidates: list[tuple[str, str]],
    key_terms: list[str],
) -> list[tuple[str, str]]:
    return [
        (url, title)
        for url, title in candidates
        if key_terms_match_title(key_terms, title)
    ]


def _cc_scrape_product_page(page, product: dict, deadline: float) -> tuple[float | None, str | None]:
    if time.monotonic() >= deadline:
        return None, None

    page.set_default_timeout(_cc_remaining_ms(deadline, 8000))
    page_title = _read_page_product_title(page, "canada_computers")
    log.info(f"[canada_computers] Found page title: {page_title}")

    key_terms = extract_key_terms(product["name"])
    if not key_terms_match_title(key_terms, page_title):
        log.warning(
            f"[canada_computers] Title mismatch for {product['name']}: "
            f"required {key_terms}, found '{page_title}'"
        )
        return None, None

    price = _extract_dom_price(page, "canada_computers")
    if price is not None:
        return price, page_title
    return None, page_title


def _staples_js_failed_title(page_title: str) -> bool:
    title = (page_title or "").strip().lower()
    return not title or title == "www.staples.ca"


def _log_search_page_debug(page, retailer: str):
    try:
        html = page.content()
        log.info(f"[{retailer}] Search page HTML preview: {html[:500]}")
    except Exception as e:
        log.warning(f"[{retailer}] Could not read search page HTML: {e}")


def _collect_links_from_html(page, retailer: str, base_url: str) -> list[str]:
    links: list[str] = []
    seen = set()
    try:
        html = page.content()
        if retailer == "canada_computers":
            pattern = re.compile(
                r'href="((?:https://www\.canadacomputers\.com)?/en/[^/]+/\d+/[^"?#]+\.html[^"]*)"',
                re.I,
            )
        elif retailer == "memory_express":
            pattern = re.compile(r'href="(/Products/MX\d+)"', re.I)
        else:
            return links
        for match in pattern.finditer(html):
            full_url = urljoin(base_url, match.group(1))
            if full_url in seen:
                continue
            if not _is_product_href(retailer, full_url):
                continue
            seen.add(full_url)
            links.append(full_url)
            if len(links) >= 15:
                break
    except Exception:
        pass
    return links


def _collect_links_by_selectors(
    page,
    base_url: str,
    selectors: list[str],
    retailer: str | None = None,
) -> list[str]:
    links: list[str] = []
    seen = set()
    for selector in selectors:
        try:
            anchors = page.locator(selector).all()
            for anchor in anchors[:50]:
                href = anchor.get_attribute("href")
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                full_url = urljoin(base_url, href)
                if full_url in seen:
                    continue
                if retailer and not _is_product_href(retailer, full_url):
                    continue
                seen.add(full_url)
                links.append(full_url)
                if len(links) >= 15:
                    return links
            if links:
                return links
        except Exception:
            continue
    return links


def _scrape_retailer_search(
    page,
    product: dict,
    retailer: str,
    link_selectors: list[str],
    url_patterns: list[str],
    seller_ok,
    search_wait_ms: int,
    timeout_ms: int = 60000,
    max_links: int = 10,
) -> float | None:
    url = product["url"]
    base_url = RETAILER_BASE_URLS.get(retailer, url)

    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(search_wait_ms)

    links = _collect_links_by_selectors(page, base_url, link_selectors, retailer)
    if not links:
        _log_search_page_debug(page, retailer)
        log.warning(f"[{retailer}] No product links found on search page for {product['name']}")
        return None

    for link in links[:max_links]:
        try:
            link_captured = _goto_with_json_capture(
                page, link, url_patterns, wait_ms=2000,
            )
            price = _scrape_product_page(
                page, product, retailer, link_captured, seller_ok,
            )
            if price is not None:
                return price
        except PlaywrightTimeoutError:
            continue
        except Exception as e:
            log.warning(f"[{retailer}] Error checking {link}: {e}")
            continue

    log.warning(f"[{retailer}] No matching product page found for {product['name']}")
    return None


def _create_bestbuy_stealth_context(browser):
    context = browser.new_context(
        user_agent=HTTP_USER_AGENT,
        locale="en-CA",
        timezone_id="America/Toronto",
        viewport={"width": 1920, "height": 1080},
        extra_http_headers=HTTP_HEADERS,
    )
    context.add_init_script(BESTBUY_STEALTH_SCRIPT)
    return context


def _scrape_bestbuy_playwright(page, product: dict) -> float | None:
    browser = page.context.browser
    if browser is None:
        log.warning(f"[bestbuy_ca] No browser available for {product['name']}")
        return None

    context = _create_bestbuy_stealth_context(browser)
    bb_page = context.new_page()
    try:
        return _scrape_with_title_verification(
            bb_page,
            product,
            retailer="bestbuy_ca",
            url_patterns=[
                "bestbuy.ca/ecomm-api", "bestbuy.ca/api/",
                "bestbuy.ca/graphql", "/product/",
            ],
            seller_ok=_bestbuy_seller_ok,
            wait_ms=4000,
            wait_until="networkidle",
        )
    finally:
        context.close()


class _AmazonPriceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.prices: list[float] = []
        self._capture_price = False

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        attrs_d = dict(attrs)
        cls = attrs_d.get("class", "")
        if "a-offscreen" in cls:
            self._capture_price = True

    def handle_data(self, data):
        if not self._capture_price:
            return
        price = parse_price(data)
        if price:
            self.prices.append(price)
        self._capture_price = False


def _amazon_blocked(html: str) -> bool:
    html_l = html.lower()
    block_hints = (
        "captcha", "robot check", "enter the characters you see",
        "sorry, we just need to make sure you're not a robot",
        "to discuss automated access to amazon",
    )
    return any(hint in html_l for hint in block_hints)


def _extract_amazon_result_title(chunk: str) -> str:
    title_patterns = (
        r'<span[^>]*class="[^"]*a-text-normal[^"]*"[^>]*>(.*?)</span>',
        r'<h2[^>]*>.*?<span[^>]*class="[^"]*a-size-medium[^"]*"[^>]*>(.*?)</span>',
        r'<span[^>]*class="[^"]*a-size-medium[^"]*"[^>]*>(.*?)</span>',
    )
    for pattern in title_patterns:
        match = re.search(pattern, chunk, re.I | re.S)
        if not match:
            continue
        title = re.sub(r"<[^>]+>", " ", match.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title
    return ""


def _amazon_chunk_sold_by_amazon(chunk: str) -> bool:
    chunk_l = chunk.lower()
    sold_by_amazon = (
        "sold by amazon" in chunk_l
        or "sold by amazon.ca" in chunk_l
        or 'aria-label="sold by amazon' in chunk_l
    )
    ships_from_amazon = (
        "ships from amazon" in chunk_l
        or "ships from amazon.ca" in chunk_l
    )
    if sold_by_amazon and ships_from_amazon:
        return True
    if sold_by_amazon and "amazon.ca" in chunk_l:
        return True
    return False


def _extract_amazon_result_price(chunk: str) -> float | None:
    parser = _AmazonPriceParser()
    parser.feed(chunk)
    return min(parser.prices) if parser.prices else None


def _scrape_amazon_requests(product: dict) -> float | None:
    response = requests.get(product["url"], headers=HTTP_HEADERS, timeout=30)
    if response.status_code != 200:
        log.warning(
            f"[amazon_ca] HTTP {response.status_code} for {product['name']}"
        )
        return None

    html = response.text
    if _amazon_blocked(html):
        log.warning(f"[amazon_ca] Bot detection blocked request for {product['name']}")
        return None

    chunks = re.split(r'data-component-type="s-search-result"', html)[1:]
    if not chunks:
        log.warning(f"[amazon_ca] No search results found for {product['name']}")
        return None

    key_terms = extract_key_terms(product["name"])
    for chunk in chunks[:20]:
        title = _extract_amazon_result_title(chunk)
        if not title:
            continue
        log.info(f"[amazon_ca] Found page title: {title}")
        if not key_terms_match_title(key_terms, title):
            continue
        if not _amazon_chunk_sold_by_amazon(chunk):
            continue
        price = _extract_amazon_result_price(chunk)
        if price is not None:
            return price

    log.warning(f"[amazon_ca] No matching Amazon.ca listing for {product['name']}")
    return None


def _goto_with_json_capture(
    page,
    url: str,
    url_patterns: list[str],
    wait_ms: int = 4000,
    wait_until: str = "domcontentloaded",
    pre_delay_range: tuple[int, int] | None = None,
) -> list[tuple[str, object]]:
    captured = []

    def on_response(response):
        try:
            resp_url = response.url
            if not any(pattern in resp_url for pattern in url_patterns):
                return
            if response.status != 200:
                return
            captured.append((resp_url, response.json()))
        except Exception:
            pass

    page.on("response", on_response)
    try:
        if pre_delay_range:
            page.wait_for_timeout(random.randint(pre_delay_range[0], pre_delay_range[1]))
        page.goto(url, wait_until=wait_until, timeout=60000)
        page.wait_for_timeout(wait_ms)
    finally:
        page.remove_listener("response", on_response)

    return captured


def _extract_dom_price(page, retailer: str) -> float | None:
    price_selectors = {
        "bestbuy_ca": "div[data-automation='buybox-price'], div[class*='price']",
        "amazon_ca": "#corePrice_feature_div .a-price .a-offscreen, span.a-price-whole",
        "canada_computers": "span.price, div.product-info-price span",
        "memory_express": "span.ProductPrice, div.price",
        "newegg_ca": "div.price-current, li.price-current",
        "staples_ca": "span.price-value, div.product-price",
    }
    selectors = price_selectors.get(retailer, "span[class*='price'], div[class*='price']")
    for selector in selectors.split(", "):
        try:
            el = page.locator(selector.strip()).first
            text = el.text_content(timeout=3000)
            price = parse_price(text)
            if price:
                return price
        except Exception:
            continue
    return None


def _pick_lowest_offer(offers: list[dict], seller_ok) -> float | None:
    prices = []
    for offer in offers:
        if not seller_ok(offer):
            continue
        prices.append(offer["price"])
    return min(prices) if prices else None


def _collect_search_product_links(page, retailer: str, base_url: str) -> list[str]:
    selector = RETAILER_PRODUCT_LINKS.get(retailer, "a[href]")
    links: list[str] = []
    seen = set()
    try:
        anchors = page.locator(selector).all()
        for anchor in anchors[:50]:
            href = anchor.get_attribute("href")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            full_url = urljoin(base_url, href)
            if full_url in seen:
                continue
            if not _is_product_href(retailer, full_url):
                continue
            seen.add(full_url)
            links.append(full_url)
            if len(links) >= 15:
                break
    except Exception:
        pass
    if not links and retailer in ("canada_computers", "memory_express"):
        links = _collect_links_from_html(page, retailer, base_url)
    return links


def _scrape_product_page(
    page,
    product: dict,
    retailer: str,
    captured: list[tuple[str, object]],
    seller_ok,
) -> float | None:
    if not _verify_page_title(page, product, retailer):
        return None

    offers = _collect_offers_from_page(page, captured)
    price = _pick_lowest_offer(offers, seller_ok)
    if price is not None:
        return price
    return _extract_dom_price(page, retailer)


def _scrape_with_title_verification(
    page,
    product: dict,
    retailer: str,
    url_patterns: list[str],
    seller_ok,
    wait_ms: int = 4000,
    wait_until: str = "domcontentloaded",
    pre_delay_range: tuple[int, int] | None = None,
    skip_initial_navigation: bool = False,
) -> float | None:
    url = product["url"]
    base_url = RETAILER_BASE_URLS.get(retailer, url)

    if skip_initial_navigation and _is_search_url(url):
        captured: list[tuple[str, object]] = []
    else:
        captured = _goto_with_json_capture(
            page, url, url_patterns, wait_ms, wait_until, pre_delay_range,
        )

    if not _is_search_url(url) and not (
        retailer == "bestbuy_ca" and _is_bestbuy_listing_url(url)
    ):
        return _scrape_product_page(page, product, retailer, captured, seller_ok)

    links = _collect_search_product_links(page, retailer, base_url)
    if not links:
        if retailer in ("canada_computers", "memory_express"):
            _log_search_page_debug(page, retailer)
        log.warning(f"[{retailer}] No product links found on search page for {product['name']}")
        return None

    consecutive_bad_titles = 0
    for link in links:
        try:
            link_captured = _goto_with_json_capture(
                page, link, url_patterns, wait_ms=2000,
                wait_until=wait_until, pre_delay_range=pre_delay_range,
            )
            if retailer == "staples_ca":
                page_title = page.title() or ""
                if _staples_js_failed_title(page_title):
                    consecutive_bad_titles += 1
                    if consecutive_bad_titles >= 3:
                        log.warning("[staples_ca] Page JS not rendering, skipping")
                        return None
                    continue
                consecutive_bad_titles = 0
            price = _scrape_product_page(
                page, product, retailer, link_captured, seller_ok,
            )
            if price is not None:
                return price
        except PlaywrightTimeoutError:
            continue
        except Exception as e:
            log.warning(f"[{retailer}] Error checking {link}: {e}")
            continue

    log.warning(f"[{retailer}] No matching product page found for {product['name']}")
    return None


def _bestbuy_seller_ok(offer: dict) -> bool:
    if offer.get("marketplace") is True:
        return False
    blob = _offer_blob(offer)
    if "marketplace" in blob and "best buy" not in blob:
        return False
    return "best buy" in blob or "sold and shipped by best buy" in blob


def _amazon_seller_ok(offer: dict) -> bool:
    blob = _offer_blob(offer)
    sold_by_amazon = "sold by" in blob and "amazon" in blob
    ships_from_amazon = "ships from" in blob and "amazon" in blob
    return sold_by_amazon and ships_from_amazon


def _newegg_seller_ok(offer: dict) -> bool:
    blob = _offer_blob(offer)
    if "marketplace" in blob and "newegg" not in blob:
        return False
    return "newegg" in blob


# -------------------------
# SCRAPERS
# -------------------------

def scrape_bestbuy_ca(page, product: dict) -> ScrapeResult:
    """
    Best Buy CA: Playwright with stealth on category, shop, or product pages.
    Only records price if sold by Best Buy.
    """
    try:
        price = _scrape_bestbuy_playwright(page, product)
        return ScrapeResult(price)
    except PlaywrightTimeoutError:
        log.warning(f"[bestbuy_ca] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[bestbuy_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


def scrape_amazon_ca(page, product: dict) -> ScrapeResult:
    """
    Amazon CA: Fetches search HTML via requests. Only records price if sold by Amazon.ca.
    """
    try:
        price = _scrape_amazon_requests(product)
        return ScrapeResult(price)
    except requests.RequestException as e:
        log.warning(f"[amazon_ca] Request failed for {product['name']}: {e}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[amazon_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


APPLE_REFURB_GOTO_TIMEOUT_MS = 60000
APPLE_REFURB_SHELL_WAIT_MS = 20000
APPLE_REFURB_SHELL_SELECTORS = (
    "section.rf-refurb-filter-content",
    "#content",
    "main",
)
APPLE_REFURB_JS_RENDER_WAIT_MS = 8000
APPLE_REFURB_TILE_SELECTORS = (
    "li.rf-refurb-product-tile",
    "[data-autom='refurb-product-tile']",
    ".rf-refurb-product",
    "li[class*='refurb']",
)
APPLE_REFURB_TITLE_SELECTORS = "h3, [data-autom='productName']"
APPLE_REFURB_TILE_PRICE_LOCATORS = (
    "span.rc-prices-currentprice",
    "[class*='price']",
    "span[aria-label*='$']",
    "span[class*='current']",
)


def _read_apple_refurb_tile_title(tile) -> str | None:
    for selector in APPLE_REFURB_TITLE_SELECTORS.split(", "):
        try:
            locator = tile.locator(selector.strip())
            if locator.count() == 0:
                continue
            text = locator.first.text_content(timeout=2000)
            if text and text.strip():
                return text.strip()
        except Exception:
            continue
    return None


def _read_apple_refurb_tile_full_text(tile) -> str:
    try:
        text = tile.text_content(timeout=2000)
        if text and text.strip():
            return re.sub(r"\s+", " ", text.strip())
    except Exception:
        pass
    return ""


def _apple_refurb_matched_text_for_price(
    tiles: list[tuple[object, str]],
    key_terms: list[str],
    lowest_price: float,
) -> str | None:
    for tile, title in tiles:
        matched, failed = apple_refurb_analyze_tile_terms(key_terms, title)
        if failed:
            continue
        price = _extract_apple_refurb_tile_price(tile)
        if price != lowest_price:
            continue
        full_text = _read_apple_refurb_tile_full_text(tile)
        return full_text or title
    return None


def _read_apple_refurb_tile_price(tile) -> tuple[str, float | None]:
    for selector in APPLE_REFURB_TILE_PRICE_LOCATORS:
        try:
            locator = tile.locator(selector)
            if locator.count() == 0:
                continue
            el = locator.first
            if selector == "span[aria-label*='$']":
                raw_text = el.get_attribute("aria-label") or el.text_content(timeout=2000) or ""
            else:
                raw_text = el.text_content(timeout=2000) or ""
            raw_text = raw_text.strip()
            if not raw_text:
                continue
            log.info(f"[apple_refurb_ca] Raw price text ({selector}): {raw_text}")
            price = parse_price(raw_text)
            if price:
                return raw_text, price
        except Exception:
            continue
    return "", None


def _extract_apple_refurb_tile_price(tile) -> float | None:
    _, price = _read_apple_refurb_tile_price(tile)
    return price


def _read_apple_refurb_tile(tile) -> tuple[str, float | None] | None:
    title = _read_apple_refurb_tile_title(tile)
    if not title:
        return None
    return title, _extract_apple_refurb_tile_price(tile)


def _wait_for_apple_refurb_shell(page) -> bool:
    combined = ", ".join(APPLE_REFURB_SHELL_SELECTORS)
    try:
        page.wait_for_selector(combined, timeout=APPLE_REFURB_SHELL_WAIT_MS)
        return True
    except PlaywrightTimeoutError:
        return False


def _find_apple_refurb_tile_selector(page) -> str | None:
    for selector in APPLE_REFURB_TILE_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return selector
        except Exception:
            continue
    return None


def _collect_apple_refurb_listings(page, tile_selector: str) -> list[tuple[str, float | None]]:
    listings: list[tuple[str, float | None]] = []
    seen_titles: set[str] = set()

    for tile in page.locator(tile_selector).all():
        row = _read_apple_refurb_tile(tile)
        if not row:
            continue
        title, price = row
        if title in seen_titles:
            continue
        seen_titles.add(title)
        listings.append((title, price))

    return listings


APPLE_REFURB_FAMILY_TERMS = (
    "macbook air",
    "macbook pro",
    "mac studio",
    "mac mini",
)
APPLE_REFURB_COMPOUND_CHIPS = ("m3 ultra", "m4 pro", "m5 pro", "m4 max", "m5 max")
APPLE_REFURB_PLAIN_CHIPS = ("m3", "m4", "m5")


def _normalize_apple_refurb_title(title: str) -> str:
    text = title.replace("\xa0", " ")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = text.lower()
    text = re.sub(r"(\d{2})\s*-?\s*inch", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _apple_refurb_chip_matches(chip: str, title_l: str) -> bool:
    if chip in APPLE_REFURB_COMPOUND_CHIPS:
        pattern = rf"\b(?:apple\s+)?{re.escape(chip)}(?:\s+chip)?\b"
        return bool(re.search(pattern, title_l))

    if chip in APPLE_REFURB_PLAIN_CHIPS:
        pattern = rf"\b(?:apple\s+)?{re.escape(chip)}(?:\s+chip)?\b"
        if not re.search(pattern, title_l):
            return False
        for suffix in ("pro", "max", "ultra"):
            if re.search(rf"\b{re.escape(chip)}\s+{suffix}\b", title_l):
                return False
        return True

    return chip in title_l


def _apple_refurb_term_matches(term: str, title_l: str) -> bool:
    term = _normalize_size_term(term)
    if term in APPLE_REFURB_COMPOUND_CHIPS or term in APPLE_REFURB_PLAIN_CHIPS:
        return _apple_refurb_chip_matches(term, title_l)
    if re.fullmatch(r"\d{2}", term):
        return bool(re.search(rf"\b{re.escape(term)}\b", title_l))
    if term in APPLE_REFURB_FAMILY_TERMS:
        pattern = r"\b" + r"\s+".join(re.escape(word) for word in term.split()) + r"\b"
        return bool(re.search(pattern, title_l))
    return term in title_l


def apple_refurb_analyze_tile_terms(
    key_terms: list[str],
    tile_title: str,
) -> tuple[list[str], list[str]]:
    if not key_terms or not tile_title:
        return [], list(key_terms)

    title_l = _normalize_apple_refurb_title(tile_title)
    matched: list[str] = []
    failed: list[str] = []
    for term in key_terms:
        if _apple_refurb_term_matches(term, title_l):
            matched.append(term)
        else:
            failed.append(term)
    return matched, failed


def apple_refurb_key_terms_match(key_terms: list[str], tile_title: str) -> bool:
    if not key_terms:
        return True
    if not tile_title:
        return False

    matched, failed = apple_refurb_analyze_tile_terms(key_terms, tile_title)
    log.debug(
        f"[apple_refurb_ca] Tile '{tile_title[:80]}': matched={matched}, failed={failed}"
    )
    return not failed


def _apple_refurb_tile_dedup_key(title: str) -> str:
    key = _normalize_apple_refurb_title(title)
    key = re.sub(r"^refurbished\s+", "", key)
    key = re.sub(r"^apple\s+", "", key)
    key = re.sub(r"[,\-]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def _collect_deduped_apple_refurb_tiles(
    page,
    tile_selector: str,
) -> list[tuple[object, str]]:
    tiles_with_titles: list[tuple[object, str]] = []
    seen_exact: set[str] = set()
    seen_dedup: set[str] = set()
    duplicates_removed = 0

    for tile in page.locator(tile_selector).all():
        title = _read_apple_refurb_tile_title(tile)
        if not title:
            continue
        if title in seen_exact:
            duplicates_removed += 1
            continue
        dedup_key = _apple_refurb_tile_dedup_key(title)
        if dedup_key in seen_dedup:
            duplicates_removed += 1
            continue
        seen_exact.add(title)
        seen_dedup.add(dedup_key)
        tiles_with_titles.append((tile, title))

    if duplicates_removed:
        log.warning(
            f"[apple_refurb_ca] Removed {duplicates_removed} duplicate tile(s) before matching."
        )
    return tiles_with_titles


def _apple_refurb_outcome_from_stats(
    any_term_matched: bool,
    full_matches: int,
    matching_prices: list[float],
    product_name: str,
) -> tuple[list[float], str | None]:
    if full_matches == 0:
        if not any_term_matched:
            log.info(
                f"[apple_refurb_ca] {product_name}: OUT_OF_STOCK - no tiles matched any key "
                f"terms. Likely sold out, category may have redirected to a generic browse page."
            )
            return [], "out_of_stock"
        log.info(
            f"[apple_refurb_ca] {product_name}: NO_MATCH - tiles present but none matched "
            f"all key terms."
        )
        return [], "no_match"

    if not matching_prices:
        log.warning(
            f"[apple_refurb_ca] Matching refurb tiles found for {product_name} "
            f"but no price could be extracted"
        )
        return [], "no_price"

    return matching_prices, None


def _match_apple_refurb_tiles(
    tiles: list[tuple[object, str]],
    key_terms: list[str],
    product_name: str,
) -> tuple[list[float], str | None]:
    matching_prices: list[float] = []
    full_matches = 0
    any_term_matched = False

    for tile, title in tiles:
        matched, failed = apple_refurb_analyze_tile_terms(key_terms, title)
        if matched:
            any_term_matched = True
        log.info(f"[apple_refurb_ca] Listing: {title}")
        if failed:
            continue
        full_matches += 1
        price = _extract_apple_refurb_tile_price(tile)
        if price is not None:
            matching_prices.append(price)

    return _apple_refurb_outcome_from_stats(
        any_term_matched, full_matches, matching_prices, product_name,
    )


def scrape_apple_refurb_ca(page, product: dict) -> ScrapeResult:
    """
    Apple Certified Refurbished CA: scrape refurb tiles, match listings
    by key terms from the product name, and return the lowest matching price.
    """
    try:
        page.set_default_timeout(APPLE_REFURB_GOTO_TIMEOUT_MS)
        page.goto(
            product["url"],
            wait_until="domcontentloaded",
            timeout=APPLE_REFURB_GOTO_TIMEOUT_MS,
        )

        if not _wait_for_apple_refurb_shell(page):
            log.warning(
                f"[apple_refurb_ca] Refurb page shell did not load for {product['name']}, "
                f"continuing after JS render wait"
            )

        page.wait_for_timeout(APPLE_REFURB_JS_RENDER_WAIT_MS)

        tile_selector = _find_apple_refurb_tile_selector(page)
        if not tile_selector:
            try:
                html = page.content()
                log.warning(
                    f"[apple_refurb_ca] No refurb tiles found for {product['name']}. "
                    f"HTML preview: {html[:300]}"
                )
            except Exception as e:
                log.warning(
                    f"[apple_refurb_ca] No refurb tiles found for {product['name']}. "
                    f"Could not read page HTML: {e}"
                )
            return ScrapeResult(None)

        key_terms = extract_key_terms(product["name"])
        tiles = _collect_deduped_apple_refurb_tiles(page, tile_selector)
        if not tiles:
            log.info(f"[apple_refurb_ca] No refurb listings found for {product['name']}")
            return ScrapeResult(None)

        matching_prices, outcome = _match_apple_refurb_tiles(
            tiles, key_terms, product["name"],
        )
        if outcome:
            return ScrapeResult(None)

        lowest = min(matching_prices)
        matched_text = _apple_refurb_matched_text_for_price(
            tiles, key_terms, lowest,
        )
        log.info(
            f"[apple_refurb_ca] {product['name']}: "
            f"{len(matching_prices)} matching listing(s). Lowest: ${lowest:,.2f}"
        )
        return ScrapeResult(lowest, matched_text=matched_text)

    except PlaywrightTimeoutError:
        log.warning(f"[apple_refurb_ca] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[apple_refurb_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


def _extract_apple_chip(product_name: str) -> str | None:
    name_l = product_name.lower()
    if not any(
        marker in name_l
        for marker in ("macbook air", "macbook pro", "mac studio", "mac mini")
    ):
        return None
    for chip in CHIP_PATTERNS:
        if chip in name_l:
            return chip
    return None


def _normalize_apple_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().lower()


def _read_apple_primary_product_text(page) -> str:
    for selector in ("h1", "[data-autom='productName']", ".form-selector-title"):
        try:
            el = page.locator(selector).first
            text = el.text_content(timeout=3000)
            if text and text.strip():
                return text.strip()
        except Exception:
            continue
    try:
        doc_title = page.title()
        if doc_title:
            return doc_title.strip()
    except Exception:
        pass
    return ""


def _read_apple_page_product_text(page) -> str:
    parts: list[str] = []
    try:
        doc_title = page.title()
        if doc_title:
            parts.append(doc_title.strip())
    except Exception:
        pass

    primary = _read_apple_primary_product_text(page)
    if primary and primary not in parts:
        parts.append(primary)

    return " ".join(parts)


def _apple_model_no_longer_sold_new(product_name: str) -> bool:
    name_l = product_name.lower()
    chip = _extract_apple_chip(product_name)
    if not chip:
        return False
    if "macbook air" in name_l and chip in ("m3", "m4"):
        return True
    if "macbook pro" in name_l and chip in ("m4", "m4 pro"):
        return True
    return False


def _apple_chip_matches(product_name: str, page_text: str) -> bool:
    chip = _extract_apple_chip(product_name)
    if not chip:
        return True
    return chip.lower() in _normalize_apple_text(page_text)


def _verify_apple_chip(page, product: dict) -> bool:
    chip = _extract_apple_chip(product["name"])
    if not chip:
        return True

    page_text = _read_apple_page_product_text(page)
    log.info(f"[apple_ca] Found page title: {page_text}")

    if not _apple_chip_matches(product["name"], page_text):
        if _apple_model_no_longer_sold_new(product["name"]):
            log.warning(
                f"[apple_ca] Model no longer sold new by Apple: {product['name']}. "
                "Consider removing from watchlist or monitoring refurb only."
            )
        else:
            log.warning(
                f"[apple_ca] Chip mismatch for {product['name']}: "
                f"required '{chip}', found '{page_text}'"
            )
        return False
    return True


def _is_apple_generic_buy_page(url: str) -> bool:
    url_l = url.rstrip("/").lower()
    return url_l.endswith("/mac-mini") or url_l.endswith("/mac-studio")


def _apple_chip_link_fragment(product_name: str, chip: str) -> str | None:
    name_l = product_name.lower()
    if "mac mini" in name_l and chip == "m4":
        return "/mac-mini/m4-chip"
    if "mac mini" in name_l and chip == "m4 pro":
        return "/mac-mini/m4-pro-chip"
    if "mac studio" in name_l and chip == "m4 max":
        return "/mac-studio/m4-max-chip"
    if "mac studio" in name_l and chip == "m3 ultra":
        return "/mac-studio/m3-ultra-chip"
    return None


def _apple_chip_on_config_page(url: str, product_name: str, chip: str) -> bool:
    fragment = _apple_chip_link_fragment(product_name, chip)
    if not fragment:
        return True
    url_l = url.lower()
    if fragment not in url_l:
        return False
    if chip == "m4" and "m4-pro" in url_l:
        return False
    return True


def _apple_chip_has_text_label(product_name: str, chip: str) -> str:
    name_l = product_name.lower()
    if chip == "m4" and "mac mini" in name_l:
        return "M4 Chip"
    if chip == "m4 pro":
        return "M4 Pro"
    if chip == "m4 max":
        return "M4 Max"
    if chip == "m3 ultra":
        return "M3 Ultra"
    return chip.upper()


def _resolve_apple_chip_config_page(page, product: dict) -> None:
    chip = _extract_apple_chip(product["name"])
    if not chip:
        return

    if _apple_chip_on_config_page(page.url, product["name"], chip):
        return

    fragment = _apple_chip_link_fragment(product["name"], chip)
    if not fragment:
        return

    log.info(
        f"[apple_ca] Short chip URL redirected to generic buy page for {product['name']}. "
        f"Requested: {product['url']} Final: {page.url}"
    )
    log.info(f"[apple_ca] Page title after redirect: {page.title()}")

    try:
        links = page.locator('a[href*="/buy-mac/"]').all()
        for link in links:
            href = link.get_attribute("href") or ""
            href_l = href.lower()
            if fragment not in href_l:
                continue
            if chip == "m4" and "m4-pro" in href_l:
                continue
            target = urljoin("https://www.apple.com", href)
            page.goto(target, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            log.info(
                f"[apple_ca] Opened chip config page for {product['name']}: "
                f"{page.url} title={page.title()}"
            )
            return
    except Exception as e:
        log.warning(f"[apple_ca] Could not follow chip config link for {product['name']}: {e}")


def _price_from_apple_tile(tile) -> float | None:
    for price_sel in (
        "span.as-price-currentprice",
        "span.rc-prices-currentprice",
        ".rc-prices-currentprice",
        "[data-autom='price']",
        "div.rc-prices",
    ):
        try:
            locator = tile.locator(price_sel)
            if locator.count() == 0:
                continue
            price = parse_price(locator.first.text_content(timeout=2000) or "")
            if price:
                return price
        except Exception:
            continue
    return None


def _extract_apple_config_price(page, product: dict) -> float | None:
    chip = _extract_apple_chip(product["name"])
    if not chip:
        return None

    label = _apple_chip_has_text_label(product["name"], chip)
    price_selectors = (
        "section, li, article, div, button, a, label, h2, h3, h4, [role='radio']"
    )

    try:
        tile = page.locator(price_selectors).filter(has_text=label).first
        if tile.count() > 0:
            if chip == "m4" and "mac mini" in product["name"].lower():
                tile_text = tile.text_content(timeout=2000) or ""
                if re.search(r"m4\s+pro", tile_text, re.I):
                    tiles = page.locator(price_selectors).filter(has_text=re.compile(r"M4\s+Chip", re.I))
                    for i in range(tiles.count()):
                        candidate = tiles.nth(i)
                        candidate_text = candidate.text_content(timeout=1000) or ""
                        if not re.search(r"m4\s+pro", candidate_text, re.I):
                            tile = candidate
                            break
            price = _price_from_apple_tile(tile)
            if price:
                return price
    except Exception:
        pass

    return None


def _extract_apple_price(page, product: dict | None = None) -> float | None:
    if product:
        chip = _extract_apple_chip(product["name"])
        if chip and _apple_chip_link_fragment(product["name"], chip):
            if not _apple_chip_on_config_page(page.url, product["name"], chip):
                config_price = _extract_apple_config_price(page, product)
                if config_price is not None:
                    return config_price

    selectors = (
        "span.rc-prices-currentprice, "
        "[data-autom='price'], "
        ".as-price-currentprice, "
        "div[class*='price']"
    )
    for selector in selectors.split(", "):
        try:
            el = page.locator(selector.strip()).first
            text = el.text_content(timeout=5000)
            price = parse_price(text)
            if price:
                return price
        except Exception:
            continue
    return None


def scrape_apple_ca(page, product: dict) -> ScrapeResult:
    """
    Apple Store CA: Official retail price. Useful as baseline.
    """
    try:
        page.goto(product["url"], wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        _resolve_apple_chip_config_page(page, product)

        page_text = _read_apple_page_product_text(page)
        if not _verify_apple_chip(page, product):
            return ScrapeResult(None)

        price = _extract_apple_price(page, product)
        if price is None:
            return ScrapeResult(None)
        return ScrapeResult(price, matched_text=page_text)

    except PlaywrightTimeoutError:
        log.warning(f"[apple_ca] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[apple_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


def scrape_canada_computers(page, product: dict) -> ScrapeResult:
    """
    Canada Computers: Playwright search with JS rendering. Direct retailer.
    """
    deadline = time.monotonic() + (CC_SCRAPER_TIMEOUT_MS / 1000)
    previous_timeout = CC_SCRAPER_TIMEOUT_MS
    try:
        search_url = product["url"]
        base_url = RETAILER_BASE_URLS["canada_computers"]
        key_terms = extract_key_terms(product["name"])

        if time.monotonic() >= deadline:
            log.warning(
                f"[canada_computers] Timed out after {CC_SCRAPER_TIMEOUT_MS // 1000}s "
                f"for {product['name']}"
            )
            return ScrapeResult(None)

        page.set_default_timeout(_cc_remaining_ms(deadline, CC_SCRAPER_TIMEOUT_MS))
        page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=_cc_remaining_ms(deadline, CC_SCRAPER_TIMEOUT_MS),
        )
        _cc_wait_ms(page, deadline, 4000)

        loaded_url = page.url or ""
        page_title = page.title() or ""
        log.info(
            f"[canada_computers] Search page title: {page_title}"
        )
        log.info(
            f"[canada_computers] Loaded URL: {loaded_url[:200]}"
        )

        if "404" in page_title:
            log.warning(
                f"[canada_computers] Search page returned 404 for {product['name']}: "
                f"{search_url}"
            )
            _log_search_page_debug(page, "canada_computers")
            return ScrapeResult(None)

        candidates = _collect_cc_search_candidates(page, base_url)
        if not candidates:
            _log_search_page_debug(page, "canada_computers")
            log.warning(
                f"[canada_computers] No product links found on search page for {product['name']}"
            )
            return ScrapeResult(None)

        filtered = _prefilter_cc_candidates(candidates, key_terms)
        log.info(
            f"[canada_computers] Pre-filtered {len(candidates)} search result(s) to "
            f"{len(filtered)} for {product['name']} (key terms: {key_terms})"
        )
        if not filtered:
            log.warning(
                f"[canada_computers] No search results matched key terms for {product['name']}"
            )
            return ScrapeResult(None)

        for link, listing_title in filtered:
            if time.monotonic() >= deadline:
                log.warning(
                    f"[canada_computers] Timed out after {CC_SCRAPER_TIMEOUT_MS // 1000}s "
                    f"for {product['name']}"
                )
                return ScrapeResult(None)
            try:
                nav_timeout = _cc_remaining_ms(deadline, CC_SCRAPER_TIMEOUT_MS)
                if nav_timeout <= 0:
                    log.warning(
                        f"[canada_computers] Timed out after {CC_SCRAPER_TIMEOUT_MS // 1000}s "
                        f"for {product['name']}"
                    )
                    return ScrapeResult(None)
                page.set_default_timeout(nav_timeout)
                page.goto(
                    link,
                    wait_until="domcontentloaded",
                    timeout=nav_timeout,
                )
                _cc_wait_ms(page, deadline, 2000)
                price, page_product_title = _cc_scrape_product_page(
                    page, product, deadline,
                )
                if price is not None:
                    matched_text = page_product_title or listing_title
                    return ScrapeResult(price, matched_text=matched_text)
            except PlaywrightTimeoutError:
                if time.monotonic() >= deadline:
                    log.warning(
                        f"[canada_computers] Timed out after {CC_SCRAPER_TIMEOUT_MS // 1000}s "
                        f"for {product['name']}"
                    )
                    return ScrapeResult(None)
                continue
            except Exception as e:
                log.warning(f"[canada_computers] Error checking {link}: {e}")
                continue

        log.warning(
            f"[canada_computers] No matching product page found for {product['name']}"
        )
        return ScrapeResult(None)

    except PlaywrightTimeoutError:
        log.warning(
            f"[canada_computers] Timed out after {CC_SCRAPER_TIMEOUT_MS // 1000}s "
            f"for {product['name']}"
        )
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[canada_computers] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)
    finally:
        page.set_default_timeout(previous_timeout)


def scrape_memory_express(page, product: dict) -> ScrapeResult:
    """
    Memory Express: Playwright search (Cloudflare bypass). Direct retailer.
    """
    try:
        price = _scrape_retailer_search(
            page,
            product,
            retailer="memory_express",
            link_selectors=ME_LINK_SELECTORS,
            url_patterns=["memoryexpress.com", "/api/", "Search", ".json"],
            seller_ok=lambda _offer: True,
            search_wait_ms=4000,
        )
        return ScrapeResult(price)
    except PlaywrightTimeoutError:
        log.warning(f"[memory_express] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[memory_express] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


def scrape_newegg_ca(page, product: dict) -> ScrapeResult:
    """
    Newegg CA: Only records price if sold by Newegg Canada directly, not marketplace sellers.
    """
    try:
        price = _scrape_with_title_verification(
            page,
            product,
            retailer="newegg_ca",
            url_patterns=["newegg.ca/api", "newegg.ca/Common", "newegg.ca/Product", "newegg.ca/p/"],
            seller_ok=_newegg_seller_ok,
            wait_ms=4000,
        )
        return ScrapeResult(price)
    except PlaywrightTimeoutError:
        log.warning(f"[newegg_ca] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[newegg_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


def scrape_staples_ca(page, product: dict) -> ScrapeResult:
    """
    Staples CA: Direct retailer. No marketplace filtering needed.
    """
    try:
        page.goto(product["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        if _staples_js_failed_title(page.title() or ""):
            log.warning("[staples_ca] JS not rendering on search page, skipping")
            return ScrapeResult(None)

        price = _scrape_with_title_verification(
            page,
            product,
            retailer="staples_ca",
            url_patterns=["staples.ca/api", "staples.ca/search", "staples.ca/product", ".json"],
            seller_ok=lambda _offer: True,
            wait_ms=4000,
            skip_initial_navigation=True,
        )
        return ScrapeResult(price)
    except PlaywrightTimeoutError:
        log.warning(f"[staples_ca] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[staples_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


ASUS_PRICE_SELECTORS = (
    "span.product-price",
    "[class*='price']",
    "span[data-bind*='price']",
    ".formatted-price",
)


def _extract_asus_model_number(product_name: str) -> str | None:
    base = product_name.split(" - ")[0]
    for token in re.findall(r"[A-Za-z0-9]+", base):
        token_u = token.upper()
        if token_u in ("PA27JCV", "XG27JCG"):
            return token_u
    return None


def _parse_asus_price_text(text: str) -> float | None:
    if not text:
        return None
    prices: list[float] = []
    for match in re.finditer(r"\$[\d,]+(?:\.\d{2})?", text):
        price = parse_price(match.group())
        if price:
            prices.append(price)
    if prices:
        return min(prices)
    return None


def _extract_asus_ldjson_price(page) -> float | None:
    try:
        for script in page.locator('script[type="application/ld+json"]').all():
            raw = script.text_content()
            if not raw:
                continue
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers")
                if isinstance(offers, dict):
                    offers = [offers]
                if not isinstance(offers, list):
                    continue
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    price = _coerce_price(offer.get("price"))
                    if price:
                        return price
    except Exception:
        pass
    return None


def _extract_asus_price(page, product_name: str) -> float | None:
    candidates: list[float] = []
    for selector in ASUS_PRICE_SELECTORS:
        try:
            locator = page.locator(selector.strip())
            count = locator.count()
            for idx in range(count):
                text = locator.nth(idx).text_content(timeout=3000) or ""
                price = _parse_asus_price_text(text)
                if price and validate_price_sanity(product_name, price):
                    candidates.append(price)
        except Exception:
            continue

    if candidates:
        return min(candidates)

    ldjson_price = _extract_asus_ldjson_price(page)
    if ldjson_price and validate_price_sanity(product_name, ldjson_price):
        return ldjson_price
    return None


def _read_asus_page_title(page) -> str:
    parts: list[str] = []
    try:
        doc_title = page.title()
        if doc_title:
            parts.append(doc_title.strip())
    except Exception:
        pass

    for selector in ("h1", "[data-product-title]"):
        try:
            el = page.locator(selector).first
            text = el.text_content(timeout=3000)
            if text and text.strip():
                parts.append(text.strip())
        except Exception:
            continue

    return " ".join(parts)


def scrape_asus_ca(page, product: dict) -> ScrapeResult:
    """
    ASUS Canada official store: direct retailer product page scrape.
    """
    try:
        page.goto(product["url"], wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        page_title = _read_asus_page_title(page)
        log.info(f"[asus_ca] Found page title: {page_title}")

        model = _extract_asus_model_number(product["name"])
        if model and model not in page_title.upper():
            log.warning(
                f"[asus_ca] Title mismatch for {product['name']}: "
                f"required '{model}', found '{page_title}'"
            )
            return ScrapeResult(None)

        price = _extract_asus_price(page, product["name"])
        if price is not None:
            return ScrapeResult(price, matched_text=page_title)

        log.warning(f"[asus_ca] No price found for {product['name']}")
        return ScrapeResult(None)

    except PlaywrightTimeoutError:
        log.warning(f"[asus_ca] Timeout loading {product['name']}")
        return ScrapeResult(None)
    except Exception as e:
        log.error(f"[asus_ca] Error scraping {product['name']}: {e}")
        return ScrapeResult(None)


# bestbuy_ca intentionally omitted from RETAILER_MAP (bot detection / zero yield; scraper retained above).
RETAILER_MAP = {
    "amazon_ca": scrape_amazon_ca,
    "apple_refurb_ca": scrape_apple_refurb_ca,
    "apple_ca": scrape_apple_ca,
    "canada_computers": scrape_canada_computers,
    "memory_express": scrape_memory_express,
    "newegg_ca": scrape_newegg_ca,
    "staples_ca": scrape_staples_ca,
    "asus_ca": scrape_asus_ca,
}


# -------------------------
# MAIN RUNNER
# -------------------------

def run():
    log.info("=== Price Tracker Run Started ===")
    init_db()

    with open(PRODUCTS_FILE) as f:
        products = json.load(f)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-CA",
            timezone_id="America/Toronto"
        )
        page = context.new_page()

        for product in products:
            retailer = product.get("retailer")
            scraper = RETAILER_MAP.get(retailer)

            if not scraper:
                log.warning(f"No scraper for retailer '{retailer}' (product: {product['name']}). Skipping.")
                continue

            log.info(f"Checking: {product['name']} via {retailer}")
            scrape_result = scraper(page, product)
            price = scrape_result.price
            matched_text = scrape_result.matched_text

            if price is None:
                log.info(f"No price found for {product['name']}. Skipping update.")
                continue

            if not validate_price_sanity(product["name"], price):
                log.info(f"Skipping {product['name']} due to failed price sanity check.")
                continue

            log.info(f"Found price: ${price:,.2f} CAD for {product['name']}")

            previous_low = get_stored_low(product["id"])
            is_new_low = previous_low is None or price < previous_low
            checked_at = datetime.now().astimezone().isoformat()
            ram_gb, storage_gb = resolve_ram_storage_for_notion(
                product, retailer, matched_text,
            )

            update_price_record(product, price)

            log_to_notion(
                product_name=product["name"],
                retailer=retailer,
                price=price,
                is_new_low=is_new_low,
                checked_at=checked_at,
                url=product["url"],
                ram_gb=ram_gb,
                storage_gb=storage_gb,
            )

            if is_new_low:
                label = "New all-time low" if previous_low is not None else "First recorded price"
                log.info(f"*** {label} for {product['name']}: ${price:,.2f} CAD ***")
                send_notification(
                    product_name=product["name"],
                    price=price,
                    previous_low=previous_low,
                    url=product["url"],
                    retailer=retailer,
                )

        context.close()
        browser.close()

    log.info("=== Price Tracker Run Complete ===")


if __name__ == "__main__":
    run()
