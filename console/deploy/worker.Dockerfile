# Adds console code on top of the exact deployed StreamScale image; the measured
# consumer source and dependencies are reused unchanged.
ARG BASE
FROM ${BASE}
ARG CONSOLE_SOURCE_SHA256
LABEL org.opencontainers.image.title="StreamScale console worker" \
      io.streamscale.console.source=${CONSOLE_SOURCE_SHA256}
COPY --chown=10001:10001 streamscale_console_worker /console/streamscale_console_worker
ENV PYTHONPATH=/console
ENTRYPOINT ["/app/.venv/bin/python", "-m"]
CMD ["streamscale_console_worker.agent"]
