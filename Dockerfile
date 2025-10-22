# Use official Python slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY main.py requirements.txt ./

# Create a virtual environment
RUN python -m venv /opt/venv

# Activate venv and install dependencies
RUN /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Make sure venv binaries are on PATH
ENV PATH="/opt/venv/bin:$PATH"

# Expose port
EXPOSE 8000

# Run the app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
