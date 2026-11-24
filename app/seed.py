from __future__ import annotations

import argparse

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, make_engine, make_session_factory
from app.models import AuthSession, CartItem, Order, OrderItem, Product, User
from app.security import hash_password, seed_secret


def seed_if_empty(session_factory: sessionmaker[Session]) -> None:
    """Insert demo users and products when the database has none."""
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


def reset_database(session_factory: sessionmaker[Session]) -> None:
    """Delete shop rows and insert the demo users and products again."""
    db = session_factory()
    try:
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            db.execute(
                text(
                    "TRUNCATE order_items, orders, cart_items, sessions, products, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
        else:
            for model in (OrderItem, Order, CartItem, AuthSession, Product, User):
                db.query(model).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    seed_if_empty(session_factory)


def main(argv: list[str] | None = None) -> None:
    """Create tables and seed them, rebuilding existing rows when --reset is set."""
    parser = argparse.ArgumentParser(description="Seed the shop database")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing shop rows before seeding",
    )
    args = parser.parse_args(argv)
    engine = make_engine()
    factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    if args.reset:
        reset_database(factory)
    else:
        seed_if_empty(factory)
    engine.dispose()


if __name__ == "__main__":
    main()
