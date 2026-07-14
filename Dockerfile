FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable data dir (Hugging Face Spaces runs containers as a non-root user)
RUN mkdir -p /app/data && chmod -R 777 /app/data /app/profiles

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn batchpilot.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
