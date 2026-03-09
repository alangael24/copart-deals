"""
Copart Deal Finder - Scrapes Copart's public search API to find the best vehicle deals in USA.
Calculates a deal score based on current bid vs estimated retail value.
"""

import httpx
import json
import time
import csv
import math
from datetime import datetime
from dataclasses import dataclass, asdict


@dataclass
class Vehicle:
    lot_number: str = ""
    year: str = ""
    make: str = ""
    model: str = ""
    trim: str = ""
    title: str = ""
    damage_type: str = ""
    secondary_damage: str = ""
    condition: str = ""
    color: str = ""
    engine: str = ""
    transmission: str = ""
    drive: str = ""
    fuel_type: str = ""
    body_style: str = ""
    odometer: str = ""
    odometer_status: str = ""
    vin: str = ""
    current_bid: float = 0.0
    high_bid: float = 0.0
    buy_now_price: float = 0.0
    estimated_retail: float = 0.0
    repair_cost: float = 0.0
    sale_date: str = ""
    sale_status: str = ""
    location: str = ""
    city: str = ""
    state: str = ""
    title_type: str = ""
    title_group: str = ""
    image_url: str = ""
    url: str = ""
    deal_score: float = 0.0
    potential_profit: float = 0.0
    discount_pct: float = 0.0


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.copart.com",
    "Referer": "https://www.copart.com/lotSearchResults/",
}

SEARCH_URL = "https://www.copart.com/public/lots/search-results"
LOT_URL_TEMPLATE = "https://www.copart.com/lot/{lot_number}"


def build_search_query(
    page: int = 0,
    size: int = 100,
    make: str = None,
    model: str = None,
    year_from: int = None,
    year_to: int = None,
) -> dict:
    """Build the JSON query payload for Copart's search API."""
    # Filters must be a dict, not a list
    filters = {}

    # Only vehicles (not motorcycles, boats, etc.)
    filters["MISC"] = ["#VehicleTypeCode:VEHTYPE_V"]

    if make:
        filters["MAKE"] = [f"lot_make_desc:{make.upper()}"]
    if model:
        filters["MODEL"] = [f"lot_model_desc:{model.upper()}"]
    if year_from or year_to:
        yr_from = year_from or 2000
        yr_to = year_to or datetime.now().year + 1
        filters["YEAR"] = [f"lot_year:[{yr_from} TO {yr_to}]"]

    return {
        "query": ["*"],
        "filter": filters,
        "sort": ["auction_date_type desc"],
        "page": page,
        "size": size,
        "start": page * size,
    }


def parse_vehicle(item: dict) -> Vehicle:
    """Parse a single vehicle from the API response."""
    v = Vehicle()

    v.lot_number = str(item.get("ln", ""))
    v.year = str(item.get("lcy", ""))
    v.make = item.get("mkn", "") or ""
    v.model = item.get("lm", "") or ""
    v.trim = item.get("ltd", "") or ""
    v.title = f"{v.year} {v.make} {v.model} {v.trim}".strip()
    v.damage_type = item.get("dd", "") or ""
    v.secondary_damage = item.get("sdd", "") or ""
    v.condition = item.get("lcd", "") or ""  # e.g. "RUNS AND DRIVES"
    v.color = item.get("clr", "") or ""
    v.engine = item.get("egn", "") or ""
    v.transmission = item.get("tmtp", "") or ""
    v.drive = item.get("drv", "") or ""
    v.fuel_type = item.get("ft", "") or ""
    v.body_style = item.get("bstl", "") or ""

    orr = item.get("orr", 0)
    v.odometer = str(int(orr)) if orr else "N/A"
    v.odometer_status = item.get("ord", "") or ""

    v.vin = item.get("fv", "") or ""

    dld = item.get("dynamicLotDetails", {})
    v.current_bid = float(dld.get("currentBid", 0) or 0)
    v.high_bid = float(item.get("hb", 0) or 0)
    v.buy_now_price = float(item.get("bnp", 0) or 0)

    v.estimated_retail = float(item.get("la", 0) or 0)
    v.repair_cost = float(item.get("rc", 0) or 0)

    # Sale date from epoch ms
    ad = item.get("ad", "")
    if ad and isinstance(ad, (int, float)) and ad > 0:
        v.sale_date = datetime.fromtimestamp(ad / 1000).strftime("%Y-%m-%d %H:%M")
    else:
        v.sale_date = "TBD"

    v.sale_status = item.get("ess", "") or ""  # e.g. "Pure Sale", "Minimum Bid"
    v.location = item.get("yn", "") or ""
    v.city = item.get("locCity", "") or ""
    v.state = item.get("locState", "") or item.get("ts", "") or ""
    v.title_type = item.get("td", "") or ""  # e.g. "SALVAGE CERTIFICATE"
    v.title_group = item.get("tgd", "") or ""  # e.g. "SALVAGE TITLE"
    v.image_url = item.get("tims", "") or ""
    v.url = LOT_URL_TEMPLATE.format(lot_number=v.lot_number)

    return v


def calculate_deal_score(v: Vehicle) -> Vehicle:
    """
    Calculate deal quality metrics.

    Higher score = better deal, considering:
    - Discount % from estimated retail value
    - Potential profit (retail - bid - estimated repair)
    - Damage severity (minor damage = better deal)
    - Vehicle condition (runs and drives = bonus)
    """
    if v.estimated_retail > 0 and v.current_bid > 0:
        v.discount_pct = round((1 - (v.current_bid / v.estimated_retail)) * 100, 1)
        v.potential_profit = v.estimated_retail - v.current_bid - v.repair_cost
    elif v.estimated_retail > 0 and v.current_bid == 0:
        v.discount_pct = 100.0
        v.potential_profit = v.estimated_retail - v.repair_cost
    else:
        v.discount_pct = 0
        v.potential_profit = 0

    # Damage multiplier
    damage_multiplier = 1.0
    damage_upper = (v.damage_type or "").upper()

    minor_damages = ["MINOR DENT", "NORMAL WEAR", "HAIL", "VANDALISM"]
    major_damages = ["BURN", "STRIPPED", "ROLLOVER", "TOTAL BURN", "BIOHAZARD"]

    if any(d in damage_upper for d in minor_damages):
        damage_multiplier = 1.3
    elif any(d in damage_upper for d in major_damages):
        damage_multiplier = 0.5

    # Condition bonus
    condition_multiplier = 1.0
    cond = (v.condition or "").upper()
    if "RUNS AND DRIVES" in cond:
        condition_multiplier = 1.4
    elif "ENGINE START" in cond:
        condition_multiplier = 1.1

    if v.estimated_retail > 0:
        discount_score = max(v.discount_pct, 0) * 0.4
        profit_score = min(max(v.potential_profit, 0) / 100, 50) * 0.4
        value_score = min(v.estimated_retail / 500, 20) * 0.2
        v.deal_score = round(
            (discount_score + profit_score + value_score)
            * damage_multiplier
            * condition_multiplier,
            1,
        )
    else:
        v.deal_score = 0

    return v


def fetch_vehicles(
    client: httpx.Client,
    page: int = 0,
    size: int = 100,
    **search_kwargs,
) -> tuple[list[Vehicle], int]:
    """Fetch a page of vehicles from Copart search API."""
    query = build_search_query(page=page, size=size, **search_kwargs)

    try:
        resp = client.post(SEARCH_URL, json=query, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        print(f"  [ERROR] HTTP {e.response.status_code}: {e.response.text[:200]}")
        return [], 0
    except Exception as e:
        print(f"  [ERROR] Request failed: {e}")
        return [], 0

    results = data.get("data", {}).get("results", {})
    total_elements = results.get("totalElements", 0)
    content = results.get("content", [])

    vehicles = []
    for item in content:
        v = parse_vehicle(item)
        v = calculate_deal_score(v)
        vehicles.append(v)

    return vehicles, total_elements


def scrape_copart(
    max_pages: int = 10,
    min_retail_value: float = 3000,
    min_discount_pct: float = 40,
    year_from: int = 2015,
    year_to: int = None,
    make: str = None,
    model: str = None,
) -> list[Vehicle]:
    """Main scraping function. Fetches vehicles and filters for best deals."""
    all_vehicles = []
    page_size = 100

    print("=" * 60)
    print("  COPART DEAL FINDER")
    print("=" * 60)
    print(f"  Year {year_from}+, Min retail ${min_retail_value:,.0f}, Min discount {min_discount_pct}%")
    if make:
        print(f"  Make: {make}")
    if model:
        print(f"  Model: {model}")
    print("-" * 60)

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        # Get cookies first
        print("\n  Initializing session...", end=" ", flush=True)
        client.get("https://www.copart.com/", timeout=15)
        print("OK")

        # First page
        print(f"  Fetching page 1...", end=" ", flush=True)
        vehicles, total = fetch_vehicles(
            client, page=0, size=page_size,
            make=make, model=model, year_from=year_from, year_to=year_to,
        )
        print(f"Got {len(vehicles)} vehicles (total available: {total:,})")
        all_vehicles.extend(vehicles)

        if total == 0:
            print("  No vehicles found with these criteria.")
            return []

        total_pages = min(max_pages, math.ceil(total / page_size))

        for page in range(1, total_pages):
            time.sleep(1.5)  # Rate limiting
            print(f"  Fetching page {page + 1}/{total_pages}...", end=" ", flush=True)
            vehicles, _ = fetch_vehicles(
                client, page=page, size=page_size,
                make=make, model=model, year_from=year_from, year_to=year_to,
            )
            print(f"Got {len(vehicles)} vehicles")
            all_vehicles.extend(vehicles)

    # Filter for deals
    print(f"\n  Total vehicles fetched: {len(all_vehicles)}")
    deals = [
        v for v in all_vehicles
        if v.estimated_retail >= min_retail_value
        and v.discount_pct >= min_discount_pct
        and v.current_bid > 0
    ]

    # Sort by deal score (best deals first)
    deals.sort(key=lambda v: v.deal_score, reverse=True)

    print(f"  Deals found (>{min_discount_pct}% off, >${min_retail_value:,.0f} retail): {len(deals)}")
    return deals


def save_to_csv(vehicles: list[Vehicle], filename: str = None) -> str:
    """Save vehicles to CSV file."""
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"copart_deals_{timestamp}.csv"

    fields = [
        "deal_score", "title", "year", "make", "model", "trim",
        "current_bid", "estimated_retail", "discount_pct",
        "potential_profit", "repair_cost", "condition",
        "damage_type", "secondary_damage", "odometer", "odometer_status",
        "color", "engine", "transmission", "drive", "fuel_type", "body_style",
        "vin", "title_type", "title_group",
        "location", "city", "state", "sale_date", "sale_status",
        "lot_number", "url", "image_url",
    ]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for v in vehicles:
            row = asdict(v)
            writer.writerow({k: row[k] for k in fields})

    return filename


def print_top_deals(deals: list[Vehicle], top_n: int = 25):
    """Print top deals to console."""
    print(f"\n{'=' * 90}")
    print(f"  TOP {min(top_n, len(deals))} BEST DEALS")
    print(f"{'=' * 90}")

    for i, v in enumerate(deals[:top_n], 1):
        print(f"\n  #{i} [Score: {v.deal_score}] {v.title}")
        print(f"     Bid: ${v.current_bid:>10,.0f} | Retail: ${v.estimated_retail:>10,.0f} | Discount: {v.discount_pct}%")
        print(f"     Profit est: ${v.potential_profit:>10,.0f} | Repair est: ${v.repair_cost:>10,.0f}")
        print(f"     Damage: {v.damage_type} | Condition: {v.condition}")
        print(f"     Odo: {v.odometer} ({v.odometer_status}) | Color: {v.color} | {v.title_type}")
        print(f"     Location: {v.location} ({v.state}) | Sale: {v.sale_date} [{v.sale_status}]")
        print(f"     Lot: {v.lot_number} | {v.url}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Copart Deal Finder")
    parser.add_argument("--pages", type=int, default=10, help="Max pages to scrape (100 vehicles/page)")
    parser.add_argument("--min-retail", type=float, default=5000, help="Min estimated retail value ($)")
    parser.add_argument("--min-discount", type=float, default=50, help="Min discount percentage (%%)")
    parser.add_argument("--year-from", type=int, default=2015, help="Min vehicle year")
    parser.add_argument("--year-to", type=int, default=None, help="Max vehicle year")
    parser.add_argument("--make", type=str, default=None, help="Filter by make (e.g., TOYOTA)")
    parser.add_argument("--model", type=str, default=None, help="Filter by model (e.g., CAMRY)")
    parser.add_argument("--top", type=int, default=25, help="Number of top deals to display")
    parser.add_argument("--output", type=str, default=None, help="Output CSV filename")

    args = parser.parse_args()

    deals = scrape_copart(
        max_pages=args.pages,
        min_retail_value=args.min_retail,
        min_discount_pct=args.min_discount,
        year_from=args.year_from,
        year_to=args.year_to,
        make=args.make,
        model=args.model,
    )

    if deals:
        print_top_deals(deals, top_n=args.top)
        filename = save_to_csv(deals, filename=args.output)
        print(f"\n  Results saved to: {filename}")
        print(f"  Total deals exported: {len(deals)}")
    else:
        print("\n  No deals found matching criteria. Try adjusting filters.")


if __name__ == "__main__":
    main()
