"""
Shared pytest fixtures for the API server test suite.

Uses an isolated in-memory SQLite database (never touches the real
ai_center.db file) bound to the project's actual SQLAlchemy models, so
tests exercise the real schema/ORM behavior without risking any
production data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models  # noqa: F401 — ensures all model classes are registered on Base


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
