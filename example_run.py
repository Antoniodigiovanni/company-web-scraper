# Databricks notebook source
# MAGIC %md
# MAGIC # CompanyScraper: batched run with Delta checkpointing/resume
# MAGIC
# MAGIC Every `CompanyScraper` config option is set explicitly below. The input is processed in
# MAGIC batches of `BATCH_SIZE` companies; each batch is scraped with one `.scrape()` call, and
# MAGIC `output_delta_path`/`delta_log_path` make that call **append the batch straight to the
# MAGIC Delta tables** (see `_write_delta` in `scraper.py`) before moving to the next batch.
# MAGIC
# MAGIC That append-per-batch is the checkpoint — there's no separate checkpoint file. If the
# MAGIC cluster dies or the notebook is cancelled mid-run, whatever batches already completed are
# MAGIC durably in the Delta table. Re-running this notebook queries that same table for `id`s
# MAGIC already present and skips them, so it picks up wherever it left off.
# MAGIC
# MAGIC Runs on the driver only (no Spark UDFs) — use a single-node cluster, per the README's
# MAGIC note that Playwright isn't supported inside executor processes. For higher throughput,
# MAGIC combine this with the README's sharding pattern: run one copy of this notebook per shard,
# MAGIC each with its own `pending` slice, all appending to the same Delta tables.

# COMMAND ----------

import pandas as pd
from delta.tables import DeltaTable

from scraper import CompanyScraper, DEFAULT_HIGH_VALUE_KEYWORDS, DEFAULT_LOW_VALUE_KEYWORDS

# COMMAND ----------

# MAGIC %md
# MAGIC ## Config
# MAGIC
# MAGIC `output_delta_path` and `delta_log_path` are the two Delta tables `CompanyScraper` writes
# MAGIC to internally on every `.scrape()` call. `output_delta_path` is also what we read back from
# MAGIC to figure out which companies are already done.

# COMMAND ----------

dbutils.widgets.text("output_delta_path", "dbfs:/mnt/data/scrape_results")
dbutils.widgets.text("delta_log_path", "dbfs:/mnt/data/scrape_log")
dbutils.widgets.text("batch_size", "25")

OUTPUT_DELTA_PATH = dbutils.widgets.get("output_delta_path")
DELTA_LOG_PATH = dbutils.widgets.get("delta_log_path")
BATCH_SIZE = int(dbutils.widgets.get("batch_size"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Input
# MAGIC
# MAGIC Inline for this example — in practice, load this from a table (e.g.
# MAGIC `spark.table("companies").toPandas()`).

# COMMAND ----------

COMPANIES = pd.DataFrame(
    [
        ("tesla", "https://www.tesla.com"),
        ("vercel", "https://vercel.com"),
        ("discord", "https://www.discord.com"),
    ],
    columns=["company_id", "website"],
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resume: filter out companies already appended to the Delta table
# MAGIC
# MAGIC On a fresh run `output_delta_path` won't exist yet, so everything is pending. On a resumed
# MAGIC run it holds one row per completed company from prior batches — same idea as the README's
# MAGIC "Resume / deduplication" section, just done automatically here instead of as a manual step.

# COMMAND ----------

def load_pending(df: pd.DataFrame) -> pd.DataFrame:
    """Drops rows whose company_id already has a row in output_delta_path."""
    if not DeltaTable.isDeltaTable(spark, OUTPUT_DELTA_PATH):
        return df
    done_ids = set(
        spark.read.format("delta").load(OUTPUT_DELTA_PATH).select("id").distinct().toPandas()["id"]
    )
    return df[~df["company_id"].isin(done_ids)]


pending = load_pending(COMPANIES)
print(f"{len(COMPANIES) - len(pending)} already done, {len(pending)} remaining")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scraper
# MAGIC
# MAGIC `spark=None` picks up the notebook's active Spark session. `CompanyScraper` manages its
# MAGIC own dedicated Playwright thread internally (`_pw_executor` in `scraper.py`), so `.scrape()`
# MAGIC can be called directly from the notebook thread — no extra threading needed here.

# COMMAND ----------

scraper = CompanyScraper(
    max_subpages=8,
    high_value_keywords=DEFAULT_HIGH_VALUE_KEYWORDS,
    low_value_keywords=DEFAULT_LOW_VALUE_KEYWORDS,
    retry_mode="full",
    impersonate_profiles=("chrome124", "safari17_2", "firefox133"),
    timeout_s=15.0,
    subpage_workers=5,
    js_fallback=True,
    output_delta_path=OUTPUT_DELTA_PATH,
    delta_log_path=DELTA_LOG_PATH,
    persist_raw_html=False,
    spark=None,
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run: one `.scrape()` call per batch, each appending to Delta on completion

# COMMAND ----------

try:
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending.iloc[start : start + BATCH_SIZE]
        result = scraper.scrape(batch, id_col="company_id", url_col="website")
        # scraper.scrape() already appended `result` to OUTPUT_DELTA_PATH/DELTA_LOG_PATH above —
        # that Delta write *is* the checkpoint for this batch.
        for _, r in result.iterrows():
            print(f"[{r['id']}] {r['status']} ({r['num_pages_ok']}/{r['num_pages_tried']} pages)")
finally:
    scraper.close()

print(f"Done. Results in {OUTPUT_DELTA_PATH}, log in {DELTA_LOG_PATH}")
