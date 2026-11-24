from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Username and password for POST /auth/login."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """Bearer token returned after a successful login."""

    token: str
    username: str


class ProductOut(BaseModel):
    """Public product fields."""

    id: int
    sku: str
    name: str
    price_cents: int
    stock: int

    model_config = {"from_attributes": True}


class CartItemIn(BaseModel):
    """Add or replace a cart line."""

    product_id: int
    quantity: int = Field(ge=1)


class CartItemOut(BaseModel):
    """Cart line with computed line total."""

    product_id: int
    sku: str
    name: str
    quantity: int
    price_cents: int
    line_total_cents: int


class CartOut(BaseModel):
    """Full cart with items and total."""

    items: list[CartItemOut]
    total_cents: int


class OrderItemOut(BaseModel):
    """Line item on an order."""

    product_id: int
    sku: str
    name: str
    quantity: int
    price_cents: int


class OrderOut(BaseModel):
    """Order header plus line items."""

    id: int
    status: str
    total_cents: int
    created_at: datetime
    items: list[OrderItemOut]


class HealthOut(BaseModel):
    """Liveness payload for database and payment stub."""

    status: str
    database: dict[str, str]
    payment: dict[str, str]
