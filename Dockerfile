FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY gateway.py .
COPY pyproject.toml .

RUN uv pip install --system --no-cache fastmcp httpx uvicorn pyyaml

ENV HOST=0.0.0.0
ENV PORT=8000
ENV LOG_LEVEL=info

EXPOSE 8000

ENTRYPOINT ["python", "gateway.py"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
