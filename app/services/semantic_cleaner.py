"""
Semantic Cleaner — uses the LLM to map messy categorical values
to canonical forms.

Example:
  Input:  ['M', 'm', 'male', 'Male', 'MALE', 'F', 'female', 'FEMALE']
  Output: {'M':'M', 'm':'M', 'male':'M', 'Male':'M', 'MALE':'M',
           'F':'F', 'female':'F', 'FEMALE':'F'}

Safety: after the LLM returns a mapping, we validate it against the
actual column values before applying it.
"""

import json
from typing import Dict, Any
from app.services.llm.llm_factory import get_llm


SEMANTIC_PROMPT = """You are a data standardization expert.

Given a column name, a user hint, and a list of unique values, map each value to a canonical form.

RULES:
1. Preserve meaning — do NOT merge values that are legitimately different.
2. Prefer SHORT canonical forms: "M" / "F" for gender, "US" for United States, "Yes" / "No" for booleans.
3. If a value can't be confidently mapped, keep it as-is (map to itself).
4. Return ONLY a JSON object. Keys = original values. Values = canonical form.
5. All keys and values MUST be strings.

Examples:
- gender column, values ["M","m","male","Male","MALE","F","female"] 
  → {"M":"M","m":"M","male":"M","Male":"M","MALE":"M","F":"F","female":"F"}
- country column, values ["USA","United States","US","us","U.S.A."] 
  → {"USA":"US","United States":"US","US":"US","us":"US","U.S.A.":"US"}
- boolean column, values ["Y","yes","YES","N","no","No"] 
  → {"Y":"Yes","yes":"Yes","YES":"Yes","N":"No","no":"No","No":"No"}

Return ONLY the JSON object. No markdown, no explanation.
"""


def _safe_str(v) -> str:
    if v is None:
        return ""
    return str(v)


def analyze_column(df, column: str, max_samples: int = 60, hint: str = "") -> Dict[str, Any]:
    """Return a semantic mapping for one column."""
    if column not in df.columns:
        return {"ok": False, "error": f"Column '{column}' not found", "mapping": {}}

    series = df[column].dropna().astype(str)
    unique_vals = series.unique().tolist()

    if len(unique_vals) == 0:
        return {"ok": True, "mapping": {}, "unique_count": 0, "sampled_count": 0, "change_count": 0}

    # For huge categorical columns, use the top N by frequency
    if len(unique_vals) > 200:
        unique_vals = series.value_counts().head(200).index.tolist()

    sample = unique_vals[:max_samples]

    prompt = f"""Column name: {column}
User hint: {hint or "(none)"}
Unique values ({len(sample)} shown):
{json.dumps(sample, indent=2)}

Return the JSON mapping now."""

    try:
        raw = get_llm().generate_json(prompt=prompt, system_instruction=SEMANTIC_PROMPT)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw
            raw = raw.rsplit("```", 1)[0]
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start : end + 1]
        mapping = json.loads(raw)
        if not isinstance(mapping, dict):
            raise ValueError("LLM did not return a JSON object")

        # Validate every key/value is a string
        clean_mapping: Dict[str, str] = {}
        for k, v in mapping.items():
            if k is None or v is None:
                continue
            clean_mapping[str(k)] = str(v)

        # Validate: every key in the mapping must exist in the actual column
        valid_values = set(unique_vals)
        clean_mapping = {k: v for k, v in clean_mapping.items() if k in valid_values}

        change_count = sum(1 for k, v in clean_mapping.items() if k != v)

        return {
            "ok": True,
            "mapping": clean_mapping,
            "unique_count": len(unique_vals),
            "sampled_count": len(sample),
            "change_count": change_count,
        }
    except Exception as e:
        print(f"[semantic_cleaner] failed: {e}")
        return {
            "ok": False,
            "error": str(e)[:200],
            "mapping": {},
            "unique_count": len(unique_vals),
        }