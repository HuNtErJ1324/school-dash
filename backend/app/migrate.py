"""Schema upgrades for an existing database.

New tables come from create_all. New columns on existing tables are added here, which is
enough as long as every schema change is additive and each new column is nullable or has
a default. A change that needs more than that (renames, type changes) should bring in Alembic.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

from .db import Base

log = logging.getLogger(__name__)


def upgrade(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
                if not column.nullable:
                    default = column.default.arg if column.default is not None else None
                    if default is None or callable(default):
                        raise RuntimeError(f"can't add required column {table.name}.{column.name} without a plain default")
                    ddl += f" NOT NULL DEFAULT {_sql_literal(default)}"
                conn.execute(text(ddl))
                log.info("added column %s.%s", table.name, column.name)
            for index in table.indexes:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {index.name} ON {table.name} "
                    f"({', '.join(c.name for c in index.columns)})"
                ))  # fmt: skip


def _sql_literal(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
