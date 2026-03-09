"""
Copart Deal Finder - Multi-Agent System
Uses Claude Agent SDK to orchestrate specialized agents that:
1. Scrape Copart listings across all USA
2. Analyze vehicle photos with AI vision
3. Score and rank the best deals

Usage:
    python main.py                          # Default: 10 pages, top 10 analyzed
    python main.py --pages 20 --top 15      # More pages, more analysis
    python main.py --make TOYOTA --top 5    # Filter by make
"""

import anyio
import json
import os
import sys
import argparse
from datetime import datetime

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AgentDefinition,
    ResultMessage,
    AssistantMessage,
    SystemMessage,
)

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))
from agents.scraper import scrape_copart, filter_deals

WORK_DIR = os.path.dirname(os.path.abspath(__file__))


async def run_scraper(args) -> str:
    """Phase 1: Scrape Copart and save deals to JSON."""
    print("\n" + "=" * 70)
    print("  PHASE 1: SCRAPING COPART LISTINGS")
    print("=" * 70)

    vehicles = scrape_copart(
        max_pages=args.pages,
        year_from=args.year_from,
        year_to=args.year_to,
        make=args.make,
        model=args.model,
    )

    deals = filter_deals(
        vehicles,
        min_retail=args.min_retail,
        min_discount=args.min_discount,
    )

    print(f"\n  Total vehicles scraped: {len(vehicles)}")
    print(f"  Deals matching criteria: {len(deals)}")

    # Save all deals
    deals_file = os.path.join(WORK_DIR, "data", "all_deals.json")
    os.makedirs(os.path.join(WORK_DIR, "data"), exist_ok=True)
    with open(deals_file, "w") as f:
        json.dump(deals, f, indent=2)

    # Save top N for analysis
    top_deals = deals[: args.top]
    top_file = os.path.join(WORK_DIR, "data", "top_deals.json")
    with open(top_file, "w") as f:
        json.dump(top_deals, f, indent=2)

    print(f"  All deals saved to: data/all_deals.json")
    print(f"  Top {len(top_deals)} deals saved to: data/top_deals.json")

    return top_file


async def run_analysis_agent(top_deals_file: str) -> str:
    """Phase 2 & 3: AI agents analyze photos and score deals."""
    print("\n" + "=" * 70)
    print("  PHASE 2: AI PHOTO ANALYSIS & DEAL SCORING")
    print("=" * 70)

    with open(top_deals_file) as f:
        deals = json.load(f)

    print(f"  Analyzing {len(deals)} vehicles with AI vision...\n")

    # Build the vehicle summary for the orchestrator
    vehicles_summary = ""
    for i, d in enumerate(deals, 1):
        vehicles_summary += f"""
--- Vehicle #{i} ---
Title: {d['title']}
Lot: {d['lot_number']} | VIN: {d['vin']}
Current Bid: ${d['current_bid']:,.0f} | Retail Value: ${d['estimated_retail']:,.0f} | Discount: {d['discount_pct']}%
Potential Profit: ${d['potential_profit']:,.0f} | Repair Cost: ${d['repair_cost']:,.0f}
Damage: {d['damage_type']} | Secondary: {d['secondary_damage']}
Condition: {d['condition']} | Odometer: {d['odometer']} ({d['odometer_status']})
Color: {d['color']} | Engine: {d['engine']} | Trans: {d['transmission']} | Drive: {d['drive']}
Title Type: {d['title_type']} | Title Group: {d['title_group']}
Location: {d['location']} ({d['state']}) | Sale: {d['sale_date']} [{d['sale_status']}]
Photo URL: {d['image_url']}
Copart URL: {d['url']}
"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join(WORK_DIR, "data", f"deal_report_{timestamp}.md")

    prompt = f"""You are the Copart Deal Analysis Orchestrator. Your job is to analyze vehicle deals from Copart auctions and produce an investment-quality report.

You have {len(deals)} vehicles to analyze. For each vehicle:

1. **Photo Analysis**: Use the "photo-analyzer" agent to analyze each vehicle's photo. Pass it the photo URL and vehicle details. The agent will assess visible damage, overall condition, and red flags from the image.

2. **Deal Evaluation**: Use the "deal-evaluator" agent for each vehicle. Pass it ALL the data (specs + photo analysis results). It will produce a final investment score and recommendation.

3. **Final Report**: After all vehicles are analyzed, compile a comprehensive markdown report ranking all deals from best to worst.

Here are the vehicles to analyze:
{vehicles_summary}

IMPORTANT INSTRUCTIONS:
- Analyze ALL {len(deals)} vehicles, not just a few
- For each vehicle, first spawn the photo-analyzer agent, then the deal-evaluator agent with the photo analysis results
- The final report must include for each vehicle:
  * Investment score (1-100)
  * Photo analysis summary
  * Pros and cons
  * Estimated true repair cost
  * Buy/Pass/Watch recommendation
  * Risk level (Low/Medium/High)
- Save the final report to: {report_file}
- Sort vehicles by investment score (best first)
"""

    result_text = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=WORK_DIR,
            model="claude-sonnet-4-6",
            allowed_tools=["Read", "Write", "Glob", "Grep", "WebFetch", "Agent", "Bash"],
            permission_mode="bypassPermissions",
            allow_dangerously_skip_permissions=True,
            max_turns=100,
            env={"CLAUDECODE": ""},
            agents={
                "photo-analyzer": AgentDefinition(
                    description="Expert vehicle photo analyst. Analyzes Copart vehicle photos to assess damage severity, hidden issues, and overall condition.",
                    prompt="""You are an expert vehicle damage assessor and automotive photographer analyst.

When given a vehicle photo URL and details, you must:
1. Use WebFetch to retrieve and analyze the vehicle photo
2. Assess the visible damage from the photo:
   - Is the listed damage consistent with what you see?
   - Severity: Minor (cosmetic only), Moderate (panel replacement), Severe (structural)
   - Are there signs of hidden damage not listed? (frame damage, flood, airbag deployment)
3. Evaluate overall condition:
   - Body alignment (gaps between panels)
   - Paint condition
   - Tire condition (if visible)
   - Glass condition
   - Rust or corrosion
4. Provide a photo condition score (1-10, where 10 = excellent)
5. List specific observations and red flags

Respond with a structured analysis. Be specific about what you observe.""",
                    tools=["WebFetch", "Read"],
                ),
                "deal-evaluator": AgentDefinition(
                    description="Expert automotive deal evaluator. Scores vehicle deals based on market value, damage assessment, repair costs, and investment potential.",
                    prompt="""You are a professional used car dealer and auction buyer with 20+ years of experience.

When given vehicle data and photo analysis, evaluate the deal:

1. **Market Analysis**:
   - Is the estimated retail value realistic for this year/make/model/mileage?
   - What would this vehicle sell for in repaired condition locally?
   - Factor in current market demand for this vehicle type

2. **Repair Cost Estimation**:
   - Based on damage type and photo analysis, estimate TRUE repair costs
   - Include: parts, labor, paint, alignment, hidden damage buffer (20%)
   - Consider if repairs need specialized tools/knowledge

3. **Investment Score** (1-100):
   - 90-100: Exceptional deal, buy immediately
   - 70-89: Good deal, worth bidding
   - 50-69: Decent, proceed with caution
   - 30-49: Marginal, only if you can do repairs yourself
   - 1-29: Pass, not worth the risk

4. **Risk Assessment**:
   - Title type impact on resale
   - Damage type risk level
   - Location/transport costs
   - Auction fee considerations ($500-$1500 typical)

5. **Final Recommendation**: BUY / WATCH / PASS with reasoning

Be realistic and conservative. Actual repair costs are usually 30-50% higher than estimates.""",
                    tools=["WebFetch", "Read"],
                ),
            },
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result
            print(f"\n  Agent completed. Stop reason: {message.stop_reason}")

    return report_file


async def main():
    parser = argparse.ArgumentParser(description="Copart Deal Finder - AI Multi-Agent System")
    parser.add_argument("--pages", type=int, default=10, help="Pages to scrape (100 vehicles/page)")
    parser.add_argument("--top", type=int, default=10, help="Top N deals to analyze with AI")
    parser.add_argument("--min-retail", type=float, default=5000, help="Min estimated retail value ($)")
    parser.add_argument("--min-discount", type=float, default=50, help="Min discount percentage")
    parser.add_argument("--year-from", type=int, default=2015, help="Min vehicle year")
    parser.add_argument("--year-to", type=int, default=None, help="Max vehicle year")
    parser.add_argument("--make", type=str, default=None, help="Filter by make (TOYOTA, HONDA, etc)")
    parser.add_argument("--model", type=str, default=None, help="Filter by model")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping, use existing data")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  COPART DEAL FINDER - AI MULTI-AGENT SYSTEM")
    print("=" * 70)
    print(f"  Pages: {args.pages} | Top: {args.top} | Year: {args.year_from}+")
    print(f"  Min retail: ${args.min_retail:,.0f} | Min discount: {args.min_discount}%")
    if args.make:
        print(f"  Make: {args.make}")
    if args.model:
        print(f"  Model: {args.model}")

    # Phase 1: Scrape
    if args.skip_scrape:
        top_file = os.path.join(WORK_DIR, "data", "top_deals.json")
        if not os.path.exists(top_file):
            print("  ERROR: No existing data found. Run without --skip-scrape first.")
            return
        print(f"\n  Using existing data from {top_file}")
    else:
        top_file = await run_scraper(args)

    # Phase 2 & 3: AI Analysis
    report_file = await run_analysis_agent(top_file)

    print("\n" + "=" * 70)
    print("  COMPLETE")
    print("=" * 70)
    print(f"  Report: {report_file}")
    print(f"  All deals: data/all_deals.json")


if __name__ == "__main__":
    anyio.run(main)
