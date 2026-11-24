from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import CartItemIn, CartOut
from app.security import current_user
from app.shop import cart_out, remove_cart_item, upsert_cart_item

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get("", response_model=CartOut)
def get_cart(user: User = Depends(current_user), db: Session = Depends(get_db)) -> CartOut:
    """Return the authenticated user's cart."""
    return cart_out(db, user)


@router.put("/items", response_model=CartOut)
def upsert_item(
    body: CartItemIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CartOut:
    """Add or update a cart line if stock allows."""
    return upsert_cart_item(db, user, body.product_id, body.quantity)


@router.delete("/items/{product_id}", response_model=CartOut)
def remove_item(
    product_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CartOut:
    """Remove a product from the cart if present."""
    return remove_cart_item(db, user, product_id)
