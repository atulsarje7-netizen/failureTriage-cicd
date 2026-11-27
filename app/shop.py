from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import CartItem, Order, OrderItem, Product, User
from app.payment import PaymentError, charge
from app.schemas import CartItemOut, CartOut

logger = logging.getLogger("app.shop")


def cart_count(db: Session, user: User) -> int:
    """Return how many units are in the user's cart."""
    items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    return sum(item.quantity for item in items)


def cart_out(db: Session, user: User) -> CartOut:
    """Build the cart payload for the current user."""
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


def upsert_cart_item(db: Session, user: User, product_id: int, quantity: int) -> CartOut:
    """Add or replace a cart line if stock allows."""
    if quantity < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be at least 1"
        )
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if quantity > product.stock:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough stock")
    item = (
        db.query(CartItem)
        .filter(CartItem.user_id == user.id, CartItem.product_id == product_id)
        .one_or_none()
    )
    if item is None:
        db.add(CartItem(user_id=user.id, product_id=product_id, quantity=quantity))
    else:
        item.quantity = quantity
    db.flush()
    return cart_out(db, user)


def remove_cart_item(db: Session, user: User, product_id: int) -> CartOut:
    """Remove a product from the cart if present."""
    item = (
        db.query(CartItem)
        .filter(CartItem.user_id == user.id, CartItem.product_id == product_id)
        .one_or_none()
    )
    if item is not None:
        db.delete(item)
        db.flush()
    return cart_out(db, user)


def place_order(db: Session, user: User) -> Order:
    """Pay the cart, decrement stock, and return the new order."""
    cart_items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    if not cart_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is empty")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="payment failed"
    )
    total = 0
    priced: list[tuple[CartItem, Product]] = []
    for cart_item in cart_items:
        product = db.get(Product, cart_item.product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        if cart_item.quantity < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Quantity must be at least 1"
            )
        if product.stock < cart_item.quantity:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough stock")
        total += product.price_cents * cart_item.quantity
        priced.append((cart_item, product))
    try:
        charge(total, reference=f"user-{user.id}")
    except PaymentError as exc:
        logger.warning("payment_failed %s", exc.detail)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    order = Order(user_id=user.id, total_cents=0, status="paid")
    db.add(order)
    db.flush()
    for cart_item, product in priced:
        decremented = db.execute(
            update(Product)
            .where(Product.id == product.id, Product.stock >= cart_item.quantity)
            .values(stock=Product.stock - cart_item.quantity)
            .returning(Product.id)
        ).first()
        if decremented is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough stock")
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=cart_item.quantity,
                price_cents=product.price_cents,
            )
        )
        db.delete(cart_item)
    order.total_cents = total
    db.flush()
    db.refresh(order)
    return order
