FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create output dir for logs
RUN mkdir -p out

EXPOSE 8000

CMD ["uvicorn", "bridge_server:app", "--host", "0.0.0.0", "--port", "8000"]
