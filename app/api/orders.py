from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CartItem, Order, OrderItem, Product, User
from app.schemas import OrderItemOut, OrderOut
from app.security import current_user

router = APIRouter(tags=["orders"])


def _order_out(db: Session, order: Order) -> OrderOut:
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
    cart_items = db.query(CartItem).filter(CartItem.user_id == user.id).all()
    if not cart_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is empty")
    total = 0
    order = Order(user_id=user.id, total_cents=0, status="paid")
    db.add(order)
    db.flush()
    for cart_item in cart_items:
        product = db.get(Product, cart_item.product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        if cart_item.quantity > product.stock:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough stock")
        product.stock -= cart_item.quantity
        line = product.price_cents * cart_item.quantity
        total += line
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
    return _order_out(db, order)


@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[OrderOut]:
    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.id.desc()).all()
    return [_order_out(db, order) for order in orders]


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> OrderOut:
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return _order_out(db, order)
