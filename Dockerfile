FROM ghcr.io/astral-sh/uv:0.12.20@sha256:100047e74f30778ab704942321a09750d6158739573ff58bf3924085cc6cd2d8 AS uv
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tools/workload ./tools/workload
COPY schemas ./schemas
COPY db/migrations ./db/migrations
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG GIT_COMMIT
ARG GIT_DIRTY
ARG SOURCE_SHA256
ARG IMAGE_REF
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    STREAMSCALE_GIT_COMMIT=${GIT_COMMIT} STREAMSCALE_GIT_DIRTY=${GIT_DIRTY} \
    STREAMSCALE_SOURCE_SHA256=${SOURCE_SHA256} STREAMSCALE_IMAGE=${IMAGE_REF}
LABEL org.opencontainers.image.title="StreamScale" org.opencontainers.image.revision=${GIT_COMMIT}
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
RUN mkdir /runs && chown 10001:10001 /runs
USER 10001:10001
EXPOSE 9100
ENTRYPOINT ["/app/.venv/bin/streamscale"]
CMD ["--help"]
