import uuid
import uuid as uuid_pkg
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, TypeDecorator

from app.db.base import Base


class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses CHAR(36), storing as stringified hex values.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, uuid_pkg.UUID):
                return str(uuid_pkg.UUID(value))
            else:
                return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid_pkg.UUID):
                return uuid_pkg.UUID(value)
            else:
                return value


class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    bank_code: Mapped[int] = mapped_column(Integer, nullable=False, default=861)
    payment_method: Mapped[str] = mapped_column(String, nullable=False, default="cbe")  # 'cbe' | 'telebirr'
    account_number: Mapped[str] = mapped_column(String, nullable=False)
    account_name: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tips: Mapped[list["Tip"]] = relationship("Tip", back_populates="creator", lazy="selectin", cascade="all, delete-orphan")


class Tip(Base):
    __tablename__ = "tips"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    tipper_telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    tipper_display_name: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    platform_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    tx_ref: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    ref_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # 'pending' | 'pending_verification' | 'success' | 'failed'
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_method: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # provider name ('verify_et' | 'check_et' | 'justverify') | 'creator_approval'
    verified_amount: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )

    creator: Mapped["Creator"] = relationship("Creator", back_populates="tips")


class VerificationLog(Base):
    """Append-only audit trail of every verification attempt on a tip."""

    __tablename__ = "verification_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tip_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tip: Mapped["Tip"] = relationship("Tip")
