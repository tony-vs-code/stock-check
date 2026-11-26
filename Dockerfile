# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Install curl for container healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

# Copy project files into the container
COPY . .

# Install dependencies using uv
RUN uv sync --frozen

# Create directories for persistent data (volumes will mount here)
RUN mkdir -p /usr/src/app/data /usr/src/app/logs

# Expose the web UI & API port
EXPOSE 8000

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Run main.py when the container launches
CMD ["uv", "run", "python", "main.py"]
