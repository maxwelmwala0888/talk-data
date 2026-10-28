import google.generativeai as genai
from app.core.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)


class EmbeddingService:
    """Embeddings always use Gemini — Groq doesn't offer an embedding API."""
    def __init__(self):
        self.model = settings.GEMINI_EMBED_MODEL
        self.dim = settings.GEMINI_EMBED_DIM

    def embed_documents(self, texts: list) -> list:
        if not texts:
            return []
        result = genai.embed_content(
            model=self.model,
            content=texts,
            task_type="retrieval_document",
            output_dimensionality=self.dim,
        )
        emb = result["embedding"]
        if emb and isinstance(emb[0], (int, float)):
            return [emb]
        return emb

    def embed_query(self, text: str) -> list:
        result = genai.embed_content(
            model=self.model,
            content=text,
            task_type="retrieval_query",
            output_dimensionality=self.dim,
        )
        return result["embedding"]


embedding_service = EmbeddingService()