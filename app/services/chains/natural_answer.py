"""
Decide whether a query result should be answered as a natural-language
message or as a table/chart, and generate the natural-language answer.

Design:
  - Simple questions (single count, total, lookup) → natural answer
  - Analytical questions (rank, trend, comparison) → chart + table
"""

import json
import re
from typing import List, Dict, Any, Optional, Tuple
from app.services.llm.llm_factory import get_llm


# Keywords that suggest the user just wants a single number / fact
_SIMPLE_PATTERNS = re.compile(
    r"\b(how many|how much|what is the (total|count|number|average|mean)|"
    r"count of|total (number|count|of)|number of|"
    r"what(?:'s| is) the|give me the (count|total|number))\b",
    re.IGNORECASE,
)

# Keywords that force complex mode (chart-worthy)
_COMPLEX_PATTERNS = re.compile(
    r"\b(compare|trend|over time|by (month|year|week|day|region|country|category|group)|"
    r"top \d+|bottom \d+|rank(ing)?|distribution|correlation|scatter|"
    r"histogram|breakdown|across|per (month|year|region|country)|"
    r"chart|plot|graph|visuali[sz]e|show me the (trend|distribution))\b",
    re.IGNORECASE,
)


def _is_chartable_shape(columns: List[str], rows: List[Dict[str, Any]]) -> bool:
    """Same heuristic as insight_chain.is_chartable — a shape that wants a chart."""
    if len(rows) < 2 or len(columns) < 2:
        return False
    sample = rows[:10]
    numeric_cols = 0
    text_cols = 0
    for col in columns:
        vals = [r.get(col) for r in sample if r.get(col) is not None]
        if not vals:
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            numeric_cols += 1
        else:
            text_cols += 1
    return numeric_cols >= 1 and text_cols >= 1


def classify_response_mode(
    question: str,
    columns: List[str],
    rows: List[Dict[str, Any]],
) -> str:
    """
    Return "simple" or "complex".

    Order of precedence:
      1. Explicit "chart" keywords → complex
      2. Explicit "count/total" keywords AND tiny result → simple
      3. Single row + <=3 columns → simple
      4. Chartable shape → complex
      5. Fallback → complex (safer default)
    """
    q = (question or "").strip()

    # 1. User asked for a chart/trend/ranking → always complex
    if _COMPLEX_PATTERNS.search(q):
        return "complex"

    # 2. Single row + few columns → almost certainly a "how many / total" ask
    if len(rows) == 1 and len(columns) <= 3:
        return "simple"

    # 3. Simple keyword + small result → simple
    if _SIMPLE_PATTERNS.search(q) and len(rows) <= 3:
        return "simple"

    # 4. Chartable → complex
    if _is_chartable_shape(columns, rows):
        return "complex"

    return "complex"


# ---------------- Natural answer generation ----------------

NATURAL_PROMPT = """You are a friendly data analyst answering a question in plain English.

You will be given:
  - The user's question
  - The query result (columns + rows, possibly many rows)

Write a friendly summary. Rules:
1. ALWAYS end with a useful summary — even for large results.
2. If there is 1 row: state the answer directly. "You have 312 customers."
3. If there are 2-5 rows: list them naturally with their numbers.
4. If there are more than 5 rows: describe the pattern.
   - "You have 143 products across 8 categories. The top ones are Electronics ($45k), Clothing ($38k), and Home ($29k)."
   - Mention the top 2-3 by number if the data is sortable.
5. Never mention SQL, queries, tables, columns, or the database.
6. Never say "based on the data", "according to", "the query returned".
7. If the result is empty, say so naturally: "You don't have any X yet."
8. Use the human meaning of column names, not raw identifiers.
9. Do NOT add bullet points or markdown. Just a paragraph.
10. Round large numbers for readability (1234567 → "1.23 million").
11. Keep it under 3 sentences.

Examples:

Question: "how many customers do we have?"
Result: {"columns": ["total"], "rows": [{"total": 312}]}
Answer: "You have 312 customers in total."

Question: "how many customers by gender?"
Result: {"columns": ["gender", "count"], "rows": [{"gender": "male", "count": 150}, {"gender": "female", "count": 130}, {"gender": "unknown", "count": 32}]}
Answer: "You have 312 customers total — 150 male, 130 female, and 32 with no gender recorded."

Question: "what is the total revenue?"
Result: {"columns": ["sum_revenue"], "rows": [{"sum_revenue": 1234567.89}]}
Answer: "Your total revenue is $1.23 million."

Question: "how many orders were placed last month?"
Result: {"columns": ["order_count"], "rows": [{"order_count": 0}]}
Answer: "No orders were placed last month."

Return ONLY the plain-text answer. No quotes, no JSON, no explanation.
"""


def _humanize_col(col: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[_\s]+", col) if w)


def generate_natural_answer(
    question: str,
    columns: List[str],
    rows: List[Dict[str, Any]],
    max_rows: int = 5,
) -> str:
    """Call the LLM to turn the result into a plain-English sentence."""
    if not columns:
        return "I couldn't find any data for that question."

    payload = {
        "question": question,
        "columns": columns,
        "rows": rows[:max_rows],
        "total_rows": len(rows),
    }

    prompt = f"RESULT:\n{json.dumps(payload, default=str, indent=2)}\n\nWrite the answer now."

    try:
        answer = get_llm().generate(
            prompt=prompt,
            system_instruction=NATURAL_PROMPT,
            temperature=0.3,
        )
        answer = (answer or "").strip()
        # Strip accidental markdown / quotes
        answer = answer.strip('"').strip("'")
        answer = re.sub(r"^\*\*(.+?)\*\*$", r"\1", answer)
        return answer[:500] if answer else "No result."
    except Exception as e:
        print(f"[natural_answer] failed: {e}")
        # Fallback: build a crude sentence from the first row
        try:
            first = rows[0] if rows else {}
            if len(columns) == 1:
                return f"{_humanize_col(columns[0])}: {first.get(columns[0])}"
            parts = []
            for c in columns[:3]:
                parts.append(f"{_humanize_col(c)}: {first.get(c)}")
            return ". ".join(parts) + "."
        except Exception:
            return "Here's what I found."