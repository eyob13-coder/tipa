import uuid
import uuid as uuid_pkg
from datetime import datetime, timezone
from decimal import Decimal

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
    payment_method: Mapped[str] = mapped_column(String, nullable=False, default="cbe")  # see app.payment_methods
    account_number: Mapped[str] = mapped_column(String, nullable=False)
    account_name: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Ownership proof: creator sends a small coded transfer from the registered
    # account to Tipa's account, verified like any other payment receipt.
    account_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    account_verification_code: Mapped[str | None] = mapped_column(String, nullable=True)
    account_verification_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    # Admin abuse control: frozen creators cannot receive new tips.
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # UI language ('en' | 'am').
    language: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    # Last time the creator received their weekly digest DM (dedup across workers).
    last_weekly_digest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional private channel every verified tipper is invited into (pay-to-unlock).
    vip_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tips: Mapped[list["Tip"]] = relationship("Tip", back_populates="creator", lazy="selectin", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="creator", lazy="selectin", cascade="all, delete-orphan"
    )
    goals: Mapped[list["TipGoal"]] = relationship(
        "TipGoal", back_populates="creator", lazy="selectin", cascade="all, delete-orphan"
    )


class TipGoal(Base):
    """A creator's public fundraising goal ("🎯 5,000/10,000 ETB for new camera").

    One active goal per creator. Progress is never stored — it is recomputed as
    the sum of verified tips since the goal was created, so disputes/refunds
    self-correct. When attached to a channel post (bound_channel_id /
    bound_message_id), the post's progress bar is edited live after each
    verified tip using bound_text (post body without the goal line).
    """

    __tablename__ = "tip_goals"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active", index=True
    )  # 'active' | 'reached' | 'cancelled'
    # Live-update binding to the channel post showing this goal.
    bound_channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    bound_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    bound_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bound_is_caption: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped["Creator"] = relationship("Creator", back_populates="goals")


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
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tx_ref: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    ref_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", index=True
    )  # 'pending' | 'pending_verification' | 'success' | 'failed' | 'disputed'
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Client-supplied idempotency key: replays return the original tip.
    idempotency_key: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True
    )
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
    verified_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # Persisted receipt screenshot (dispute evidence); stored on disk/blob.
    receipt_file_path: Mapped[str | None] = mapped_column(String, nullable=True)

    creator: Mapped["Creator"] = relationship("Creator", back_populates="tips")


class Subscription(Base):
    """A creator's Tipa Pro subscription purchase attempt and lifecycle.

    Creators pay Tipa directly (same no-custody direct-transfer flow as tips).
    A row starts as ``pending`` when /pro shows payment instructions, moves to
    ``pending_verification`` once a receipt reference is claimed, then either
    auto-verifies via a provider or is approved manually by an admin.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("creators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan: Mapped[str] = mapped_column(String, nullable=False, default="pro")
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", index=True
    )  # 'pending' | 'pending_verification' | 'active' | 'expired' | 'rejected'
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tx_ref: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    verification_method: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    creator: Mapped["Creator"] = relationship("Creator", back_populates="subscriptions")


class VerificationLog(Base):
    """Append-only audit trail of every verification attempt on a tip or subscription."""

    __tablename__ = "verification_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tip_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tips.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tip: Mapped["Tip"] = relationship("Tip")


class RateLimitBucket(Base):
    """Fixed-window rate-limit counters shared across workers/replicas.

    Replaces the old per-process in-memory sliding window, which was useless
    with multiple uvicorn workers and leaked memory for every client IP.
    """

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
