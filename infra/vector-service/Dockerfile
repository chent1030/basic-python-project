ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_REQUIRE_HASHES=false
WORKDIR /app
COPY requirements.txt .
RUN PIP_REQUIRE_HASHES=false pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" --timeout 600 --retries 10 -r requirements.txt
COPY main.py .
RUN mkdir -p /data
EXPOSE 8008
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8008"]
