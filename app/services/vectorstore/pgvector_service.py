"""
SQLite-backed vector store.

Stores embeddings as JSON-encoded text in a schema_embeddings table,
then computes cosine similarity in Python for retrieval.

For a personal project with a few hundred columns, this is plenty fast.
For 10,000+ columns, switch to a real vector DB (Qdrant, Chroma, Pinecone).
"""

import json
import math
from sqlalchemy import text
from sqlalchemy.orm import Session


class SQLiteVectorService:
    def ensure_table(self, db: Session):
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id INTEGER NOT NULL,
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                column_type TEXT,
                description TEXT,
                embedding TEXT NOT NULL
            )
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS schema_embeddings_conn_idx
            ON schema_embeddings (connection_id)
        """))
        db.commit()

    def clear_for_connection(self, db: Session, connection_id: int):
        db.execute(
            text("DELETE FROM schema_embeddings WHERE connection_id = :cid"),
            {"cid": connection_id},
        )
        db.commit()

    def store_items(self, db: Session, connection_id: int, items: list, embeddings: list):
        for item, emb in zip(items, embeddings):
            db.execute(
                text("""
                    INSERT INTO schema_embeddings
                        (connection_id, table_name, column_name, column_type, description, embedding)
                    VALUES (:cid, :tn, :cn, :ct, :desc, :emb)
                """),
                {
                    "cid": connection_id,
                    "tn": item["table_name"],
                    "cn": item["column_name"],
                    "ct": item.get("column_type", ""),
                    "desc": item.get("description", ""),
                    "emb": json.dumps(emb),
                },
            )
        db.commit()

    def count(self, db: Session, connection_id: int) -> int:
        row = db.execute(
            text("SELECT COUNT(*) FROM schema_embeddings WHERE connection_id = :cid"),
            {"cid": connection_id},
        ).fetchone()
        return int(row[0]) if row else 0

    def search(self, db: Session, connection_id: int, query_embedding: list, top_k: int = 20):
        rows = db.execute(
            text("""
                SELECT table_name, column_name, column_type, description, embedding
                FROM schema_embeddings
                WHERE connection_id = :cid
            """),
            {"cid": connection_id},
        ).fetchall()

        scored = []
        for r in rows:
            emb = json.loads(r[4])
            sim = _cosine_similarity(query_embedding, emb)
            scored.append((sim, {
                "table_name": r[0],
                "column_name": r[1],
                "column_type": r[2],
                "description": r[3],
            }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # Kept for API compatibility - no-op in SQLite version
    def ensure_extension(self, db: Session):
        pass


def _cosine_similarity(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


sqlite_vector_service = SQLiteVectorService()
# Alias for backward compat - some files import pgvector_service
pgvector_service = sqlite_vector_service
