"""Low-level shared helpers: env loading, HTTP, file encoding.

Ported and trimmed from occ's ``occ/common.py``. No vendor logic lives here —
only generic plumbing the backend can rely on.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# --------------------------------------------------------------------------
# Environment / .env loading
# --------------------------------------------------------------------------
def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from a .env file into ``os.environ``.

    Existing environment variables are never overwritten.
    """
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def load_env(paths: list[Path]) -> None:
    """Load every .env candidate, first writer wins per key."""
    for candidate in paths:
        load_env_file(Path(candidate))


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def file_to_base64(path: str) -> str:
    """Encode a local file's raw bytes as base64 (Seed Audio's ``audio_data``
    / ``image_data`` fields want the bare base64 string, not a data: URL)."""
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def file_to_data_url(path: str) -> str:
    """Encode a local file as a ``data:`` URL."""
    file_path = Path(path)
    mime_type, _ = mimetypes.guess_type(file_path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def is_remote(value: str) -> bool:
    return value.startswith(("http://", "https://", "data:"))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (URLError, OSError)):
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "connection reset", "connection aborted", "reset by peer",
            "temporarily unavailable", "timed out", "remote end closed connection",
        )
    )


def _request(request: Request, *, timeout: int, label: str) -> bytes:
    retries = max(1, get_env_int("HTTP_RETRIES", 3))
    backoff = 2.0
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries or not _retryable(exc):
                break
            print(f"[http] retry {attempt}/{retries} {label}: {exc}",
                  file=sys.stderr, flush=True)
            time.sleep(min(30.0, backoff * attempt))
    assert last_exc is not None
    raise last_exc


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 120) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers=headers, method="POST")
    try:
        body = _request(request, timeout=timeout, label=f"POST {url}")
        return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        detail = _read_error_body(exc)
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {detail}") from exc


def get_json(url: str, headers: dict[str, str], timeout: int = 120) -> dict:
    request = Request(url, headers=headers)
    try:
        body = _request(request, timeout=timeout, label=f"GET {url}")
        return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        detail = _read_error_body(exc)
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {detail}") from exc


def _read_error_body(exc: HTTPError) -> str:
    try:
        return exc.read().decode("utf-8") or exc.reason
    except Exception:  # noqa: BLE001
        return str(exc.reason)
