# Telegram Video Download Bot

Secure, easy-to-use Telegram bot for downloading and encoding videos from YouTube, TikTok, X, and 1000+ other platforms directly on your Raspberry Pi.

## Features

- 🎬 Download videos from YouTube, TikTok, X, Instagram, and 1000+ other platforms
- ⚙️ Encode with HandBrake (720p mobile preset, configurable)
- 🔒 User whitelist authentication (only authorized users can use)
- 💾 SQLite database for user settings and history
- 📊 Real-time progress notifications
- 🚀 Easy installation and management via scripts
- 📝 Minimal, secure logging with 48h auto-cleanup
- 🔄 Auto-restart on failure via systemd

## Installation

### Requirements

- Raspberry Pi OS (Debian-based)
- Internet connection
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

### One-Command Install

```bash
# Clone or download the bot
git clone <repo-url> video-bot
cd video-bot

# Run installer
sudo ./install.sh
```

That's it! The installer will:
- Create `/opt/video-bot/` directory
- Set up Python virtual environment
- Install all dependencies (HandBrake, yt-dlp, etc.)
- Create `.env` configuration file
- Initialize SQLite database
- Set up systemd service
- Start the bot automatically

### Configuration

The bot is configured via `.env` file in `/opt/video-bot/.env`:

```env
BOT_TOKEN=your_telegram_bot_token_here
ALLOWED_USER_ID=23682616  # Replace with your Telegram user ID
```

Find your Telegram user ID by messaging [@userinfobot](https://t.me/userinfobot).

## Usage

### Starting/Stopping the Bot

```bash
# Start
sudo ./manage.sh start

# Stop
sudo ./manage.sh stop

# Restart
sudo ./manage.sh restart

# Check status
sudo ./manage.sh status
```

### Viewing Logs

```bash
# View live logs
sudo ./manage.sh logs

# Exit logs with Ctrl+C
```

### Updating the Bot

```bash
# Update from git and restart
sudo ./manage.sh update
```

### Uninstalling

```bash
# Remove bot completely
sudo ./manage.sh uninstall
```

## How to Use

1. **Start a chat** with your bot on Telegram
2. **Send a video URL**:
   ```
   https://www.youtube.com/watch?v=...
   https://www.tiktok.com/@.../video/...
   https://x.com/.../status/...
   ```
3. **Choose encoding preset** (if prompted)
4. **Wait** for download and encoding to complete
5. **Receive** the encoded video in Telegram

### Encoding Presets

- **⚡ Fast (1h+ video)**: Fast encoding, lower quality (~1-2x real time)
- **⚙️ Balanced**: Good balance of quality and speed (~2-3x real time)
- **🎬 Quality (short video)**: Best quality, slower (~3-5x real time)

Change preset anytime via `/settings` in Telegram.

## Directory Structure

```
/opt/video-bot/
├── venv/                    # Python virtual environment
├── src/
│   ├── bot.py              # Main bot logic
│   ├── database.py         # SQLite database handler
│   ├── utils.py            # Utilities and logging
│   └── handlers/
│       ├── download_handler.py   # Video download/encode logic
│       └── settings_handler.py   # User settings UI
├── bot.db                  # SQLite database (auto-created)
├── .env                    # Configuration (created from sample.env)
├── sample.env              # Configuration template
├── install.sh              # Installation script
├── manage.sh               # Management script
└── README.md               # This file
```

## Troubleshooting

### Bot won't start

Check the logs:
```bash
sudo ./manage.sh logs
```

Common issues:
- **BOT_TOKEN not set**: Edit `/opt/video-bot/.env` and add your token
- **Dependencies missing**: Run `sudo apt-get install handbrake-cli ffmpeg`
- **Permission denied**: Ensure install was run with `sudo`

### Encoding is slow

The Raspberry Pi 4 encodes at ~2-3x real time. Use "Fast" preset for longer videos.

### Video upload fails

Check file size:
- Telegram limit: 50MB
- Bot limit: 50MB (early detection)
- Use shorter video or "Fast" preset to reduce file size

### Service crashes unexpectedly

View systemd logs:
```bash
sudo journalctl -u video-bot -n 50
```

### Database corruption

The bot auto-restarts on failure. If database is corrupt, manually restart:
```bash
sudo ./manage.sh restart
```

## Security

- Only one user ID can use the bot (whitelist in database)
- `.env` file is readable only by the bot user (600 permissions)
- Logs contain no sensitive data (URLs truncated)
- Subprocess isolation for downloads and encoding
- Automatic cleanup of temporary files
- 48-hour log rotation and retention

## Requirements Met

✅ One-command installation (sudo ./install.sh)  
✅ Systemd service with auto-restart  
✅ Management commands (start/stop/restart/update)  
✅ SQLite database for auth and settings  
✅ User whitelist (23682616 by default)  
✅ yt-dlp for multi-platform support  
✅ HandBrake CLI encoding  
✅ Early 50MB file size detection  
✅ Progress notifications with status emojis  
✅ User-configurable settings via Telegram UI  
✅ Automatic temp file cleanup  
✅ Systemd journal logging (resilient)  
✅ 48-hour log retention  
✅ Minimal documentation  

## License

MIT

## Support

For issues, check logs with `sudo ./manage.sh logs` and refer to the troubleshooting section above.
