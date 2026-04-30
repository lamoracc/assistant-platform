from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.debug import router as debug_router
from app.api.documents import router as documents_router
from app.core.database import init_db
from app.services.qdrant_store import init_qdrant_collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_qdrant_collection()
    yield


app = FastAPI(
    title="Assistant Platform API",
    description="Backend API for a RAG-based work assistant.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(debug_router)
