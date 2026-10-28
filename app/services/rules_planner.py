"""
Rules Planner — asks the LLM to propose cross-column validation rules
from a dataset's schema + sample values.
"""

import json
import re
from typing import Dict, Any, List
from app.services.llm.llm_factory import get_llm


RULES_PROMPT = """You are a senior data quality engineer.

Given a dataset schema (column names, types, sample values), propose 3-8
CROSS-COLUMN validation rules that catch real data problems.

A GOOD rule catches things a single-column check can't:
  - Date order:                signup_date <= last_login_date
  - Math identity (with tol):  abs(total - quantity * price) < 0.01
  - Conditional not-null:      (status != "shipped") | shipped_date.notna()
  - Range:                     (age >= 0) & (age <= 120)
  - Mutual exclusion:          (is_personal == False) | (is_business == False)
  - String pattern:            email.str.contains("@", na=False)

Respond with ONLY a raw JSON array (no markdown, no wrapper object).

Each rule must be:
{
  "name": "Short human name (<= 40 chars)",
  "description": "One sentence explaining what it checks",
  "check": "pandas-evaluable boolean expression using column names",
  "severity": "error" | "warning",
  "message": "Short message shown when the rule is violated",
  "suggested_fix": "drop" | "null" | "review"
}

STRICT RULES:
1. Column names in the check MUST exist exactly in the schema (case-sensitive).
2. Use pandas Series methods where useful:
   .notna(), .isna(), .str.contains(pat, na=False), .str.match(pat, na=False),
   .str.startswith(), .str.endswith(), .str.lower(), .str.upper()
3. Use parentheses around every comparison.
4. Use `&` and `|` and `~` for boolean logic (NOT `and`/`or`/`not`).
5. For equality use `==` (NOT `=`).
6. For string literals use double quotes.
7. Only propose CROSS-COLUMN rules — do NOT propose simple single-column
   checks like "column is not null" or "column > 0".
8. Maximum 8 rules. Return [] if you can't find any valid relationships.
9. Respond with ONLY the JSON array. No explanation, no markdown fences.
"""


def _extract_json_array(raw: str) -> List[Dict[str, Any]]:
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")
    return data


def _schema_for_prompt(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    cols = []
    for col in profile.get("columns", []):
        cols.append({
            "name": col["name"],
            "dtype": col["dtype"],
            "sample_values": (col.get("sample_values") or [])[:4],
            "unique_count": col.get("unique_count", 0),
            "null_pct": col.get("null_pct", 0),
        })
    return cols


def plan_rules(profile: Dict[str, Any], max_rules: int = 8) -> List[Dict[str, Any]]:
    """Return a list of proposed rules (dicts). May be empty."""
    schema = _schema_for_prompt(profile)
    if not schema:
        return []

    payload = {
        "row_count": profile.get("row_count", 0),
        "column_count": profile.get("column_count", 0),
        "columns": schema,
    }

    prompt = (
        f"DATASET SCHEMA:\n{json.dumps(payload, indent=2)}\n\n"
        f"Return the JSON array of up to {max_rules} rules now."
    )

    try:
        raw = get_llm().generate_json(prompt=prompt, system_instruction=RULES_PROMPT)
    except Exception as e:
        print(f"[rules_planner] LLM call failed: {e}")
        return []

    try:
        rules = _extract_json_array(raw)
    except Exception as e:
        print(f"[rules_planner] JSON parse failed: {e}; raw={raw[:300]}")
        return []

    cols = {c["name"] for c in schema}
    cleaned: List[Dict[str, Any]] = []
    for r in rules[:max_rules]:
        if not isinstance(r, dict):
            continue
        check = (r.get("check") or "").strip()
        if not check:
            continue
        # Make sure every referenced identifier is a real column
        idents = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", check))
        reserved = {
            "and", "or", "not", "in", "is", "True", "False", "None",
            "abs", "min", "max", "round", "len", "notna", "isna", "str",
            "match", "contains", "startswith", "endswith", "lower", "upper",
            "strip", "replace", "fillna",
        }
        unknown = [i for i in idents if i not in reserved and i not in cols]
        if unknown:
            print(f"[rules_planner] skipping rule with unknown columns: {unknown}")
            continue

        cleaned.append({
            "name": (r.get("name") or "Rule")[:255],
            "description": r.get("description"),
            "check": check,
            "severity": r.get("severity", "warning"),
            "message": r.get("message"),
            "suggested_fix": r.get("suggested_fix"),
        })

    return cleaned