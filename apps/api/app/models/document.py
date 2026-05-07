import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    file_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ingested")
    text_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    doc_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    outgoing_links: Mapped[list["DocumentLink"]] = relationship(
        back_populates="source_document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentLink.source_document_id",
    )
    image_assets: Mapped[list["ImageAsset"]] = relationship(
        back_populates="referenced_by_document",
        cascade="all, delete-orphan",
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vector_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    heading: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class DocumentLink(Base):
    __tablename__ = "document_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    link_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    href: Mapped[str] = mapped_column(String(2048), nullable=False)
    target_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    link_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source_document: Mapped[Document] = relationship(
        back_populates="outgoing_links",
        foreign_keys=[source_document_id],
    )
    target_document: Mapped[Document | None] = relationship(
        foreign_keys=[target_document_id],
    )


class ImageAsset(Base):
    __tablename__ = "image_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    referenced_by_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="stored")
    asset_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    referenced_by_document: Mapped[Document | None] = relationship(
        back_populates="image_assets"
    )
