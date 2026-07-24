FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml to install base requirements
COPY pyproject.toml .

# Install dependencies using pip
RUN pip install --no-cache-dir fastapi google-genai pillow pydantic python-multipart uvicorn python-dotenv

# Copy application files
COPY main.py .
COPY static static

# Expose port 8080
EXPOSE 8080

# Run FastAPI app, using PORT environment variable if set by Cloud Run, otherwise defaulting to 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
