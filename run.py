#!/usr/bin/env python3
"""
FPL Tracker — Daily Runner
===========================

This is the main entry point. Run it daily via cron, GitHub Actions, or manually.

Usage:
    python run.py                     # Fetch data, predict, output JSON
    python run.py --email             # Also send email report
    python run.py --email --preview   # Generate email HTML file for preview (no send)
    python run.py --top 100           # Process top 100 players (default: 200)

Environment Variables (for email):
    FPL_EMAIL_TO          Recipient email address
    FPL_SMTP_USER         SMTP username (Gmail address)
    FPL_SMTP_PASSWORD     SMTP App Password
    --- OR for Resend ---
    FPL_RESEND_API_KEY    Resend API key
    FPL_RESEND_FROM       Sender address (e.g. "FPL <noreply@yourdomain.com>")
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.fetcher import FPLFetcher
from src.processor import FPLProcessor
from src.predictor import PredictionModel
from src.emailer import generate_email_html, send_email_smtp, send_email_resend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fpl-tracker")


def main():
    parser = argparse.ArgumentParser(description="FPL Tracker — Daily Runner")
    parser.add_argument("--top", type=int, default=200, help="Number of top players to process")
    parser.add_argument("--email", action="store_true", help="Send email report")
    parser.add_argument("--preview", action="store_true", help="Save email HTML to file (no send)")
    parser.add_argument("--output", type=str, default="output", help="Output directory")
    parser.add_argument("--data-dir", type=str, default="data", help="Data cache directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("FPL Tracker — Starting daily update")
    logger.info("=" * 60)

    # ─── Step 1: Fetch data ───────────────────────────────────────
    logger.info("Step 1/4: Fetching data from FPL API...")
    fetcher = FPLFetcher(data_dir=args.data_dir)

    try:
        bootstrap = fetcher.get_bootstrap()
        fixtures = fetcher.get_fixtures()
        logger.info(
            "  Loaded %d players, %d fixtures",
            len(bootstrap.get("elements", [])),
            len(fixtures),
        )
    except Exception as e:
        logger.error("Failed to fetch FPL data: %s", e)
        logger.error("Check your internet connection and try again.")
        sys.exit(1)

    # ─── Step 2: Process data ─────────────────────────────────────
    logger.info("Step 2/4: Processing player data (top %d)...", args.top)
    processor = FPLProcessor(fetcher)
    data = processor.process_all(top_n=args.top)
    logger.info(
        "  Processed %d players, %d fixtures, %d injuries",
        len(data["players"]),
        len(data["fixtures"]),
        len(data["injuries"]),
    )

    # ─── Step 3: Run predictions ──────────────────────────────────
    logger.info("Step 3/4: Running prediction model...")
    model = PredictionModel()
    model.predict_all(data["players"])

    # Recalculate captain picks and differentials with predictions
    data["captainPicks"] = sorted(
        [p for p in data["players"] if not p["injured"]],
        key=lambda p: p["predictedPts"],
        reverse=True,
    )[:5]

    data["differentials"] = sorted(
        [p for p in data["players"] if p["ownership"] < 15 and p["form"] >= 4],
        key=lambda p: p["predictedPts"],
        reverse=True,
    )[:10]

    top_pick = data["captainPicks"][0] if data["captainPicks"] else None
    if top_pick:
        logger.info(
            "  Top captain pick: %s (%.1f predicted pts)",
            top_pick["webName"],
            top_pick["predictedPts"],
        )

    # ─── Step 4: Output ──────────────────────────────────────────
    logger.info("Step 4/4: Writing output files...")

    # Save main JSON for the dashboard
    json_path = output_dir / "fpl_data.json"
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("  Dashboard JSON: %s", json_path)

    # Save a slim version (just what the React dashboard needs)
    slim_data = {
        "meta": data["meta"],
        "players": [
            {k: v for k, v in p.items() if k != "predictionBreakdown"}
            for p in data["players"][:100]
        ],
        "fixtures": data["fixtures"],
        "captainPicks": [
            {k: v for k, v in p.items() if k != "predictionBreakdown"}
            for p in data["captainPicks"]
        ],
        "differentials": [
            {k: v for k, v in p.items() if k != "predictionBreakdown"}
            for p in data["differentials"]
        ],
        "injuries": [
            {k: v for k, v in p.items() if k != "predictionBreakdown"}
            for p in data["injuries"]
        ],
    }
    slim_path = output_dir / "fpl_dashboard.json"
    with open(slim_path, "w") as f:
        json.dump(slim_data, f)
    logger.info("  Dashboard slim JSON: %s", slim_path)

    # ─── Email ───────────────────────────────────────────────────
    if args.email or args.preview:
        logger.info("Generating email report...")
        html = generate_email_html(data)

        if args.preview:
            preview_path = output_dir / "email_preview.html"
            with open(preview_path, "w") as f:
                f.write(html)
            logger.info("  Email preview saved: %s", preview_path)

        if args.email and not args.preview:
            to_email = os.environ.get("FPL_EMAIL_TO")
            if not to_email:
                logger.error("Set FPL_EMAIL_TO environment variable")
                sys.exit(1)

            # Try Resend first, then SMTP
            resend_key = os.environ.get("FPL_RESEND_API_KEY")
            if resend_key:
                resend_from = os.environ.get("FPL_RESEND_FROM", "FPL Tracker <noreply@yourdomain.com>")
                gw = data["meta"]["nextGW"]
                success = send_email_resend(
                    html, to_email,
                    subject=f"⚽ FPL Tracker — GW{gw} Daily Briefing",
                    from_email=resend_from,
                    api_key=resend_key,
                )
            else:
                smtp_user = os.environ.get("FPL_SMTP_USER", "")
                smtp_pass = os.environ.get("FPL_SMTP_PASSWORD", "")
                if not smtp_user or not smtp_pass:
                    logger.error("Set SMTP credentials or Resend API key")
                    sys.exit(1)
                gw = data["meta"]["nextGW"]
                success = send_email_smtp(
                    html, to_email,
                    subject=f"⚽ FPL Tracker — GW{gw} Daily Briefing",
                    smtp_user=smtp_user,
                    smtp_password=smtp_pass,
                )

            if success:
                logger.info("  Email sent to %s ✅", to_email)
            else:
                logger.error("  Email failed ❌")

    # ─── Summary ──────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ Daily update complete!")
    logger.info("   GW: %s | Players: %d | Injuries: %d",
                data["meta"]["nextGW"], len(data["players"]), len(data["injuries"]))
    logger.info("   Output: %s", output_dir.resolve())
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
