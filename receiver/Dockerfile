FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 5002

ENV PRISM_RCV_HOST=0.0.0.0
ENV PRISM_RCV_PORT=5002

CMD ["prism-receiver"]
