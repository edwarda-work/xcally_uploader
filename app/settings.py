from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_prefix: str
    verify_ssl: bool | str
    username: str
    password: str
    request_timeout: int
    max_workers: int
    max_upload_bytes: int
    history_limit: int
    import_wait_seconds: int = 60

    @classmethod
    def from_environment(cls, project_dir: Path) -> "Settings":
        load_local_env(project_dir / ".env")
        ca_bundle = os.getenv("XCALLY_CA_BUNDLE", "").strip()
        return cls(
            base_url=os.getenv("XCALLY_BASE_URL", "https://52.49.51.30").rstrip("/"),
            api_prefix=os.getenv("XCALLY_API_PREFIX", "/api"),
            # The current xCALLY installation uses a self-signed certificate.
            # Prefer XCALLY_CA_BUNDLE in production; false preserves legacy connectivity.
            verify_ssl=ca_bundle or os.getenv("XCALLY_VERIFY_SSL", "false").lower() in {"1", "true", "yes"},
            username=os.getenv("XCALLY_USERNAME", "").strip(),
            password=os.getenv("XCALLY_PASSWORD", ""),
            request_timeout=int(os.getenv("XCALLY_REQUEST_TIMEOUT", "300")),
            max_workers=max(1, min(int(os.getenv("UPLOAD_MAX_WORKERS", "5")), 10)),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(60 * 1024 * 1024))),
            history_limit=int(os.getenv("HISTORY_LIMIT", "500")),
            import_wait_seconds=max(1, int(os.getenv("XCALLY_IMPORT_WAIT_SECONDS", "60"))),
        )
