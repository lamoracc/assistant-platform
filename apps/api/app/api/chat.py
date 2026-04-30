from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.llm_provider import get_llm_provider
from app.services.prompt_builder import (
    build_chat_prompt,
    build_retrieval_only_answer,
)
from app.services.retrieval import retrieve_chunks
from app.services.text_sanitizer import sanitize_text

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    debug: bool = False
    filters: dict[str, Any] | None = None


class ChatSource(BaseModel):
    document: str
    source_file: str
    heading: str | None
    chunk_index: int
    score: float
    ranking_reason: list[str] | None = None


class ChatQueryResponse(BaseModel):
    answer: str
    sources: list[ChatSource]
    diagnostics: dict[str, Any] | None = None


@router.post("/query", response_model=ChatQueryResponse)
def query_chat(
    request: ChatQueryRequest,
    db: Session = Depends(get_db),
) -> ChatQueryResponse:
    question = sanitize_text(request.question)
    retrieval = retrieve_chunks(question, db, filters=request.filters)
    chunks = retrieval.chunks
    messages = build_chat_prompt(question, chunks)
    provider_answer = get_llm_provider().generate(messages)
    answer = provider_answer or build_retrieval_only_answer(question, chunks)

    return ChatQueryResponse(
        answer=answer,
        sources=[
            ChatSource(
                document=chunk.document,
                source_file=chunk.source_file,
                heading=chunk.heading,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
                ranking_reason=chunk.ranking_reason if request.debug else None,
            )
            for chunk in chunks
        ],
        diagnostics=retrieval.diagnostics if request.debug else None,
    )
