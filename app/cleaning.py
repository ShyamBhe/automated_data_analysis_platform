"""Type inference, profiling, outlier detection and cleaning rules."""
import warnings

import numpy as np
import pandas as pd

NULL_TOKENS = {"", "na", "n/a", "n.a.", "nan", "null", "none", "nil", "-", "--", "?", "missing",
               "undefined", "#n/a", "not available"}
BOOL_TRUE, BOOL_FALSE = {"true", "yes", "y", "t"}, {"false", "no", "n", "f"}
DATE_RE = (r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}:\d{2}|"
           r"[A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-z]{3,9} \d{4}")


# ---------------------------------------------------------------- type inference
def normalize(df: pd.DataFrame):
    """Strip spaces, turn null-like tokens into real nulls, infer a type per column and convert.
    Values that do not fit the inferred type become null and are reported as 'invalid'."""
    out, notes = {}, {}
    for col in df.columns:
        s = df[col].astype(object).map(lambda v: v.strip() if isinstance(v, str) else v)
        tok = s.map(lambda v: isinstance(v, str) and v.lower() in NULL_TOKENS)
        blank = s.map(lambda v: isinstance(v, str) and v == "")
        n_tok = int((tok & ~blank).sum())
        s = s.where(~tok, np.nan)
        s = s.where(s.notna(), np.nan)
        nn = s.dropna()
        note = {"kind": "text", "null_tokens": n_tok, "invalid": 0, "invalid_examples": []}
        if nn.empty:
            note["kind"] = "empty"
            out[col] = s
        else:
            nn = nn.astype(str)
            num = pd.to_numeric(nn, errors="coerce")
            num = num.where(np.isfinite(num))
            if num.notna().mean() >= 0.9:
                note["kind"] = "numeric"
                bad = nn[num.isna()]
                out[col] = pd.to_numeric(s, errors="coerce").where(lambda x: np.isfinite(x))
            else:
                dt, is_date = None, nn.str.contains(DATE_RE).mean() >= 0.9
                if is_date:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        dt = pd.to_datetime(nn, errors="coerce", format="mixed", utc=True)
                if dt is not None and dt.notna().mean() >= 0.9:
                    note["kind"] = "datetime"
                    bad = nn[dt.isna()]
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        out[col] = pd.to_datetime(s, errors="coerce", format="mixed",
                                                  utc=True).dt.tz_localize(None)
                elif set(nn.str.lower().unique()) <= (BOOL_TRUE | BOOL_FALSE):
                    note["kind"] = "boolean"
                    bad = nn.iloc[0:0]
                    out[col] = s.map(lambda v: pd.NA if not isinstance(v, str)
                                     else (v.lower() in BOOL_TRUE)).astype("boolean")
                else:
                    bad = nn.iloc[0:0]
                    u = nn.nunique()
                    note["kind"] = "categorical" if (u <= 20 or u / len(nn) <= 0.05) else "text"
                    out[col] = s
            note["invalid"] = int(len(bad))
            note["invalid_examples"] = [str(x) for x in bad.unique()[:5]]
        notes[col] = note
    return pd.DataFrame(out, index=df.index), notes


def read_typed(path):
    return normalize(pd.read_csv(path, dtype=str, keep_default_na=False))


def save_csv(df: pd.DataFrame, path):
    d = df.copy()
    for c in d.columns:
        s = d[c]
        if pd.api.types.is_float_dtype(s):
            s = s.round(6)
            nn = s.dropna()
            if len(nn) and (nn % 1 == 0).all() and nn.abs().max() < 1e15:
                s = s.astype("Int64")
            d[c] = s
        elif pd.api.types.is_datetime64_any_dtype(s):
            nn = s.dropna()
            midnight = len(nn) and (nn == nn.dt.normalize()).all()
            d[c] = s.dt.strftime("%Y-%m-%d" if midnight else "%Y-%m-%d %H:%M:%S")
    d.to_csv(path, index=False)


# ---------------------------------------------------------------- outliers
def outlier_bounds(s: pd.Series, method="iqr"):
    v = s.dropna().astype(float)
    if len(v) < 4:
        return None
    if method == "zscore":
        sd = v.std()
        if not sd or np.isnan(sd):
            return None
        return v.mean() - 3 * sd, v.mean() + 3 * sd
    q1, q3 = v.quantile(0.25), v.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return None
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(x) or np.isinf(x)) else round(x, 4)


# ---------------------------------------------------------------- profiling
def profile(df: pd.DataFrame, notes: dict):
    rows, ncols = df.shape
    cols, null_cells, outlier_cells = [], 0, 0
    for c in df.columns:
        s, n = df[c], notes[c]
        kind = n["kind"]
        nulls = int(s.isna().sum())
        null_cells += nulls
        info = {"name": c, "kind": kind, "nulls": nulls,
                "null_pct": round(nulls / rows * 100, 1) if rows else 0.0,
                "unique": int(s.nunique()), "null_tokens": n["null_tokens"],
                "invalid": n["invalid"], "invalid_examples": n["invalid_examples"],
                "outliers": None, "stats": None, "top": [], "case_variants": 0}
        sug = {"nulls": "leave", "outliers": "keep", "standardize": "none", "fill_value": ""}
        out_n = 0
        if kind == "numeric":
            v = s.dropna().astype(float)
            if len(v):
                info["stats"] = {"min": _f(v.min()), "q1": _f(v.quantile(.25)),
                                 "median": _f(v.median()), "mean": _f(v.mean()),
                                 "q3": _f(v.quantile(.75)), "max": _f(v.max()),
                                 "std": _f(v.std())}
            skew = v.skew() if len(v) > 2 else 0
            b = outlier_bounds(s)
            z = outlier_bounds(s, "zscore")
            if b:
                lo, hi = b
                mask = (s.astype(float) < lo) | (s.astype(float) > hi)
                out_n = int(mask.sum())
                ex = v[(v < lo) | (v > hi)]
                ex = ex.reindex(ex.sub(v.median()).abs().sort_values(ascending=False).index)
                info["outliers"] = {"count": out_n, "lower": _f(lo), "upper": _f(hi),
                                    "zscore_count": int(((v < z[0]) | (v > z[1])).sum()) if z else 0,
                                    "examples": [_f(x) for x in ex.head(5)]}
            outlier_cells += out_n
            if nulls:
                sug["nulls"] = "median" if (out_n or abs(skew or 0) > 1) else "mean"
            if out_n:
                sug["outliers"] = "cap" if out_n / max(len(v), 1) <= 0.10 else "keep"
        elif kind == "datetime":
            nn = s.dropna()
            if len(nn):
                info["stats"] = {"min": str(nn.min()), "max": str(nn.max())}
        else:
            v = s.dropna().astype(str)
            info["top"] = [{"value": k, "count": int(x)} for k, x in v.value_counts().head(5).items()]
            if kind in ("categorical", "text") and len(v):
                info["case_variants"] = int(v.nunique() - v.str.lower().nunique())
                if info["case_variants"]:
                    sug["standardize"] = "lower"
            if nulls:
                sug["nulls"] = "mode" if kind in ("categorical", "boolean") else "constant"
                if sug["nulls"] == "constant":
                    sug["fill_value"] = "Unknown"
        if info["null_pct"] > 60 or kind == "empty":
            sug["nulls"] = "drop_column"
        info["suggestion"] = sug
        cols.append(info)
    dup = int(df.duplicated().sum()) if rows else 0
    total = rows * ncols
    bad = null_cells + outlier_cells + dup * ncols
    score = round(max(0.0, 100 * (1 - bad / total)), 1) if total else 100.0
    return {"rows": rows, "cols": ncols, "duplicate_rows": dup, "null_cells": null_cells,
            "outlier_cells": outlier_cells, "quality_score": score, "columns": cols}


def summary(p):
    return {k: v for k, v in p.items() if k != "columns"}


def suggest(p):
    return {"drop_duplicates": p["duplicate_rows"] > 0, "outlier_method": "iqr",
            "columns": {c["name"]: c["suggestion"] for c in p["columns"]}}


# ---------------------------------------------------------------- cleaning
def _const(v, kind):
    if v is None or str(v) == "":
        return None
    try:
        if kind == "numeric":
            return float(v)
        if kind == "datetime":
            return pd.to_datetime(v)
        if kind == "boolean":
            return str(v).lower() in BOOL_TRUE
    except Exception:
        return None
    return str(v)


def apply_rules(df: pd.DataFrame, kinds: dict, rules: dict):
    """Apply rules in a fixed order: duplicates, text standardizing, outliers, nulls, row drops.
    Returns (cleaned_df, change_log, row_counts). The original data is never modified."""
    df, log, drop = df.copy(), [], set()
    n0 = len(df)
    if rules.get("drop_duplicates"):
        before = len(df)
        df = df.drop_duplicates()
        if before - len(df):
            log.append({"column": "(all columns)", "action": "Removed duplicate rows",
                        "count": before - len(df)})
    method = "zscore" if rules.get("outlier_method") == "zscore" else "iqr"
    for col, r in (rules.get("columns") or {}).items():
        if col not in df.columns:
            continue
        kind = kinds.get(col)
        std = r.get("standardize")
        if std in ("lower", "upper", "title") and kind in ("categorical", "text"):
            old = df[col]
            new = old.map(lambda v: getattr(v, std)() if isinstance(v, str) else v)
            changed = int(((old != new) & old.notna()).sum())
            df[col] = new
            if changed:
                log.append({"column": col, "action": f"Changed text to {std}case", "count": changed})
        act = r.get("outliers", "keep")
        if kind == "numeric" and act != "keep":
            s = df[col].astype(float)
            b = outlier_bounds(s, method)
            if b:
                lo, hi = b
                mask = (s < lo) | (s > hi)
                n = int(mask.sum())
                rng = f"normal range {lo:.4g} to {hi:.4g}"
                if n and act == "cap":
                    df[col] = s.clip(lo, hi)
                    log.append({"column": col, "action": f"Limited outliers to the {rng}", "count": n})
                elif n and act in ("median", "mean"):
                    rep = float(getattr(s[~mask], act)())
                    df[col] = s.mask(mask, rep)
                    log.append({"column": col, "action": f"Replaced outliers with the {act} ({rep:.4g}); {rng}", "count": n})
                elif n and act == "null":
                    df[col] = s.mask(mask, np.nan)
                    log.append({"column": col, "action": f"Treated outliers as missing; {rng}", "count": n})
                elif n and act == "drop_rows":
                    drop |= set(s.index[mask])
                    log.append({"column": col, "action": f"Marked rows with outliers for removal; {rng}", "count": n})
                elif n and act == "flag":
                    df[col + "__outlier"] = mask
                    log.append({"column": col, "action": f"Added flag column {col}__outlier; {rng}", "count": n})
        act = r.get("nulls", "leave")
        s = df[col]
        n = int(s.isna().sum())
        if act == "drop_column":
            df = df.drop(columns=[col])
            log.append({"column": col, "action": "Removed column", "count": n})
            continue
        if not n or act == "leave":
            continue
        if act == "drop_rows":
            drop |= set(s.index[s.isna()])
            log.append({"column": col, "action": "Marked rows with missing values for removal", "count": n})
            continue
        desc = None
        if act in ("mean", "median") and kind == "numeric":
            val = float(getattr(s.dropna().astype(float), act)())
            s, desc = s.fillna(val), f"Filled missing values with the {act} ({val:.4g})"
        elif act == "mode":
            m = s.mode(dropna=True)
            if len(m):
                s, desc = s.fillna(m.iloc[0]), f"Filled missing values with the most common value ({m.iloc[0]})"
        elif act == "constant":
            val = _const(r.get("fill_value"), kind)
            if val is not None:
                s, desc = s.fillna(val), f"Filled missing values with '{r.get('fill_value')}'"
        elif act == "ffill":
            s, desc = s.ffill(), "Filled missing values from the previous row"
        elif act == "bfill":
            s, desc = s.bfill(), "Filled missing values from the next row"
        elif act == "interpolate" and kind == "numeric":
            s, desc = s.astype(float).interpolate(limit_direction="both"), "Filled missing values by interpolation"
        if desc:
            df[col] = s
            log.append({"column": col, "action": desc, "count": n - int(s.isna().sum())})
        else:
            log.append({"column": col, "action": f"'{act}' does not apply to a {kind} column; skipped", "count": 0})
    if drop:
        before = len(df)
        df = df.loc[~df.index.isin(list(drop))]
        log.append({"column": "(all columns)", "action": "Removed rows marked above", "count": before - len(df)})
    return df, log, {"rows_before": n0, "rows_after": len(df)}
