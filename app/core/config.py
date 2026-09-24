"""集中配置：从环境变量读取（compose 注入），本地可覆盖 .env。"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    log_level: str = "INFO"
    service_name: str = "education-ai-service"
    enable_background_tasks: bool = True

    database_url: str = "postgresql+asyncpg://eduai:eduai_password@localhost:5432/eduai"
    database_url_sync: str = (
        "postgresql+psycopg2://eduai:eduai_password@localhost:5432/eduai"
    )
    # Compose normally runs 3 API replicas + worker + scheduler.  Keep the
    # aggregate below PostgreSQL's default max_connections=100.
    database_pool_size: int = 8
    database_max_overflow: int = 4
    database_pool_timeout_seconds: float = 10.0
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 50
    context_max_messages: int = 20
    context_ttl_seconds: int = 3600
    nats_url: str = "nats://localhost:4222"
    worker_concurrency: int = 32
    worker_fetch_batch: int = 128
    outbox_batch_size: int = 100
    outbox_publish_concurrency: int = 20
    outbox_poll_interval_seconds: float = 0.02
    metrics_collection_seconds: float = 5.0
    worker_metrics_port: int = 9101
    qdrant_url: str = "http://localhost:6333"
    otel_exporter_otlp_endpoint: str | None = None

    jwt_secret: str = "dev_secret_change_me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    tenant_rate_limit_per_minute: int = 600
    user_rate_limit_per_minute: int = 120

    mock_im_url: str = "http://localhost:8100"
    mock_llm_url: str = "http://localhost:8101"
    mock_knowledge_url: str = "http://localhost:8102"
    mock_platform_url: str = "http://localhost:8103"
    mock_finance_url: str = "http://localhost:8104"

    # OpenAI-compatible LLM (DeepSeek in the local demo).  Keep secrets server-side.
    deepseek_api_url: str = "http://localhost:8101/v1/chat/completions"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 800
    llm_global_max_inflight: int = 600
    llm_global_starts_per_second: int = 20
    llm_input_cost_per_million_usd: float = 0.0
    llm_output_cost_per_million_usd: float = 0.0

    # Local interview demo. Disable DEMO_MODE outside a disposable demo environment.
    demo_mode: bool = False
    demo_tenant_id: str = "10000000-0000-0000-0000-000000000001"
    demo_tenant_name: str = "演示租户1"
    demo_admin_user_id: str = "10000000-0000-0000-0000-000000000100"
    demo_admin_conversation_id: str = "10000000-0000-0000-0000-000000000200"
    demo_tenant_admin_email: str = "admin@example.com"
    demo_tenant_admin_password: str = "change_me"
    # The public customer shortcut is deliberately isolated from the writable
    # tenant workbench above. Its knowledge comes only from this fixed fixture.
    demo_customer_tenant_id: str = "20000000-0000-0000-0000-000000000001"
    demo_customer_tenant_name: str = "星河未来成长中心"
    demo_customer_user_id: str = "20000000-0000-0000-0000-000000000101"
    demo_customer_conversation_id: str = "20000000-0000-0000-0000-000000000201"
    demo_customer_email: str = "customer@example.com"
    demo_customer_knowledge_root: str = "sample-data/tenants/your-tenant"
    demo_max_upload_chars: int = 200_000
    knowledge_lexical_scan_limit: int = 500
    rag_semantic_candidate_limit: int = 40

    @model_validator(mode="after")
    def validate_security_boundary(self):
        environment = self.app_env.strip().lower()
        if environment not in {"development", "test", "demo"}:
            if not self.jwt_secret or self.jwt_secret == "dev_secret_change_me":
                raise ValueError("production-like environments require a non-default JWT_SECRET")
            if self.demo_mode:
                raise ValueError("DEMO_MODE must be disabled outside development/demo/test")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
