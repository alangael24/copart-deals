"""
Scraper module - fetches Copart listings and returns structured data.
Used by the agent system to gather raw vehicle data.
"""

import httpx
import time
import math
import json
from datetime import datetime
from dataclasses import dataclass, asdict


@dataclass
class VehicleData:
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
    discount_pct: float = 0.0
    potential_profit: float = 0.0


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.copart.com",
    "Referer": "https://www.copart.com/lotSearchResults/",
}

SEARCH_URL = "https://www.copart.com/public/lots/search-results"


def parse_vehicle(item: dict) -> VehicleData:
    v = VehicleData()
    v.lot_number = str(item.get("ln", ""))
    v.year = str(item.get("lcy", ""))
    v.make = item.get("mkn", "") or ""
    v.model = item.get("lm", "") or ""
    v.trim = item.get("ltd", "") or ""
    v.title = f"{v.year} {v.make} {v.model} {v.trim}".strip()
    v.damage_type = item.get("dd", "") or ""
    v.secondary_damage = item.get("sdd", "") or ""
    v.condition = item.get("lcd", "") or ""
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

    ad = item.get("ad", "")
    if ad and isinstance(ad, (int, float)) and ad > 0:
        v.sale_date = datetime.fromtimestamp(ad / 1000).strftime("%Y-%m-%d %H:%M")
    else:
        v.sale_date = "TBD"

    v.sale_status = item.get("ess", "") or ""
    v.location = item.get("yn", "") or ""
    v.city = item.get("locCity", "") or ""
    v.state = item.get("locState", "") or item.get("ts", "") or ""
    v.title_type = item.get("td", "") or ""
    v.title_group = item.get("tgd", "") or ""
    v.image_url = item.get("tims", "") or ""
    v.url = f"https://www.copart.com/lot/{v.lot_number}"

    if v.estimated_retail > 0 and v.current_bid > 0:
        v.discount_pct = round((1 - v.current_bid / v.estimated_retail) * 100, 1)
        v.potential_profit = v.estimated_retail - v.current_bid - v.repair_cost

    return v


def scrape_copart(
    max_pages: int = 10,
    year_from: int = 2015,
    year_to: int = None,
    make: str = None,
    model: str = None,
) -> list[dict]:
    """Scrape Copart and return list of vehicle dicts."""
    all_vehicles = []
    page_size = 100

    filters = {"MISC": ["#VehicleTypeCode:VEHTYPE_V"]}
    if make:
        filters["MAKE"] = [f"lot_make_desc:{make.upper()}"]
    if model:
        filters["MODEL"] = [f"lot_model_desc:{model.upper()}"]
    if year_from or year_to:
        yr_from = year_from or 2000
        yr_to = year_to or datetime.now().year + 1
        filters["YEAR"] = [f"lot_year:[{yr_from} TO {yr_to}]"]

    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        client.get("https://www.copart.com/", timeout=15)

        for page in range(max_pages):
            payload = {
                "query": ["*"],
                "filter": filters,
                "sort": ["auction_date_type desc"],
                "page": page,
                "size": page_size,
                "start": page * page_size,
            }

            try:
                resp = client.post(SEARCH_URL, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [Page {page+1}] Error: {e}")
                continue

            results = data.get("data", {}).get("results", {})
            content = results.get("content", [])
            total = results.get("totalElements", 0)

            if not content:
                break

            for item in content:
                v = parse_vehicle(item)
                all_vehicles.append(asdict(v))

            print(f"  Page {page+1}: {len(content)} vehicles (total: {total:,})")

            if (page + 1) * page_size >= total:
                break

            time.sleep(1.5)

    return all_vehicles


def filter_deals(
    vehicles: list[dict],
    min_retail: float = 5000,
    min_discount: float = 50,
    require_bid: bool = True,
) -> list[dict]:
    """Filter and sort vehicles by deal quality."""
    deals = []
    for v in vehicles:
        if v["estimated_retail"] < min_retail:
            continue
        if v["discount_pct"] < min_discount:
            continue
        if require_bid and v["current_bid"] <= 0:
            continue
        deals.append(v)

    deals.sort(key=lambda v: v["discount_pct"], reverse=True)
    return deals


if __name__ == "__main__":
    print("Scraping Copart...")
    vehicles = scrape_copart(max_pages=5)
    deals = filter_deals(vehicles)
    print(f"\nFound {len(deals)} deals out of {len(vehicles)} vehicles")

    output = "scraped_deals.json"
    with open(output, "w") as f:
        json.dump(deals, f, indent=2)
    print(f"Saved to {output}")
