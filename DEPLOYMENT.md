#!/bin/bash
# Quick deployment reference

cat << 'EOF'

╔════════════════════════════════════════════════════════════════════════════╗
║                     QUICK DEPLOYMENT REFERENCE                            ║
╚════════════════════════════════════════════════════════════════════════════╝

🚀 STEP 1: PREPARE
═══════════════════════════════════════════════════════════════════════════

1. Get Telegram Bot Token: https://t.me/botfather
   - Talk to @BotFather
   - Create new bot (/newbot)
   - Copy the token

2. Get Your User ID: https://t.me/userinfobot
   - Message @userinfobot
   - Note your ID (you'll use default 23682616 or change it)

3. Get Raspberry Pi ready:
   - SSH access enabled
   - Debian-based OS (Raspberry Pi OS)
   - Internet connection

📋 STEP 2: CLONE PROJECT ON PI
═══════════════════════════════════════════════════════════════════════════

On Raspberry Pi:
    $ ssh pi@192.168.1.100
    $ git clone https://github.com/tokual/pi400.git ~/pi400

Replace 192.168.1.100 with your Raspberry Pi's IP address.

🔧 STEP 3: INSTALL ON RASPBERRY PI
═══════════════════════════════════════════════════════════════════════════

On Raspberry Pi:
    $ cd ~/pi400
    $ sudo ./install.sh

When prompted:
    Enter your Telegram Bot Token: [paste your token]

Wait for installation to complete (3-5 minutes).

✅ STEP 4: VERIFY INSTALLATION
═══════════════════════════════════════════════════════════════════════════

Check if bot is running:
    $ sudo ./manage.sh status

Check logs:
    $ sudo ./manage.sh logs

Expected output:
    ✓ Service is active and running
    ✓ No errors in logs

📱 STEP 5: TEST THE BOT
═══════════════════════════════════════════════════════════════════════════

1. Open Telegram
2. Search for your bot (name you chose in @BotFather)
3. Send: /start
4. You should see the main menu with buttons
5. Try downloading a short video from YouTube

🎛️ MANAGEMENT COMMANDS
═══════════════════════════════════════════════════════════════════════════

Start bot:
    $ sudo ./manage.sh start

Stop bot:
    $ sudo ./manage.sh stop

Restart bot:
    $ sudo ./manage.sh restart

Check status:
    $ sudo ./manage.sh status

View live logs (Ctrl+C to exit):
    $ sudo ./manage.sh logs

Update bot from git:
    $ sudo ./manage.sh update

Uninstall completely:
    $ sudo ./manage.sh uninstall

🔧 CONFIGURATION
═══════════════════════════════════════════════════════════════════════════

Edit settings:
    $ sudo nano /opt/video-bot/.env

Available settings:
    BOT_TOKEN=your_token_here
    ALLOWED_USER_ID=23682616
    LOG_LEVEL=INFO
    MAX_FILE_SIZE_MB=50
    HANDBRAKE_PRESET=Fast Mobile 720p30

After editing, restart:
    $ sudo ./manage.sh restart

🚨 TROUBLESHOOTING
═══════════════════════════════════════════════════════════════════════════

Bot not starting?
    $ sudo ./manage.sh logs
    Check error messages

Bot crashes frequently?
    $ sudo systemctl status video-bot
    $ sudo journalctl -u video-bot -n 50

Encoding is slow?
    Use "Fast" preset in Telegram settings

File upload fails?
    Files must be < 50MB
    Try shorter videos or "Fast" preset

📊 SYSTEM INFORMATION
═══════════════════════════════════════════════════════════════════════════

Bot location:     /opt/video-bot/
Database file:    /opt/video-bot/bot.db
Config file:      /opt/video-bot/.env
Log file:         /opt/video-bot/bot.log
Service name:     video-bot.service
Service user:     _video-bot

View service status:
    $ sudo systemctl status video-bot

View journal logs:
    $ sudo journalctl -u video-bot -f

🎯 EXPECTED BEHAVIOR
═══════════════════════════════════════════════════════════════════════════

After installation:
    ✓ Service starts automatically
    ✓ Bot responds to /start
    ✓ Only your user ID can use it
    ✓ Logs are saved automatically
    ✓ Database created automatically
    ✓ Service auto-restarts on failure
    ✓ Temp files auto-cleaned up

🔒 SECURITY NOTES
═══════════════════════════════════════════════════════════════════════════

✓ Only whitelisted user can access
✓ .env file is readable only by bot (600 perms)
✓ Bot token is never logged
✓ Downloads happen in isolated temp directory
✓ Temp files auto-deleted after use
✓ Logs are auto-rotated (48h retention)

📞 GET HELP
═══════════════════════════════════════════════════════════════════════════

Check logs:
    $ sudo ./manage.sh logs

Read documentation:
    See /opt/video-bot/README.md

Restart service:
    $ sudo ./manage.sh restart

Check systemd status:
    $ sudo systemctl status video-bot

════════════════════════════════════════════════════════════════════════════════

For more details, read: ~/pi400/README.md

EOF
