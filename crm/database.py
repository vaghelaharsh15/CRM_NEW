import os
from sqlalchemy import create_engine # type: ignore
from sqlalchemy.orm import sessionmaker, declarative_base # type: ignore

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://crm_user:crm_pass@127.0.0.1:3306/crm",
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()