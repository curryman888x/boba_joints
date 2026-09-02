from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from boba.config import DATABASE_URL

_ALEMBIC_INI = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
_MIGRATIONS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


@pytest.fixture(scope="session")
def test_db_url() -> str:
    base = make_url(DATABASE_URL)
    test_url = base.set(database=f"{base.database}_test")
    admin = sa.create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{test_url.database}" WITH (FORCE)'))
            c.execute(sa.text(f'CREATE DATABASE "{test_url.database}"'))
    except sa.exc.OperationalError as exc:
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__})")
    finally:
        admin.dispose()
    yield test_url.render_as_string(hide_password=False)
    admin = sa.create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{test_url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def alembic_config(test_db_url):
    from alembic.config import Config

    cfg = Config(_ALEMBIC_INI)
    cfg.set_main_option("script_location", _MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    return cfg


@pytest.fixture(scope="session")
def migrated_engine(alembic_config, test_db_url):
    from alembic import command

    command.upgrade(alembic_config, "head")
    eng = sa.create_engine(test_db_url, future=True)
    yield eng
    eng.dispose()
