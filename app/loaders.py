"""Read CSV / TSV / JSON / JSON Lines / XML / Excel into a flat table of strings.
Everything becomes text first, so the cleaning step can see the odd values ("N/A", "ERR", ...)
before any type conversion hides them."""
import csv
import io
import json
from collections import Counter
from pathlib import Path

import pandas as pd

SUPPORTED = {".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".xml", ".xlsx"}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _conv(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def _stringify(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    for c in df.columns:
        df[c] = df[c].astype(object).map(_conv)
    return df


def load_csv(path: Path) -> pd.DataFrame:
    text = _read_text(path)
    sep = "\t" if path.suffix.lower() == ".tsv" else ","
    if path.suffix.lower() != ".tsv":
        try:
            sep = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|").delimiter
        except csv.Error:
            pass
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False,
                       skipinitialspace=True)


def _find_records(data, depth=0):
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if lists:
            return max(lists, key=len)
        if depth < 2:
            for v in data.values():
                if isinstance(v, dict):
                    found = _find_records(v, depth + 1)
                    if found and len(found) > 1:
                        return found
        return [data]
    return [{"value": data}]


def load_json(path: Path) -> pd.DataFrame:
    text = _read_text(path).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:  # maybe JSON Lines (one object per line)
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    return pd.json_normalize(_find_records(data), sep=".")


def _local(tag):
    return tag.split("}")[-1] if isinstance(tag, str) else tag


def _put(out, key, val):
    k, i = key, 2
    while k in out:
        k, i = f"{key}_{i}", i + 1
    out[k] = val


def _flat(el, prefix, out):
    for k, v in el.attrib.items():
        _put(out, f"{prefix}.{_local(k)}" if prefix else _local(k), v)
    kids = [c for c in el if isinstance(c.tag, str)]
    text = (el.text or "").strip()
    if not kids:
        if prefix:
            _put(out, prefix, text)
        return
    if text and prefix:
        _put(out, f"{prefix}._text", text)
    for c in kids:
        name = _local(c.tag)
        _flat(c, f"{prefix}.{name}" if prefix else name, out)


def load_xml(path: Path) -> pd.DataFrame:
    from lxml import etree  # hardened parser: no entity expansion, no network
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True,
                             huge_tree=False)
    node = etree.parse(str(path), parser).getroot()
    records = None
    for _ in range(10):  # walk down to the repeating element (the "rows")
        kids = [k for k in node if isinstance(k.tag, str)]
        if not kids:
            break
        tag, n = Counter(k.tag for k in kids).most_common(1)[0]
        if n >= 2:
            records = [k for k in kids if k.tag == tag]
            break
        if len(kids) == 1:
            node = kids[0]
            continue
        records = [node]
        break
    if records is None:
        records = [node]
    rows = []
    for r in records:
        out = {}
        _flat(r, "", out)
        rows.append(out)
    return pd.DataFrame(rows)


def load_any(path: Path) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv", ".txt"):
        df = load_csv(path)
    elif ext in (".json", ".jsonl", ".ndjson"):
        df = load_json(path)
    elif ext == ".xml":
        df = load_xml(path)
    elif ext == ".xlsx":
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    if df.empty or len(df.columns) == 0:
        raise ValueError("No records found in the file.")
    return _stringify(df)
