# AutoAnalytics

An open-source platform that analyzes data automatically and keeps itself up to date when new or
changed data arrives. It has two parts:

1. **Sales dashboard** (`/`): watches an inbox for CSV/Excel order files, loads only what is new or
   changed, and refreshes KPIs, charts and alerts.
2. **Data cleaning and analysis** (`/datasets`): upload any CSV, TSV, JSON, JSONL, XML or Excel file,
   find missing values, odd values, duplicates and outliers, fix them, then see an automatic analysis.

## Quick start
    pip install -r requirements.txt
    python scripts/make_sample.py            # sales sample files  -> ./samples
    python scripts/make_messy_samples.py     # messy CSV/JSON/XML  -> ./samples
    uvicorn app.main:app --reload
    # open http://localhost:8000

Ready-made sample files are already in `samples/`.

## 1. Sales dashboard
Feed it data in three ways:
- Click **Upload** on the dashboard.
- Drop a `.csv`/`.xlsx` into `data/inbox/` (scanned every 30 s, change with `SCAN_SECONDS`).
- `curl -F file=@samples/sales_batch1.csv http://localhost:8000/api/upload`

Required columns: `order_id, order_date, region, product, amount`.
Demo: upload `sales_batch1.csv`, then `sales_batch2.csv` (new days, 20 corrected orders, 3 bad rows,
and a revenue drop that raises an alert).

| Concern | How it is handled |
|---|---|
| Raw data never modified | originals kept in `data/raw/` |
| Same file twice | skipped (SHA-256 in the `files` table) |
| New and changed rows | upsert by `order_id` |
| Late or corrected data | only the affected days are re-aggregated |
| Bad rows | quarantined (`/api/quarantine`), with an alert |
| Missing columns (schema drift) | run fails with an alert, file moved to `data/failed/` |
| Analysis | KPIs, by region and product, z-score anomalies, 7-day drop alert |
| History | run history and alerts tables |

## 2. Data cleaning and analysis (`/datasets`)
1. **Upload** CSV, TSV, JSON, JSONL, XML or XLSX. Nested JSON and XML become columns such as `address.city`.
2. **Profile**: column types are detected, then missing values, odd values ("N/A", "ERR", "?"),
   duplicates and outliers (IQR or z-score) are counted, with a 0-100 quality score.
3. **Clean**: choose a fix per column (suggestions are pre-selected): fill missing values (median, mean,
   most common, a value, interpolate, copy from neighbors), limit or replace outliers, standardize text
   case, remove duplicates, rows or columns. You get a before/after comparison, a log of every change,
   and a cleaned CSV. The original is never modified.
4. **Analyze** (original or cleaned data): plain-language findings, histograms, category counts,
   trends over time, a correlation heatmap and group comparisons.

Notes: outliers are statistical, not proof of error, so you choose the fix. Correlation is not causation.
Run the analysis on the cleaned data for conclusions.

## API
| Method and path | Purpose |
|---|---|
| `POST /api/upload`, `POST /api/scan` | sales files |
| `GET /api/summary`, `/api/runs`, `/api/alerts`, `/api/quarantine` | sales dashboard data |
| `POST /api/datasets` | upload a dataset |
| `GET /api/datasets`, `GET /api/datasets/{id}` | list, profile and suggested fixes |
| `POST /api/datasets/{id}/clean` | apply rules (`{"auto": true}` uses the suggestions) |
| `GET /api/datasets/{id}/analysis?version=original\|cleaned` | analysis |
| `GET /api/datasets/{id}/preview`, `/download` | preview rows, download CSV |
| `GET /api/health`, `GET /api/config` | health check, demo flag |

## Project layout
    app/main.py       API, login, demo mode, scheduler
    app/pipeline.py   sales ingestion and incremental analysis
    app/loaders.py    CSV / JSON / XML / Excel readers
    app/cleaning.py   type detection, profiling, outliers, cleaning rules
    app/analysis.py   automatic analysis and findings
    app/datasets.py   dataset API
    static/           dashboard (index.html) and cleaning page (datasets.html)
    scripts/          sample data generators
    samples/          ready-made sample files

## Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `data` | where files are stored |
| `DB_PATH` | `data/app.db` | SQLite database |
| `SCAN_SECONDS` | 30 | inbox scan interval |
| `MAX_UPLOAD_MB` | 10 | upload size limit |
| `DROP_ALERT_PCT` | 20 | revenue-drop alert threshold |
| `ANOMALY_Z` | 3 | z-score for sales anomalies |
| `APP_USER`, `APP_PASSWORD` | admin, (empty) | set a password to require a login |
| `DEMO_MODE` | off | read-only public demo |

## Security and sharing
This project is open source (MIT, see LICENSE): anyone can run **their own copy**. A deployed instance is
one shared copy, so protect it:
- **Private instance:** set `APP_PASSWORD`. Every page and API call then needs a login
  (`/api/health` stays open for health checks). The login only protects you over HTTPS.
- **Public demo:** set `DEMO_MODE=true`. Sample data is preloaded; visitors cannot upload or delete.
  Cleaning works on the sample datasets. Never put real data in a demo instance.
- Do not set both on the same instance.

## Deploy
The app is a long-running Python server with a built-in scheduler, a SQLite database and files on disk.
It needs a host that runs a server process and offers a **persistent disk**.

**Render (recommended managed option)**
1. Push this project to GitHub.
2. In Render: New > Web Service, connect the repository, runtime **Docker**.
3. Choose a **paid instance** and add a **disk mounted at `/data`** (free instances lose files on every
   restart and redeploy, and cannot have disks).
4. Set the health check path to `/api/health`, and set `APP_PASSWORD` (private) or `DEMO_MODE=true` (demo).
5. A `render.yaml` is included as a starting point; check the plan and disk settings in the dashboard.

**Netlify: not suitable.** Netlify hosts static sites and short-lived serverless functions. It cannot run
this server, its scheduler, or keep a database and uploaded files.

**Cold starts on Render's free plan:** free services spin down after about 15 minutes without traffic, so
the next visit waits while the app restarts. Paid instances do not spin down.

**Your own server or laptop (Docker)**
    docker build -t autoanalytics .
    docker run -d --name autoanalytics --restart unless-stopped \
      -v autoanalytics_data:/data -p 8000:8000 autoanalytics
Keep a single worker (the scheduler runs inside the app) and back up the `/data` volume.
Put a reverse proxy with HTTPS (for example Caddy) in front of it.

## Scaling up
SQLite -> PostgreSQL (`app/db.py`), APScheduler -> Prefect/Airflow/Dagster, local folders -> S3,
pandas -> Polars/DuckDB/Spark. Multiple servers need the database and storage moved off the local disk first.

## License
MIT, see `LICENSE`.