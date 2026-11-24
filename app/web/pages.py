from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CartItem, Order, Product, User
from app.security import (
    COOKIE_NAME,
    CSRF_COOKIE,
    create_session,
    csrf_token_for,
    optional_user,
    require_csrf,
    revoke_session,
    set_signed_cookie,
    verify_password,
)
from app.shop import cart_count, cart_out, place_order, remove_cart_item, upsert_cart_item

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _ctx(
    request: Request,
    user: User | None,
    db: Session | None = None,
    csrf_token: str = "",
    **extra: object,
) -> dict[str, object]:
    """Build a template context with nav user, cart count, and CSRF token."""
    count = cart_count(db, user) if user is not None and db is not None else 0
    return {
        "request": request,
        "user": user,
        "cart_count": count,
        "csrf_token": csrf_token,
        **extra,
    }


def render(
    request: Request,
    name: str,
    user: User | None,
    db: Session | None = None,
    status_code: int = 200,
    **extra: object,
) -> Response:
    """Render a template and ensure a CSRF cookie is set."""
    token = csrf_token_for(request)
    response = templates.TemplateResponse(
        request,
        name,
        _ctx(request, user, db, csrf_token=token, **extra),
        status_code=status_code,
    )
    if request.cookies.get(CSRF_COOKIE) != token:
        set_signed_cookie(response, request, CSRF_COOKIE, token)
    return response


@router.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    """Send browsers to the catalog."""
    return RedirectResponse("/catalog", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(optional_user)) -> Response:
    """Render the login page, or skip it when already signed in."""
    if user is not None:
        return RedirectResponse("/catalog", status_code=303)
    return render(request, "login.html", None, error=None)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    """Create a session cookie for valid form credentials."""
    require_csrf(request, csrf_token)
    found = db.query(User).filter(User.username == username).one_or_none()
    if found is None or not verify_password(password, found.password_hash):
        return render(request, "login.html", None, status_code=401, error="Invalid credentials")
    token = create_session(db, found)
    response = RedirectResponse("/catalog", status_code=303)
    set_signed_cookie(response, request, COOKIE_NAME, token)
    csrf = csrf_token_for(request)
    if request.cookies.get(CSRF_COOKIE) != csrf:
        set_signed_cookie(response, request, CSRF_COOKIE, csrf)
    return response


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    """Revoke the server session and clear cookies."""
    require_csrf(request, csrf_token)
    revoke_session(db, request.cookies.get(COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/catalog", response_class=HTMLResponse)
def catalog(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    """Show the product catalog."""
    products = db.query(Product).order_by(Product.id).all()
    return render(request, "catalog.html", user, db, products=products)


@router.get("/basket", response_class=HTMLResponse)
def basket(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    """Show the cart, or send anonymous users to login."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return render(request, "cart.html", user, db, cart=cart_out(db, user))


@router.post("/basket/items", response_class=HTMLResponse)
def add_basket_item(
    request: Request,
    product_id: int = Form(),
    quantity: Annotated[int, Form(ge=1)] = 1,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    """HTMX: add a product to the cart and return the badge HTML."""
    require_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    existing = (
        db.query(CartItem)
        .filter(CartItem.user_id == user.id, CartItem.product_id == product_id)
        .one_or_none()
    )
    new_qty = existing.quantity + quantity if existing is not None else quantity
    upsert_cart_item(db, user, product_id, new_qty)
    db.commit()
    return render(request, "partials/cart_badge.html", user, db)


@router.post("/basket/items/{product_id}/delete", response_class=HTMLResponse)
def delete_basket_item(
    request: Request,
    product_id: int,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    """HTMX: remove a line and return the cart body."""
    require_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    cart = remove_cart_item(db, user, product_id)
    db.commit()
    return render(request, "partials/cart_body.html", user, db, cart=cart)


@router.get("/checkout", response_class=HTMLResponse)
def checkout_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    """Show a checkout confirmation for the current cart."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return render(request, "checkout.html", user, db, cart=cart_out(db, user))


@router.post("/checkout/place", response_class=HTMLResponse)
def checkout_submit(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
) -> Response:
    """Place the order from the HTML checkout form."""
    require_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        order = place_order(db, user)
    except HTTPException:
        return RedirectResponse("/basket", status_code=303)
    db.commit()
    return RedirectResponse(f"/history?placed={order.id}", status_code=303)


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
    placed: int | None = None,
) -> Response:
    """Show order history for the signed-in user."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.id.desc()).all()
    return render(request, "orders.html", user, db, orders=orders, placed=placed)
