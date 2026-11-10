"""Automatic analysis of a dataset: summaries, distributions, relationships, trends, findings."""
import numpy as np
import pandas as pd

from .cleaning import _f, outlier_bounds


def _fmt(x):
    if x is None:
        return "n/a"
    x = float(x)
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.3g}"


def _id_like(s):
    v = s.dropna()
    return len(v) >= 10 and v.nunique() / len(v) > 0.98 and bool((v % 1 == 0).all())


def _hist(s, bounds):
    v, hidden = s.dropna().astype(float), 0
    if bounds:  # keep extreme values from flattening the chart; report how many were left out
        q1, q3 = v.quantile(.25), v.quantile(.75)
        iqr = q3 - q1
        keep = v[(v >= q1 - 3 * iqr) & (v <= q3 + 3 * iqr)]
        if len(keep):
            hidden, v = len(v) - len(keep), keep
    if v.nunique() <= 1:
        return {"labels": [_fmt(v.iloc[0])], "ranges": [_fmt(v.iloc[0])],
                "counts": [int(len(v))], "hidden": hidden}
    counts, edges = np.histogram(v, bins=int(min(12, max(5, np.sqrt(len(v))))))
    return {"labels": [_fmt((edges[i] + edges[i + 1]) / 2) for i in range(len(counts))],
            "ranges": [f"{_fmt(edges[i])} to {_fmt(edges[i + 1])}" for i in range(len(counts))],
            "counts": [int(c) for c in counts], "hidden": hidden}


def _period(days):
    return "hour" if days <= 2 else "day" if days <= 100 else "week" if days <= 500 else "month"


def _bucket(ts, period):
    if period == "hour":
        return ts.dt.floor("h")
    if period == "day":
        return ts.dt.floor("D")
    return ts.dt.to_period("W" if period == "week" else "M").dt.start_time


def _strength(r):
    a = abs(r)
    return ("strong" if a >= .7 else "moderate" if a >= .4 else "weak") + \
           (" positive" if r > 0 else " negative")


def analyze(df: pd.DataFrame, notes: dict):
    rows = len(df)
    kinds = {c: notes[c]["kind"] for c in df.columns}
    empty = {"overview": {"rows": rows, "cols": len(df.columns)}, "insights": ["No rows to analyze."],
             "numeric": [], "categorical": [], "timeseries": None, "correlation": None, "groups": []}
    if rows == 0:
        return empty
    num_all = [c for c in df.columns if kinds[c] == "numeric" and df[c].notna().any()]
    nums = [c for c in num_all if not _id_like(df[c])]
    cats = [c for c in df.columns if kinds[c] in ("categorical", "boolean") and df[c].nunique() >= 2]
    dts = [c for c in df.columns if kinds[c] == "datetime" and df[c].notna().any()]
    insights = []

    # ---- numeric columns
    numeric = []
    for c in nums[:8]:
        v = df[c].dropna().astype(float)
        b = outlier_bounds(df[c])
        out = int(((v < b[0]) | (v > b[1])).sum()) if b else 0
        skew = float(v.skew()) if len(v) > 2 else 0.0
        numeric.append({"name": c, "skew": _f(skew),
                        "stats": {"min": _f(v.min()), "q1": _f(v.quantile(.25)), "median": _f(v.median()),
                                  "mean": _f(v.mean()), "q3": _f(v.quantile(.75)), "max": _f(v.max()),
                                  "std": _f(v.std())},
                        "outliers": {"count": out, "lower": _f(b[0]) if b else None,
                                     "upper": _f(b[1]) if b else None},
                        "hist": _hist(df[c], b)})

    # ---- categorical columns
    categorical = []
    for c in cats[:6]:
        vc = df[c].dropna().astype(str).value_counts()
        tot = int(vc.sum())
        categorical.append({"name": c, "total": tot, "unique": int(len(vc)),
                            "items": [{"value": k, "count": int(n), "pct": round(n / tot * 100, 1)}
                                      for k, n in vc.head(8).items()]})

    # ---- time series
    ts = None
    if dts:
        dc = dts[0]
        span = (df[dc].max() - df[dc].min()).days
        period = _period(span)
        b = _bucket(df[dc], period)
        ok = b.notna()
        pick = sorted(nums, key=lambda c: df[c].isna().mean())[:3]
        tmp = df.loc[ok, pick].copy()
        tmp["_b"] = b[ok]
        g = tmp.groupby("_b")
        cnt = g.size()
        if len(cnt) >= 3:
            fmt = "%Y-%m-%d %H:00" if period == "hour" else "%Y-%m-%d"
            means = g[pick].mean() if pick else None
            ts = {"column": dc, "period": period, "labels": [i.strftime(fmt) for i in cnt.index],
                  "count": [int(x) for x in cnt.values],
                  "series": [{"name": c, "values": [_f(x) for x in means[c].values]} for c in pick]}
            for sr in ts["series"]:
                vals = [x for x in sr["values"] if x is not None]
                k = max(1, len(vals) // 3)
                if len(vals) >= 6 and np.mean(vals[:k]) != 0:
                    pct = (np.mean(vals[-k:]) - np.mean(vals[:k])) / abs(np.mean(vals[:k])) * 100
                    sr["change_pct"] = round(float(pct), 1)

    # ---- correlations
    corr = None
    if len(nums) >= 2:
        cols = nums[:10]
        m = df[cols].astype(float).corr(min_periods=10)
        pairs = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                r = m.iloc[i, j]
                if pd.notna(r) and abs(r) >= 0.3:
                    pairs.append({"a": cols[i], "b": cols[j], "r": round(float(r), 2)})
        pairs.sort(key=lambda p: -abs(p["r"]))
        corr = {"columns": cols, "matrix": [[_f(x) for x in row] for row in m.values],
                "pairs": pairs[:5]}

    # ---- group comparison
    combos = []
    for cc in cats:
        if not 2 <= df[cc].nunique() <= 10:
            continue
        for nc in nums[:4]:
            g = df.groupby(df[cc].astype("object"))[nc].agg(["mean", "count"]).dropna()
            g = g[g["count"] >= 2]
            sd = df[nc].std()
            if len(g) >= 2 and sd and not np.isnan(sd):
                g = g.sort_values("mean", ascending=False)
                combos.append(((g["mean"].max() - g["mean"].min()) / sd,
                               {"category": cc, "numeric": nc,
                                "items": [{"name": str(k), "mean": _f(r["mean"]), "count": int(r["count"])}
                                          for k, r in g.iterrows()]}))
    combos.sort(key=lambda t: -t[0])
    groups = [dict(c, effect=round(float(e), 2)) for e, c in combos[:3]]

    # ---- plain-language findings
    kc = {k: sum(1 for c in df.columns if kinds[c] == k) for k in ("numeric", "categorical", "datetime", "text")}
    insights.append(f"{rows:,} rows and {len(df.columns)} columns: {kc['numeric']} numeric, "
                    f"{kc['categorical']} category, {kc['datetime']} date and {kc['text']} free-text.")
    miss = sorted(((df[c].isna().sum(), c) for c in df.columns if df[c].isna().any()), reverse=True)
    if miss:
        insights.append("Most missing data: " + ", ".join(
            f"{c} ({n / rows * 100:.1f}%)" for n, c in miss[:3]) + ".")
    else:
        insights.append("There are no missing values.")
    for n in numeric:
        if n["outliers"]["count"]:
            insights.append(f"{n['name']} has {n['outliers']['count']} value(s) outside the normal range "
                            f"of {_fmt(n['outliers']['lower'])} to {_fmt(n['outliers']['upper'])}.")
            break
    for n in numeric:
        if n["skew"] is not None and abs(n["skew"]) > 1 and n["stats"]["mean"] is not None:
            side = "high" if n["skew"] > 0 else "low"
            insights.append(f"{n['name']} is uneven: a few very {side} values pull the mean "
                            f"({_fmt(n['stats']['mean'])}) away from the median ({_fmt(n['stats']['median'])}).")
            break
    if corr and corr["pairs"]:
        p = corr["pairs"][0]
        insights.append(f"{p['a']} and {p['b']} have a {_strength(p['r'])} relationship (r = {p['r']}).")
    for c in categorical:
        top = c["items"][0]
        if top["pct"] >= 50:
            insights.append(f"In {c['name']}, '{top['value']}' makes up {top['pct']:.0f}% of the rows.")
            break
    if ts:
        best = max((s for s in ts["series"] if abs(s.get("change_pct", 0)) >= 10),
                   key=lambda s: abs(s["change_pct"]), default=None)
        if best:
            word = "rose" if best["change_pct"] > 0 else "fell"
            insights.append(f"Over time, {best['name']} {word} {abs(best['change_pct']):.0f}% "
                            f"from the start to the end of the period.")
        else:
            insights.append(f"No large trend over time (by {ts['period']}, using {ts['column']}).")
    if groups and groups[0]["effect"] >= 0.5:  # only report gaps that are large relative to the spread
        g = groups[0]["items"]
        insights.append(f"{groups[0]['numeric']} differs by {groups[0]['category']}: highest for "
                        f"'{g[0]['name']}' ({_fmt(g[0]['mean'])}), lowest for '{g[-1]['name']}' ({_fmt(g[-1]['mean'])}).")

    return {"overview": {"rows": rows, "cols": len(df.columns), **kc}, "insights": insights[:9],
            "numeric": numeric, "categorical": categorical, "timeseries": ts,
            "correlation": corr, "groups": groups}
