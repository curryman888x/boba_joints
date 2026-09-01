"""Alembic environment.

- DB URL comes from boba.config.DATABASE_URL (which reads .env)
- target_metadata is boba.models.Base.metadata.
- GeoAlchemy2's alembic_helpers keep autogenerate sane around PostGIS
  (spatial indexes, geometry type rendering, ignoring spatial_ref_sys).
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool

from boba.config import DATABASE_URL
from boba.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    """Keep autogenerate scoped to the public schema (ignore postgis/tiger/topology)."""
    if type_ == "schema":
        return name in (None, "public")
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=alembic_helpers.include_object,
        include_name=include_name,
        render_item=alembic_helpers.render_item,
        process_revision_directives=alembic_helpers.writer,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=alembic_helpers.include_object,
            include_name=include_name,
            render_item=alembic_helpers.render_item,
            process_revision_directives=alembic_helpers.writer,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
