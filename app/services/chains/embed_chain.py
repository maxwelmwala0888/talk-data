from sqlalchemy.orm import Session
from app.core.config import settings
from app.services.llm.embedding_service import embedding_service
from app.services.vectorstore.pgvector_service import pgvector_service


def _describe(item: dict) -> str:
    return f"table {item['table_name']} column {item['column_name']} type {item.get('column_type','')}"


def index_schema(db: Session, connection_id: int, schema_items: list) -> int:
    if not schema_items:
        return 0
    pgvector_service.ensure_table(db)
    pgvector_service.clear_for_connection(db, connection_id)
    descriptions = [_describe(i) for i in schema_items]
    embeddings = embedding_service.embed_documents(descriptions)
    pgvector_service.store_items(db, connection_id, schema_items, embeddings)
    return len(schema_items)


def retrieve_relevant_schema(db: Session, connection_id: int, user_question: str) -> list:
    if pgvector_service.count(db, connection_id) == 0:
        return []
    qvec = embedding_service.embed_query(user_question)
    return pgvector_service.search(db, connection_id, qvec, top_k=settings.SCHEMA_TOP_K)