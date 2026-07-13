FROM python:3.11-slim

# Install iproute2 (for tc) and conntrack-tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iproute2 \
        conntrack \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Copy application
COPY src/ src/
COPY tests/ tests/

# Expose API port
EXPOSE 8443

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8443"]
