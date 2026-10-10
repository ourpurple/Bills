from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .core import Settings


def make_engine(settings: Settings):
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 10}}
    if settings.database_url == "sqlite:///:memory:":
        kwargs["poolclass"] = StaticPool
    engine = create_engine(settings.database_url, **kwargs)

    @event.listens_for(engine, "connect")
    def sqlite_settings(connection, _):
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
