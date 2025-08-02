# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Install uv
RUN pip install uv

# Copy the current directory contents into the container at /usr/src/app
COPY . .

# Install dependencies using uv
RUN uv sync --frozen

# Make port 80 available to the world outside this container
EXPOSE 80

# Run main.py when the container launches
CMD ["uv", "run", "python", "main.py"]