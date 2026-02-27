# GA4 Inventory Tracker

**Stop losing leads to inventory pages nobody sees.**

GA4 Inventory Tracker is a lightweight Python tool built for automotive dealerships. It connects to your Google Analytics 4 property, identifies vehicle listing pages that are underperforming in organic and direct traffic, and exports a ready-to-use CSV of stock numbers and VINs — so your marketing team can take immediate action.

> Point it at any GA4-connected dealership website. Configure once. Run on a schedule. Know exactly which vehicles need promotion.

---

## Why It Matters

Every dealership has inventory pages that slip through the cracks — vehicles that sit on the lot with almost zero digital visibility. These aren't just missed impressions; they're missed sales.

GA4 Inventory Tracker surfaces those vehicles automatically:

- No manual GA4 report building
- No spreadsheet gymnastics
- No guessing which cars need a push

The output is a clean CSV of VIN + stock number pairs ready to feed into any advertising workflow — Google Ads inventory campaigns, Facebook/Meta dynamic ads, email remarketing, or your CRM.

---

## How It Works

1. Authenticates with GA4 via a Google Cloud service account (read-only access)
2. Queries all `/inventory/` pages over a configurable rolling date window
3. Filters out stale and deleted pages using configurable path patterns
4. Flags pages with fewer pageviews than your threshold, capped at a configurable maximum
5. Sorts results newest vehicle year first, so recent inventory is always prioritized
6. Scrapes each flagged URL to extract **stock number** and **VIN**
7. Exports a timestamped CSV containing only rows where both values were found

---

## Prerequisites

- Python 3.9 or newer
- A Google account with access to the GA4 property for your dealership's website
- A Google Cloud project (free tier is sufficient)
- Your dealership website must use standard `/inventory/` URL paths

---

## Setup Guide

### Step 1 — Create a Google Cloud Project

1. Go to [https://console.cloud.google.com/](https://console.cloud.google.com/)
2. Click the project selector at the top → **New Project**
3. Give it a name (e.g. `ga4-inventory-tracker`) and click **Create**
4. Make sure the new project is selected in the top bar

---

### Step 2 — Enable the Google Analytics Data API

1. In Google Cloud Console, open the left menu → **APIs & Services** → **Library**
2. Search for **Google Analytics Data API**
3. Click it, then click **Enable**

---

### Step 3 — Create a Service Account

1. In Google Cloud Console, open the left menu → **IAM & Admin** → **Service Accounts**
2. Click **+ Create Service Account**
3. Fill in a name (e.g. `ga4-inventory-tracker`) and click **Create and Continue**
4. Skip the role step (permission is granted directly in GA4) → **Continue** → **Done**

---

### Step 4 — Generate and Download a JSON Key

1. On the Service Accounts list page, find the account you just created
2. Click the three-dot menu (⋮) → **Manage keys** → **Add Key** → **Create new key**
3. Select **JSON** → **Create**
4. Move the downloaded file into the `credentials/` folder in this project:

```
ga4_inventory_tracker/
└── credentials/
    └── service_account.json    ← rename/move it here
```

> **Security note:** Never commit the `credentials/` folder or `config.yaml` to version control. Both are listed in `.gitignore`.

---

### Step 5 — Grant the Service Account Access to GA4

1. Open [Google Analytics](https://analytics.google.com/) and select your dealership's property
2. Go to **Admin** (gear icon, lower-left) → **Property Access Management**
3. Click **+** → **Add users**
4. Enter the service account email (e.g. `ga4-inventory-tracker@YOUR-PROJECT-ID.iam.gserviceaccount.com`)
5. Set the role to **Viewer** → **Add**

---

### Step 6 — Find Your GA4 Property ID

1. In Google Analytics, go to **Admin** → **Property Settings**
2. The **Property ID** is a numeric value, e.g. `123456789`
3. Copy it — you'll need it in the next step

---

### Step 7 — Configure the Tool

Copy the example config:

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and fill in the values:

#### Required — GA4 / Authentication

| Key | Description | Example |
|---|---|---|
| `ga4_property_id` | Your GA4 Property ID | `"123456789"` |
| `service_account_key_path` | Path to the JSON key file | `"credentials/service_account.json"` |

#### Site

| Key | Description | Example |
|---|---|---|
| `domain` | Site domain (no protocol) | `"your-dealership.com"` |
| `inventory_path_prefix` | URL path prefix to monitor | `"/inventory/"` |

#### Query

| Key | Description | Example |
|---|---|---|
| `date_range_days` | Days to look back in GA4 (rolling window) | `30` |
| `pageview_threshold` | Flag pages with fewer views than this | `10` |
| `max_results` | Max pages to scrape per run | `200` |
| `page_path_pattern` | Regex a path **must match** to be processed. Blocks sub-paths like `/inventory/slug/VIN`. Set to `""` to disable. | `"^/inventory/[^/]+/?$"` |
| `page_path_exclude_pattern` | Regex a path **must not match**. Blocks placeholder/garbage slugs. Set to `""` to disable. | `"(-\\d+){3,}/?$"` |

#### Output

| Key | Description | Example |
|---|---|---|
| `output_csv_path` | Base path for the output file. A timestamp (`_YYYYMMDD_HHMMSS`) is appended before `.csv` at runtime. | `"output/underexposed_inventory.csv"` |

#### Scraping

| Key | Description | Example |
|---|---|---|
| `scrape_delay_seconds` | Pause between page requests (seconds) | `1.0` |
| `request_timeout_seconds` | HTTP timeout per page | `10` |
| `user_agent` | User agent string for requests | `"ga4-inventory-tracker/1.0"` |
| `max_retries` | Retry attempts on transient HTTP errors | `3` |

#### UTM Parameters

UTM parameters are appended to every URL fetched by the scraper so bot traffic is identifiable in GA4.

| Key | Description | Default |
|---|---|---|
| `utm_source` | UTM source tag | `"InventoryBot"` |
| `utm_medium` | UTM medium tag | `"under_exposed"` |

#### Detection Patterns

These control how the scraper locates VIN and stock number on each page. Adjust if your site's HTML structure differs from the defaults.

| Key | Description |
|---|---|
| `vin_text_pattern` | Regex to find a VIN in visible page text. Group 1 must capture the 17-char VIN. |
| `stock_patterns` | Ordered list of regexes for stock number extraction. Group 1 captures the stock number. First match wins. |
| `jsonld_vin_fields` | JSON-LD field names checked for VIN (in order). Checked before the text pattern fallback. |

#### Testing

| Key | Description | Default |
|---|---|---|
| `test_mode` | Set to `true` to activate test mode (same as `--test` CLI flag) | `false` |
| `test_limit` | Number of pages to process in test mode | `5` |

---

### Step 8 — Install Dependencies

```bash
pip install -r requirements.txt
```

Or with a virtual environment (recommended):

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

---

### Step 9 — Run the Tool

```bash
python main.py
```

To use a different config file:

```bash
python main.py --config path/to/config.yaml
```

#### Test Mode

Run against a small sample with verbose (DEBUG) logging to verify everything works before a full run:

```bash
python main.py --test
```

Test mode limits scraping to `test_limit` records (default: 5) and sets the log level to DEBUG so you can see exactly which patterns matched, which paths were filtered, and what each page returned.

Test mode can also be activated from `config.yaml` by setting `test_mode: true`.

---

## Output CSV

The filename includes a run timestamp, e.g. `output/underexposed_inventory_20260227_093012.csv`.

Only rows where **both** stock number and VIN were successfully extracted are written.

| Column | Description |
|---|---|
| `stock_number` | Dealer stock number extracted from the page (e.g. `M71097`) |
| `vin_number` | 17-character VIN extracted from the page |

The CSV is ready to upload directly into:
- Google Ads inventory vehicle campaigns
- Meta/Facebook dynamic inventory ads
- VinSolutions, DealerSocket, or similar CRM imports
- Email marketing platforms

---

## Scheduling

To run this automatically on a schedule:

- **Windows**: Task Scheduler pointing at `python main.py`
- **macOS/Linux**: `cron` or `systemd` timer
- **Cloud**: Any scheduler (AWS EventBridge, GCP Cloud Scheduler, GitHub Actions) targeting the script in a container or VM

---

## Troubleshooting

### `FileNotFoundError: Service account key file not found`
The path in `service_account_key_path` does not match the actual file location. Check that you placed the JSON file in `credentials/` and spelled the filename correctly.

### `403 Permission Denied` from GA4 API
The service account has not been granted access to the GA4 property. Repeat Step 5, ensuring you used the correct service account email address.

### `ga4_property_id is still set to a placeholder value`
Open `config.yaml` and replace `YOUR_PROPERTY_ID` with your real numeric Property ID.

### `google.api_core.exceptions.NotFound: 404 Property not found`
The `ga4_property_id` value is wrong. Double-check it in GA4 → Admin → Property Settings.

### CSV is empty / all rows filtered
GA4 may be returning only stale/deleted pages that 404. Run with `--test` and check the DEBUG log for `Skipping path` messages. You may need to relax `page_path_pattern` or `page_path_exclude_pattern` in `config.yaml`.

### All `vin_number` / `stock_number` cells are empty
The page structure may have changed. Run with `--test` to see DEBUG-level extraction detail. Adjust `vin_text_pattern`, `stock_patterns`, or `jsonld_vin_fields` in `config.yaml` to match the current HTML.

### Rate limiting or connection timeouts
Increase `scrape_delay_seconds` in `config.yaml` to slow down the scraper.

---

## License

MIT
