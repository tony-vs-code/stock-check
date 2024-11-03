# Stock-Check

Stock-Check is a stock manager system designed to help watch for when something comes back into stock.

## Features

- Real-time monitoring of stock status
- Automated messaging via Discord

## Prerequisites

- Python 3.9 or higher
- Node.js (if using the Node.js version)
- Docker (optional, for containerized deployment)

## Installation

### Clone the Repository
1. Clone the repository:
    ```bash
    git clone https://github.com/tony-vs-code/stock-check.git
    ```
2. Navigate to the project directory:
    ```bash
    cd stock-check
    ```
3. Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
4. Set up the environment variables:
    ```bash
    cp .env.example .env
    ```
> [!NOTE]  
> Make sure you provide your own values!
    
5. Run the application:
    ```bash
    python main.py
    ```

## Environment Variables

- `DISCORD_TOKEN`: Your Discord bot token.
- `DISCORD_CHANNEL_ID`: The ID of the Discord channel where messages will be sent.

## Docker Instructions

[Latest Tag](https://github.com/tony-vs-code/stock-check/pkgs/container/stock-check/299502125?tag=latest)

1. Build the Docker image:
    ```bash
    docker build -t stock-check .
    ```
2. Run the Docker container:
    ```bash
    docker run -d -p 80:80 --env-file .env stock-check
    ```

## Logging

Logs are stored in the `logs` directory. The main log file is `logs/ui_stock_bot.log`. The log file is reset each time the application starts. Old log files are automatically cleaned up every week.

## Usage

After running the application, it will automatically monitor the stock status and send messages to the specified Discord channel when an item comes back into stock.

## FAQ

**Q: How do I get a Discord bot token?**
A: You can create a new bot and get a token from the Discord Developer Portal.

**Q: What should I do if the bot is not sending messages?**
A: Ensure that the bot has the correct permissions and that the environment variables are set correctly.

## Contributing

We welcome contributions! Please see our [CONTRIBUTING.md](CONTRIBUTING.md) for more details.