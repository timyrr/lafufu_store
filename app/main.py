from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import Base, SessionLocal, engine, get_db
from app.models import Product, User, Permission
from app.security import hash_password, verify_password
from app.utils import delete_image, ensure_uploads_dir, save_image

import os

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent

FLAG = os.getenv("FLAG")
FLAG_SENDED = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_uploads_dir()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.middleware("http")
async def load_current_user(request: Request, call_next):
    request.state.user = None

    user_id = request.session.get("user_id") if "session" in request.scope else None
    if user_id is not None:
        db = SessionLocal()
        try:
            request.state.user = db.get(User, user_id)
        finally:
            db.close()

    response = await call_next(request)
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    same_site="lax",
)


def render(request: Request, template_name: str, **context):
    context.update(
        {
            "request": request,
            "current_user": getattr(request.state, "user", None),
        }
    )
    return templates.TemplateResponse(request, template_name, context)



def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)



def require_authentication(request: Request) -> User | None:
    return getattr(request.state, "user", None)



def require_product_owner(request: Request, product: Product) -> User | None:
    current_user = require_authentication(request)
    if current_user is None:
        return None
    if product.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Изменять товар может только владелец")
    return current_user


def grant_temporary_session_permission(request: Request, action: str, db: Session = Depends(get_db)) -> None:
    if not has_temporary_session_permission(request,action,db):
        db.add(Permission(cookie=request.cookies.get('marketplace_session'),actions=action))
        db.commit()


def has_temporary_session_permission(request: Request, action: str, db: Session = Depends(get_db)) -> bool:
    permission = db.execute(
        select(Permission)
        .where(Permission.cookie == request.cookies.get("marketplace_session"), Permission.actions == action)
    ).all()
    return len(permission) > 0


def revoke_temporary_session_permission(request: Request, action: str, db: Session = Depends(get_db)) -> None:
    if has_temporary_session_permission(request,action,db):
        db.execute(delete(Permission)
                       .where(Permission.actions == action, Permission.cookie==request.cookies.get('marketplace_session')))
        db.commit()


def authorize_and_grant_session_permission(request: Request, product: Product, action: str, db: Depends(get_db)) -> User | None:
    current_user = require_product_owner(request, product)
    if current_user is None:
        return None

    grant_temporary_session_permission(request, action, db)
    if not has_temporary_session_permission(request, action, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Не удалось выдать временное разрешение на действие")

    return current_user


@app.get("/", response_class=HTMLResponse)
def read_home(request: Request, db: Session = Depends(get_db), q: str = ""):
    stmt = select(Product).options(selectinload(Product.owner)).order_by(Product.id.desc())
    if q.strip():
        stmt = stmt.where(Product.title.ilike(f"%{q.strip()}%"))

    products = db.execute(stmt).scalars().all()
    return render(request, "index.html", products=products, query=q)


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    current_user = require_authentication(request)
    if current_user is None:
        return redirect("/login")

    profile_user = db.get(User, current_user.id)
    user_products = db.execute(
        select(Product)
        .options(selectinload(Product.owner))
        .where(Product.owner_id == current_user.id)
        .order_by(Product.id.desc())
    ).scalars().all()

    return render(
        request,
        "profile.html",
        profile_user=profile_user,
        user_products=user_products,
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if require_authentication(request):
        return redirect("/profile")
    return render(request, "register.html", error=None)


@app.post("/register", response_class=HTMLResponse)
def register_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(...),
    contact_info: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if require_authentication(request):
        return redirect("/profile")

    username = username.strip().lower()
    display_name = display_name.strip()
    contact_info = contact_info.strip()

    if len(username) < 3:
        return render(request, "register.html", error="Логин должен содержать минимум 3 символа.")
    if len(password) < 6:
        return render(request, "register.html", error="Пароль должен содержать минимум 6 символов.")
    if password != password_confirm:
        return render(request, "register.html", error="Пароли не совпадают.")

    existing_user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if existing_user:
        return render(request, "register.html", error="Пользователь с таким логином уже существует.")

    user = User(
        username=username,
        display_name=display_name,
        contact_info=contact_info,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    return redirect("/profile")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if require_authentication(request):
        return redirect("/profile")
    return render(request, "login.html", error=None)


@app.post("/login", response_class=HTMLResponse)
def login_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if require_authentication(request):
        return redirect("/profile")

    user = db.execute(select(User).where(User.username == username.strip().lower())).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return render(request, "login.html", error="Неверный логин или пароль.")

    request.session["user_id"] = user.id
    return redirect("/profile")


@app.post("/logout")
def logout_user(request: Request):
    request.session.clear()
    return redirect("/")


@app.get("/products/{product_id}", response_class=HTMLResponse)
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.execute(
        select(Product).options(selectinload(Product.owner)).where(Product.id == product_id)
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    return render(request, "product_detail.html", product=product)


@app.get("/contact/{product_id}", response_class=HTMLResponse)
def contact_seller(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.execute(
        select(Product).options(selectinload(Product.owner)).where(Product.id == product_id)
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    return render(request, "contact.html", product=product)


@app.post("/products")
def create_product(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    image: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    current_user = require_authentication(request)
    if current_user is None:
        return redirect("/login")

    image_filename = save_image(image)
    product = Product(
        title=title.strip(),
        description=description.strip(),
        price=price,
        image_filename=image_filename,
        owner_id=current_user.id,
    )
    db.add(product)
    db.commit()

    return redirect("/profile")


@app.get("/products/{product_id}/edit", response_class=HTMLResponse)
def edit_product_page(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.execute(
        select(Product).options(selectinload(Product.owner)).where(Product.id == product_id)
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    current_user = require_product_owner(request, product)
    if current_user is None:
        return redirect("/login")

    return render(request, "edit_product.html", product=product)


@app.post("/products/{product_id}/edit")
def edit_product(
    product_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    price: float = Form(...),
    image: UploadFile | None = File(default=None),
    remove_image: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    product = db.execute(
        select(Product).options(selectinload(Product.owner)).where(Product.id == product_id)
    ).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    if not has_temporary_session_permission(request, "update_products", db):
        current_user = authorize_and_grant_session_permission(request, product, "update_products", db)
        if current_user is None:
            return redirect("/login")

    try:
        if not has_temporary_session_permission(request, "update_products", db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет разрешения на обновление товара")

        replacement_image = save_image(image) if image and image.filename else None
        if replacement_image:
            old_image = product.image_filename
            product.image_filename = replacement_image
            delete_image(old_image)
        elif remove_image:
            delete_image(product.image_filename)
            product.image_filename = None

        product.title = title.strip()
        product.description = description.strip()
        product.price = price
        db.commit()
    finally:
        revoke_temporary_session_permission(request, "update_products", db)

    return redirect("/profile")


@app.post("/products/{product_id}/delete")
def delete_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    global FLAG_SENDED
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    if not has_temporary_session_permission(request, "delete_products", db):
        current_user = authorize_and_grant_session_permission(request, product, "delete_products", db)
        if current_user is None:
            return redirect("/login")

    try:
        if not has_temporary_session_permission(request, "delete_products", db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет разрешения на удаление товара")

        delete_image(product.image_filename)
        db.delete(product)
        db.commit()
        check_flag = db.execute(select(Product).where(Product.id == 6)).all()
        if len(check_flag) == 0 and not (FLAG_SENDED):
            FLAG_SENDED = True
            return HTMLResponse(FLAG, status_code=status.HTTP_200_OK, headers={"Content-Type": "text/html"})
    finally:
        revoke_temporary_session_permission(request, "delete_products", db)

    return redirect("/profile")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
