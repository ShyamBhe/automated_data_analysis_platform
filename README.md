# AutoAnalytics

Automated data analysis that keeps itself up to date when new or changed data arrives.
Link: https://automated-data-analysis-platform.onrender.com/


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
DATA_DIR, DB_PATH, SCAN_SECONDS, DROP_ALERT_PCT (default 20), ANOMALY_Z (default 3)

## Deploy with Docker
    docker build -t autoanalytics .
    docker run -d --name autoanalytics --restart unless-stopped \
      -v autoanalytics_data:/data -p 8000:8000 autoanalytics
Keep a single worker (the scheduler runs inside the app) and mount a persistent volume at /data.
Add authentication (e.g. Caddy basic auth) before exposing it to the internet.
