from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Submission Review Assistant", version="0.1.0")
app.include_router(router)

# Mounted last so /api/* routes take priority over static files.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
