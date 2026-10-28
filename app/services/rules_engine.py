"""
Cross-column rules engine.

Safely evaluates boolean expressions over a pandas DataFrame.

- Only column names are allowed as identifiers (no builtins, no function calls
  except a small whitelist of pandas helpers).
- Supports: comparisons, boolean ops, arithmetic, .notna(), .isna(),
  .str.match(), .str.contains(), .str.startswith(), .str.endswith(),
  .str.lower(), .str.upper(), abs(), min(), max(), round().
- Blocks: __, import, exec, eval, open, os, sys, subprocess, lambda, etc.
"""

import re
from typing import Any, Dict, List, Optional
import pandas as pd


# Operators/keywords we allow in the expression text
_ALLOWED_TOKENS = {
    "and", "or", "not", "in", "is", "True", "False", "None",
    "abs", "min", "max", "round", "len",
    "notna", "isna", "str", "match", "contains", "startswith", "endswith",
    "lower", "upper", "strip", "replace", "fillna",
}

# Patterns that must NEVER appear
_BLOCKED_PATTERNS = re.compile(
    r"(__|import|exec|eval|open|os\.|sys\.|subprocess|lambda|globals|locals|"
    r"getattr|setattr|compile|input|\.system|\.popen)",
    re.IGNORECASE,
)

# Identifier regex — used to detect the columns referenced by a rule
_IDENT_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")


class RuleError(Exception):
    pass


def _identifiers(expr: str) -> List[str]:
    """Extract every identifier from the expression."""
    return list(set(_IDENT_RE.findall(expr)))


def validate_expression(expr: str, columns: List[str]) -> Dict[str, Any]:
    """
    Return {"ok": bool, "error": str | None, "columns_used": list[str]}.
    Does not execute the expression, just sanity-checks it.
    """
    if not expr or not expr.strip():
        return {"ok": False, "error": "Expression is empty", "columns_used": []}

    if _BLOCKED_PATTERNS.search(expr):
        return {"ok": False, "error": "Expression contains a blocked keyword", "columns_used": []}

    cols_set = set(columns)
    identifiers = _identifiers(expr)

    unknown = [
        ident for ident in identifiers
        if ident not in _ALLOWED_TOKENS and ident not in cols_set
    ]

    if unknown:
        return {
            "ok": False,
            "error": f"Unknown column(s): {', '.join(unknown[:5])}",
            "columns_used": [],
        }

    # Columns actually referenced
    used = [ident for ident in identifiers if ident in cols_set]
    return {"ok": True, "error": None, "columns_used": used}


def evaluate_rule(df: pd.DataFrame, rule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a single rule against the DataFrame.

    Returns:
      {
        "ok": bool,
        "error": str | None,
        "violations": int,
        "sample_violations": [dict, ...],   # up to 5 rows
        "columns_used": [str, ...],
      }
    """
    expr = (rule.get("check") or "").strip()
    if not expr:
        return {"ok": False, "error": "Rule has no check expression", "violations": 0, "sample_violations": [], "columns_used": []}

    cols = list(df.columns)
    v = validate_expression(expr, cols)
    if not v["ok"]:
        return {"ok": False, "error": v["error"], "violations": 0, "sample_violations": [], "columns_used": []}

    columns_used = v["columns_used"]
    if not columns_used:
        return {"ok": False, "error": "Rule doesn't reference any column", "violations": 0, "sample_violations": [], "columns_used": []}

    # Only evaluate on rows where all referenced columns are non-null
    try:
        sub = df[columns_used]
    except KeyError as e:
        return {"ok": False, "error": f"Missing column: {e}", "violations": 0, "sample_violations": [], "columns_used": columns_used}

    # Ignore rows with any null in the referenced columns
    non_null_mask = sub.notna().all(axis=1)

    # Build restricted namespace: only the columns + safe helpers
    local_ns = {c: df[c] for c in columns_used}
    global_ns = {
        "__builtins__": {},
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
    }

    try:
        result = eval(expr, global_ns, local_ns)  # noqa: S307 — expression is validated
    except Exception as e:
        return {
            "ok": False,
            "error": f"Evaluation failed: {type(e).__name__}: {str(e)[:150]}",
            "violations": 0,
            "sample_violations": [],
            "columns_used": columns_used,
        }

    # Coerce to boolean Series
    try:
        if isinstance(result, pd.Series):
            bool_series = result.fillna(True).astype(bool)
        elif isinstance(result, bool):
            bool_series = pd.Series([result] * len(df), index=df.index)
        else:
            bool_series = pd.Series(result, index=df.index).fillna(True).astype(bool)
    except Exception as e:
        return {
            "ok": False,
            "error": f"Rule didn't produce a boolean: {e}",
            "violations": 0,
            "sample_violations": [],
            "columns_used": columns_used,
        }

    # Violations = rows that fail the rule AND are not null-ignored
    violations_mask = non_null_mask & (~bool_series)
    violating = df[violations_mask]

    sample = violating.head(5).astype(object).where(pd.notnull(violating), None).to_dict(orient="records")
    # Make JSON-safe
    for row in sample:
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = str(v)

    return {
        "ok": True,
        "error": None,
        "violations": int(violations_mask.sum()),
        "sample_violations": sample,
        "columns_used": columns_used,
    }


def evaluate_rules(df: pd.DataFrame, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run every rule; return one result per rule (same order)."""
    out = []
    for r in rules:
        res = evaluate_rule(df, r)
        res["rule_id"] = r.get("id")
        res["rule_name"] = r.get("name") or "Rule"
        out.append(res)
    return out