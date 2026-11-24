from __future__ import annotations

from pathlib import Path

import pytest

from app.db import make_engine, make_session_factory
from app.models import Product, User
from app.seed import main


def test_reset_rebuilds_demo_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset replaces existing rows with the demo users and products."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'seed.db'}")
    main(["--reset"])
    main(["--reset"])
    engine = make_engine()
    session = make_session_factory(engine)()
    try:
        assert session.query(User).count() == 2
        widget = session.query(Product).filter(Product.sku == "WIDGET").one()
        assert widget.price_cents == 1299
        assert widget.stock == 20
    finally:
        session.close()
        engine.dispose()
