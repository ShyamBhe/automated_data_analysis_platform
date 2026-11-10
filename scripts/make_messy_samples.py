"""Creates three deliberately messy files (CSV, JSON, XML) to try the cleaning module."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

rng = np.random.default_rng(11)
out = Path("samples"); out.mkdir(exist_ok=True)

# 1) CSV: sensor readings with outliers, text errors, null tokens, case issues, duplicates
n = 300
df = pd.DataFrame({
    "timestamp": pd.date_range("2026-09-01", periods=n, freq="h").strftime("%Y-%m-%d %H:%M"),
    "sensor_id": rng.choice(["S1", "S2", "S3", "S4"], n),
    "temperature": rng.normal(21, 1.8, n).round(1).astype(object),
    "humidity": rng.normal(50, 6, n).round(1).astype(object),
    "status": rng.choice(["ok", "OK", " Ok", "fail", "FAIL"], n, p=[.4, .2, .1, .2, .1]),
})
for i, v in zip(rng.choice(n, 6, replace=False), [999, -50, 85, 120, -40, 300]):
    df.loc[i, "temperature"] = v
for i in rng.choice(n, 5, replace=False):
    df.loc[i, "temperature"] = "ERR"
for i in rng.choice(n, 14, replace=False):
    df.loc[i, "humidity"] = rng.choice(["N/A", "", "null", "?"])
df = pd.concat([df, df.sample(10, random_state=2)])
df.to_csv(out / "messy_sensors.csv", index=False)

# 2) JSON: nested customers
rows = []
for i in range(1, 121):
    rows.append({"id": i, "name": f"Customer {i}",
                 "age": int(rng.integers(18, 80)),
                 "active": str(rng.choice(["yes", "no"])),
                 "address": {"city": str(rng.choice(["Kathmandu", "Pokhara", "Lalitpur", "kathmandu"])),
                             "zip": str(rng.integers(44000, 44999))},
                 "spent": round(float(rng.gamma(3, 100)), 2),
                 "tags": ["new"] if i % 7 == 0 else []})
for i, v in zip([4, 30, 77], [250, 999, -5]):
    rows[i]["age"] = v
for i in [8, 15, 40, 41, 90]:
    rows[i]["age"] = None
for i in [12, 60]:
    rows[i]["age"] = "n/a"
rows[20]["spent"] = 1_000_000.0
rows[55]["address"]["city"] = None
(out / "messy_customers.json").write_text(json.dumps({"customers": rows}, indent=1))

# 3) XML: orders with mixed date formats and odd totals
lines = ['<?xml version="1.0"?>', "<orders>"]
for i in range(1, 81):
    total = round(float(rng.gamma(3, 40)), 2)
    if i in (9, 33): total = "n/a"
    if i in (14, 50, 51): total = ""
    if i == 22: total = 99999
    if i == 66: total = "abc"
    d = pd.Timestamp("2026-07-01") + pd.Timedelta(days=int(rng.integers(0, 90)))
    ds = d.strftime(rng.choice(["%Y-%m-%d", "%d/%m/%Y", "%b %d %Y"]))
    lines.append(f'  <order id="{i}"><customer>C{int(rng.integers(1, 30))}</customer>'
                 f"<total>{total}</total><date>{ds}</date>"
                 f"<status>{rng.choice(['paid', 'Paid', 'pending'])}</status></order>")
lines.append("</orders>")
(out / "messy_orders.xml").write_text("\n".join(lines))
print("wrote", sorted(p.name for p in out.glob("messy_*")))
