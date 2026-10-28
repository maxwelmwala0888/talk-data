"""
Data profiler — pure pandas, no LLM.

Scans a DataFrame and reports:
  - Per-column stats (nulls, unique values, dtype)
  - Detected issues with severity (critical / warning / info)
  - Overall health score (0-100)
  - Duplicate row count
"""

import re
import pandas as pd

# Issue → category mapping
ISSUE_CATEGORY = {
    "has_nulls": "missing",
    "constant_value": "values",
    "trailing_whitespace": "format",
    "mixed_case": "format",
    "possible_date_string": "format",
    "mixed_date_format": "format",
    "possible_pii": "values",
    "has_outliers": "values",
    "numeric_as_string": "format",
}


def _categorize(issues):
    """Return the highest-priority category for a column."""
    priority = ["missing", "duplicate", "format", "values"]
    cats = {ISSUE_CATEGORY.get(i, "values") for i in issues}
    for p in priority:
        if p in cats:
            return p
    return "clean"


from typing import Dict, Any, List


DATE_HINTS = ("date", "time", "day", "month", "year", "created", "updated", "added")
EMAIL_HINTS = ("email", "mail", "e_mail")
PHONE_HINTS = ("phone", "mobile", "cell", "tel")
NAME_HINTS = ("name", "first_name", "last_name", "full_name")


def _severity_for_column(col_info: Dict[str, Any], total_rows: int) -> str:
    """Return 'critical', 'warning', or 'info' based on the column's issues."""
    issues = col_info["issues"]
    if not issues:
        return "info"

    # Critical: too many nulls, or entirely constant
    if col_info["null_pct"] > 30:
        return "critical"
    if "constant_value" in issues and total_rows > 1:
        return "critical"
    if "possible_pii" in issues:
        return "critical"

    # Warning: has nulls, whitespace, mixed case, outliers
    if any(i in issues for i in [
        "has_nulls", "trailing_whitespace", "mixed_case",
        "has_outliers", "possible_date_string", "mixed_date_format",
    ]):
        return "warning"

    return "info"


def _detect_column_issues(df: pd.DataFrame, col: str, total_rows: int) -> List[str]:
    issues: List[str] = []
    series = df[col]
    null_count = int(series.isna().sum())
    unique_count = int(series.nunique(dropna=True))
    dtype = series.dtype
    col_lower = col.lower()

    # ---- Nulls ----
    if null_count > 0:
        issues.append("has_nulls")

    # ---- Constant column ----
    if unique_count <= 1 and total_rows > 1:
        issues.append("constant_value")

    # ---- String-specific checks ----
    if dtype == "object":
        sample = series.dropna().astype(str).head(500)

        # Trailing / leading whitespace
        if sample.str.startswith(" ").any() or sample.str.endswith(" ").any():
            issues.append("trailing_whitespace")

        # Mixed case (has both 'usa' and 'USA' style values)
        has_lower = sample.str.islower().any()
        has_upper = sample.str.isupper().any()
        has_mixed = sample.str.istitle().any()
        if sum([has_lower, has_upper, has_mixed]) >= 2:
            issues.append("mixed_case")

        # Possible date stored as string
        if any(h in col_lower for h in DATE_HINTS):
            # Try to parse a sample
            non_null = sample.head(20)
            parsed_count = 0
            for v in non_null:
                try:
                    pd.to_datetime(v)
                    parsed_count += 1
                except Exception:
                    pass
            if parsed_count >= len(non_null) * 0.5:
                # It IS parseable as dates
                # Check for multiple formats
                patterns = set()
                for v in non_null[:20]:
                    v = str(v)
                    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
                        patterns.add("iso")
                    elif re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", v):
                        patterns.add("us")
                    elif re.match(r"^\d{1,2}-\d{1,2}-\d{4}$", v):
                        patterns.add("eu")
                    else:
                        patterns.add("other")
                if len(patterns) > 1:
                    issues.append("mixed_date_format")
                else:
                    issues.append("possible_date_string")

        # Numeric stored as string (e.g. "$1,234.56")
        import re as _re
        non_null_strings = sample.head(30).astype(str)
        numeric_like = non_null_strings.str.match(r"^[\s]*[$€£¥₹]?[\s]*\(?-?\d[\d,\.\s]*\)?[\s]*$").mean()
        if numeric_like > 0.7:
            issues.append("numeric_as_string")

        # PII detection (email pattern)
        if any(h in col_lower for h in EMAIL_HINTS):
            email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
            if sample.str.match(email_pattern).mean() > 0.5:
                issues.append("possible_pii")

    # ---- Numeric outliers ----
    if pd.api.types.is_numeric_dtype(dtype):
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            outlier_count = int(((series < lower) | (series > upper)).sum())
            if outlier_count > 0 and outlier_count < total_rows:
                issues.append(f"has_outliers")

    return issues


def profile_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
    """Main entry point — profile a full DataFrame."""
    total_rows = len(df)
    columns_out: List[Dict[str, Any]] = []

    for col in df.columns:
        series = df[col]
        null_count = int(series.isna().sum())
        null_pct = round(null_count / total_rows * 100, 2) if total_rows > 0 else 0.0

        # Sample values (first 5 non-null, converted to strings)
        sample = []
        for v in series.dropna().head(5).tolist():
            try:
                sample.append(str(v)[:80])
            except Exception:
                pass

        col_info = {
            "name": col,
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_pct": null_pct,
            "unique_count": int(series.nunique(dropna=True)),
            "sample_values": sample,
            "issues": _detect_column_issues(df, col, total_rows),
        }

        # Numeric extras
        if pd.api.types.is_numeric_dtype(series.dtype):
            try:
                col_info["min"] = float(series.min()) if not pd.isna(series.min()) else None
                col_info["max"] = float(series.max()) if not pd.isna(series.max()) else None
                col_info["mean"] = round(float(series.mean()), 2) if not pd.isna(series.mean()) else None
            except Exception:
                pass

        col_info["severity"] = _severity_for_column(col_info, total_rows)
        col_info["category"] = _categorize(col_info["issues"])
        columns_out.append(col_info)

    # ---- Duplicates ----
    duplicate_rows = int(df.duplicated().sum())

    # ---- Overall issues ----
    overall = []
    if duplicate_rows > 0:
        overall.append(f"{duplicate_rows} exact duplicate rows")

    null_cols = [c for c in columns_out if c["null_count"] > 0]
    if null_cols:
        overall.append(f"{len(null_cols)} columns contain null values")

    date_issues = [c for c in columns_out if "mixed_date_format" in c["issues"]]
    if date_issues:
        overall.append(f"{len(date_issues)} column(s) have mixed date formats")

    case_issues = [c for c in columns_out if "mixed_case" in c["issues"]]
    if case_issues:
        overall.append(f"{len(case_issues)} column(s) have inconsistent casing")

    whitespace_issues = [c for c in columns_out if "trailing_whitespace" in c["issues"]]
    if whitespace_issues:
        overall.append(f"{len(whitespace_issues)} column(s) have trailing whitespace")

    # ---- Health score (transparent, reachable) ----
    n_cols = max(1, len(df.columns))
    total_cells = max(1, total_rows * n_cols)
    total_nulls = sum(c["null_count"] for c in columns_out)

    format_cols = sum(1 for c in columns_out if c["category"] == "format")
    outlier_cols = sum(1 for c in columns_out if "has_outliers" in c["issues"])
    const_cols = sum(1 for c in columns_out if "constant_value" in c["issues"])

    missing_ratio = total_nulls / total_cells
    dup_ratio = duplicate_rows / max(1, total_rows)

    breakdown = {
        "missing":   round(min(30.0, missing_ratio * 100.0), 1),
        "duplicate": round(min(25.0, dup_ratio * 100.0 * 1.5), 1),
        "format":    round(min(20.0, (format_cols / n_cols) * 20.0), 1),
        "outlier":   round(min(15.0, (outlier_cols / n_cols) * 15.0), 1),
        "constant":  round(min(10.0, (const_cols / n_cols) * 10.0), 1),
    }

    total_deduction = sum(breakdown.values())
    score = max(0, min(100, round(100 - total_deduction)))

    # Severity counts
    counts = {"critical": 0, "warning": 0, "info": 0}
    for c in columns_out:
        if c["issues"]:
            counts[c["severity"]] += 1

    # Category counts
    cat_counts = {"missing": 0, "duplicate": 0, "format": 0, "values": 0}
    for c in columns_out:
        cat = c.get("category")
        if cat in cat_counts:
            cat_counts[cat] += 1
    cat_counts["duplicate"] = 1 if duplicate_rows > 0 else 0

    return {
        "row_count": total_rows,
        "column_count": len(df.columns),
        "duplicate_rows": duplicate_rows,
        "health_score": score,
        "score_breakdown": breakdown,
        "severity_counts": counts,
        "category_counts": cat_counts,
        "columns": columns_out,
        "overall_issues": overall,
    }