"""
main.py — Entry point for the GA4 Inventory Tracker.

Orchestrates the full pipeline:
  1. Load and validate configuration
  2. Query GA4 for underexposed inventory pages
  3. Scrape each page for stock number and VIN
  4. Export results to CSV

Usage:
    python main.py
    python main.py --config path/to/config.yaml
    python main.py --test                         # test mode: limited records + DEBUG logging
    python main.py --test --config path/to/config.yaml
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict

import yaml

from csv_exporter import records_to_dicts, write_csv
from ga4_client import GA4Client
from scraper import InventoryScraper, ScrapeResult

# Required keys that must be present in the config file
_REQUIRED_KEYS = [
    "ga4_property_id",
    "service_account_key_path",
    "domain",
    "inventory_path_prefix",
    "date_range_days",
    "pageview_threshold",
    "max_results",
    "output_csv_path",
    "scrape_delay_seconds",
    "request_timeout_seconds",
    "user_agent",
    "max_retries",
    "utm_source",
    "utm_medium",
    "vin_text_pattern",
    "stock_patterns",
    "jsonld_vin_fields",
]


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger to write to stdout with ISO timestamps."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def load_config(path: str) -> Dict[str, Any]:
    """
    Load and validate the YAML configuration file.

    Args:
        path: Path to config.yaml.

    Returns:
        Parsed configuration dictionary.

    Raises:
        SystemExit: If the file is missing, unreadable, or has missing required keys.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        logging.critical(
            "Configuration file not found: %r. "
            "Copy config.example.yaml to config.yaml and fill in the required values.",
            path,
        )
        sys.exit(1)
    except yaml.YAMLError as exc:
        logging.critical("Failed to parse configuration file %r: %s", path, exc)
        sys.exit(1)

    if not isinstance(config, dict):
        logging.critical(
            "Configuration file %r is malformed (expected a YAML mapping at the top level).",
            path,
        )
        sys.exit(1)

    missing = [key for key in _REQUIRED_KEYS if key not in config]
    if missing:
        logging.critical(
            "Configuration file %r is missing required keys: %s",
            path,
            ", ".join(missing),
        )
        sys.exit(1)

    # Warn if the user left placeholder values unchanged
    if str(config.get("ga4_property_id", "")).startswith("YOUR_"):
        logging.critical(
            "ga4_property_id is still set to a placeholder value. "
            "Update config.yaml with your real GA4 Property ID."
        )
        sys.exit(1)

    if str(config.get("service_account_key_path", "")).startswith("credentials/YOUR_"):
        logging.critical(
            "service_account_key_path is still set to a placeholder value. "
            "Update config.yaml with the path to your service account JSON key."
        )
        sys.exit(1)

    return config


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="GA4 Inventory Tracker — identify underexposed car pages and export a CSV."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to the YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        default=False,
        help=(
            "Run in test mode: process only test_limit records (from config) "
            "and enable DEBUG-level logging for detailed output."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """
    Run the full pipeline: GA4 query → scrape → CSV export.
    """
    args = parse_args()

    # Enable DEBUG immediately if --test flag was passed so config-load messages are verbose too
    setup_logging(level=logging.DEBUG if args.test else logging.INFO)
    logger = logging.getLogger(__name__)

    config = load_config(args.config)

    # Resolve test mode: CLI flag overrides config; config.test_mode overrides default False
    test_mode: bool = args.test or bool(config.get("test_mode", False))
    test_limit: int = int(config.get("test_limit", 5))

    if test_mode and not args.test:
        # test_mode was set via config rather than --test; upgrade logging level now
        logging.getLogger().setLevel(logging.DEBUG)

    if test_mode:
        logger.info(
            "TEST MODE ACTIVE — processing up to %d records with DEBUG logging.", test_limit
        )

    domain: str = config["domain"]
    delay: float = float(config["scrape_delay_seconds"])

    # Stamp the output filename with the run start time (e.g. underexposed_inventory_20260226_214038.csv)
    _base, _ext = os.path.splitext(config["output_csv_path"])
    output_path: str = f"{_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{_ext}"

    # --- Step 1: Query GA4 ---
    try:
        ga4 = GA4Client(config)
        underexposed_pages = ga4.get_underexposed_pages()
    except FileNotFoundError as exc:
        logger.critical("Service account key file error: %s", exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.critical("GA4 query failed: %s", exc)
        sys.exit(1)

    if not underexposed_pages:
        logger.info(
            "No underexposed inventory pages found (all pages meet or exceed the threshold). "
            "Nothing to export."
        )
        sys.exit(0)

    if test_mode and len(underexposed_pages) > test_limit:
        logger.info(
            "Test mode: truncating %d pages to %d records.",
            len(underexposed_pages),
            test_limit,
        )
        underexposed_pages = underexposed_pages[:test_limit]

    logger.info(
        "Starting scrape of %d underexposed pages (delay=%.1fs between requests)...",
        len(underexposed_pages),
        delay,
    )

    # --- Step 2: Scrape each page ---
    scraper = InventoryScraper(config)
    scrape_results: list[ScrapeResult] = []

    for i, row in enumerate(underexposed_pages, start=1):
        full_url = f"https://{domain}{row['page_path']}"

        result = scraper.scrape_page(full_url)
        scrape_results.append(result)

        logger.info(
            "[%d/%d] %s (GA4 views: %d) → stock=%s, vin=%s, status=%s",
            i,
            len(underexposed_pages),
            row["page_path"],
            row["page_views"],
            result.stock_number or "(not found)",
            result.vin_number or "(not found)",
            result.scrape_status,
        )

        # Respect delay between requests (skip delay after the last page)
        if i < len(underexposed_pages):
            time.sleep(delay)

    # --- Step 3: Export to CSV ---
    records = records_to_dicts(underexposed_pages, scrape_results, domain)
    records = [r for r in records if r.get("stock_number") and r.get("vin_number")]
    write_csv(records, output_path)

    # --- Summary ---
    total = len(scrape_results)
    success = sum(1 for r in scrape_results if r.scrape_status == "success")
    not_found = sum(1 for r in scrape_results if r.scrape_status == "not_found")
    failed = sum(1 for r in scrape_results if r.scrape_status == "failed")
    missing_vin = sum(1 for r in scrape_results if not r.vin_number)
    missing_stock = sum(1 for r in scrape_results if not r.stock_number)

    logger.info(
        "Done. Total=%d | Success=%d | Not found=%d | Failed=%d | "
        "Missing VIN=%d | Missing stock=%d | Output: %s",
        total,
        success,
        not_found,
        failed,
        missing_vin,
        missing_stock,
        output_path,
    )


if __name__ == "__main__":
    main()
