from app.services.llm.llm_factory import get_llm


SYSTEM_PROMPT = """You are an expert SQL analyst. Given a database schema and a user question, produce ONE valid {dialect} SQL query.

STRICT RULES (READ ALL):
1. Return ONLY the raw SQL. No markdown, no explanation, no code fences.

1a. RANKING / GROUPING RULES (VERY IMPORTANT):
    If the question asks to RANK, COUNT, GROUP, or COMPARE by a category
    (keywords: "top N", "rank", "by <X>", "per <X>", "compare", "highest",
    "lowest", "most", "least", "distribution of <X>"), you MUST use
    GROUP BY + aggregate + ORDER BY + LIMIT.

    Examples:
    - "Top 5 employees by customers added"
        SELECT AddedBy, COUNT(*) AS customer_count
        FROM data
        GROUP BY AddedBy
        ORDER BY customer_count DESC
        LIMIT 5

    - "Sales by region"
        SELECT region, SUM(sales) AS total_sales
        FROM data
        GROUP BY region
        ORDER BY total_sales DESC

    - "Average harvest by crop group"
        SELECT crop_group, AVG(harvest_value) AS avg_harvest
        FROM data
        GROUP BY crop_group
        ORDER BY avg_harvest DESC

    NEVER write a bare aggregate like `SELECT COUNT(*) FROM data` when a
    category column exists in the question. That would return 1 row and
    break the chart.

1b. COLUMN ORDER FOR CHARTS:
    Put the CATEGORY column FIRST, then the numeric aggregate SECOND.
    The frontend renders X = first column, Y = second.
    Good: SELECT region, SUM(sales) AS total FROM ... GROUP BY region
    Bad:  SELECT SUM(sales) AS total, region FROM ... GROUP BY region

1c. If the user asks for "column names" or "schema", use: DESCRIBE <actual_table_name>.

1d. NEVER use: UPDATE, DELETE, DROP, ALTER, INSERT, TRUNCATE, CREATE, GRANT, REVOKE.

1e. Every column alias MUST start with a letter (e.g. total_count, NOT AS 2).

1f. Limit results to 500 rows unless the user explicitly asks for more.

1g. {dialect_rules}

1h. CAST ERRORS: If you see an error like "Could not convert string X to INT32" or "Invalid Input Error",
    it means the source column has bad values (empty strings, "unknown", "N/A").
    In DuckDB, ALWAYS use TRY_CAST(col AS TYPE) instead of CAST(col AS TYPE).
    In Postgres/MySQL, use a CASE WHEN col ~ '^[0-9]+$' THEN col::INT ELSE NULL END or SAFE_CAST equivalents.
"""

DIALECT_RULES = {
    "duckdb": (
        "DuckDB RULES (VERY IMPORTANT):\n"
        "  - Use COUNT(*), SUM(x), AVG(x) normally.\n"
        "  - For date grouping: date_trunc('month', TRY_CAST(col AS DATE)).\n"
        "  - ALWAYS use TRY_CAST instead of CAST. CAST crashes on bad values; TRY_CAST returns NULL.\n"
        "  - For numeric casts: TRY_CAST(col AS DOUBLE) — never CAST().\n"
        "  - If a column is already DATE/TIMESTAMP, still wrap: TRY_CAST(col AS DATE) is safe.\n"
        "  - Example: SELECT date_trunc('month', TRY_CAST(transaction_date AS DATE)) AS month_period,"
        " SUM(TRY_CAST(total_amount AS DOUBLE)) AS total FROM data"
        " WHERE TRY_CAST(transaction_date AS DATE) IS NOT NULL"
        " GROUP BY month_period ORDER BY month_period"
    ),
    "postgres": (
        "Postgres: Use date_trunc('month', col::date) for date grouping. "
        "For safe casts, still prefer regular CAST — Postgres raises a clear error."
    ),
    "mysql": (
        "MySQL: Use DATE_FORMAT(col, '%Y-%m') for month grouping."
    ),
    "snowflake": "Snowflake: Use DATE_TRUNC('MONTH', TRY_TO_DATE(col)) for date grouping.",
    "bigquery": "BigQuery: Use DATE_TRUNC(DATE(col), MONTH) for date grouping.",
    "databricks": "Databricks: Use date_trunc('MONTH', col) for date grouping.",
    "sqlserver": "SQL Server: Use DATEFROMPARTS(YEAR(col), MONTH(col), 1) for month grouping.",
}

CHART_HINTS = {
    "box": (
        "The user wants a BOX PLOT (distribution comparison). "
        "Return RAW rows - one row per observation, no aggregation. "
        "SELECT the group column AND the numeric value column. "
        "NEVER use GROUP BY, AVG, SUM, or any aggregation."
    ),
    "histogram": (
        "The user wants a HISTOGRAM. Return ALL raw values of the numeric column."
    ),
    "scatter": (
        "The user wants a SCATTER PLOT. Return RAW rows with BOTH numeric columns. "
        "Do NOT aggregate."
    ),
    "bar": (
        "The user wants a BAR chart. Aggregate with GROUP BY on the category "
        "column and SUM/COUNT/AVG on the value column."
    ),
    "horizontal_bar": (
        "The user wants a HORIZONTAL BAR chart (usually a ranking). "
        "Group by the category, ORDER BY the aggregate DESC, LIMIT to top N."
    ),
    "line": (
        "The user wants a LINE chart. Aggregate over a date bucket with GROUP BY."
    ),
    "pie": (
        "The user wants a PIE chart. Aggregate with GROUP BY on the category column."
    ),
    "area": "The user wants an AREA chart. Aggregate over time with GROUP BY.",
    "radar": "The user wants a RADAR chart. Aggregate by category.",
    "treemap": "The user wants a TREEMAP. Aggregate by category.",
    "funnel": "The user wants a FUNNEL chart. Group by stage/category with ORDER.",
    "heatmap": "The user wants a HEATMAP. Return TWO categorical columns + one numeric aggregate.",
    "table": "The user wants a plain TABLE. Return rows as-is.",
    "": (
        "No specific chart type requested. Infer the best approach. "
        "If it's a ranking or comparison, GROUP BY + ORDER BY + LIMIT. "
        "If it's a distribution, return raw rows."
    ),
}


def build_schema_context(schema_items: list) -> str:
    by_table = {}
    for item in schema_items:
        by_table.setdefault(item["table_name"], []).append(item)
    lines = []
    for table, cols in by_table.items():
        lines.append(f"TABLE `{table}` (use this exact name):")
        for c in cols:
            lines.append(f"  - {c['column_name']} ({c['column_type']})")
    return "\n".join(lines)


def generate_sql(user_question: str, schema_items: list, dialect: str = "postgres",
                 chart_hint: str = "") -> str:
    schema_text = build_schema_context(schema_items)
    rules = DIALECT_RULES.get(dialect, DIALECT_RULES["postgres"])
    hint_text = CHART_HINTS.get(chart_hint, CHART_HINTS[""])

    prompt = f"""DATABASE SCHEMA:
{schema_text}

USER QUESTION:
{user_question}

Use the EXACT table and column names shown above (do NOT write 'my_table').
If this is a ranking question, GROUP BY + ORDER BY + LIMIT.
Generate the SQL now."""

    sql = get_llm().generate(
        prompt=prompt,
        system_instruction=SYSTEM_PROMPT.format(dialect=dialect, dialect_rules=rules, chart_hint=hint_text),
        temperature=0.0,
    )
    return _strip_sql(sql)


def fix_sql(user_question: str, bad_sql: str, error_message: str, schema_items: list,
            dialect: str = "duckdb", chart_hint: str = "") -> str:
    schema_text = build_schema_context(schema_items)
    rules = DIALECT_RULES.get(dialect, DIALECT_RULES["postgres"])
    hint_text = CHART_HINTS.get(chart_hint, CHART_HINTS[""])

    prompt = f"""SCHEMA:
{schema_text}

QUESTION:
{user_question}

FAILED SQL:
{bad_sql}

ERROR FROM DATABASE:
{error_message}

Fix the SQL. Use the ACTUAL table names from the schema above."""

    sql = get_llm().generate(
        prompt=prompt,
        system_instruction=SYSTEM_PROMPT.format(dialect=dialect, dialect_rules=rules, chart_hint=hint_text),
        temperature=0.0,
    )
    return _strip_sql(sql)


def _strip_sql(sql: str) -> str:
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1] if "\n" in sql else sql
        sql = sql.rsplit("```", 1)[0]
    sql = sql.replace("```sql", "").replace("```", "").strip()
    sql = _convert_backticks(sql)
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    return sql


def _convert_backticks(sql: str) -> str:
    import re as _re
    return _re.sub(r"`([^`]+)`", r'"\1"', sql)


def is_safe_sql(sql: str) -> bool:
    forbidden = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"]
    upper = sql.upper()
    return not any(f" {f} " in f" {upper} " for f in forbidden)