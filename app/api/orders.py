from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order, Product, User
from app.schemas import OrderItemOut, OrderOut
from app.security import current_user
from app.shop import place_order

router = APIRouter(tags=["orders"])


def _order_out(db: Session, order: Order) -> OrderOut:
    """Serialize an order and its line items."""
    items: list[OrderItemOut] = []
    for item in order.items:
        product = db.get(Product, item.product_id)
        items.append(
            OrderItemOut(
                product_id=item.product_id,
                sku=product.sku if product else "",
                name=product.name if product else "",
                quantity=item.quantity,
                price_cents=item.price_cents,
            )
        )
    return OrderOut(
        id=order.id,
        status=order.status,
        total_cents=order.total_cents,
        created_at=order.created_at,
        items=items,
    )


@router.post("/checkout", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def checkout(user: User = Depends(current_user), db: Session = Depends(get_db)) -> OrderOut:
    """Pay the cart, decrement stock, and return the new order."""
    return _order_out(db, place_order(db, user))


@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[OrderOut]:
    """List the current user's orders, newest first."""
    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.id.desc()).all()
    return [_order_out(db, order) for order in orders]


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> OrderOut:
    """Return one of the current user's orders or 404."""
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return _order_out(db, order)
