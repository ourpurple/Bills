from alembic import context

from app.core import Settings
from app.database import make_engine
from app.models import Base

settings = Settings()
settings.validate_runtime()
config = context.config

if context.is_offline_mode():
    context.configure(
        url=settings.database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(settings)
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(
            connection=connection, target_metadata=Base.metadata, render_as_batch=True, compare_type=True
        )
        context.run_migrations()
        connection.commit()
    engine.dispose()
