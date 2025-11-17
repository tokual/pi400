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
    echo -e "${RED}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                    ⚠  WARNING  ⚠                          ║${NC}"
    echo -e "${RED}║                                                            ║${NC}"
    echo -e "${RED}║  This will COMPLETELY REMOVE the bot and ALL data:        ║${NC}"
    echo -e "${RED}║                                                            ║${NC}"
    echo -e "${RED}║  • Bot service and daemon                                  ║${NC}"
    echo -e "${RED}║  • Bot directory (/opt/video-bot)                          ║${NC}"
    echo -e "${RED}║  • Database and user settings                              ║${NC}"
    echo -e "${RED}║  • System user (_video-bot)                                ║${NC}"
    echo -e "${RED}║  • Python virtual environment                              ║${NC}"
    echo -e "${RED}║  • Temporary files                                         ║${NC}"
    echo -e "${RED}║  • System dependencies (optional)                          ║${NC}"
    echo -e "${RED}║                                                            ║${NC}"
    echo -e "${RED}║  This action CANNOT be undone!                             ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    read -p "Type 'DELETE EVERYTHING' to confirm uninstall: " confirm
    
    if [ "$confirm" != "DELETE EVERYTHING" ]; then
        echo -e "${YELLOW}Cancelled.${NC}"
        return
    fi
    
    echo ""
    echo -e "${YELLOW}Uninstalling bot...${NC}"
    echo ""
    
    # Stop service
    echo -e "${YELLOW}[1/11] Stopping service...${NC}"
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    echo -e "${GREEN}✓ Service stopped${NC}"
    
    # Disable service
    echo -e "${YELLOW}[2/11] Disabling service...${NC}"
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    echo -e "${GREEN}✓ Service disabled${NC}"
    
    # Remove service file
    echo -e "${YELLOW}[3/11] Removing systemd service file...${NC}"
    sudo rm -f "/etc/systemd/system/$SERVICE_NAME"
    echo -e "${GREEN}✓ Service file removed${NC}"
    
    # Reload systemd
    echo -e "${YELLOW}[4/11] Reloading systemd daemon...${NC}"
    sudo systemctl daemon-reload
    sudo systemctl reset-failed 2>/dev/null || true
    echo -e "${GREEN}✓ Systemd reloaded${NC}"
    
    # Clean up temporary files in /tmp
    echo -e "${YELLOW}[5/11] Cleaning up temporary files...${NC}"
    sudo rm -rf /tmp/video-bot-* 2>/dev/null || true
    sudo rm -rf /tmp/yt-dlp-* 2>/dev/null || true
    sudo rm -rf /tmp/*.mp4 2>/dev/null || true
    sudo rm -rf /tmp/*.mkv 2>/dev/null || true
    echo -e "${GREEN}✓ Temporary files cleaned${NC}"
    
    # Remove bot directory
    echo -e "${YELLOW}[6/11] Removing bot directory and all data...${NC}"
    if [ -d "$BOT_DIR" ]; then
        # Clean up Python cache files first
        sudo find "$BOT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        sudo find "$BOT_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
        sudo find "$BOT_DIR" -type f -name "*.pyo" -delete 2>/dev/null || true
        
        # Remove the entire directory
        sudo rm -rf "$BOT_DIR"
        echo -e "${GREEN}✓ Bot directory removed${NC}"
    else
        echo -e "${YELLOW}⚠ Bot directory not found (already removed?)${NC}"
    fi
    
    # Remove system user
    echo -e "${YELLOW}[7/11] Removing system user...${NC}"
    if id "_video-bot" &>/dev/null; then
        sudo userdel "_video-bot" 2>/dev/null || true
        echo -e "${GREEN}✓ System user removed${NC}"
    else
        echo -e "${YELLOW}⚠ System user not found (already removed?)${NC}"
    fi
    
    # Remove system group
    echo -e "${YELLOW}[8/11] Removing system group...${NC}"
    if getent group "_video-bot" &>/dev/null; then
        sudo groupdel "_video-bot" 2>/dev/null || true
        echo -e "${GREEN}✓ System group removed${NC}"
    else
        echo -e "${YELLOW}⚠ System group not found (already removed?)${NC}"
    fi
    
    # Clean up logs
    echo -e "${YELLOW}[9/11] Cleaning up service logs...${NC}"
    sudo journalctl --vacuum-time=1s --unit="$SERVICE_NAME" 2>/dev/null || true
    echo -e "${GREEN}✓ Logs cleaned${NC}"
    
    # Ask about system dependencies
    echo ""
    echo -e "${YELLOW}[10/11] Remove system dependencies?${NC}"
    echo "The following packages were installed for the bot:"
    echo "  • handbrake-cli (video transcoding)"
    echo "  • ffmpeg (video processing)"
    echo "  • python3-venv (Python virtual environments)"
    echo "  • python3-dev (Python development headers)"
    echo ""
    echo -e "${YELLOW}Note: These may be used by other applications.${NC}"
    read -p "Remove system dependencies? (yes/no): " remove_deps
    
    if [ "$remove_deps" = "yes" ]; then
        echo -e "${YELLOW}Removing system dependencies...${NC}"
        sudo apt-get remove -y handbrake-cli ffmpeg python3-venv python3-dev 2>/dev/null || true
        sudo apt-get autoremove -y 2>/dev/null || true
        echo -e "${GREEN}✓ System dependencies removed${NC}"
    else
        echo -e "${YELLOW}⚠ Skipping system dependencies removal${NC}"
    fi
    
    # Final cleanup
    echo -e "${YELLOW}[11/11] Final cleanup...${NC}"
    # Remove any remaining configuration or cache files
    sudo rm -rf ~/.cache/yt-dlp 2>/dev/null || true
    echo -e "${GREEN}✓ Cleanup complete${NC}"
    
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                 ✓ Uninstall Complete                       ║${NC}"
    echo -e "${GREEN}║                                                            ║${NC}"
    echo -e "${GREEN}║  The bot has been completely removed from your system.    ║${NC}"
    echo -e "${GREEN}║                                                            ║${NC}"
    echo -e "${GREEN}║  To reinstall:                                            ║${NC}"
    echo -e "${GREEN}║    1. Clone the repository again                          ║${NC}"
    echo -e "${GREEN}║    2. Run: sudo ./install.sh                              ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
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
