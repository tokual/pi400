"""Main Telegram bot implementation using aiogram."""

import logging
import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.database import Database
from src.utils import logger, get_user_setting, set_user_setting
from src.handlers import download_handler, settings_handler

# Configure logging
logging.basicConfig(level=logging.INFO)


class BotConfig:
    """Bot configuration."""
    
    BOT_TOKEN = os.getenv('BOT_TOKEN', '')
    ALLOWED_USER_ID = 23682616
    DOWNLOAD_DIR = '/tmp'
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
    HANDBRAKE_PRESET = "Fast Mobile 720p30"


class DownloadStates(StatesGroup):
    """FSM states for download flow."""
    waiting_for_url = State()
    downloading = State()
    encoding = State()
    uploading = State()


async def check_authorization(user_id: int, db: Database) -> bool:
    """Check if user is whitelisted."""
    return await db.is_user_whitelisted(user_id)


async def start_handler(message: types.Message, state: FSMContext, db: Database):
    """Handle /start command."""
    user_id = message.from_user.id
    
    if not await check_authorization(user_id, db):
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        await message.answer("❌ You are not authorized to use this bot.")
        return
    
    logger.info(f"User {user_id} started the bot")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Download Video", callback_data="start_download")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="show_help")],
    ])
    
    await message.answer(
        "🎬 **Video Download Bot**\n\n"
        "Send me a video URL from YouTube, TikTok, X, or any supported platform "
        "and I'll download and encode it for you.\n\n"
        "Use the buttons below to get started.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def help_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Show help information."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    help_text = (
        "**📖 How to Use**\n\n"
        "1. Send me a video URL (YouTube, TikTok, X, etc.)\n"
        "2. Choose your encoding preferences\n"
        "3. I'll download, encode, and send you the file\n\n"
        "**⚙️ Supported Platforms:**\n"
        "• YouTube\n"
        "• TikTok\n"
        "• X (Twitter)\n"
        "• Instagram\n"
        "• Facebook\n"
        "• And 1000+ more via yt-dlp\n\n"
        "**📊 File Size Limit:** 50MB\n"
        "**🎬 Output:** H.264 MP4, 720p Mobile\n"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Back", callback_data="back_to_menu")],
    ])
    
    await callback_query.message.edit_text(help_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()


async def back_to_menu_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Return to main menu."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Download Video", callback_data="start_download")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="show_help")],
    ])
    
    await callback_query.message.edit_text(
        "🎬 **Video Download Bot**\n\n"
        "Send me a video URL from YouTube, TikTok, X, or any supported platform "
        "and I'll download and encode it for you.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback_query.answer()


async def start_download_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Start download flow."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await state.set_state(DownloadStates.waiting_for_url)
    
    await callback_query.message.edit_text(
        "📎 Send me a video URL:\n\n"
        "Examples:\n"
        "• https://www.youtube.com/watch?v=...\n"
        "• https://www.tiktok.com/@.../video/...\n"
        "• https://x.com/.../status/...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_menu")],
        ])
    )
    await callback_query.answer()


async def url_message_handler(message: types.Message, state: FSMContext, db: Database):
    """Handle URL submission."""
    user_id = message.from_user.id
    
    if not await check_authorization(user_id, db):
        await message.answer("❌ Unauthorized")
        return
    
    current_state = await state.get_state()
    if current_state != DownloadStates.waiting_for_url.state:
        return
    
    url = message.text
    logger.info(f"User {user_id} submitted URL: {url[:50]}...")
    
    # Call download handler
    await download_handler.process_download(message, state, db, url, BotConfig)


async def show_settings_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Show settings menu."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await settings_handler.show_settings_menu(callback_query, state, db)


async def setup_handlers(dp: Dispatcher, db: Database):
    """Register all message and callback handlers."""
    
    # Command handlers
    dp.message.register(start_handler, Command("start"))
    
    # Callback query handlers
    dp.callback_query.register(help_handler, F.data == "show_help")
    dp.callback_query.register(back_to_menu_handler, F.data == "back_to_menu")
    dp.callback_query.register(start_download_handler, F.data == "start_download")
    dp.callback_query.register(show_settings_handler, F.data == "show_settings")
    
    # Message handlers for URL input
    dp.message.register(url_message_handler)
    
    # Settings handlers
    await settings_handler.register_handlers(dp, db)


async def main():
    """Main bot entry point."""
    if not BotConfig.BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment")
        raise ValueError("BOT_TOKEN must be set")
    
    # Initialize database
    db = Database()
    await db.initialize()
    
    # Add authorized user to whitelist if not exists
    await db.add_user(BotConfig.ALLOWED_USER_ID, is_whitelisted=True)
    
    # Initialize bot and dispatcher
    bot = Bot(token=BotConfig.BOT_TOKEN)
    dp = Dispatcher()
    
    # Register handlers
    await setup_handlers(dp, db)
    
    logger.info("Bot started polling...")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
