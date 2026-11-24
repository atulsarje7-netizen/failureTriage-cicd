from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.models import Product, User
from app.security import hash_password, seed_secret


def seed_if_empty(session_factory: sessionmaker[Session]) -> None:
    db = session_factory()
    try:
        if db.query(User).first() is not None:
            return
        password = hash_password(seed_secret())
        db.add_all(
            [
                User(username="alice", password_hash=password),
                User(username="bob", password_hash=password),
            ]
        )
        db.add_all(
            [
                Product(sku="WIDGET", name="Widget", price_cents=1299, stock=20),
                Product(sku="GADGET", name="Gadget", price_cents=2499, stock=10),
                Product(sku="DOODAD", name="Doodad", price_cents=499, stock=50),
            ]
        )
        db.commit()
    finally:
        db.close()
