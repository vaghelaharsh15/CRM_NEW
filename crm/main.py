from fastapi import FastAPI, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
if __package__:
    from .database import engine, SessionLocal
    from . import models  # Make sure this is imported BEFORE models.Base
    from . import schemas
    from .auth import hash_password, verify_password, create_token, SECRET_KEY, ALGORITHM
else:
    from database import engine, SessionLocal
    import models  # type: ignore
    import schemas  # type: ignore
    from auth import hash_password, verify_password, create_token, SECRET_KEY, ALGORITHM
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy import text, or_, inspect
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from fastapi.responses import RedirectResponse
from typing import Optional
import re
import os

models.Base.metadata.create_all(bind=engine)

app = FastAPI()
# CORS (VERY IMPORTANT)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalize_date_input(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None

    # Standard ISO format first
    try:
        parsed = datetime.fromisoformat(v)
        return parsed.date().isoformat()
    except ValueError:
        pass

    # Handle dd-mm-yyyy and dd/mm/yyyy legacy input
    m = re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})$", v)
    if m:
        dd, mm, yyyy = m.groups()
        try:
            parsed = datetime.strptime(f"{yyyy}-{mm}-{dd}", "%Y-%m-%d")
            return parsed.date().isoformat()
        except ValueError:
            return None

    return None


def _ensure_columns(table_name: str, required_columns: dict[str, str]):
    inspector = inspect(engine)
    if not inspector.has_table(table_name):
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    with engine.begin() as connection:
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                )


def ensure_customer_columns():
    _ensure_columns(
        "customers",
        {
            "contact_person": "VARCHAR(255)",
            "follow_up_date": "VARCHAR(255)",
        },
    )


def ensure_user_columns():
    _ensure_columns(
        "users",
        {
            "is_admin": "BOOLEAN",
            "created_at": "DATETIME",
        },
    )


ensure_user_columns()
ensure_customer_columns()


def ensure_admin_exists():
    # If you already created users but none are admins,
    # promote the oldest user so the admin panel is accessible.
    with SessionLocal() as db:
        admin = db.query(models.User).filter(models.User.is_admin == True).first()  # type: ignore[comparison-overlap]
        if admin:
            return
        first_user = db.query(models.User).order_by(models.User.id.asc()).first()
        if not first_user:
            return
        first_user.is_admin = True
        db.commit()


ensure_admin_exists()

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

def is_admin(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

# ---------------- AUTH ----------------

@app.post("/register")
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    try:
        hashed = hash_password(user.password)
    except Exception:
        raise HTTPException(status_code=500, detail="Password hashing failed. Check bcrypt installation.")

    # Make the first ever registered user an admin,
    # so the admin panel is not locked out.
    existing_admin = db.query(models.User).filter(models.User.is_admin == True).first()  # type: ignore[comparison-overlap]
    new_user = models.User(
        username=user.username,
        email=user.email,
        password=hashed,
        is_admin=False if existing_admin else True,
    )

    try:
        db.add(new_user)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Registration failed due to database error")
    return {"message": "Registered Successfully"}


@app.post("/login")
def login(user: schemas.Login, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if not db_user or not verify_password(user.password, db_user.password):
        raise HTTPException(status_code=400, detail="Invalid credentials")
    token = create_token({"user_id": db_user.id})
    return {"access_token": token, "token_type": "bearer", "is_admin": db_user.is_admin}

# ---------------- CUSTOMER CRUD ----------------

# Add Customer
@app.post("/customers")
def add_customer(
    customer: schemas.CustomerCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    payload = customer.model_dump() if hasattr(customer, "model_dump") else customer.dict()

    candidate_date = normalize_date_input(payload.get("follow_up_date"))
    payload["follow_up_date"] = candidate_date or datetime.utcnow().strftime("%Y-%m-%d")

    new_customer = models.Customer(**payload)
    db.add(new_customer)
    db.commit()
    return {"message": "Customer Added"}

# Get + Search + Filter
@app.get("/customers")
def get_customers(
    search: str = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Customer)

    if search and search.strip():
        term = search.strip()
        query = query.filter(
            or_(
                models.Customer.name.contains(term),
                models.Customer.phone.contains(term),
            )
        )

    return query.all()


def _interaction_to_dict(row: models.CustomerInteraction):
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "talked_with": row.talked_with,
        # "interaction_date": row.interaction_date,
        "interaction_date": str(row.interaction_date),
        "remark": row.remark,
        "created_at": row.created_at,
    }


@app.get("/customers/{customer_id}/interactions")
def list_customer_interactions(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    rows = (
        db.query(models.CustomerInteraction)
        .filter(models.CustomerInteraction.customer_id == customer_id)
        .order_by(models.CustomerInteraction.id.desc())
        .all()
    )
    return [_interaction_to_dict(r) for r in rows]


@app.post("/customers/{customer_id}/interactions")
def add_customer_interaction(
    customer_id: int,
    body: schemas.InteractionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    talked = (body.talked_with or "").strip()
    candidate_date = normalize_date_input(getattr(body, "interaction_date", None))
    idate = candidate_date or datetime.utcnow().strftime("%Y-%m-%d")
    remark = (body.remark or "").strip()

    # if not talked or not idate:
    #     raise HTTPException(status_code=400, detail="Talked with and date are required")
    if not talked:
       raise HTTPException(status_code=400, detail="Talked with is required")

    created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    row = models.CustomerInteraction(
        customer_id=customer_id,
        talked_with=talked,
        interaction_date=idate,
        remark=remark,
        created_at=created_at,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save remark")

    return {"message": "Saved", "interaction": _interaction_to_dict(row)}


def _apply_customer_update(db: Session, id: int, customer: schemas.CustomerUpdate):
    db_customer = db.query(models.Customer).filter(models.Customer.id == id).first()

    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if hasattr(customer, "model_dump"):
        update_data = customer.model_dump(exclude_unset=True)
    else:
        update_data = customer.dict(exclude_unset=True)

    if "follow_up_date" in update_data:
        normalized = normalize_date_input(update_data.get("follow_up_date"))
        update_data["follow_up_date"] = normalized or datetime.utcnow().strftime("%Y-%m-%d")

    for key, value in update_data.items():
        setattr(db_customer, key, value)

    try:
        db.commit()
        db.refresh(db_customer)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update customer")
    return {"message": "Customer Updated", "customer": db_customer}


# Update Customer
@app.put("/customers/{id}")
def update_customer(
    id: int,
    customer: schemas.CustomerUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _apply_customer_update(db, id, customer)


@app.post("/customers/{id}/update")
def update_customer_post(
    id: int,
    customer: schemas.CustomerUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Same as PUT /customers/{id}; some environments block PUT requests."""
    return _apply_customer_update(db, id, customer)

# Delete Customer
@app.delete("/customers/{id}")
def delete_customer(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _delete_customer(id, db)


@app.post("/customers/{id}/delete")
def delete_customer_post(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _delete_customer(id, db)

def _delete_customer(id: int, db: Session):
    customer = db.query(models.Customer).filter(models.Customer.id == id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        db.query(models.CustomerInteraction).filter(
            models.CustomerInteraction.customer_id == id
        ).delete(synchronize_session=False)
        db.delete(customer)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete customer")
    return {"message": "Deleted"}


# ---------------- HTML PAGES ----------------

@app.get("/")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={}
    )


@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={}
    )

# @app.get("/crm")
# def crm_page(request: Request):
#     return templates.TemplateResponse("crm.html", {"request": request})
@app.get("/crm")
def crm_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="crm.html",
        context={}
    )


@app.get("/crm/history")
def crm_history_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="crm_history.html",
        context={}
    )

# ---------------- ADMIN ----------------

@app.get("/admin/stats")
def get_admin_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    total_customers = db.query(models.Customer).count()
    total_users = db.query(models.User).count()
    total_interactions = db.query(models.CustomerInteraction).count()
    
    return {
        "total_customers": total_customers,
        "total_users": total_users,
        "total_interactions": total_interactions,
    }

# List All Users (Admin Only)
@app.get("/admin/users")
def get_all_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    users = db.query(models.User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
        }
        for u in users
    ]

# Make User Admin
@app.post("/admin/users/{user_id}/make-admin")
def make_user_admin(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(is_admin),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        user.is_admin = True
        db.commit()
        return {"message": "User promoted to admin"}
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update user")

# Delete User (Admin Only)
@app.delete("/admin/users/{user_id}")
def delete_user_admin(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(is_admin),
):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        db.delete(user)
        db.commit()
        return {"message": "User deleted"}
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete user")

# Admin Page
@app.get("/admin")
def admin_page(request: Request):
#     return templates.TemplateResponse("admin.html", {"request": request})
 return templates.TemplateResponse(
    request=request,
    name="admin.html",
    context={}
)

@app.get("/admin/me")
def admin_me(current_user: models.User = Depends(get_current_user)):
    return {"id": current_user.id, "is_admin": current_user.is_admin}

# List All Customers (Admin Only)
@app.get("/admin/customers")
def admin_list_customers(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return [
        {
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "phone": c.phone,
            "contact_person": c.contact_person,
            "follow_up_date": c.follow_up_date,
        }
        for c in db.query(models.Customer).order_by(models.Customer.id.desc()).all()
    ]

# List All Interactions (Admin Only)
@app.get("/admin/interactions")
def admin_list_interactions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return [
        {
            "id": i.id,
            "customer_id": i.customer_id,
            "talked_with": i.talked_with,
            "interaction_date": i.interaction_date,
            "remark": i.remark,
            "created_at": i.created_at,
        }
        for i in db.query(models.CustomerInteraction).order_by(models.CustomerInteraction.id.desc()).all()
    ]

# @app.get("/index")
# def dashboard_page():
#     return templates.TemplateResponse("index.html", {"request": request})