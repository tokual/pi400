#!/bin/bash
# Startup script for Telegram Video Bot
# This script is called by systemd on service start/restart
# It updates dependencies and then starts the bot

set -e

# Configuration
BOT_DIR="/opt/video-bot"
BOT_USER="_video-bot"
BOT_GROUP="_video-bot"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] Starting bot with dependency check...${NC}"

# Check if running as correct user
if [[ "$USER" != "$BOT_USER" ]] && [[ $EUID -ne 0 ]]; then
    echo -e "${RED}This script must be run as $BOT_USER or root${NC}"
    exit 1
fi

# Change to bot directory
cd "$BOT_DIR"

# Activate virtual environment
source venv/bin/activate

# Update dependencies (quiet mode, only install if needed)
echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] Checking and updating dependencies...${NC}"
if pip install -q --upgrade -r requirements.txt 2>/dev/null; then
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] Dependencies up to date${NC}"
else
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] Warning: Failed to update some dependencies${NC}"
    # Don't exit, continue with bot startup
fi

# Start the bot
echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] Starting bot...${NC}"
python3 -m src.bot
