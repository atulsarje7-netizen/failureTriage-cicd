from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    username: str


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    price_cents: int
    stock: int

    model_config = {"from_attributes": True}


class CartItemIn(BaseModel):
    product_id: int
    quantity: int = Field(ge=1)


class CartItemOut(BaseModel):
    product_id: int
    sku: str
    name: str
    quantity: int
    price_cents: int
    line_total_cents: int


class CartOut(BaseModel):
    items: list[CartItemOut]
    total_cents: int


class OrderItemOut(BaseModel):
    product_id: int
    sku: str
    name: str
    quantity: int
    price_cents: int


class OrderOut(BaseModel):
    id: int
    status: str
    total_cents: int
    created_at: datetime
    items: list[OrderItemOut]


class HealthOut(BaseModel):
    status: str
    database: dict[str, str]
    payment: dict[str, str]
