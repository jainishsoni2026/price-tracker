"""
add_product.py
Interactive CLI to add new products to the price tracker watchlist.

Usage:
    python add_product.py

    Or non-interactive (for scripting):
    python add_product.py --id benq_pd3220u_amazon --name "BenQ PD3220U 4K - Amazon CA" --retailer amazon_ca --url "https://www.amazon.ca/..." --notes "32-inch 4K"

    To list all tracked products:
    python add_product.py --list

    To remove a product by ID:
    python add_product.py --remove benq_pd3220u_amazon
"""

import json
import argparse
import re
import sys
from pathlib import Path

PRODUCTS_FILE = Path(__file__).parent / "products.json"

VALID_RETAILERS = [
    "apple_ca",
    "apple_refurb_ca",
    "amazon_ca",
    "canada_computers",
    "memory_express",
    "newegg_ca",
    "staples_ca",
    "asus_ca",
]


def load_products() -> list:
    if not PRODUCTS_FILE.exists():
        return []
    with open(PRODUCTS_FILE) as f:
        return json.load(f)


def save_products(products: list):
    with open(PRODUCTS_FILE, "w") as f:
        json.dump(products, f, indent=2)
    print(f"Saved {len(products)} products to {PRODUCTS_FILE}")


def slugify(text: str) -> str:
    """Convert a product name to a safe snake_case ID suggestion."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text


def list_products(products: list):
    if not products:
        print("No products tracked yet.")
        return
    print(f"\n{'ID':<45} {'Retailer':<20} Name")
    print("-" * 100)
    for p in products:
        print(f"{p['id']:<45} {p['retailer']:<20} {p['name']}")
    print(f"\nTotal: {len(products)} products\n")


def remove_product(products: list, product_id: str) -> list:
    original_count = len(products)
    products = [p for p in products if p["id"] != product_id]
    if len(products) == original_count:
        print(f"No product found with ID: {product_id}")
    else:
        print(f"Removed product: {product_id}")
    return products


def interactive_add(products: list) -> list:
    print("\n--- Add New Product to Price Tracker ---\n")
    existing_ids = {p["id"] for p in products}

    # Product name
    name = input("Product name (e.g. 'Dell UltraSharp 5K - Amazon CA'): ").strip()
    if not name:
        print("Name cannot be empty. Aborting.")
        return products

    # Suggest an ID
    suggested_id = slugify(name)
    product_id = input(f"Product ID (press Enter to use '{suggested_id}'): ").strip()
    if not product_id:
        product_id = suggested_id

    if product_id in existing_ids:
        print(f"A product with ID '{product_id}' already exists. Use --remove first if you want to replace it.")
        return products

    # Retailer
    print(f"\nValid retailers: {', '.join(VALID_RETAILERS)}")
    retailer = input("Retailer: ").strip().lower()
    if retailer not in VALID_RETAILERS:
        print(f"Unknown retailer '{retailer}'. Add it to VALID_RETAILERS in add_product.py and tracker.py if it is new.")
        confirm = input("Add anyway? (y/n): ").strip().lower()
        if confirm != "y":
            return products

    # URL
    url = input("Product URL: ").strip()
    if not url.startswith("http"):
        print("URL should start with http. Aborting.")
        return products

    # Notes (optional)
    notes = input("Notes (optional, press Enter to skip): ").strip()

    product = {
        "id": product_id,
        "name": name,
        "retailer": retailer,
        "url": url,
    }
    if notes:
        product["notes"] = notes

    print(f"\nAbout to add:\n{json.dumps(product, indent=2)}")
    confirm = input("\nConfirm? (y/n): ").strip().lower()
    if confirm == "y":
        products.append(product)
        print(f"Added: {name}")
    else:
        print("Cancelled.")

    return products


def main():
    parser = argparse.ArgumentParser(description="Manage price tracker product watchlist")
    parser.add_argument("--list", action="store_true", help="List all tracked products")
    parser.add_argument("--remove", metavar="ID", help="Remove a product by its ID")
    parser.add_argument("--id", help="Product ID (non-interactive mode)")
    parser.add_argument("--name", help="Product name (non-interactive mode)")
    parser.add_argument("--retailer", help="Retailer key (non-interactive mode)")
    parser.add_argument("--url", help="Product URL (non-interactive mode)")
    parser.add_argument("--notes", default="", help="Optional notes (non-interactive mode)")
    args = parser.parse_args()

    products = load_products()

    if args.list:
        list_products(products)
        return

    if args.remove:
        products = remove_product(products, args.remove)
        save_products(products)
        return

    # Non-interactive mode: all fields provided via flags
    if args.id and args.name and args.retailer and args.url:
        existing_ids = {p["id"] for p in products}
        if args.id in existing_ids:
            print(f"Product ID '{args.id}' already exists. Use --remove first.")
            sys.exit(1)
        product = {
            "id": args.id,
            "name": args.name,
            "retailer": args.retailer,
            "url": args.url,
        }
        if args.notes:
            product["notes"] = args.notes
        products.append(product)
        save_products(products)
        print(f"Added: {args.name}")
        return

    # Interactive mode
    products = interactive_add(products)
    save_products(products)


if __name__ == "__main__":
    main()
