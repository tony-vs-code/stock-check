# UB Stock Bot

UB Stock Bot is a sophisticated Discord bot designed to monitor product stock availability and send real-time notifications when items come back in stock. Built with a clean, modular architecture for easy maintenance and extensibility.

## ✨ Features

- **Real-time Stock Monitoring**: Continuously checks product availability using custom CSS selectors
- **Discord Integration**: Interactive slash commands for easy product management
- **Persistent Storage**: Automatic saving and loading of monitored products
- **Smart Notifications**: Only notifies when stock status changes from out-of-stock to in-stock
- **Background Processing**: Efficient async processing with configurable intervals
- **Automatic Log Management**: Log rotation and cleanup to prevent disk space issues
- **Docker Support**: Easy deployment with containerization
- **Modular Architecture**: Clean, maintainable codebase split into focused modules
- **Enhanced User Feedback**: Detailed responses for all commands with clear confirmation messages
- **Clean List Display**: Product URLs shown without Discord previews to prevent clutter

## 🏗️ Architecture

The bot is built with a modern, modular architecture:

```
ubstock/
├── main.py                 # Entry point and application startup
├── config.py              # Configuration and environment variables
├── product_manager.py     # Product data management and persistence
├── stock_checker.py       # Stock checking logic and notifications
├── bot_commands.py        # Discord slash commands
├── log_manager.py         # Logging configuration and cleanup
├── discord_client.py      # Discord client with background tasks
├── utils.py               # Utility functions for HTTP requests
└── data/
    └── products.json      # Persistent product storage
```

### Core Components

- **ProductManager**: Handles CRUD operations for monitored products
- **StockChecker**: Manages stock checking logic and Discord notifications
- **UBStockClient**: Custom Discord client with integrated background tasks
- **BotCommands**: Slash command implementations
- **LogManager**: Centralized logging and file management

## 🚀 Discord Commands

The bot provides intuitive slash commands with detailed feedback:

- `/ping` - Test bot responsiveness
- `/add_product` - Add a new product to monitor
  - `name`: Product display name
  - `url`: Product page URL
  - `selector`: CSS selector for stock button (optional, defaults to `button[label='Add to Cart']`)
  - **Enhanced Response**: Shows detailed confirmation with product info and monitoring details
- `/remove_product` - Remove a product from monitoring
- `/list_products` - Display all monitored products (URLs shown without previews for clean display)

### Example Usage
```
# Basic usage (uses default selector: button[label='Add to Cart'])
/add_product name:UVC G5 PTZ url:https://store.ui.com/us/en/products/uvc-g5-ptz

# With custom CSS selector
/add_product name:Dream Machine url:https://store.ui.com/us/en/products/udm-pro selector:.add-to-cart-button

# Other common selectors
/add_product name:Custom Product url:https://example.com/product selector:button.buy-now
/add_product name:Another Item url:https://shop.com/item selector:[data-testid="add-to-cart"]
```

## 📋 Prerequisites

- **Python 3.11+**: Required for modern type hints and performance
- **Discord Bot Token**: Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
- **Discord Channel ID**: Channel where notifications will be sent
- **Docker** (optional): For containerized deployment
- **uv** (recommended): For faster Python package management

## 🛠️ Installation

### Method 1: Using uv (Recommended)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/tony-vs-code/stock-check.git
   cd stock-check
   ```

2. **Install dependencies**:
   ```bash
   uv sync
   ```

3. **Configure environment**:
   ```bash
   cp EXAMPLE.env .env
   # Edit .env with your Discord token and channel ID
   ```

4. **Run the bot**:
   ```bash
   uv run python main.py
   ```

### Method 2: Using pip

1. **Clone and navigate**:
   ```bash
   git clone https://github.com/tony-vs-code/stock-check.git
   cd stock-check
   ```

2. **Create virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure and run**:
   ```bash
   cp EXAMPLE.env .env
   # Edit .env with your configuration
   python main.py
   ```

## ⚙️ Configuration

### Required Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# Discord Configuration
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=123456789012345678

# Optional Configuration
CHECK_STOCK_INTERVAL=15    # Stock check interval in seconds (default: 15)
CLEAN_LOGS_INTERVAL=168    # Log cleanup interval in hours (default: 168 = 7 days)
DATA_DIR=data              # Directory for persistent data (default: data)
```

### Getting Discord Credentials

1. **Create a Discord Application**:
   - Visit [Discord Developer Portal](https://discord.com/developers/applications)
   - Click "New Application" and give it a name
   - Navigate to the "Bot" section
   - Click "Add Bot"

2. **Get Bot Token**:
   - In the Bot section, click "Copy" under Token
   - Add this to your `.env` file as `DISCORD_TOKEN`

3. **Get Channel ID**:
   - Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
   - Right-click on your target channel and select "Copy ID"
   - Add this to your `.env` file as `DISCORD_CHANNEL_ID`

4. **Invite Bot to Server**:
   - Go to OAuth2 → URL Generator
   - Select "bot" and "applications.commands" scopes
   - Select "Send Messages" and "Use Slash Commands" permissions
   - Use the generated URL to invite the bot

## 🐳 Docker Deployment

### Using Pre-built Image (Recommended)

1. **Pull the latest image**:
   ```bash
   docker pull ghcr.io/tony-vs-code/stock-check:latest
   ```

2. **Run with Docker**:
   ```bash
   docker run -d \
     --name ub-stock-bot \
     --env-file .env \
     -v $(pwd)/data:/usr/src/app/data \
     -v $(pwd)/logs:/usr/src/app/logs \
     --restart unless-stopped \
     ghcr.io/tony-vs-code/stock-check:latest
   ```

### Building from Source

1. **Build the image**:
   ```bash
   docker build -t ub-stock-bot .
   ```

2. **Run the container**:
   ```bash
   docker run -d \
     --name ub-stock-bot \
     --env-file .env \
     -v $(pwd)/data:/usr/src/app/data \
     -v $(pwd)/logs:/usr/src/app/logs \
     --restart unless-stopped \
     ub-stock-bot
   ```

### Docker Compose (Recommended for Production)

Create a `docker-compose.yml` file:

```yaml
version: '3.8'
services:
  ub-stock-bot:
    image: ghcr.io/tony-vs-code/stock-check:latest
    container_name: ub-stock-bot
    env_file:
      - .env
    volumes:
      - ./data:/usr/src/app/data
      - ./logs:/usr/src/app/logs
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

Run with:
```bash
docker-compose up -d
```

## 📊 Monitoring and Logs

### Log Management

- **Location**: Logs are stored in the `logs/` directory
- **Main Log**: `logs/ui_stock_bot.log`
- **Rotation**: Automatically rotates when files exceed 5MB (keeps 5 backup files)
- **Cleanup**: Old log files are automatically removed after 7 days
- **Format**: Timestamped entries with log levels (INFO, WARNING, ERROR, etc.)

### Viewing Logs

**Local Development**:
```bash
# View recent logs
tail -f logs/ui_stock_bot.log

# Search for errors
grep ERROR logs/ui_stock_bot.log
```

**Docker**:
```bash
# View container logs
docker logs ub-stock-bot

# Follow live logs
docker logs -f ub-stock-bot

# View bot application logs
docker exec ub-stock-bot cat logs/ui_stock_bot.log
```

### Health Monitoring

The bot logs important events:
- ✅ Successful stock checks
- ⚠️ Stock status changes
- ❌ Network or parsing errors
- 🔄 Product additions/removals
- 🚀 Bot startup and shutdown

## 🎯 Usage Examples

### Basic Product Monitoring

1. **Add a product (uses default selector)**:
   ```
   /add_product name:UVC G5 PTZ url:https://store.ui.com/us/en/products/uvc-g5-ptz
   ```
   *This uses the default CSS selector: `button[label='Add to Cart']`*
   
   **Bot Response:**
   ```
   ✅ Successfully added product to monitoring!
   
   Product: UVC G5 PTZ
   URL: https://store.ui.com/us/en/products/uvc-g5-ptz
   Selector: button[label='Add to Cart']
   
   The bot will now check this product every 15 seconds and notify when it comes 
   back in stock! 🚀
   ```

2. **List monitored products**:
   ```
   /list_products
   ```
   *URLs are displayed without Discord previews for clean viewing*

3. **Remove a product**:
   ```
   /remove_product name:UVC G5 PTZ
   ```

### Advanced CSS Selectors

For sites that don't use the default `button[label='Add to Cart']` selector, specify a custom one:

```
# Class selector
/add_product name:Custom Product url:https://example.com/product selector:.buy-now-btn

# Attribute selector  
/add_product name:Another Product url:https://shop.com/item selector:[data-testid="add-to-cart"]

# ID selector
/add_product name:Special Item url:https://store.com/special selector:#purchase-button

# Multiple classes
/add_product name:Premium Product url:https://shop.com/premium selector:button.btn.btn-primary
```

### Common CSS Selectors

Here are some common patterns you might encounter:

- `button[label='Add to Cart']` - Default UI.com style
- `.add-to-cart-button` - Class-based selector
- `#buy-now` - ID-based selector  
- `[data-testid="add-to-cart"]` - Data attribute selector
- `button.btn-primary` - Button with specific class
- `.product-actions button` - Button inside a container
```

### Notification Examples

When a product comes back in stock, you'll receive a notification like:

```
🚨 **UVC G5 PTZ is back in stock!** 🚨
**Link:** https://store.ui.com/us/en/products/uvc-g5-ptz
**Detected:** 2025-08-02 14:30:25
```

## 🔧 Development

### Recent Improvements

- ✅ **Modular Refactoring**: Complete code restructure into focused modules
- ✅ **Enhanced Command Responses**: Detailed feedback for all Discord commands  
- ✅ **Clean List Display**: URLs shown without Discord previews
- ✅ **Format-on-Save**: VS Code configuration for automatic code formatting
- ✅ **Type Safety**: Full type hint coverage across all modules
- ✅ **Error Handling**: Comprehensive error handling and logging improvements

### Project Structure

The codebase follows a clean, modular architecture:

- **Separation of Concerns**: Each module has a single responsibility
- **Type Safety**: Full type hint coverage
- **Error Handling**: Comprehensive error handling and logging
- **Async/Await**: Efficient asynchronous processing
- **Configuration Management**: Centralized config with environment variables
- **Enhanced UX**: Detailed command responses and clean list displays
- **Code Quality**: Ruff formatting and linting with format-on-save configuration

### Running Tests

```bash
# Install development dependencies
uv sync --dev

# Run tests (when available)
pytest

# Type checking
mypy .

# Linting and formatting
ruff check .
ruff format .

# VS Code auto-formatting is configured for format-on-save
```

### Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes and ensure tests pass
4. Submit a pull request with a clear description

## ❓ Troubleshooting

### Common Issues

**Bot not responding to commands**:
- Verify the bot has "Send Messages" and "Use Slash Commands" permissions
- Check that `DISCORD_TOKEN` is correct
- Ensure the bot is in the server and has access to the channel

**No stock notifications**:
- Verify `DISCORD_CHANNEL_ID` is correct
- Check logs for HTTP errors or parsing issues
- Test the CSS selector on the target website
- Ensure the product URL is accessible

**Docker container not starting**:
- Check that the `.env` file exists and has correct values
- Verify volume mounts point to existing directories
- Check container logs: `docker logs ub-stock-bot`

**High memory usage**:
- The bot uses minimal memory (~50MB) under normal operation
- Check for network issues causing request timeouts
- Review log files for error loops

### Getting Help

1. **Check Logs**: Always check the logs first for error messages
2. **GitHub Issues**: Report bugs or request features
3. **Discord Developer Portal**: For Discord-specific token/permission issues

## 📈 Performance

- **Memory Usage**: ~30-50MB typical usage
- **CPU Usage**: Minimal, only during stock checks
- **Network**: Lightweight HTTP requests every 15 seconds (configurable)
- **Storage**: Logs rotate automatically, minimal disk usage

## 🔒 Security

- **Environment Variables**: Sensitive data stored in `.env` file
- **No Data Collection**: Bot only stores product URLs and names locally
- **HTTPS Only**: All external requests use HTTPS
- **Minimal Permissions**: Bot requests only necessary Discord permissions

## 📜 License

This project is open source. See the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please see our contribution guidelines for more details:

1. **Bug Reports**: Use GitHub issues with detailed reproduction steps
2. **Feature Requests**: Describe the use case and proposed solution
3. **Pull Requests**: Include tests and documentation for new features
4. **Code Style**: Follow existing patterns and use type hints

## 🙏 Acknowledgments

- Built with [discord.py](https://discordpy.readthedocs.io/) for Discord integration
- Uses [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) for HTML parsing
- Containerized with Docker for easy deployment
- Package management with [uv](https://github.com/astral-sh/uv) for performance