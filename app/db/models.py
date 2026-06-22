"""Modelos SQLAlchemy 2.0 — reflejan migrations/001_init.sql.

La migración SQL es la fuente de verdad del esquema; estos modelos dan acceso ORM.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

IMAGE_DIM = 768   # RESERVADO: modo local opcional (CLIP). No usado en modo OpenAI.
TEXT_DIM = 1536   # text-embedding-3-small


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    locale: Mapped[str] = mapped_column(Text, default="es", nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    profiles: Mapped[list[Profile]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    channels: Mapped[list[Channel]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = _pk()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="profiles")


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("channel_type", "external_id"),
        CheckConstraint("channel_type IN ('telegram','whatsapp')", name="ck_channel_type"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="channels")


class Medicine(Base):
    __tablename__ = "medicines"

    id: Mapped[uuid.UUID] = _pk()
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    dose: Mapped[str | None] = mapped_column(Text)
    lab: Mapped[str | None] = mapped_column(Text)
    presentation: Mapped[str | None] = mapped_column(Text)
    form: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Prescription(Base):
    __tablename__ = "prescriptions"

    id: Mapped[uuid.UUID] = _pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    doctor: Mapped[str | None] = mapped_column(Text)
    prescribed_at: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('receta','caja','blister','pastilla')",
            name="ck_record_source",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prescriptions.id", ondelete="SET NULL")
    )
    medicine_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("medicines.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    visible_text: Mapped[str | None] = mapped_column(Text)
    ai_description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    expiry: Mapped[str | None] = mapped_column(Text)
    lot_number: Mapped[str | None] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    images: Mapped[list[RecordImage]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class RecordImage(Base):
    __tablename__ = "record_images"
    __table_args__ = (UniqueConstraint("record_id", "sha256"),)

    id: Mapped[uuid.UUID] = _pk()
    record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), nullable=False
    )
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    record: Mapped[Record] = relationship(back_populates="images")
    embeddings: Mapped[list[RecordEmbedding]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )


class RecordEmbedding(Base):
    __tablename__ = "record_embeddings"

    id: Mapped[uuid.UUID] = _pk()
    record_image_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("record_images.id", ondelete="CASCADE"), nullable=False
    )
    image_embedding: Mapped[list[float] | None] = mapped_column(Vector(IMAGE_DIM))
    text_embedding: Mapped[list[float] | None] = mapped_column(Vector(TEXT_DIM))
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    image: Mapped[RecordImage] = relationship(back_populates="embeddings")


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[uuid.UUID] = _pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    medicine_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("medicines.id", ondelete="SET NULL")
    )
    purchased_at: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[float | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Numeric)
    pharmacy: Mapped[str | None] = mapped_column(Text)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    lot_number: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[uuid.UUID] = _pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    query_image_url: Mapped[str | None] = mapped_column(Text)
    query_embedding: Mapped[list[float] | None] = mapped_column(Vector(TEXT_DIM))
    question_text: Mapped[str | None] = mapped_column(Text)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    results: Mapped[list[QueryResult]] = relationship(
        back_populates="query", cascade="all, delete-orphan"
    )


class QueryResult(Base):
    __tablename__ = "query_results"

    id: Mapped[uuid.UUID] = _pk()
    query_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("queries.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("records.id", ondelete="SET NULL")
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    vector_score: Mapped[float | None] = mapped_column(Double)
    vision_confidence: Mapped[float | None] = mapped_column(Double)
    was_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    query: Mapped[Query] = relationship(back_populates="results")
