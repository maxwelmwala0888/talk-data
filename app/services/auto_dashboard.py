"""
Auto-dashboard builder.

When a user asks a question against a dataset (CSV or any connection),
we automatically create/update a widget in that dataset's auto-dashboard.

Layout rule: 2 widgets per row (12-column grid → width 6).
Same prompt → updates existing widget (no duplicates).
"""

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.user import User
from app.models.connection import Connection
from app.models.dashboard import Dashboard
from app.models.widget import Widget


WIDGET_WIDTH = 6        # 12-column grid, so 2 per row
WIDGET_HEIGHT = 5       # 5 * 80px = 400px tall


def _dataset_display_name(conn: Connection) -> str:
    """Turn a connection into a human-friendly dashboard title."""
    if conn.db_type == "duckdb" and conn.name:
        # CSV upload — use the filename (without extension)
        name = conn.name.rsplit(".", 1)[0]
        return f"{name}"
    if conn.db_type == "access" and conn.name:
        return conn.name.rsplit(".", 1)[0]
    # Real DB — use connection name
    return f"{conn.name}"


def get_or_create_auto_dashboard(db: Session, user: User, conn: Connection) -> Dashboard:
    """Find (or lazily create) the auto-dashboard linked to a connection."""
    d = db.query(Dashboard).filter(
        Dashboard.user_id == user.id,
        Dashboard.auto_for_connection_id == conn.id,
    ).first()
    if d:
        return d

    title = _dataset_display_name(conn)
    d = Dashboard(
        user_id=user.id,
        title=title,
        description=f"Auto-generated dashboard for {conn.name}",
        auto_for_connection_id=conn.id,
        connection_id=conn.id,
        tags=["auto", conn.db_type],
        refresh_interval=0,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    print(f"[auto-dashboard] created dashboard #{d.id} for connection {conn.id} ({title})")
    return d


def _next_layout(db: Session, dashboard_id: int) -> tuple:
    """Return (row, col) for the next widget."""
    count = db.query(func.count(Widget.id)).filter(Widget.dashboard_id == dashboard_id).scalar() or 0
    row = count // 2
    col = (count % 2) * WIDGET_WIDTH
    return row, col


def attach_widget(
    db: Session,
    user: User,
    conn: Connection,
    prompt: str,
    sql: str,
    chart_config: dict,
    title: str = None,
) -> Widget:
    """
    Add (or update) a widget in the connection's auto-dashboard.

    Dedup rule: if a widget with the same prompt already exists in this
    dashboard, we UPDATE its SQL + chart config instead of creating a new one.
    This prevents the dashboard from filling with duplicate queries.
    """
    dash = get_or_create_auto_dashboard(db, user, conn)

    # Dedup by prompt (case-insensitive)
    existing = db.query(Widget).filter(
        Widget.dashboard_id == dash.id,
        func.lower(Widget.prompt) == prompt.strip().lower(),
    ).first()

    widget_title = title or (prompt[:60] if prompt else "Untitled")

    if existing:
        existing.sql_query = sql
        existing.chart_config = chart_config
        existing.title = widget_title
        existing.connection_id = conn.id
        db.commit()
        db.refresh(existing)
        print(f"[auto-dashboard] updated widget #{existing.id} in dashboard {dash.id}")
        return existing

    row, col = _next_layout(db, dash.id)
    w = Widget(
        dashboard_id=dash.id,
        user_id=user.id,
        connection_id=conn.id,
        title=widget_title,
        prompt=prompt,
        sql_query=sql,
        chart_config=chart_config,
        widget_type="chart",
        position_row=row,
        position_col=col,
        width=WIDGET_WIDTH,
        height=WIDGET_HEIGHT,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    print(f"[auto-dashboard] created widget #{w.id} in dashboard {dash.id} ({widget_title})")
    return w