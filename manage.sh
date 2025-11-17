#!/bin/bash
# Management script for Telegram Video Bot

# Configuration
SERVICE_NAME="video-bot.service"
BOT_DIR="/opt/video-bot"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Show help
show_help() {
    echo "Telegram Video Bot Manager"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start      - Start the bot service"
    echo "  stop       - Stop the bot service"
    echo "  restart    - Restart the bot service"
    echo "  status     - Show service status"
    echo "  logs       - View service logs (live)"
    echo "  update     - Update bot from git and restart"
    echo "  uninstall  - Uninstall bot completely"
    echo "  help       - Show this help message"
    echo ""
}

# Start service
cmd_start() {
    echo -e "${YELLOW}Starting bot...${NC}"
    
    # Auto-update yt-dlp to latest version
    echo -e "${YELLOW}Updating yt-dlp to latest version...${NC}"
    sudo "$BOT_DIR/venv/bin/pip" install --upgrade yt-dlp
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ yt-dlp updated${NC}"
    else
        echo -e "${YELLOW}⚠ Warning: yt-dlp update had issues, continuing anyway${NC}"
    fi
    
    sudo systemctl start "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Bot started${NC}"
    else
        echo -e "${RED}✗ Failed to start bot${NC}"
        exit 1
    fi
}

# Stop service
cmd_stop() {
    echo -e "${YELLOW}Stopping bot...${NC}"
    sudo systemctl stop "$SERVICE_NAME"
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Bot stopped${NC}"
    else
        echo -e "${RED}✗ Failed to stop bot${NC}"
        exit 1
    fi
}

# Restart service
cmd_restart() {
    echo -e "${YELLOW}Restarting bot...${NC}"
    
    # Auto-update yt-dlp to latest version
    echo -e "${YELLOW}Updating yt-dlp to latest version...${NC}"
    sudo "$BOT_DIR/venv/bin/pip" install --upgrade yt-dlp
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ yt-dlp updated${NC}"
    else
        echo -e "${YELLOW}⚠ Warning: yt-dlp update had issues, continuing anyway${NC}"
    fi
    
    sudo systemctl restart "$SERVICE_NAME"
    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}✓ Bot restarted${NC}"
    else
        echo -e "${RED}✗ Failed to restart bot${NC}"
        exit 1
    fi
}

# Show status
cmd_status() {
    echo -e "${YELLOW}Bot Status:${NC}"
    sudo systemctl status "$SERVICE_NAME" --no-pager
}

# View logs
cmd_logs() {
    echo -e "${YELLOW}Viewing bot logs (Press Ctrl+C to exit):${NC}"
    sudo journalctl -u "$SERVICE_NAME" -f --output short-iso
}

# Update bot
cmd_update() {
    echo -e "${YELLOW}Updating bot...${NC}"
    
    # Stop service
    echo "Stopping service..."
    sudo systemctl stop "$SERVICE_NAME"
    
    # Fix git ownership issue
    echo "Configuring git safe directory..."
    sudo git config --global --add safe.directory "$BOT_DIR"
    
    # Update from git
    echo "Pulling latest changes from git..."
    cd "$BOT_DIR"
    sudo git pull origin main
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}✗ Git pull failed${NC}"
        echo "Restarting service..."
        sudo systemctl start "$SERVICE_NAME"
        exit 1
    fi
    
    # Update Python dependencies (required for any requirements.txt changes)
    echo "Updating Python dependencies..."
    sudo "$BOT_DIR/venv/bin/pip" install --upgrade -r "$BOT_DIR/requirements.txt"
    if [ $? -ne 0 ]; then
        echo -e "${YELLOW}⚠ Warning: Failed to update dependencies, continuing anyway${NC}"
    fi
    
    # Restart service
    echo "Restarting service..."
    sudo systemctl start "$SERVICE_NAME"
    sleep 3
    
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        echo -e "${GREEN}✓ Bot updated and restarted${NC}"
    else
        echo -e "${RED}✗ Failed to restart bot after update${NC}"
        exit 1
    fi
}

# Uninstall bot
cmd_uninstall() {
    echo -e "${RED}⚠ WARNING: This will completely remove the bot!${NC}"
    read -p "Continue? (yes/no): " confirm
    
    if [ "$confirm" != "yes" ]; then
        echo "Cancelled."
        return
    fi
    
    echo -e "${YELLOW}Uninstalling bot...${NC}"
    
    # Stop service
    echo "Stopping service..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    
    # Disable service
    echo "Disabling service..."
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    
    # Remove service file
    echo "Removing service file..."
    sudo rm -f "/etc/systemd/system/$SERVICE_NAME"
    
    # Reload systemd
    echo "Reloading systemd..."
    sudo systemctl daemon-reload
    
    # Remove bot directory
    echo "Removing bot directory..."
    sudo rm -rf "$BOT_DIR"
    
    echo -e "${GREEN}✓ Bot uninstalled${NC}"
}

# Main
case "${1:-help}" in
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs
        ;;
    update)
        cmd_update
        ;;
    uninstall)
        cmd_uninstall
        ;;
    help)
        show_help
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        show_help
        exit 1
        ;;
esac
