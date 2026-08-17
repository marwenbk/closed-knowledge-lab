from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.config import get_settings
from app.db import engine_for_url
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    maintenance_url = os.environ.get("TOPMED_TEST_DATABASE_URL")
    if not maintenance_url:
        pytest.skip("TOPMED_TEST_DATABASE_URL is not configured")
    url = make_url(maintenance_url)
    database_name = f"topmed_test_{uuid4().hex}"
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    test_engine: Engine | None = None
    database_created = False
    previous_database_url = os.environ.get("DATABASE_URL")
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        database_created = True
        test_url = url.set(database=database_name).render_as_string(hide_password=False)
        os.environ["DATABASE_URL"] = test_url
        get_settings.cache_clear()
        command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
        test_engine = create_engine(test_url, pool_pre_ping=True)
        yield test_engine
    finally:
        if test_engine is not None:
            test_engine.dispose()
        engine_for_url.cache_clear()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()
        if database_created:
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()
