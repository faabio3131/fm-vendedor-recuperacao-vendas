FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
CMD ["uvicorn", "fm_seller.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
