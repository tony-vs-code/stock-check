# Build the Docker image
docker build -t ghcr.io/tony-vs-code/stock-check:latest .

# Push the Docker image to GitHub Container Registry
docker push ghcr.io/tony-vs-code/stock-check:latest