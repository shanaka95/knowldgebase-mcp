"""Settings for the MCP server.

The server holds no credentials of its own. Callers authenticate with a Knowledge
Base API key, and every request is made as that user, so what a caller can reach
is exactly what their key can reach.
"""

from __future__ import annotations

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore"
    )

    # Where the Knowledge Base API lives. Inside the deployment this is the
    # container name; from a laptop it is the public URL.
    KB_API_URL: HttpUrl = Field(default=HttpUrl("http://backend:8000/api/v1"))
    KB_TIMEOUT_SECONDS: float = 120.0
    # Answers and imports take far longer than ordinary calls.
    KB_LONG_TIMEOUT_SECONDS: float = 600.0

    # How this server is served.
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # Mount path; behind a proxy this is what clients connect to.
    MCP_PATH: str = "/mcp"
    # Public URL of this server, advertised in OAuth resource metadata.
    PUBLIC_URL: HttpUrl | None = None

    # A verified key is trusted for this long before it is checked again, so a
    # burst of tool calls does not mean a burst of auth requests.
    AUTH_CACHE_SECONDS: float = 300.0

    # Upper bound on how long a tool will wait for an import or an index to
    # finish when asked to wait.
    MAX_WAIT_SECONDS: float = 540.0

    @property
    def api_url(self) -> str:
        return str(self.KB_API_URL).rstrip("/")


settings = Settings()
