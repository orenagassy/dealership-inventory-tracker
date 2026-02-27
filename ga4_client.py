"""
ga4_client.py — Google Analytics 4 Data API client.

Queries the GA4 Data API v1beta for inventory page view counts.
Handles service account authentication, dimension filtering, pagination,
and threshold-based filtering to return underexposed pages.
"""
import logging
import os
import re
from typing import Any, Dict, List, Optional

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    Metric,
    OrderBy,
    RunReportRequest,
)
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# GA4 Data API scopes required for read access
_GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

# Safe batch size per API call (API hard limit is 250,000; 10,000 is a safe default)
_PAGE_SIZE = 10_000

# Extracts the 4-digit vehicle year from an inventory slug (e.g. /inventory/2023-ford-...)
_SLUG_YEAR_RE = re.compile(r"/inventory/(\d{4})-")


def _slug_year(page_path: str) -> int:
    """Return the vehicle year embedded in an inventory page path, or 0 if not found."""
    m = _SLUG_YEAR_RE.search(page_path)
    return int(m.group(1)) if m else 0


class GA4Client:
    """Queries the GA4 Data API for inventory pages below a pageview threshold."""

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize and authenticate the GA4 client.

        Args:
            config: Loaded configuration dictionary.

        Raises:
            FileNotFoundError: If the service account key file does not exist.
            DefaultCredentialsError: If authentication fails.
            SystemExit: On any fatal initialization error.
        """
        self._property_id: str = str(config["ga4_property_id"])
        self._inventory_prefix: str = config["inventory_path_prefix"]
        self._date_range_days: int = int(config["date_range_days"])
        self._threshold: int = int(config["pageview_threshold"])
        self._max_results: int = int(config["max_results"])

        raw_pattern: str = config.get("page_path_pattern", "")
        self._page_path_re: Optional[re.Pattern] = (
            re.compile(raw_pattern, re.IGNORECASE) if raw_pattern else None
        )

        raw_exclude: str = config.get("page_path_exclude_pattern", "")
        self._page_path_exclude_re: Optional[re.Pattern] = (
            re.compile(raw_exclude, re.IGNORECASE) if raw_exclude else None
        )

        self._client: BetaAnalyticsDataClient = self._build_client(
            config["service_account_key_path"]
        )

    def _build_client(self, key_path: str) -> BetaAnalyticsDataClient:
        """
        Build an authenticated BetaAnalyticsDataClient from a service account key file.

        Args:
            key_path: Path to the service account JSON key file.

        Returns:
            Authenticated GA4 API client.

        Raises:
            FileNotFoundError: If key_path does not exist.
            DefaultCredentialsError: If the credentials are invalid.
        """
        if not os.path.isfile(key_path):
            raise FileNotFoundError(
                f"Service account key file not found: {key_path!r}. "
                "Ensure the file exists and the path in config.yaml is correct."
            )
        try:
            credentials = service_account.Credentials.from_service_account_file(
                key_path, scopes=_GA4_SCOPES
            )
            client = BetaAnalyticsDataClient(credentials=credentials)
            logger.info("GA4 client authenticated using service account: %s", key_path)
            return client
        except DefaultCredentialsError as exc:
            raise DefaultCredentialsError(
                f"Failed to authenticate with service account key {key_path!r}: {exc}"
            ) from exc

    def _build_dimension_filter(self) -> FilterExpression:
        """
        Build a FilterExpression restricting pagePath to inventory URLs.

        Returns:
            FilterExpression using BEGINS_WITH on pagePath.
        """
        return FilterExpression(
            filter=Filter(
                field_name="pagePath",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.BEGINS_WITH,
                    value=self._inventory_prefix,
                    case_sensitive=False,
                ),
            )
        )

    def _fetch_batch(self, offset: int) -> Any:
        """
        Execute a single paginated RunReport API call.

        Args:
            offset: Row offset (0-based) for pagination.

        Returns:
            RunReportResponse from the GA4 API.

        Raises:
            google.api_core.exceptions.GoogleAPIError: On API failure.
        """
        request = RunReportRequest(
            property=f"properties/{self._property_id}",
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            date_ranges=[
                DateRange(
                    start_date=f"{self._date_range_days}daysAgo",
                    end_date="yesterday",
                )
            ],
            dimension_filter=self._build_dimension_filter(),
            order_bys=[
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                    desc=False,  # ascending: lowest views first
                )
            ],
            limit=_PAGE_SIZE,
            offset=offset,
        )
        return self._client.run_report(request)

    def get_underexposed_pages(self) -> List[Dict[str, Any]]:
        """
        Fetch all inventory pages with pageviews below the configured threshold.
        Handles API pagination automatically and caps results at max_results.

        Returns:
            List of dicts, sorted ascending by page_views, each with:
                - page_path (str): e.g. '/inventory/2012-nissan-versa-sedan-4d-m71097/'
                - page_views (int)

        Raises:
            google.api_core.exceptions.GoogleAPIError: On API failure.
            RuntimeError: If the API response has an unexpected structure.
        """
        underexposed: List[Dict[str, Any]] = []
        offset = 0
        total_seen = 0
        filtered_count = 0

        logger.info(
            "Querying GA4 property %s for pages matching '%s' over last %d days...",
            self._property_id,
            self._inventory_prefix,
            self._date_range_days,
        )

        while True:
            response = self._fetch_batch(offset)
            batch_size = len(response.rows)
            total_available = response.row_count  # total rows across all pages

            logger.debug(
                "GA4 batch: offset=%d, batch_size=%d, total_available=%d",
                offset,
                batch_size,
                total_available,
            )

            if batch_size == 0:
                break

            for row in response.rows:
                try:
                    page_path = row.dimension_values[0].value
                    page_views = int(row.metric_values[0].value)
                except (IndexError, ValueError) as exc:
                    logger.warning("Skipping malformed GA4 row: %s", exc)
                    continue

                total_seen += 1

                if self._page_path_re and not self._page_path_re.match(page_path):
                    logger.debug("Skipping path (no match page_path_pattern): %s", page_path)
                    filtered_count += 1
                    continue

                if self._page_path_exclude_re and self._page_path_exclude_re.search(page_path):
                    logger.debug("Skipping path (matches page_path_exclude_pattern): %s", page_path)
                    filtered_count += 1
                    continue

                if page_views < self._threshold:
                    underexposed.append(
                        {"page_path": page_path, "page_views": page_views}
                    )

            offset += batch_size
            if offset >= total_available:
                break

        # Sort newest vehicle year first so scraping prioritises recent inventory
        # and max_results truncation keeps newer cars.
        underexposed.sort(key=lambda r: _slug_year(r["page_path"]), reverse=True)

        logger.info(
            "GA4 query complete: %d total inventory pages seen, "
            "%d filtered by path pattern, %d below threshold of %d views.",
            total_seen,
            filtered_count,
            len(underexposed),
            self._threshold,
        )

        if len(underexposed) > self._max_results:
            logger.warning(
                "Truncating results from %d to max_results=%d. "
                "Increase max_results in config.yaml to process more pages.",
                len(underexposed),
                self._max_results,
            )
            underexposed = underexposed[: self._max_results]

        return underexposed
