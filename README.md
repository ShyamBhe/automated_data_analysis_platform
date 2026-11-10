# AutoAnalytics

Automated data analysis that keeps itself up to date when new or changed data arrives.

## Run
    pip install -r requirements.txt
    python scripts/make_sample.py          # optional demo files in ./samples
    uvicorn app.main:app --reload
    # open http://localhost:8000

## Feed it data (three ways)
1. Click **Upload** in the dashboard.
2. Drop a .csv/.xlsx into `data/inbox/` (scanned every 30s, set SCAN_SECONDS to change).
3. `curl -F file=@samples/sales_batch1.csv http://localhost:8000/api/upload`

Required columns: `order_id, order_date, region, product, amount`.

Demo: upload `sales_batch1.csv`, then `sales_batch2.csv`. Batch 2 adds new days, corrects
20 old orders (updates, not duplicates), has 3 bad rows (quarantined) and a revenue drop (alert).

## How it works
| Concern | Where |
|---|---|
| Raw zone (never modified) | `data/raw/` |
| Idempotency (same file twice = skipped) | SHA-256 in `files` table |
| New + changed rows | upsert by `order_id` |
| Late / corrected data | only affected days re-aggregated (`daily_metrics`) |
| Bad data | `quarantine` table, `/api/quarantine` |
| Schema drift | missing columns fail the run with an alert, file goes to `data/failed/` |
| Analysis | KPIs, by region/product, z-score anomalies, 7-day drop alert |
| Run history + alerts | `runs`, `alerts` tables |

## Adapting to your data
Edit `REQUIRED` and the validation masks in `app/pipeline.py`, and the aggregations in `_refresh_results`.

## Scaling up
SQLite -> PostgreSQL (`app/db.py`), APScheduler -> Prefect/Airflow/Dagster, local folders -> S3,
pandas -> Polars/DuckDB/Spark.

## Environment variables
DATA_DIR, DB_PATH, SCAN_SECONDS, DROP_ALERT_PCT (default 20), ANOMALY_Z (default 3),
MAX_UPLOAD_MB (default 10), APP_USER (default admin), APP_PASSWORD, DEMO_MODE

## Deploy with Docker
    docker build -t autoanalytics .
    docker run -d --name autoanalytics --restart unless-stopped \
      -v autoanalytics_data:/data -p 8000:8000 autoanalytics
Keep a single worker (the scheduler runs inside the app) and mount a persistent volume at /data.
Add authentication (e.g. Caddy basic auth) before exposing it to the internet.

## Security and sharing
This project is open source (MIT, see LICENSE): everyone can run **their own copy**. A deployed
instance is a single shared copy, so protect it:
- **Private instance:** set `APP_PASSWORD` (and optionally `APP_USER`). Every page and API call then
  requires a login. `/api/health` stays open for platform health checks.
- **Public demo:** set `DEMO_MODE=true`. Uploads are disabled, the sample data is loaded automatically,
  and visitors can only look at the dashboard. Never put real data in a demo instance.
- Uploads are limited to `MAX_UPLOAD_MB` (default 10).
- Use HTTPS (Render and Caddy provide it) so passwords are not sent in clear text.

## Data cleaning module (JSON, XML, CSV, Excel)
Open `/datasets`. Upload any CSV/TSV/JSON/JSONL/XML/XLSX file (nested JSON and XML are flattened to
columns such as `address.city`).
1. **Profile**: column types are inferred, then missing values, odd values ("N/A", "ERR", "?"),
   duplicates and outliers (IQR or z-score) are counted. A quality score summarizes the file.
2. **Choose fixes** per column (suggestions are pre-selected): fill missing values (median, mean,
   most common, value, interpolate, copy neighbors), cap or replace outliers, standardize text case,
   remove duplicates, rows or columns.
3. **Apply**: you get a before/after comparison, a log of every change, and a cleaned CSV download.
The original is never modified. API: `/api/datasets` (POST upload, GET list), `/api/datasets/{id}`,
`/api/datasets/{id}/clean`, `/preview`, `/download`.
Try `python scripts/make_messy_samples.py` for a messy CSV, JSON and XML file.
In `DEMO_MODE`, the messy samples are preloaded; cleaning works but uploads and deletes are disabled.

### Automatic analysis
Each dataset page also shows an **Analysis** section (original or cleaned data): plain-language findings,
distribution charts for numeric columns, category counts, trends over time (first date column),
a correlation heatmap, and group comparisons (average of a number per category). API:
`GET /api/datasets/{id}/analysis?version=original|cleaned`.
