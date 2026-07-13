FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 5001

ENV PRISM_GEN_HOST=0.0.0.0
ENV PRISM_GEN_PORT=5001

CMD ["prism-traffic-gen"]
