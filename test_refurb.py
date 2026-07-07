"""
Test Apple Certified Refurbished scraper and matching helpers.

Run:
    cd /path/to/price-tracker
    source venv/bin/activate
    python test_refurb.py
"""

import json
import sys

from playwright.sync_api import sync_playwright

from tracker import (
    APPLE_REFURB_GOTO_TIMEOUT_MS,
    APPLE_REFURB_JS_RENDER_WAIT_MS,
    HTTP_USER_AGENT,
    PRODUCTS_FILE,
    _apple_refurb_chip_matches,
    _apple_refurb_tile_dedup_key,
    _apple_refurb_outcome_from_stats,
    _collect_deduped_apple_refurb_tiles,
    _find_apple_refurb_tile_selector,
    _normalize_apple_refurb_title,
    _read_apple_refurb_tile_price,
    _wait_for_apple_refurb_shell,
    apple_refurb_analyze_tile_terms,
    extract_key_terms,
    log,
)

REFURB_RETAILER = "apple_refurb_ca"

M5_AIR_TITLE = (
    "Refurbished 13\u2011inch MacBook Air Apple M5 chip "
    "with 10-Core CPU - Silver"
)


def run_unit_tests() -> bool:
    print("=== Unit tests (no Playwright) ===")
    all_passed = True

    normalized_m5_air = _normalize_apple_refurb_title(M5_AIR_TITLE)
    test1_ok = "13" in normalized_m5_air and "m5" in normalized_m5_air
    print(
        f"{'PASS' if test1_ok else 'FAIL'}: "
        f"normalize() contains '13' and 'm5' -> {normalized_m5_air!r}"
    )
    all_passed &= test1_ok

    test2_ok = _apple_refurb_chip_matches("m5", normalized_m5_air)
    print(
        f"{'PASS' if test2_ok else 'FAIL'}: "
        f"chip_matches('m5', normalized_title) -> {test2_ok}"
    )
    all_passed &= test2_ok

    test3_ok = not _apple_refurb_chip_matches("m5 pro", normalized_m5_air)
    print(
        f"{'PASS' if test3_ok else 'FAIL'}: "
        f"chip_matches('m5 pro', normalized_title) -> {not test3_ok} (expected False)"
    )
    all_passed &= test3_ok

    m4_pro_title = _normalize_apple_refurb_title(
        "refurbished 14-inch macbook pro apple m4 pro chip with 12-core cpu"
    )
    test4_ok = _apple_refurb_chip_matches("m4 pro", m4_pro_title)
    print(
        f"{'PASS' if test4_ok else 'FAIL'}: "
        f"chip_matches('m4 pro', m4_pro_title) -> {test4_ok}"
    )
    all_passed &= test4_ok

    studio_a = "Refurbished Apple Studio Display, Standard glass, Tilt-adjustable stand"
    studio_b = "Refurbished Studio Display - Standard glass - Tilt-adjustable stand"
    test5_ok = _apple_refurb_tile_dedup_key(studio_a) == _apple_refurb_tile_dedup_key(studio_b)
    print(
        f"{'PASS' if test5_ok else 'FAIL'}: "
        f"dedup_key collapses duplicate Studio Display titles"
    )
    all_passed &= test5_ok

    print()
    return all_passed


def load_refurb_products() -> list[dict]:
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        products = json.load(f)
    refurb_products = [
        product for product in products
        if product.get("retailer") == REFURB_RETAILER
    ]
    if not refurb_products:
        raise SystemExit(f"No {REFURB_RETAILER} entries found in {PRODUCTS_FILE}")
    return refurb_products


def analyze_tile_terms(key_terms: list[str], tile_title: str) -> tuple[list[str], list[str]]:
    return apple_refurb_analyze_tile_terms(key_terms, tile_title)


def format_result(price: float | None, outcome: str | None) -> str:
    if price is not None:
        return f"${price:,.2f} CAD"
    if outcome == "out_of_stock":
        return "OUT_OF_STOCK - No price found"
    if outcome == "no_match":
        return "NO_MATCH - No price found"
    if outcome == "no_price":
        return "NO_PRICE - No price found"
    return "No price found"


def scrape_refurb_with_debug(page, product: dict) -> tuple[float | None, str | None]:
    page.set_default_timeout(APPLE_REFURB_GOTO_TIMEOUT_MS)
    page.goto(
        product["url"],
        wait_until="domcontentloaded",
        timeout=APPLE_REFURB_GOTO_TIMEOUT_MS,
    )

    if not _wait_for_apple_refurb_shell(page):
        log.warning(
            f"[test_refurb] Page shell did not load for {product['name']}, "
            f"continuing after JS render wait"
        )

    page.wait_for_timeout(APPLE_REFURB_JS_RENDER_WAIT_MS)

    tile_selector = _find_apple_refurb_tile_selector(page)
    if not tile_selector:
        print("  No refurb tiles found on page")
        return None, None

    key_terms = extract_key_terms(product["name"])
    print(f"  Key terms: {key_terms}")

    tiles = _collect_deduped_apple_refurb_tiles(page, tile_selector)
    print(f"  Unique tiles: {len(tiles)}")

    matching_prices: list[float] = []
    full_matches = 0
    any_term_matched = False

    for tile, title in tiles:
        matched, failed = analyze_tile_terms(key_terms, title)
        if matched:
            any_term_matched = True
        if failed:
            print(f"  NO MATCH: {title[:90]}")
            print(f"    matched={matched} failed={failed}")
            continue
        full_matches += 1
        print(f"  MATCH: {title[:90]}")
        raw_text, parsed_price = _read_apple_refurb_tile_price(tile)
        print(f"    raw price text: {raw_text!r}")
        if parsed_price is not None:
            print(f"    parsed price: ${parsed_price:,.2f} CAD")
            matching_prices.append(parsed_price)
        else:
            print("    parsed price: No price found")

    _, outcome = _apple_refurb_outcome_from_stats(
        any_term_matched, full_matches, matching_prices, product["name"],
    )
    if outcome:
        return None, outcome
    return min(matching_prices), None


def run_playwright_tests(products: list[dict]) -> None:
    print(f"=== Playwright tests ({len(products)} {REFURB_RETAILER} products) ===")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HTTP_USER_AGENT,
            locale="en-CA",
            timezone_id="America/Toronto",
        )
        page = context.new_page()

        for product in products:
            print()
            print(f"{product['id']}: {product['name']}")
            log.info(f"Testing: {product['name']} ({product['id']})")
            price, outcome = scrape_refurb_with_debug(page, product)
            result = format_result(price, outcome)
            print(f"  Result: {result}")
            log.info(f"{product['id']}: {result}")

        browser.close()


def main() -> int:
    if not run_unit_tests():
        print("Unit tests failed. Skipping Playwright tests.")
        return 1

    log.info("=== Apple Refurb Test Run Started ===")
    products = load_refurb_products()
    run_playwright_tests(products)
    log.info("=== Apple Refurb Test Run Finished ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
