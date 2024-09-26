# Authenticate with GitHub Container Registry
echo $GITHUB_TOKEN | docker login ghcr.io -u your-github-username --password-stdin

# Build the Docker image
docker build -t ghcr.io/tony-vs-code/stock-check:latest .

# Push the Docker image to GitHub Container Registry
docker push ghcr.io/tony-vs-code/stock-check:latest