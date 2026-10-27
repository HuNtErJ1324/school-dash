"""Local API server with reload.

Uses your config.yaml if you've made one (with secrets from .env), otherwise config.dev.yaml
(made-up demo items, no secrets needed).

    python dev.py          # then, in frontend/: npm run dev
"""

import os
from pathlib import Path

import uvicorn

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_dotenv(path: Path) -> None:
    """KEY=VALUE lines from .env into the environment, without overriding anything already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv(ROOT / ".env")
config = ROOT / "config.yaml"
os.environ.setdefault("CONFIG_PATH", str(config if config.exists() else ROOT / "config.dev.yaml"))
os.environ.setdefault("DATA_DIR", str(ROOT / "data" / "dev"))

if __name__ == "__main__":
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8765")),
        reload=True,
        reload_dirs=[str(HERE / "app")],
        app_dir=str(HERE),
    )
