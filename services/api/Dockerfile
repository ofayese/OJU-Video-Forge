FROM python:3.12-slim-bookworm
WORKDIR /opt/forge
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 forge
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
USER forge
EXPOSE 8000
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips",""]
