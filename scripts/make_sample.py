"""Creates two sample files to demo incremental loading.
batch1: Aug 1 - Sep 30. batch2: Oct 1-14 (new) + 20 corrected old orders (updates)
+ 3 bad rows + a sharp revenue drop on the last day (triggers an alert)."""
import numpy as np
import pandas as pd
from pathlib import Path

rng = np.random.default_rng(7)
out = Path("samples"); out.mkdir(exist_ok=True)
regions, products = ["North", "South", "East", "West"], ["Laptop", "Phone", "Tablet", "Headset"]
n = 0


def make(days, per_day):
    global n
    rows = []
    for d in days:
        for _ in range(per_day(d)):
            n += 1
            rows.append({"order_id": f"ORD-{n:06d}", "order_date": d.strftime("%Y-%m-%d"),
                         "region": rng.choice(regions), "product": rng.choice(products),
                         "amount": round(float(rng.gamma(4, 60)), 2)})
    return pd.DataFrame(rows)


b1 = make(pd.date_range("2026-08-01", "2026-09-30"), lambda d: int(rng.integers(35, 46)))
b1.to_csv(out / "sales_batch1.csv", index=False)

oct_days = pd.date_range("2026-10-01", "2026-10-14")
b2 = make(oct_days, lambda d: 6 if d == oct_days[-1] else int(rng.integers(35, 46)))
fixes = b1.sample(20, random_state=1).copy()
fixes["amount"] = (fixes["amount"] * 1.5).round(2)
bad = pd.DataFrame([
    {"order_id": "", "order_date": "2026-10-05", "region": "East", "product": "Phone", "amount": "100"},
    {"order_id": "ORD-X1", "order_date": "not-a-date", "region": "West", "product": "Tablet", "amount": "80"},
    {"order_id": "ORD-X2", "order_date": "2026-10-06", "region": "North", "product": "Laptop", "amount": "abc"}])
pd.concat([b2, fixes, bad]).to_csv(out / "sales_batch2.csv", index=False)
print("wrote", list(out.glob("*.csv")))
