"""
Pipeline executor — takes a cleaning pipeline (list of steps)
and applies each step to a DataFrame using pandas.

No LLM is called here. Every step is a deterministic pandas operation.

Supported actions:
  drop_duplicates     {on: [cols] | null}
  drop_nulls          {column}
  fill_null           {column, value}
  strip_whitespace    {columns: [cols]}
  standardize_case    {column, to: lower|upper|title}
  parse_date          {column, formats: [fmt]}
  cast_type           {column, type: int|float|str|bool}
  replace_values      {column, mapping: {old: new}}
  drop_column         {column}
  rename_column       {old, new}
  remove_outliers     {column, method: iqr|zscore}
  clean_numeric_strings {column}   # strip $, commas, () negatives
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Any


def _safe_str(val) -> str:
    try:
        return str(val)
    except Exception:
        return ""


def execute_pipeline(df: pd.DataFrame, steps: List[Dict[str, Any]]) -> pd.DataFrame:
    """Apply each step in order. Returns a NEW DataFrame (input is not mutated)."""
    df = df.copy()
    applied = []

    for i, step in enumerate(steps):
        action = step.get("action")
        if not action:
            continue

        try:
            before = len(df)

            if action == "drop_duplicates":
                subset = step.get("on")
                if subset:
                    subset = [c for c in subset if c in df.columns]
                df = df.drop_duplicates(subset=subset if subset else None).reset_index(drop=True)

            elif action == "drop_nulls":
                col = step.get("column")
                if col and col in df.columns:
                    df = df.dropna(subset=[col]).reset_index(drop=True)

            elif action == "fill_null":
                col = step.get("column")
                val = step.get("value", "Unknown")
                if col and col in df.columns:
                    df[col] = df[col].fillna(val)

            elif action == "strip_whitespace":
                cols = step.get("columns", [])
                for c in cols:
                    if c in df.columns and df[c].dtype == "object":
                        df[c] = df[c].apply(lambda x: x.strip() if isinstance(x, str) else x)

            elif action == "standardize_case":
                col = step.get("column")
                mode = step.get("to", "lower")
                if col and col in df.columns:
                    s = df[col].astype(str)
                    if mode == "lower":
                        df[col] = s.str.lower()
                    elif mode == "upper":
                        df[col] = s.str.upper()
                    elif mode == "title":
                        df[col] = s.str.title()

            elif action == "parse_date":
                col = step.get("column")
                formats = step.get("formats", [])
                if col and col in df.columns:
                    if formats:
                        for fmt in formats:
                            mask = df[col].isna()
                            if not mask.any():
                                break
                            try:
                                parsed = pd.to_datetime(df.loc[mask, col], format=fmt, errors="coerce")
                                df.loc[mask, col] = parsed
                            except Exception:
                                pass
                        # Final pass with infer
                        still_null = df[col].isna()
                        if still_null.any():
                            df.loc[still_null, col] = pd.to_datetime(df.loc[still_null, col], errors="coerce")
                    else:
                        df[col] = pd.to_datetime(df[col], errors="coerce")
                    df[col] = df[col].astype(str).replace("NaT", np.nan)

            elif action == "clean_numeric_strings":
                col = step.get("column")
                if col and col in df.columns:
                    import re as _re
                    def _strip_num(x):
                        if x is None or (isinstance(x, float) and pd.isna(x)):
                            return None
                        if isinstance(x, (int, float)):
                            return x
                        s = str(x).strip()
                        # Remove currency symbols and commas
                        s = _re.sub(r"[$€£¥₹]", "", s)
                        s = s.replace(",", "")
                        s = s.replace(" ", "")
                        # Handle parentheses as negative: (100) -> -100
                        if s.startswith("(") and s.endswith(")"):
                            s = "-" + s[1:-1]
                        # Try to convert
                        try:
                            return float(s)
                        except Exception:
                            return None
                    df[col] = df[col].apply(_strip_num)

            elif action == "cast_type":
                col = step.get("column")
                target = step.get("type", "str")
                if col and col in df.columns:
                    if target == "int":
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
                    elif target == "float":
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                    elif target == "str":
                        df[col] = df[col].astype(str)
                    elif target == "bool":
                        df[col] = df[col].astype(bool)

            elif action == "replace_values":
                col = step.get("column")
                mapping = step.get("mapping", {})
                if col and col in df.columns and mapping:
                    df[col] = df[col].replace(mapping)

            elif action == "drop_column":
                col = step.get("column")
                if col and col in df.columns:
                    df = df.drop(columns=[col])

            elif action == "rename_column":
                old = step.get("old")
                new = step.get("new")
                if old and new and old in df.columns:
                    df = df.rename(columns={old: new})

            elif action == "remove_outliers":
                col = step.get("column")
                method = step.get("method", "iqr")
                if col and col in df.columns:
                    series = pd.to_numeric(df[col], errors="coerce")
                    if method == "iqr":
                        q1 = series.quantile(0.25)
                        q3 = series.quantile(0.75)
                        iqr = q3 - q1
                        if iqr > 0:
                            lower = q1 - 1.5 * iqr
                            upper = q3 + 1.5 * iqr
                            df = df[(series >= lower) & (series <= upper)].reset_index(drop=True)
                    elif method == "zscore":
                        mean = series.mean()
                        std = series.std()
                        if std > 0:
                            z = (series - mean) / std
                            df = df[z.abs() < 3].reset_index(drop=True)

            after = len(df)
            applied.append({
                "step": i + 1,
                "action": action,
                "row_delta": after - before,
                "row_count_after": after,
            })
        except Exception as e:
            applied.append({
                "step": i + 1,
                "action": action,
                "error": str(e)[:150],
            })

    return df, applied