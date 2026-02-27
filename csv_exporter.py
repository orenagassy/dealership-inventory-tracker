"""
csv_exporter.py — CSV output for the GA4 Inventory Tracker.

Merges GA4 page-view data with scrape results and writes them to a CSV file.
Creates the output directory if it does not exist.
"""
import csv
import logging
import os
from typing import Any, Dict, List

from scraper import ScrapeResult

logger = logging.getLogger(__name__)

# Fixed column order for the output CSV
CSV_COLUMNS = [
    "stock_number",
    "vin_number",
]


def ensure_output_dir(output_path: str) -> None:
    """
    Create the directory that will contain the output file if it does not exist.

    Args:
        output_path: Path to the intended output file (e.g. 'output/report.csv').
    """
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
        logger.debug("Output directory ensured: %s", directory)


def records_to_dicts(
    ga4_rows: List[Dict[str, Any]],
    scrape_results: List[ScrapeResult],
    domain: str,
) -> List[Dict[str, Any]]:
    """
    Merge GA4 page-view rows with scrape results into flat dicts ready for CSV.

    The two lists must be the same length and in the same order (ga4_rows[i]
    corresponds to scrape_results[i]).

    Args:
        ga4_rows: Output of GA4Client.get_underexposed_pages().
        scrape_results: Parallel list of ScrapeResult instances.
        domain: Domain string used to construct full_url if needed (e.g. 'your-dealership.com').

    Returns:
        List of dicts whose keys match CSV_COLUMNS.
    """
    if len(ga4_rows) != len(scrape_results):
        logger.warning(
            "Mismatch: %d GA4 rows vs %d scrape results. "
            "Some rows will be incomplete.",
            len(ga4_rows),
            len(scrape_results),
        )

    records: List[Dict[str, Any]] = []
    pairs = zip(ga4_rows, scrape_results)

    for ga4_row, scrape in pairs:
        page_path = ga4_row.get("page_path", "")
        full_url = scrape.full_url or f"https://{domain}{page_path}"

        record: Dict[str, Any] = {
            "page_path": page_path,
            "full_url": full_url,
            "page_views": ga4_row.get("page_views", ""),
            "stock_number": scrape.stock_number or "",
            "vin_number": scrape.vin_number or "",
            "scraped_at": scrape.scraped_at or "",
            "scrape_status": scrape.scrape_status or "",
            "error_message": scrape.error_message or "",
        }
        records.append(record)

    return records


def write_csv(records: List[Dict[str, Any]], output_path: str) -> None:
    """
    Write records to a CSV file with a fixed column schema.

    Creates the output directory if needed. Any key missing from a record
    is written as an empty string.

    Args:
        records: List of dicts with keys matching CSV_COLUMNS.
        output_path: Destination file path (e.g. 'output/underexposed_inventory.csv').

    Raises:
        OSError: If the file cannot be written (logged before re-raising).
    """
    ensure_output_dir(output_path)

    try:
        with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=CSV_COLUMNS,
                extrasaction="ignore",
                restval="",  # fills missing keys with empty string
            )
            writer.writeheader()
            writer.writerows(records)

        logger.info("Wrote %d rows to %s", len(records), output_path)

    except OSError as exc:
        logger.critical("Failed to write CSV file %s: %s", output_path, exc)
        raise
