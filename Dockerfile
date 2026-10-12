FROM python:3.12-slim

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
# Dev dependencies are included so the test suite can run inside the image.
RUN pip install --no-cache-dir -e ".[dev]"
COPY scripts ./scripts
COPY tests ./tests

ENV DATABASE_PATH=/data/runs.db
EXPOSE 8000

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
