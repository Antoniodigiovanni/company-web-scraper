# Company Web Scraper

Scrapes the useful text from a company's public website so a downstream step can generate a business description. Fetches the homepage plus up to `max_subpages` high-signal subpages, extracts clean prose, and returns a pandas DataFrame.

**Fetch strategy:** fast raw HTTP via curl_cffi (TLS/JA3 browser impersonation) → Playwright headless Chromium fallback for JS-rendered pages and bot-protected sites.

---

## Installation

```bash
pip install curl_cffi>=0.7 trafilatura>=1.12 lxml pandas
# Playwright is optional but required when js_fallback=True triggers
pip install playwright && playwright install chromium
```

On Databricks, see the [Databricks section](#databricks) below.

---

## Quick start

```python
import pandas as pd
from scraper import CompanyScraper

df = pd.DataFrame([
    {"company_id": "acme", "website": "https://acme.com"},
    {"company_id": "globex", "website": "globex.com"},   # scheme added automatically
])

with CompanyScraper() as s:
    results = s.scrape(df, id_col="company_id", url_col="website")

print(results[["id", "status", "num_pages_ok", "combined_text"]].head())
```

`CompanyScraper` is a context manager. You can also call `.close()` manually or reuse one instance across many `.scrape()` calls in a single thread.

---

## Output DataFrame

One row per input company.

| Column | Type | Description |
|---|---|---|
| `id` | same as input | value from `id_col` |
| `url` | str | normalised input URL |
| `combined_text` | str | formatted page text; empty string on total failure |
| `num_pages_tried` | int | pages attempted |
| `num_pages_ok` | int | pages with non-empty extracted text |
| `pages` | list[dict] | per-page detail — `url`, `page_name`, `status`, `text_len`, `escalated_to_js` |
| `escalated_to_js` | bool | True if any page used Playwright |
| `retries_used` | int | total retries across all pages |
| `status` | str | `ok` / `partial` / `failed` |
| `error` | str \| None | first error message, if any |
| `total_time_s` | float | wall-clock seconds |
| `ts` | datetime (UTC) | completion timestamp |

`combined_text` format:

```
[Page name: home]
<text from homepage>

[Page name: about-us]
<text from /about-us>

[Page name: products]
...
```

---

## Constructor options

```python
CompanyScraper(
    max_subpages=8,
    high_value_keywords=None,     # list[str] — default provided
    low_value_keywords=None,      # list[str] — default provided
    retry_mode="full",            # "none" | "minimal" | "full"
    impersonate_profiles=("chrome124", "safari17_2", "firefox133"),
    timeout_s=15.0,
    subpage_workers=5,
    js_fallback=True,
    output_delta_path=None,       # Delta table path for results
    delta_log_path=None,          # Delta table path for per-scrape logs
    persist_raw_html=False,       # requires output_delta_path
    spark=None,                   # SparkSession; auto-detected on Databricks
)
```

### `max_subpages` (default: `8`)

Maximum pages scraped per company, including the homepage. The scraper always fetches the homepage and picks the top `max_subpages - 1` subpages by score.

### `high_value_keywords` / `low_value_keywords`

Lists of path substrings used to rank candidate subpages. Each keyword is matched case-insensitively against the URL path.

- **+2** for any `high_value_keywords` match
- **−1** for any `low_value_keywords` match
- Candidates sorted by score descending, then by path depth ascending (shallower first)

Default high-value: `about`, `about-us`, `company`, `who-we-are`, `what-we-do`, `mission`, `vision`, `team`, `products`, `product`, `services`, `solutions`, `technology`, `platform`, `industries`, `customers`, `case-studies`

Default low-value: `careers`, `jobs`, `press`, `blog`, `news`, `events`, `contact`, `legal`, `privacy`, `terms`, `cookie`, `login`, `signin`, `signup`, `cart`

Pass your own lists to override the defaults entirely:

```python
CompanyScraper(
    high_value_keywords=["platform", "solutions", "product"],
    low_value_keywords=["blog", "careers"],
)
```

### `retry_mode` (default: `"full"`)

Controls how many HTTP attempts are made before escalating to Playwright (or giving up when `js_fallback=False`).

| Mode | Behaviour |
|---|---|
| `"none"` | 1 attempt; blocking status or error → escalate immediately |
| `"minimal"` | 1 attempt + 1 retry with a different browser profile → escalate |
| `"full"` | 2 fast retries same profile (1 s → 2 s + jitter), 2 more rotating profile (4 s → 8 s + jitter) → escalate. 404 is always terminal. |

### `impersonate_profiles` (default: `("chrome124", "safari17_2", "firefox133")`)

curl_cffi browser fingerprint profiles to rotate through on retries. The starting profile is randomised per site. Accepted values are any profile supported by your installed curl_cffi version (e.g. `chrome110`, `safari15_5`, `firefox117`).

### `timeout_s` (default: `15.0`)

Per-request timeout in seconds for curl_cffi fetches.

### `subpage_workers` (default: `5`)

Number of parallel threads used to fetch subpages for a single company. The homepage is always fetched first (single-threaded); subpages are fetched in the thread pool.

### `js_fallback` (default: `True`)

When `True`, Playwright escalation is attempted when any of the following hold after retries:

- HTTP status is 403, 429, or 503
- Extracted text is empty or under 200 characters (JS-rendered content)
- Homepage yields zero discoverable links (SPA — Playwright re-fetches the homepage first)

Set to `False` to disable Playwright entirely (useful in environments where Chromium is not available).

### `persist_raw_html` (default: `False`)

When `True`, raw HTML for every fetched page is written to a separate Delta table at `{output_delta_path}_raw`. Requires `output_delta_path` to be set.

---

## Databricks

### One-time cluster setup

Install dependencies in a notebook cell or cluster init script:

```python
%pip install curl_cffi>=0.7 trafilatura>=1.12 lxml pandas playwright
```

Then install the Chromium browser binary (once per cluster):

```sh
%sh playwright install chromium
```

Restart the Python environment after `%pip install`.

### Usage on Databricks

Copy `scraper.py` into your repo or workspace, then import it normally.

```python
from scraper import CompanyScraper

# Read a table of companies
df = spark.table("company_data").select("company_id", "website").toPandas()

scraper = CompanyScraper(
    max_subpages=8,
    retry_mode="full",
    js_fallback=True,
    output_delta_path="dbfs:/mnt/data/scrape_results",
    delta_log_path="dbfs:/mnt/data/scrape_log",
)

results = scraper.scrape(df, id_col="company_id", url_col="website")
scraper.close()
```

Results are written to Delta once per `.scrape()` call (bulk append, not per row). The active `SparkSession` is picked up automatically — no need to pass `spark=` unless you want a specific session.

### Delta table schemas

**Results table** (`output_delta_path`):

```
id string, url string, combined_text string,
num_pages_tried int, num_pages_ok int,
pages array<struct<url:string, page_name:string, status:int, text_len:int, escalated_to_js:boolean>>,
escalated_to_js boolean, retries_used int,
status string, error string,
total_time_s double, ts timestamp
```

**Log table** (`delta_log_path`) — one row per company:

```
id string, url string, ts timestamp,
status string, subpages_tried int, subpages_ok int,
escalated_to_js boolean, retries_used int,
total_time_s double, error string
```

**Raw HTML table** (`{output_delta_path}_raw`, only when `persist_raw_html=True`) — one row per page:

```
id string, url string, page_url string,
html string, fetched_at timestamp, escalated_to_js boolean
```

### Large-scale parallelism

`CompanyScraper` is designed for caller-level parallelism. The recommended pattern on Databricks is to shard the input DataFrame and run one scraper per shard in separate tasks or threads, each writing to the same Delta table. Delta's optimistic concurrency handles concurrent appends; the scraper has a built-in 3-attempt retry loop for `ConcurrentAppendException`.

```python
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from scraper import CompanyScraper

def scrape_shard(shard_df: pd.DataFrame) -> pd.DataFrame:
    with CompanyScraper(
        output_delta_path="dbfs:/mnt/data/scrape_results",
        delta_log_path="dbfs:/mnt/data/scrape_log",
    ) as s:
        return s.scrape(shard_df, id_col="company_id", url_col="website")

shards = [df.iloc[i::4] for i in range(4)]   # 4 shards

with ThreadPoolExecutor(max_workers=4) as pool:
    futures = [pool.submit(scrape_shard, shard) for shard in shards]
    results = pd.concat([f.result() for f in futures], ignore_index=True)
```

> **Note:** Use a **single-node cluster** or dedicate one job task per shard. Running Playwright inside Spark executor processes (via UDFs) is not supported.

### Resume / deduplication

The scraper does not track which companies have already been processed. Filter your input DataFrame before calling `.scrape()`:

```python
already_done = spark.read.format("delta").load("dbfs:/mnt/data/scrape_results") \
    .select("id").toPandas()["id"].tolist()

pending = df[~df["company_id"].isin(already_done)]
results = scraper.scrape(pending, id_col="company_id", url_col="website")
```

---

## Error handling

| Scenario | Behaviour |
|---|---|
| Network timeout | counted as a retry, then escalate or mark page failed |
| HTTP 404 | terminal for that page, no retry |
| All pages fail | `status="failed"`, `combined_text=""` |
| Some pages succeed | `status="partial"`, `error` = first error message |
| Playwright not installed but escalation triggered | `ImportError` with install instructions |
| Delta path set but no Spark session | `RuntimeError` at init time |
| `persist_raw_html=True` without `output_delta_path` | `ValueError` at init time |
