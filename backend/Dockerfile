FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 bills \
    && useradd --uid 10001 --gid bills --no-create-home bills \
    && mkdir -p /srv/data /srv/data/backups \
    && chown -R bills:bills /srv/data
COPY --chown=bills:bills backend/app ./app
COPY --chown=bills:bills backend/migrations ./migrations
COPY --chown=bills:bills backend/alembic.ini ./alembic.ini
COPY deploy/entrypoint.sh /usr/local/bin/bills-entrypoint
RUN chmod 755 /usr/local/bin/bills-entrypoint
USER bills
EXPOSE 8000
ENTRYPOINT ["bills-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
