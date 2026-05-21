FROM python:3.12-slim

WORKDIR /app

# httpx is the only runtime dep for the eval harness itself.
RUN pip install --no-cache-dir "httpx>=0.27,<1.0"

COPY eval /app/eval

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default: hit the in-network agent on its container port (8001), not the
# host-mapped 8002. Override CRITCOM_EVAL_BASE_URL for the live VM run.
ENV CRITCOM_EVAL_BASE_URL=http://critcom-agent:8001

# Reports are written under /app/eval/reports, mounted from the host volume.
ENTRYPOINT ["sh", "-c", "python -m eval --base-url ${CRITCOM_EVAL_BASE_URL} \"$@\"", "--"]
CMD []
