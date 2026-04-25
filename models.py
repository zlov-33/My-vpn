from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Boolean, DateTime, Text, ForeignKey, BigInteger
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base
import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    client = "client"


class Plan(str, enum.Enum):
    lite = "lite"
    standard = "standard"
    family = "family"


class PaymentMethod(str, enum.Enum):
    yookassa = "yookassa"
    telegram_stars = "telegram_stars"
    robokassa = "robokassa"
    crypto = "crypto"
    manual = "manual"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    refunded = "refunded"


# Traffic limits in GB per plan (0 = unlimited)
PLAN_TRAFFIC_GB = {
    "lite": 100,
    "standard": 500,
    "family": 0,
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="client", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    telegram_link_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    referral_code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, nullable=True)
    referred_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reset_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reset_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    clients: Mapped[List["Client"]] = relationship("Client", back_populates="user")


class Server(Base):
    """
    VPN server node. Each server has its own VLESS API instance.
    Credentials are stored encrypted (Fernet) in api_pass_encrypted.
    """
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    location: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    api_url: Mapped[str] = mapped_column(String(512), nullable=False)          # http://127.0.0.1:8100
    api_user: Mapped[str] = mapped_column(String(128), default="admin", nullable=False)
    api_pass_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    reality_sni: Mapped[str] = mapped_column(String(256), default="", nullable=False)  # e.g. max.ru
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # 0 = highest
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)

    # Traffic
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=500, nullable=False)  # 0 = unlimited
    traffic_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # VLESS subscription
    vless_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    vless_sub_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    user: Mapped[Optional["User"]] = relationship("User", back_populates="clients")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="client")

    @property
    def traffic_limit_bytes(self) -> int:
        return self.traffic_limit_gb * 1024 ** 3

    @property
    def traffic_used_gb(self) -> float:
        return round(self.traffic_used_bytes / 1024 ** 3, 2)

    @property
    def traffic_percent(self) -> int:
        if self.traffic_limit_gb == 0:
            return 0
        pct = int(self.traffic_used_bytes / self.traffic_limit_bytes * 100)
        return min(pct, 100)

    @property
    def sub_url(self) -> str:
        from config import settings
        return f"{settings.site_url}/sub/{self.vless_sub_token}" if self.vless_sub_token else ""


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # kopecks
    currency: Mapped[str] = mapped_column(String(8), default="RUB", nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    months: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    client: Mapped["Client"] = relationship("Client", back_populates="payments")


class Promo(Base):
    __tablename__ = "promos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extra_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
