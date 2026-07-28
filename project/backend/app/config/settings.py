from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_key: str
    supabase_service_key: str
    connect_to_db: bool = True

    # LiteLLM proxy — all LLM calls route here
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "gemini/gemini-2.5-flash"
    litellm_embedding_model: str = "text-embedding-004"
    litellm_whisper_model: str = "whisper-1"   # Whisper model name on your LiteLLM proxy

    # Qdrant Cloud
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection: str = "masar_questions"
    qdrant_cards_collection: str = "masar_memory_cards"
    qdrant_cv_collection: str = "masar_cv_chunks"

    # Email
    resend_api_key: str = ""
    report_from_email: str = "masar@yourdomain.com"
    admin_notification_email: str = ""   # comma-separated admin inboxes for completion alerts

    # E2B
    e2b_api_key: str = ""

    # Langfuse
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # App
    base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"
    secret_key: str = "change-me"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
