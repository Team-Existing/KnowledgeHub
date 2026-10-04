"""
Entry point of the backend in the one-command local stack (docker-compose.yml
at the repository root): `docker compose up` must work without a .env file.

SECRET_KEY and CREDENTIALS_KEY are taken from the environment when set.
Otherwise each is generated once, stored in KH_SECRETS_DIR (a named volume,
readable only by the app user) and reused on every later start, so sign-ins
and stored connector credentials survive restarts and rebuilds.

The production stack (deploy/) never uses this: it requires every secret
to be set explicitly.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Callable

from app.crypto import generate_key

SECRETS_DIR = Path(os.getenv("KH_SECRETS_DIR", "/var/lib/knowledge-hubs"))


def load_or_create(name: str, make: Callable[[], str]) -> None:
    if os.getenv(name):
        return
    path = SECRETS_DIR / name.lower()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(make())
        print(f"local stack: generated {name} (stored in the {SECRETS_DIR} volume)", flush=True)
    os.environ[name] = path.read_text().strip()


def main() -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    load_or_create("SECRET_KEY", lambda: secrets.token_urlsafe(48))
    load_or_create("CREDENTIALS_KEY", generate_key)
    # the same server command as the image's default; the web container proxies to it
    os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
                          "--proxy-headers", "--forwarded-allow-ips", "*"])


if __name__ == "__main__":
    main()
