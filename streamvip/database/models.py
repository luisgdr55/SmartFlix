from __future__ import annotations

from datetime import datetime
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, Field


class UserModel(BaseModel):
    id: Optional[UUID] = None
    telegram_id: int
    username: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    created_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    is_admin: bool = False
    total_purchases: int = 0
    status: str = "active"
    preferred_platform: Optional[str] = None
    receives_promos: bool = True
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class PlatformModel(BaseModel):
    id: Optional[UUID] = None
    name: str
    slug: str
    icon_emoji: Optional[str] = None
    color_hex: Optional[str] = None
    monthly_price_usd: Optional[float] = None
    express_price_usd: float = 1.00
    week_price_usd: Optional[float] = None
    max_profiles: int = 5
    is_active: bool = True
    instructions_monthly: Optional[str] = None
    instructions_express: Optional[str] = None
    tmdb_provider_id: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AccountModel(BaseModel):
    id: Optional[UUID] = None
    platform_id: UUID
    email: str
    password: str
    billing_date: Optional[datetime] = None
    gmail_api_enabled: bool = False
    gmail_credentials: Optional[dict] = None
    status: str = "active"
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProfileModel(BaseModel):
    id: Optional[UUID] = None
    account_id: UUID
    platform_id: UUID
    profile_name: str
    pin: Optional[str] = None
    profile_type: str = "monthly"
    status: str = "available"
    last_released: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SubscriptionModel(BaseModel):
    id: Optional[UUID] = None
    user_id: UUID
    profile_id: Optional[UUID] = None
    platform_id: UUID
    plan_type: str
    start_date: Optional[datetime] = None
    end_date: datetime
    price_usd: Optional[float] = None
    price_bs: Optional[float] = None
    rate_used: Optional[float] = None
    status: str = "pending_payment"
    payment_reference: Optional[str] = None
    payment_image_url: Optional[str] = None
    payment_confirmed_at: Optional[datetime] = None
    reminder_sent: bool = False
    expiry_notified: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaymentConfigModel(BaseModel):
    id: Optional[UUID] = None
    banco: Optional[str] = None
    telefono: Optional[str] = None
    cedula: Optional[str] = None
    titular: Optional[str] = None
    is_active: bool = True
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ExchangeRateModel(BaseModel):
    id: Optional[UUID] = None
    usd_binance: Optional[float] = None
    usd_bcv: Optional[float] = None
    eur_bcv: Optional[float] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[int] = None

    class Config:
        from_attributes = True


class ExpressQueueModel(BaseModel):
    id: Optional[UUID] = None
    user_id: UUID
    platform_id: UUID
    requested_at: Optional[datetime] = None
    status: str = "waiting"
    notified_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CampaignModel(BaseModel):
    id: Optional[UUID] = None
    title: str
    platform_id: Optional[UUID] = None
    content_title: Optional[str] = None
    content_type: Optional[str] = None
    content_year: Optional[int] = None
    synopsis_vzla: Optional[str] = None
    flyer_image_url: Optional[str] = None
    audience: str = "all"
    sent_count: int = 0
    clicked_count: int = 0
    converted_count: int = 0
    scheduled_for: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    created_by: Optional[int] = None
    status: str = "draft"
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminLogModel(BaseModel):
    id: Optional[UUID] = None
    admin_telegram_id: int
    action: str
    details: Optional[dict] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
