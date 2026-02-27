"""
scraper.py — Web scraper for automotive dealership inventory pages.

Extracts stock number and VIN from individual inventory pages.
Uses JSON-LD structured data as the primary source for VIN, with regex
fallback on visible page text. Implements retry logic with exponential backoff.
"""
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class PageNotFoundError(Exception):
    """Raised when a page returns HTTP 404. Not retried."""


@dataclass
class ScrapeResult:
    """Holds the outcome of scraping a single inventory page."""

    full_url: str
    stock_number: Optional[str] = None
    vin_number: Optional[str] = None
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    scrape_status: str = "success"  # "success" | "failed" | "not_found"
    error_message: Optional[str] = None


# --- Compiled regex patterns ---

# VIN: standard 17-char format per ISO 3779 (excludes I, O, Q).
# This is the structural validator and is not configurable.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)


def _is_valid_vin(value: str) -> bool:
    """Return True if value is a structurally valid 17-char VIN (ISO 3779)."""
    return bool(_VIN_RE.match(value.strip()))


class InventoryScraper:
    """Scrapes stock number and VIN from inventory pages on a dealership website."""

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Args:
            config: Loaded configuration dict. Uses keys:
                user_agent, request_timeout_seconds, max_retries, scrape_delay_seconds,
                utm_source, utm_medium,
                vin_text_pattern, stock_patterns, jsonld_vin_fields
        """
        self._timeout: int = int(config["request_timeout_seconds"])
        self._max_retries: int = int(config["max_retries"])
        self._delay: float = float(config["scrape_delay_seconds"])

        # UTM tracking parameters appended to every fetched URL
        self._utm_source: str = config["utm_source"]
        self._utm_medium: str = config["utm_medium"]

        # Detection patterns loaded from config
        self._vin_text_re: re.Pattern = re.compile(
            config["vin_text_pattern"], re.IGNORECASE
        )
        self._stock_patterns: List[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in config["stock_patterns"]
        ]
        self._jsonld_vin_fields: List[str] = list(config["jsonld_vin_fields"])

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": config["user_agent"]})

    def _append_utm(self, url: str) -> str:
        """
        Append configured UTM parameters to a URL, preserving any existing query string.

        Args:
            url: Original page URL.

        Returns:
            URL with utm_source and/or utm_medium appended (unchanged if both are empty).
        """
        params: Dict[str, str] = {}
        if self._utm_source:
            params["utm_source"] = self._utm_source
        if self._utm_medium:
            params["utm_medium"] = self._utm_medium
        if not params:
            return url

        parsed = urllib.parse.urlparse(url)
        existing = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        existing.update({k: [v] for k, v in params.items()})
        new_query = urllib.parse.urlencode(existing, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

    def _fetch_html(self, url: str) -> str:
        """
        Fetch page HTML with retry logic and exponential backoff.
        UTM parameters are appended to the request URL automatically.

        Args:
            url: Full URL to fetch.

        Returns:
            Response body as a string.

        Raises:
            PageNotFoundError: If the server returns 404 (not retried).
            requests.exceptions.RequestException: After all retries are exhausted.
        """
        fetch_url = self._append_utm(url)
        if fetch_url != url:
            logger.debug("Fetching with UTM: %s", fetch_url)

        last_exc: Optional[Exception] = None

        for attempt in range(self._max_retries):
            try:
                response = self._session.get(fetch_url, timeout=self._timeout)

                if response.status_code == 404:
                    raise PageNotFoundError(f"404 Not Found: {url}")

                response.raise_for_status()
                return response.text

            except PageNotFoundError:
                raise  # Do not retry 404s

            except requests.exceptions.RequestException as exc:
                last_exc = exc
                wait = 2 ** attempt  # 1s, 2s, 4s, ...
                logger.warning(
                    "Attempt %d/%d failed for %s: %s. Retrying in %ds.",
                    attempt + 1,
                    self._max_retries,
                    fetch_url,
                    exc,
                    wait,
                )
                if attempt < self._max_retries - 1:
                    time.sleep(wait)

        raise requests.exceptions.RequestException(
            f"All {self._max_retries} attempts failed for {fetch_url}"
        ) from last_exc

    def _extract_vin_from_jsonld(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Search all <script type="application/ld+json"> blocks for a VIN.

        Handles both top-level objects and objects nested inside @graph arrays.
        Checks fields: vehicleIdentificationNumber, vin, serialNumber.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            17-character VIN string if found, else None.
        """
        for script_tag in soup.find_all("script", type="application/ld+json"):
            raw = script_tag.string
            if not raw:
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.debug("Failed to parse JSON-LD block: %s", exc)
                continue

            # Normalise to a list of candidate objects
            if isinstance(data, dict):
                # Some plugins wrap everything in @graph
                candidates: List[Any] = data.get("@graph", [data])
            elif isinstance(data, list):
                candidates = data
            else:
                continue

            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                for field_name in self._jsonld_vin_fields:
                    raw_value = obj.get(field_name)
                    if raw_value and isinstance(raw_value, str):
                        candidate = raw_value.strip()
                        if _is_valid_vin(candidate):
                            logger.debug(
                                "VIN found in JSON-LD field '%s': %s",
                                field_name,
                                candidate,
                            )
                            return candidate.upper()

        return None

    def _extract_vin_from_text(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Fallback: search visible page text for "VIN: XXXXXXXXXXXXXXXXX".

        Scopes the search to the Vehicle Identification section if identifiable,
        otherwise falls back to the full page text.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            17-character VIN string if found, else None.
        """
        section = _find_vehicle_id_section(soup)
        text = (
            section.get_text(" ", strip=True)
            if section
            else soup.get_text(" ", strip=True)
        )

        match = self._vin_text_re.search(text)
        if match:
            candidate = match.group(1).strip()
            if _is_valid_vin(candidate):
                logger.debug("VIN found via text pattern: %s", candidate)
                return candidate.upper()

        return None

    def _extract_stock_number(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract stock number from visible page text.

        Checks for "STOCK:XXXXXXXX" (Vehicle Identification section format) and
        "Stock: #XXXXXXXX" (listing card format). Scopes to the Vehicle
        Identification section first, then falls back to the full page.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            Stock number string (e.g. 'M71097') if found, else None.
        """
        section = _find_vehicle_id_section(soup)
        text = (
            section.get_text(" ", strip=True)
            if section
            else soup.get_text(" ", strip=True)
        )

        for pattern in self._stock_patterns:
            match = pattern.search(text)
            if match:
                result = match.group(1).strip().upper()
                logger.debug("Stock number found via pattern '%s': %s", pattern.pattern, result)
                return result

        return None

    def scrape_page(self, url: str) -> ScrapeResult:
        """
        Fetch an inventory page and extract stock number and VIN.

        All exceptions are caught and recorded in the result rather than
        propagated, so the caller's loop continues on individual page failures.

        Args:
            url: Full URL, e.g. 'https://your-dealership.com/inventory/2024-toyota-camry-le/'

        Returns:
            ScrapeResult dataclass instance.
        """
        logger.info("Scraping: %s", url)
        result = ScrapeResult(full_url=url)

        try:
            html = self._fetch_html(url)
        except PageNotFoundError as exc:
            result.scrape_status = "not_found"
            result.error_message = str(exc)
            logger.warning("Page not found: %s", url)
            return result
        except requests.exceptions.RequestException as exc:
            result.scrape_status = "failed"
            result.error_message = str(exc)
            logger.error("Failed to fetch %s: %s", url, exc)
            return result

        try:
            soup = BeautifulSoup(html, "lxml")

            # VIN: prefer JSON-LD (structured, reliable), fall back to text
            vin = self._extract_vin_from_jsonld(soup)
            if vin is None:
                vin = self._extract_vin_from_text(soup)

            stock = self._extract_stock_number(soup)

            result.vin_number = vin
            result.stock_number = stock

            if vin is None:
                logger.warning("VIN not found on page: %s", url)
            if stock is None:
                logger.warning("Stock number not found on page: %s", url)

        except Exception as exc:  # noqa: BLE001
            result.scrape_status = "failed"
            result.error_message = f"Parse error: {exc}"
            logger.error("Error parsing %s: %s", url, exc)

        return result


def _find_vehicle_id_section(soup: BeautifulSoup) -> Optional[Any]:
    """
    Locate the "Vehicle Identification" section element in the page.

    Searches for any tag whose text contains "vehicle identification" (case-insensitive)
    and returns its nearest useful ancestor container.

    Args:
        soup: Parsed BeautifulSoup document.

    Returns:
        A Tag element if the section is found, else None.
    """
    heading = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4", "h5", "th", "td", "div", "span")
        and "vehicle identification" in tag.get_text(strip=True).lower()
    )
    if heading is None:
        return None

    # Walk up to a meaningful container that would include sibling content
    container = heading.parent
    if container and container.parent:
        return container.parent
    return container
