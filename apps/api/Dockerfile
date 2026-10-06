FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY start-api.sh ./start-api.sh
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["sh", "/app/start-api.sh"]
