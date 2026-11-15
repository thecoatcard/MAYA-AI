FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

WORKDIR /opt/s1
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.17 \
    && uv sync --frozen --no-dev --no-install-project --no-managed-python

COPY __init__.py arq_worker.py driver.py metrics.py orchestrator.py runtime.py /opt/s1/s1/
ENV PATH="/opt/s1/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["python", "-m", "s1.driver"]
