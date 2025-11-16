#!/bin/bash
# Installation script for Telegram Video Download Bot

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
BOT_DIR="/opt/video-bot"
BOT_USER="_video-bot"
BOT_GROUP="_video-bot"
SYSTEMD_SERVICE="video-bot.service"

echo -e "${GREEN}=== Telegram Video Bot Installer ===${NC}"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}This script must be run with sudo${NC}"
    exit 1
fi

# Create bot directory
echo -e "${YELLOW}Creating bot directory...${NC}"
mkdir -p "$BOT_DIR"
echo -e "${GREEN}✓ Directory created${NC}"

# Create system user if not exists
if ! id "$BOT_USER" &>/dev/null; then
    echo -e "${YELLOW}Creating system user...${NC}"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER" 2>/dev/null || true
    echo -e "${GREEN}✓ System user created${NC}"
else
    echo -e "${GREEN}✓ System user already exists${NC}"
fi

# Install system dependencies
echo -e "${YELLOW}Installing system dependencies...${NC}"
apt-get update -qq
apt-get install -y -qq \
    python3-venv \
    python3-dev \
    git \
    handbrake-cli \
    ffmpeg \
    > /dev/null 2>&1
echo -e "${GREEN}✓ Dependencies installed${NC}"

# Clone or update repository (assuming we're in the project directory)
echo -e "${YELLOW}Setting up bot files...${NC}"
if [ ! -d "$BOT_DIR/.git" ]; then
    # Copy files from current directory
    cp -r . "$BOT_DIR/" 2>/dev/null || cp -r ./* "$BOT_DIR/"
    echo -e "${GREEN}✓ Bot files copied${NC}"
else
    echo -e "${GREEN}✓ Bot files already exist${NC}"
fi

# Create Python virtual environment
echo -e "${YELLOW}Creating Python virtual environment...${NC}"
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip setuptools wheel

# Install Python dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip install -q \
    aiogram==3.0.0 \
    aiosqlite==0.19.0 \
    yt-dlp==2024.10.22 \
    python-dotenv==1.0.0
echo -e "${GREEN}✓ Python dependencies installed${NC}"

# Create .env file if it doesn't exist
echo -e "${YELLOW}Configuring .env...${NC}"
if [ ! -f "$BOT_DIR/.env" ]; then
    cp "$BOT_DIR/sample.env" "$BOT_DIR/.env"
    
    # Prompt for bot token
    read -p "Enter your Telegram Bot Token: " BOT_TOKEN
    sed -i "s/BOT_TOKEN=.*/BOT_TOKEN=$BOT_TOKEN/" "$BOT_DIR/.env"
    
    echo -e "${GREEN}✓ .env file created${NC}"
else
    echo -e "${GREEN}✓ .env file already exists (preserved)${NC}"
fi

# Set .env permissions to 600 (readable only by bot user)
chmod 600 "$BOT_DIR/.env"
echo -e "${GREEN}✓ .env permissions set to 600${NC}"

# Initialize database
echo -e "${YELLOW}Initializing database...${NC}"
cd "$BOT_DIR"
source venv/bin/activate
python3 -c "
import asyncio
from src.database import Database

async def init_db():
    db = Database()
    await db.initialize()
    await db.close()

asyncio.run(init_db())
"
echo -e "${GREEN}✓ Database initialized${NC}"

# Create systemd service file
echo -e "${YELLOW}Creating systemd service...${NC}"
cat > "/etc/systemd/system/$SYSTEMD_SERVICE" << EOF
[Unit]
Description=Telegram Video Download Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$BOT_USER
Group=$BOT_GROUP
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/venv/bin/python3 -m src.bot
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo -e "${GREEN}✓ Systemd service created${NC}"

# Set ownership
echo -e "${YELLOW}Setting permissions...${NC}"
chown -R "$BOT_USER:$BOT_GROUP" "$BOT_DIR"
chown root:root "/etc/systemd/system/$SYSTEMD_SERVICE"
chmod 644 "/etc/systemd/system/$SYSTEMD_SERVICE"
echo -e "${GREEN}✓ Permissions set${NC}"

# Reload systemd and enable service
echo -e "${YELLOW}Enabling service...${NC}"
systemctl daemon-reload
systemctl enable "$SYSTEMD_SERVICE"
echo -e "${GREEN}✓ Service enabled${NC}"

# Start service
echo -e "${YELLOW}Starting service...${NC}"
systemctl start "$SYSTEMD_SERVICE"
sleep 2

# Check service status
if systemctl is-active --quiet "$SYSTEMD_SERVICE"; then
    echo -e "${GREEN}✓ Service started successfully${NC}"
else
    echo -e "${RED}✗ Service failed to start. Check logs with:${NC}"
    echo "  sudo journalctl -u video-bot -f"
    exit 1
fi

echo ""
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo ""
echo "📝 Next steps:"
echo "  • Check bot status: sudo ./manage.sh status"
echo "  • View logs: sudo ./manage.sh logs"
echo "  • Start bot: sudo ./manage.sh start"
echo "  • Stop bot: sudo ./manage.sh stop"
echo ""
echo "📖 Documentation: See README.md"
