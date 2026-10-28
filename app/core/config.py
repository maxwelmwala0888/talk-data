from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    APP_NAME: str = "Talk to Data"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080

    # Database (SQLite)
    DATABASE_URL: str

    # Encryption
    FERNET_KEY: str

    # Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_EMBED_MODEL: str = "models/gemini-embedding-001"
    GEMINI_EMBED_DIM: int = 768

    # Groq (optional)
    LLM_PROVIDER: str = "gemini"  # "gemini" | "groq" | "grok"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # xAI / Grok
    XAI_API_KEY: str = ""
    XAI_MODEL: str = "grok-4.3"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""

    # CORS
    FRONTEND_URL: str = "http://localhost:3000"

    # Storage
    TEMP_ACCESS_DIR: str = "./data/temp_access_files"
    MAX_UPLOAD_SIZE_MB: int = 100

    # Retrieval
    SCHEMA_TOP_K: int = 20

    # OpenRouter - free tier, 25+ models, no card
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "meta-llama/llama-3.3-70b-instruct:free"
    LLM_FALLBACK_CHAIN: str = "openrouter,groq,gemini"


settings = Settings()