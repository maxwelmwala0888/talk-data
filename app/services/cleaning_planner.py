"""
Cleaning Planner — asks the LLM to design a cleaning pipeline
based on the dataset's health profile.

Uses the existing LLM factory (Groq preferred, Gemini fallback).
"""

import json
import re
from typing import Dict, Any, List
from app.services.llm.llm_factory import get_llm


PLANNER_PROMPT = """You are a senior data engineer. You will be given a dataset health profile and (optionally) a user hint.

Design a cleaning pipeline as a JSON object. Respond with ONLY the raw JSON, no markdown.

Format:
{
  "summary": "One-sentence description of what will be fixed.",
  "steps": [
    { "action": "drop_duplicates", "on": ["col1"], "reason": "12 exact duplicates",
      "safety": "safe", "recommendation": "Only apply if these are truly duplicate records." },
    { "action": "strip_whitespace", "columns": ["name","email"], "reason": "trailing whitespace",
      "safety": "safe", "recommendation": "Always safe — removes invisible spaces." },
    { "action": "standardize_case", "column": "email", "to": "lower", "reason": "mixed casing",
      "safety": "safe", "recommendation": "Emails are case-insensitive so this is safe." },
    { "action": "parse_date", "column": "signup_date", "formats": ["%Y-%m-%d","%m/%d/%Y"], "reason": "mixed date formats",
      "safety": "review", "recommendation": "Check that dd/mm and mm/dd aren't being swapped." },
    { "action": "fill_null", "column": "country", "value": "Unknown", "reason": "8 nulls",
      "safety": "review", "recommendation": "Choose a different value if 'Unknown' is misleading." },
    { "action": "remove_outliers", "column": "revenue", "method": "iqr", "reason": "3 extreme values",
      "safety": "risky", "recommendation": "These may be legitimate large transactions — review before applying." }
  ]
}

ALLOWED ACTIONS (no others):
  drop_duplicates  {on: [cols]}
  drop_nulls       {column}
  fill_null        {column, value}
  strip_whitespace {columns: [cols]}
  standardize_case {column, to: "lower"|"upper"|"title"}
  parse_date       {column, formats: ["%Y-%m-%d", ...]}
  cast_type        {column, type: "int"|"float"|"str"|"bool"}
  replace_values   {column, mapping: {"old": "new"}}
  drop_column      {column}
  rename_column    {old, new}
  remove_outliers  {column, method: "iqr"|"zscore"}
  clean_numeric_strings {column}   # strips $, commas, spaces, (negatives)

CRITICAL PRIORITY — ALWAYS INCLUDE MISSING VALUES:
If ANY column in the profile has issues containing "has_nulls" with null_pct > 0,
you MUST include a "fill_null" or "drop_nulls" step for it. Do not skip missing values.

RULES:
1. Only propose steps for issues that actually exist in the profile.
1a. Prioritize in this order: missing values → duplicates → formats → values.
1b. For a column with nulls < 30%: propose fill_null (with "Unknown" or 0 or "N/A" depending on dtype).
1c. For a column with nulls >= 30%: propose drop_column or fill_null, whichever makes sense.
1d. If a column is numeric with nulls, propose fill_null with value 0.
1e. If a column has issue "numeric_as_string" (numbers stored with $ , spaces), propose clean_numeric_strings for that column.
2. Do NOT modify PII columns (email, phone, ssn, name) unless it's just trimming whitespace or standardizing case.
3. Maximum 12 steps.
4. Order matters: drop_duplicates and drop_nulls first, formatting next, fill_null last.
5. If the user provided a hint, prioritize their request.
6. Return ONLY the JSON, no explanation.

EACH STEP MUST ALSO INCLUDE:
  - "safety": "safe" | "review" | "risky"
      safe    = deterministic & reversible (strip_whitespace, standardize_case, drop_duplicates, rename_column)
      review  = changes meaning (fill_null, parse_date, cast_type, replace_values, drop_column)
      risky   = can lose legitimate data (remove_outliers, drop_nulls on critical columns)
  - "recommendation": 1 short sentence explaining what the user should check before applying.
      Example: "Only apply if you're sure those rows are truly duplicates."
      Example: "Consider filling with 'Unknown' instead if you want to keep the rows."

When in doubt, choose the HIGHER risk level.
"""


def _extract_json(raw: str) -> Dict[str, Any]:
    """Strip markdown fences and parse JSON."""
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Find the first { and last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    return json.loads(raw)


def plan_cleaning(profile: Dict[str, Any], user_hint: str = "") -> Dict[str, Any]:
    """Generate a cleaning pipeline from a dataset profile."""
    # Trim the profile to keep the prompt small
    trimmed_columns = []
    for col in profile.get("columns", []):
        if not col.get("issues"):
            continue
        trimmed_columns.append({
            "name": col["name"],
            "dtype": col["dtype"],
            "null_pct": col.get("null_pct", 0),
            "unique_count": col.get("unique_count", 0),
            "issues": col.get("issues", []),
            "sample": col.get("sample_values", [])[:3],
        })

    payload = {
        "row_count": profile.get("row_count", 0),
        "duplicate_rows": profile.get("duplicate_rows", 0),
        "problematic_columns": trimmed_columns,
        "user_hint": user_hint or None,
    }

    prompt = f"DATASET PROFILE:\n{json.dumps(payload, indent=2)}\n\nReturn the JSON pipeline now."

    try:
        raw = get_llm().generate_json(prompt=prompt, system_instruction=PLANNER_PROMPT)
        parsed = _extract_json(raw)
        steps = parsed.get("steps", [])
        # Sanity-check: keep only valid actions
        allowed = {
            "drop_duplicates", "drop_nulls", "fill_null", "strip_whitespace",
            "standardize_case", "parse_date", "cast_type", "replace_values",
            "drop_column", "rename_column", "remove_outliers", "clean_numeric_strings",
        }
        steps = [s for s in steps if s.get("action") in allowed]
        parsed["steps"] = steps[:12]
        return parsed
    except Exception as e:
        print(f"[cleaning_planner] failed: {e}")
        return {
            "summary": "Could not generate a pipeline automatically.",
            "steps": [],
            "error": str(e)[:200],
        }