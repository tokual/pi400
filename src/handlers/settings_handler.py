"""Settings handler for user preferences."""

import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext

from src.database import Database
from src.utils import logger


async def show_settings_menu(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Show settings menu with inline keyboard."""
    user_id = callback_query.from_user.id
    
    keyboard_buttons = [
        [types.InlineKeyboardButton(text="← Back", callback_data="back_to_menu")]
    ]
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    settings_text = (
        "⚙️ *Settings*\n\n"
        "All videos are automatically optimized:\n\n"
        "• Dynamic resolution selection\n"
        "  - 720p for videos ≤60 seconds\n"
        "  - 480p for videos >60 seconds\n\n"
        "• Automatic bitrate limiting\n"
        "  - 2 Mbps for short videos\n"
        "  - 1.6 Mbps for longer videos\n\n"
        "• H.264 + AAC codec\n"
        "  - Mobile compatible\n"
        "  - Telegram optimized\n\n"
        "No manual configuration needed!"
    )
    
    await callback_query.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()


async def register_handlers(dp: Dispatcher, db: Database):
    """Register settings handlers."""
    pass
