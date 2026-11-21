# syntax=docker/dockerfile:1.7
# Two stages: build the wheel with the toolchain, run it on a slim image as a non-root user.

FROM python:3.12-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir --upgrade pip build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /wheels

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install /wheels/*.whl "azure-monitor-opentelemetry>=1.6" "opentelemetry-instrumentation-fastapi" "azure-identity>=1.17" \
 && rm -rf /wheels \
 && useradd --uid 10001 --create-home rahat
USER rahat
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# One process on purpose: the incident board lives in memory (see docs/ARCHITECTURE.md).
CMD ["uvicorn", "rahat.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
