from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CartItem, Product, User
from app.schemas import CartItemIn, CartItemOut, CartOut
from app.security import current_user

router = APIRouter(prefix="/cart", tags=["cart"])


def _cart_out(db: Session, user: User) -> CartOut:
    items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    out_items: list[CartItemOut] = []
    total = 0
    for item in items:
        product = db.get(Product, item.product_id)
        if product is None:
            continue
        line = product.price_cents * item.quantity
        total += line
        out_items.append(
            CartItemOut(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                quantity=item.quantity,
                price_cents=product.price_cents,
                line_total_cents=line,
            )
        )
    return CartOut(items=out_items, total_cents=total)


@router.get("", response_model=CartOut)
def get_cart(user: User = Depends(current_user), db: Session = Depends(get_db)) -> CartOut:
    return _cart_out(db, user)


@router.put("/items", response_model=CartOut)
def upsert_item(
    body: CartItemIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CartOut:
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if body.quantity > product.stock:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough stock")
    item = (
        db.query(CartItem)
        .filter(CartItem.user_id == user.id, CartItem.product_id == body.product_id)
        .one_or_none()
    )
    if item is None:
        db.add(CartItem(user_id=user.id, product_id=body.product_id, quantity=body.quantity))
    else:
        item.quantity = body.quantity
    db.flush()
    return _cart_out(db, user)


@router.delete("/items/{product_id}", response_model=CartOut)
def remove_item(
    product_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> CartOut:
    item = (
        db.query(CartItem)
        .filter(CartItem.user_id == user.id, CartItem.product_id == product_id)
        .one_or_none()
    )
    if item is not None:
        db.delete(item)
        db.flush()
    return _cart_out(db, user)
