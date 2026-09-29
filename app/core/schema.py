from sqlalchemy import Engine, inspect, text

from app.core.errors import ConfigurationError

SCHEMA_REVISION = "0009"


def check_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        if "alembic_version" not in inspect(connection).get_table_names():
            raise ConfigurationError(
                "Database is not initialized. Run: python -m alembic upgrade head"
            )
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != SCHEMA_REVISION:
            raise ConfigurationError(
                "Database schema is outdated. Run: python -m alembic upgrade head"
            )
