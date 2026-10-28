import json
import re
import math
from app.services.llm.llm_factory import get_llm


COMBINED_PROMPT = """You are a SENIOR DATA ANALYST. Given a user question, columns, sample rows, and pre-computed statistics, return ONE JSON object with:

{
  "chart_type": "bar" | "horizontal_bar" | "line" | "pie" | "area" | "scatter" | "box" | "histogram" | "radar" | "treemap" | "funnel" | "heatmap" | "table",
  "x_axis": "exact_column_name_or_null",
  "y_axis": "exact_column_name_or_null",
  "color_scheme": "vibrant" | "cool" | "warm" | "monochrome",
  "title": "Short descriptive title (max 60 chars)",
  "x_label": "Human-readable X axis label",
  "y_label": "Human-readable Y axis label",
  "insights": ["...", "...", "..."]
}

=== WHEN TO USE "table" (VERY IMPORTANT) ===
Use "table" - NEVER a chart - in these cases:
1. The result contains NO numeric columns.
2. The result is a schema listing (columns like: column_name, data_type, table_name, table_schema).
3. The result is a single text column (e.g. just a list of names or IDs).
4. The result is 1 row + 1 column (a single value).
5. The question asks for column names, table names, schema, structure, DESCRIBE, or metadata.
6. The result is a list of records with no clear category-to-value relationship.

Charts require: at least ONE numeric column AND at least TWO rows AND a meaningful x/y pair.

When in doubt, choose "table".

=== CHART TYPE SELECTION (in this exact priority) ===
1. If user EXPLICITLY says a chart type, use it:
   - "box plot" / "boxplot" / "quartile" -> "box"
   - "histogram" / "frequency distribution" -> "histogram"
   - "scatter" / "correlation" / "relationship" / "vs" / "against" -> "scatter"
   - "radar" / "spider" -> "radar"
   - "treemap" -> "treemap"
   - "funnel" -> "funnel"
   - "heatmap" -> "heatmap"
   - "pie" / "share" / "proportion" -> "pie"
   - "line" / "trend" / "over time" / "growth" -> "line"
   - "area" -> "area"
   - "bar" / "column" / "rank" / "compare" -> "bar"
   - "horizontal bar" / "barh" -> "horizontal_bar"
2. Otherwise infer:
   - x = date/time, y = numeric -> "line"
   - x = category, y = numeric, unique(x) <= 6 -> "pie"
   - x = category, y = numeric, unique(x) > 6 -> "bar"
   - both numeric + "distribution" -> "box"
   - both numeric + "relationship" -> "scatter"
   - only one numeric column + "distribution" -> "histogram"

=== INSIGHT RULES ===
Every insight MUST contain at least one specific number or entity name.
Match the chart type:
- BAR: rank + gaps + concentration
- LINE: direction + peak + trough + totals
- PIE: dominant share + smallest share
- SCATTER: correlation coefficient + outliers
- AREA: same as line
- BOX: median per group + widest IQR + tightest IQR
- HISTOGRAM: most common bucket range + shape
- RADAR: strongest/weakest category
- TREEMAP: largest block + share of total
- FUNNEL: largest drop-off between stages
- HEATMAP: hottest cell + coldest cell
- TABLE: min/max/mean

NEVER use: "varies", "shows variation", "influences", "affects", "plot area", "the data shows", "may indicate".

Return ONLY the raw JSON object."""


def _humanize(col: str) -> str:
    if not col:
        return ""
    return " ".join(w.capitalize() for w in re.split(r"[_\s]+", col) if w)


def _detect_explicit_chart(question: str):
    q = question.lower()
    if re.search(r"\b(horizontal\s*bar|barh|rank.*bar)\b", q): return "horizontal_bar"
    if re.search(r"\b(box\s*plot|boxplot|box-plot|quartile|iqr)\b", q): return "box"
    if re.search(r"\b(histogram|frequency distribution)\b", q): return "histogram"
    if re.search(r"\b(radar|spider)\b", q): return "radar"
    if re.search(r"\b(treemap|tree\s*map)\b", q): return "treemap"
    if re.search(r"\b(funnel|drop-?off)\b", q): return "funnel"
    if re.search(r"\b(heatmap|heat\s*map|cross-?tab)\b", q): return "heatmap"
    if re.search(r"\b(scatter|correlation|relationship|vs\.?|versus|against)\b", q): return "scatter"
    if re.search(r"\b(pie|donut|doughnut|share|proportion)\b", q): return "pie"
    if re.search(r"\b(line|trend|time series|over time|growth)\b", q): return "line"
    if re.search(r"\b(area|filled)\b", q): return "area"
    if re.search(r"\b(bar|column|rank|compare|distribution|by)\b", q): return "bar"
    if re.search(r"\b(table|list|show all|rows|records)\b", q): return "table"
    return None


def detect_chart_hint(question: str):
    """Public alias."""
    return _detect_explicit_chart(question)


def _numeric(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except Exception:
            return None
    return None


def _compute_stats(columns, rows):
    stats = {}
    numeric_cols = []
    for col in columns:
        vals = [x for x in (_numeric(r.get(col)) for r in rows) if x is not None]
        if vals:
            numeric_cols.append(col)
            stats[col] = {
                "count": len(vals),
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "mean": round(sum(vals) / len(vals), 2),
            }
    correlations = []
    for i, a in enumerate(numeric_cols):
        for b in numeric_cols[i + 1:]:
            xs, ys = [], []
            for r in rows:
                xa, xb = _numeric(r.get(a)), _numeric(r.get(b))
                if xa is not None and xb is not None:
                    xs.append(xa)
                    ys.append(xb)
            if len(xs) >= 3:
                r_val = _pearson(xs, ys)
                if r_val is not None:
                    correlations.append({
                        "col_a": a, "col_b": b,
                        "r": round(r_val, 3),
                        "strength": _corr_strength(r_val),
                        "direction": "positive" if r_val > 0 else "negative",
                    })
    return stats, correlations


def _pearson(xs, ys):
    n = len(xs)
    if n < 2: return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0: return None
    return num / (dx * dy)


def _corr_strength(r):
    a = abs(r)
    if a >= 0.7: return "strong"
    if a >= 0.4: return "moderate"
    if a >= 0.2: return "weak"
    return "very weak"


def _quantile(sorted_vals, p):
    n = len(sorted_vals)
    if n == 0: return None
    idx = (n - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi: return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _box_stats_by_group(x_key, y_key, rows):
    groups: dict = {}
    for r in rows:
        g = str(r.get(x_key) if r.get(x_key) is not None else "-")
        v = _numeric(r.get(y_key))
        if v is not None:
            groups.setdefault(g, []).append(v)
    out = {}
    for g, vals in groups.items():
        vals.sort()
        q1 = _quantile(vals, 0.25)
        q3 = _quantile(vals, 0.75)
        med = _quantile(vals, 0.5)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        inliers = [v for v in vals if lower <= v <= upper]
        out[g] = {
            "min": inliers[0] if inliers else vals[0],
            "max": inliers[-1] if inliers else vals[-1],
            "q1": q1, "median": med, "q3": q3, "n": len(vals),
        }
    return out


def _fallback_insights(chart_type, x, y, columns, rows, user_question):
    insights = []
    if not rows or not y:
        return [f"Result contains {len(rows)} rows across {len(columns)} columns."]

    try:
        if chart_type in ("bar", "pie", "area", "treemap", "funnel", "radar"):
            sorted_rows = sorted(rows, key=lambda r: _numeric(r.get(y)) or 0, reverse=True)
            top, bottom = sorted_rows[0], sorted_rows[-1]
            top_val = _numeric(top.get(y)) or 0
            bottom_val = _numeric(bottom.get(y)) or 0
            total = sum(_numeric(r.get(y)) or 0 for r in rows)
            insights.append(f"{top.get(x)} leads with {top_val:,.0f} in {_humanize(y).lower()}.")
            if top_val > 0:
                pct = (top_val - bottom_val) / top_val * 100
                insights.append(f"{bottom.get(x)} is lowest at {bottom_val:,.0f}, {pct:.0f}% below the top.")
            if total > 0:
                insights.append(f"Total across {len(rows)} categories: {total:,.0f}.")

        elif chart_type in ("line", "scatter"):
            points = [(r.get(x), _numeric(r.get(y))) for r in rows if _numeric(r.get(y)) is not None]
            ys = [p[1] for p in points]
            if chart_type == "scatter" and len(ys) >= 3:
                xs_num = [_numeric(p[0]) for p in points]
                clean = [(a, b) for a, b in zip(xs_num, ys) if a is not None]
                if len(clean) >= 3:
                    r_val = _pearson([p[0] for p in clean], [p[1] for p in clean])
                    if r_val is not None:
                        insights.append(f"{_corr_strength(r_val).capitalize()} {'positive' if r_val > 0 else 'negative'} correlation (r={r_val:.2f}).")
                    insights.append(f"{_humanize(y)} ranges from {min(ys):,.0f} to {max(ys):,.0f}.")
                    mean_y = sum(ys) / len(ys)
                    far = max(points, key=lambda p: abs(p[1] - mean_y))
                    insights.append(f"Furthest point from mean: {far[1]:,.0f} at {far[0]}.")
            elif chart_type == "line" and len(ys) >= 2:
                first, last = ys[0], ys[-1]
                if first > 0:
                    pct = (last - first) / first * 100
                    direction = "grew" if pct >= 0 else "declined"
                    insights.append(f"Value {direction} {abs(pct):.0f}% from {first:,.0f} to {last:,.0f}.")
                insights.append(f"Peak {max(ys):,.0f} at {points[ys.index(max(ys))][0]}; trough {min(ys):,.0f} at {points[ys.index(min(ys))][0]}.")

        elif chart_type == "box":
            stats = _box_stats_by_group(x, y, rows)
            if stats:
                top_med = max(stats.items(), key=lambda kv: kv[1]["median"])
                insights.append(f"{top_med[0]} has the highest median {_humanize(y).lower()} at {top_med[1]['median']:,.0f}.")
                widest = max(stats.items(), key=lambda kv: (kv[1]["q3"] - kv[1]["q1"]))
                iqr = widest[1]["q3"] - widest[1]["q1"]
                insights.append(f"{widest[0]} shows the widest spread: IQR {widest[1]['q1']:,.0f}-{widest[1]['q3']:,.0f} (width {iqr:,.0f}).")
                tight = min(stats.items(), key=lambda kv: (kv[1]["q3"] - kv[1]["q1"]))
                iqr_t = tight[1]["q3"] - tight[1]["q1"]
                insights.append(f"{tight[0]} is tightest: median {tight[1]['median']:,.0f}, IQR width {iqr_t:,.0f}.")

        elif chart_type == "histogram":
            vals = [_numeric(r.get(y)) for r in rows if _numeric(r.get(y)) is not None]
            if vals:
                vals.sort()
                n = len(vals)
                bins = 10
                lo, hi = min(vals), max(vals)
                width = (hi - lo) / bins if hi > lo else 1
                counts = [0] * bins
                for v in vals:
                    idx = min(int((v - lo) / width), bins - 1) if width else 0
                    counts[idx] += 1
                peak = counts.index(max(counts))
                peak_lo = lo + peak * width
                peak_hi = peak_lo + width
                insights.append(f"Most common range is {peak_lo:,.0f} to {peak_hi:,.0f} with {max(counts)} of {n} values.")
                insights.append(f"Values range from {lo:,.0f} to {hi:,.0f}, mean {sum(vals)/n:,.0f}.")
                insights.append(f"Median is {_quantile(vals, 0.5):,.0f}.")

    except Exception:
        pass

    if not insights:
        insights = [f"Result contains {len(rows)} rows across {len(columns)} columns."]
    return insights[:3]


# ============================================================
#  CHARTABILITY VALIDATOR
# ============================================================

_METADATA_COLUMN_HINTS = {
    "column_name", "column_type", "data_type", "table_name", "table_schema",
    "schema_name", "table_catalog", "ordinal_position", "constraint_name",
    "index_name", "key_column_usage", "column_default", "is_nullable",
    "description", "column_comment", "character_maximum_length",
    "numeric_precision", "numeric_scale", "datetime_precision",
}


def is_chartable(columns: list, rows: list) -> bool:
    """Return True only if a chart would make sense for this data."""
    if not rows or not columns:
        return False
    if len(rows) < 2:
        return False
    if len(columns) < 2:
        return False

    lowered = {str(c).lower() for c in columns}
    if lowered & _METADATA_COLUMN_HINTS:
        return False

    sample = rows[:10]
    numeric_cols = []
    text_cols = []
    for col in columns:
        vals = [r.get(col) for r in sample if r.get(col) is not None]
        if not vals:
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            numeric_cols.append(col)
        else:
            text_cols.append(col)

    if not numeric_cols:
        return False
    if len(columns) == 1:
        return False
    if not text_cols and len(numeric_cols) < 2:
        return False

    return True


def force_table_if_not_chartable(parsed: dict, columns: list, rows: list) -> dict:
    """Override chart_type to 'table' when data can't be charted."""
    if not is_chartable(columns, rows):
        if parsed.get("chart_type") not in (None, "table"):
            print(f"[chartability] overriding '{parsed.get('chart_type')}' -> 'table' (cols={len(columns)}, rows={len(rows)})")
        parsed["chart_type"] = "table"
        parsed["x_axis"] = None
        parsed["y_axis"] = None
    return parsed


def generate_chart_and_insights(user_question: str, columns: list, rows: list,
                                 include_insights: bool = True) -> dict:
    stats, correlations = _compute_stats(columns, rows)
    explicit = _detect_explicit_chart(user_question)

    sample = {
        "question": user_question,
        "columns": columns,
        "sample_rows": rows[:15],
        "total_rows": len(rows),
        "statistics": stats,
        "numeric_correlations": correlations,
    }
    prompt = f"DATA:\n{json.dumps(sample, default=str)}\n\nReturn the combined JSON now."

    try:
        raw = get_llm().generate_json(prompt=prompt, system_instruction=COMBINED_PROMPT)
        print(f"[DEBUG] LLM response: {raw[:300]}")
        parsed = json.loads(raw)
        parsed.setdefault("chart_type", "table")
        parsed.setdefault("x_axis", None)
        parsed.setdefault("y_axis", None)
        parsed.setdefault("color_scheme", "vibrant")
        parsed.setdefault("title", user_question[:60])
        parsed.setdefault("x_label", _humanize(parsed.get("x_axis")))
        parsed.setdefault("y_label", _humanize(parsed.get("y_axis")))
        parsed.setdefault("insights", [])

        if explicit:
            parsed["chart_type"] = explicit

        if include_insights and _looks_generic(parsed.get("insights", [])):
            parsed["insights"] = _fallback_insights(
                parsed["chart_type"], parsed["x_axis"], parsed["y_axis"],
                columns, rows, user_question,
            )

        if not include_insights:
            parsed["insights"] = []

        parsed = force_table_if_not_chartable(parsed, columns, rows)
        return parsed

    except Exception as e:
        print(f"[insight_chain] fallback: {e}")
        fallback_chart = explicit or "table"
        x = columns[0] if columns else None
        y = columns[1] if len(columns) > 1 else None
        if not explicit and len(columns) >= 2:
            for col in columns[1:]:
                if any(isinstance(r.get(col), (int, float)) for r in rows[:5]):
                    y = col
                    break
            if any(isinstance(r.get(x), (int, float)) for r in rows[:5]):
                fallback_chart = "scatter"
            else:
                uniq = len({str(r.get(x)) for r in rows})
                fallback_chart = "pie" if uniq <= 6 else "bar"

        result = {
            "chart_type": fallback_chart,
            "x_axis": x, "y_axis": y,
            "color_scheme": "vibrant",
            "title": user_question[:60] if user_question else "Result",
            "x_label": _humanize(x), "y_label": _humanize(y),
            "insights": _fallback_insights(fallback_chart, x, y, columns, rows, user_question),
        }
        return force_table_if_not_chartable(result, columns, rows)


def _looks_generic(insights: list) -> bool:
    if not insights:
        return True
    generic = [
        "varies", "shows variation", "the data shows", "general trend",
        "plot area", "overall value", "significant difference",
        "appears to", "seems to", "may indicate", "could be",
        "influences", "affects",
    ]
    for ins in insights:
        low = str(ins).lower()
        if not any(c.isdigit() for c in low):
            return True
        for g in generic:
            if g in low:
                return True
    return False