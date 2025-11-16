"""Main Telegram bot implementation using aiogram."""

import logging
import asyncio
import os
import shutil
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
    ALLOWED_USER_ID = int(os.getenv('ALLOWED_USER_ID', '23682616'))
    DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', '/tmp')
    MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE_MB', '50')) * 1024 * 1024
    HANDBRAKE_PRESET = os.getenv('HANDBRAKE_PRESET', 'Very Fast 720p30')
    UPLOAD_TIMEOUT_SECONDS = int(os.getenv('UPLOAD_TIMEOUT_SECONDS', '600'))


class DownloadStates(StatesGroup):
    """FSM states for download flow."""
    waiting_for_url = State()
    waiting_for_confirmation = State()
    waiting_for_encoding_choice = State()
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
    
    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Download Video", callback_data="start_download")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="show_help")],
    ])
    
    await message.answer(
        "🎬 *Video Download Bot*\n\n"
        "Download and encode videos from YouTube, TikTok, X, Instagram, and 1000+ other platforms!\n\n"
        "📝 *How to use:*\n"
        "1️⃣ Click 'Download Video' or just send me a URL\n"
        "2️⃣ I'll process and encode your video\n"
        "3️⃣ Receive your video in Telegram\n\n"
        "⚙️ *Features:*\n"
        "• Supports 1000+ video platforms\n"
        "• Multiple encoding presets\n"
        "• Auto-cleanup of temp files\n"
        "• Real-time progress updates\n\n"
        "👇 Get started below or just paste a video URL!",
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
        "📖 *How to Use*\n\n"
        "1️⃣ *Send a Video URL*\n"
        "Just paste any video link:\n"
        "• YouTube: youtube.com/watch?v=...\n"
        "• TikTok: tiktok.com/@.../video/...\n"
        "• Instagram, X, Facebook, etc.\n"
        "• And 1000+ more platforms!\n\n"
        "2️⃣ *Choose Quality (Optional)*\n"
        "Use Settings → ⚙️ to pick encoding speed\n\n"
        "3️⃣ *Wait for Processing*\n"
        "• Download takes 1-5 minutes\n"
        "• Encoding takes 2-10 minutes\n"
        "• Progress updates shown in real-time\n\n"
        "4️⃣ *Get Your Video*\n"
        "Video sent when ready!\n\n"
        "⚙️ *Encoding Presets*\n"
        "• Very Fast: Lowest quality, fastest (1-2x)\n"
        "• Fast: Good quality, faster (2-3x)\n"
        "• Fast 1080p: Better resolution (3-5x)\n"
        "• HQ: Best quality, slowest (4-6x)\n\n"
        "❓ *Troubleshooting*\n"
        "• Video too large? Try shorter clips\n"
        "• Encoding slow? Use Very Fast preset\n"
        "• URL not working? Try another video\n\n"
        "💡 Max file size: 50MB"
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
    url = message.text
    
    # Validate URL input
    if not url or not isinstance(url, str):
        await message.answer("❌ Invalid input")
        return
    
    # Limit URL length (max 2048 chars is reasonable for URLs)
    if len(url) > 2048:
        await message.answer("❌ URL is too long (max 2048 characters)")
        return
    
    # Strip whitespace
    url = url.strip()
    
    # Validate FSM state - only allow URLs in waiting_for_url state or from menu
    if current_state and current_state not in [DownloadStates.waiting_for_url.state, None]:
        logger.warning(f"User {user_id} sent URL in wrong state: {current_state}")
        try:
            await message.answer("❌ Please use the menu buttons to start a download.")
        except Exception as e:
            logger.error(f"Failed to send state error to user {user_id}: {e}")
        return
    
    # Check if we're in waiting_for_url state or if user just sent a URL
    if current_state == DownloadStates.waiting_for_url.state or url.startswith(('http://', 'https://')):
        logger.info(f"User {user_id} submitted URL: {url[:50]}...")
        
        # Call download handler
        await download_handler.process_download(message, state, db, url, BotConfig, DownloadStates)


async def show_settings_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Show settings menu."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    await settings_handler.show_settings_menu(callback_query, state, db)


async def confirmation_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Handle file size confirmation."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    # Validate FSM state before processing confirmation
    current_state = await state.get_state()
    if current_state != DownloadStates.waiting_for_confirmation.state:
        logger.warning(f"User {user_id} sent confirmation callback in wrong state: {current_state}")
        await callback_query.answer("❌ Invalid state. Please start over.", show_alert=True)
        return
    
    # Validate callback data
    if not callback_query.data or not isinstance(callback_query.data, str):
        await callback_query.answer("❌ Invalid request", show_alert=True)
        return
    
    # Retrieve pending URL from database (more reliable than FSM state)
    try:
        pending_url = await db.get_user_setting(user_id, 'pending_url')
    except Exception as e:
        logger.error(f"Failed to get pending URL for user {user_id}: {e}")
        await callback_query.answer("❌ Database error. Please try again.", show_alert=True)
        await state.clear()
        return
    
    # Validate pending URL exists
    if not pending_url or not isinstance(pending_url, str):
        logger.warning(f"No pending URL for user {user_id}")
        await callback_query.answer("❌ No pending download", show_alert=True)
        await state.clear()
        return
    
    # Validate URL length
    if len(pending_url) > 2048:
        logger.warning(f"Pending URL too long for user {user_id}")
        await callback_query.answer("❌ URL is invalid", show_alert=True)
        try:
            await db.set_user_setting(user_id, 'pending_url', '')  # Clear pending URL
        except Exception as e:
            logger.error(f"Failed to clear pending URL for user {user_id}: {e}")
        await state.clear()
        return
    
    if callback_query.data == "confirm_yes":
        # User confirmed, proceed with download
        logger.info(f"User {user_id} confirmed download")
        await callback_query.answer("✅ Starting download...")
        
        # Get message object and delete the confirmation dialog
        message = callback_query.message
        try:
            await message.delete()
        except Exception as e:
            logger.debug(f"Failed to delete confirmation message for user {user_id}: {e}")
        
        # Send a fresh message to use for status updates during download
        try:
            new_message = await message.answer("⏳ Processing your video...")
        except Exception as e:
            logger.error(f"Failed to send processing message for user {user_id}: {e}")
            return
        
        # Clear pending URL from database before processing
        try:
            await db.set_user_setting(user_id, 'pending_url', '')
        except Exception as e:
            logger.error(f"Failed to clear pending URL for user {user_id}: {e}")
        
        # Process the download (skip file size re-check)
        await download_handler.execute_confirmed_download(user_id, new_message, state, db, pending_url, BotConfig, DownloadStates)
    
    elif callback_query.data == "confirm_no":
        # User declined
        logger.info(f"User {user_id} declined download")
        await callback_query.answer("❌ Download cancelled")
        
        # Clear pending URL from database
        try:
            await db.set_user_setting(user_id, 'pending_url', '')
        except Exception as e:
            logger.error(f"Failed to clear pending URL for user {user_id}: {e}")
        
        # Delete the confirmation message
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.debug(f"Failed to delete confirmation message for user {user_id}: {e}")
        
        await state.clear()
    else:
        # Invalid callback data
        logger.warning(f"Invalid callback data from user {user_id}: {callback_query.data}")
        await callback_query.answer("❓ Please choose Yes or No", show_alert=True)


async def encoding_choice_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Handle encoding quality choice from user."""
    user_id = callback_query.from_user.id
    
    if not await check_authorization(user_id, db):
        await callback_query.answer("❌ Unauthorized", show_alert=True)
        return
    
    # Validate FSM state
    current_state = await state.get_state()
    if current_state != DownloadStates.waiting_for_encoding_choice.state:
        logger.warning(f"User {user_id} sent encoding choice in wrong state: {current_state}")
        await callback_query.answer("❌ Invalid state. Please start over.", show_alert=True)
        return
    
    # Validate callback data
    if not callback_query.data or not isinstance(callback_query.data, str):
        await callback_query.answer("❌ Invalid request", show_alert=True)
        return
    
    # Handle skip encoding
    if callback_query.data == "encode_skip":
        logger.info(f"User {user_id} skipped encoding")
        await callback_query.answer("⏭️ Skipped encoding")
        
        # Get state data to clean up temp files
        state_data = await state.get_data()
        temp_dir = state_data.get('temp_dir')
        
        # Cleanup temp directory
        if temp_dir:
            import threading
            def cleanup():
                try:
                    if os.path.isdir(temp_dir):
                        shutil.rmtree(temp_dir)
                        logger.debug(f"Cleaned up temp directory for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp directory for user {user_id}: {e}")
            
            thread = threading.Thread(target=cleanup, daemon=True)
            thread.start()
        
        await state.clear()
        
        # Send menu
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬇️ Download Video", callback_data="start_download")],
            [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
            [InlineKeyboardButton(text="ℹ️ Help", callback_data="show_help")],
        ])
        
        try:
            await callback_query.message.answer(
                "🎬 *Video Download Bot*\n\n"
                "Download and encode videos from YouTube, TikTok, X, Instagram, and 1000+ other platforms!",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send menu to user {user_id}: {e}")
        
        return
    
    # Handle quality selection
    if callback_query.data.startswith("encode_quality:"):
        chosen_quality = callback_query.data.split(":", 1)[1]
        
        # Validate quality
        if chosen_quality not in ['720p', '480p', '360p']:
            logger.warning(f"User {user_id} sent invalid quality: {chosen_quality}")
            await callback_query.answer("❌ Invalid quality", show_alert=True)
            return
        
        logger.info(f"User {user_id} chose {chosen_quality} encoding")
        await callback_query.answer(f"✅ Encoding at {chosen_quality}...")
        
        # Delete the choices message
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.debug(f"Failed to delete choices message for user {user_id}: {e}")
        
        # Get message for status updates
        message = callback_query.message
        
        # Process encoding
        await download_handler.handle_encoding_choice(user_id, message, state, db, chosen_quality, BotConfig, DownloadStates)
        return
    
    # Invalid callback data
    logger.warning(f"Invalid encoding choice callback from user {user_id}: {callback_query.data}")
    await callback_query.answer("❌ Invalid choice", show_alert=True)


async def setup_handlers(dp: Dispatcher, db: Database):
    """Register all message and callback handlers."""
    
    # Create handler wrappers that inject db parameter
    async def start_handler_wrapper(message: types.Message, state: FSMContext):
        return await start_handler(message, state, db)
    
    async def help_handler_wrapper(callback_query: types.CallbackQuery, state: FSMContext):
        return await help_handler(callback_query, state, db)
    
    async def back_to_menu_wrapper(callback_query: types.CallbackQuery, state: FSMContext):
        return await back_to_menu_handler(callback_query, state, db)
    
    async def start_download_wrapper(callback_query: types.CallbackQuery, state: FSMContext):
        return await start_download_handler(callback_query, state, db)
    
    async def show_settings_wrapper(callback_query: types.CallbackQuery, state: FSMContext):
        return await show_settings_handler(callback_query, state, db)
    
    async def confirmation_wrapper(callback_query: types.CallbackQuery, state: FSMContext):
        return await confirmation_handler(callback_query, state, db)
    
    async def url_message_wrapper(message: types.Message, state: FSMContext):
        return await url_message_handler(message, state, db)
    
    async def encoding_choice_wrapper(callback_query: types.CallbackQuery, state: FSMContext):
        return await encoding_choice_handler(callback_query, state, db)
    
    # Command handlers
    dp.message.register(start_handler_wrapper, Command("start"))
    
    # Callback query handlers
    dp.callback_query.register(help_handler_wrapper, F.data == "show_help")
    dp.callback_query.register(back_to_menu_wrapper, F.data == "back_to_menu")
    dp.callback_query.register(start_download_wrapper, F.data == "start_download")
    dp.callback_query.register(show_settings_wrapper, F.data == "show_settings")
    dp.callback_query.register(confirmation_wrapper, F.data.in_(["confirm_yes", "confirm_no"]))
    dp.callback_query.register(encoding_choice_wrapper, F.data.startswith("encode_quality:") | (F.data == "encode_skip"))
    
    # Message handlers for URL input (must be last to not interfere with other handlers)
    dp.message.register(url_message_wrapper)
    
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
    
    # Initialize bot with default session
    # Note: Connection pooling and timeouts are handled by aiogram internally
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
