"""Settings handler for user preferences."""

import logging
from aiogram import types, Dispatcher, F
from aiogram.fsm.context import FSMContext

from src.database import Database
from src.utils import logger

# Encoding presets available
ENCODING_PRESETS = {
    '⚡ Very Fast 720p30 (1-2x)': 'Very Fast 720p30',
    '⚙️ Fast 720p30 (2-3x)': 'Fast 720p30',
    '🎬 Fast 1080p30 (3-5x)': 'Fast 1080p30',
    '🎯 HQ 720p30 (4-6x)': 'HQ 720p30 Surround',
}


async def show_settings_menu(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Show settings menu with inline keyboard."""
    user_id = callback_query.from_user.id
    
    # Get current preset
    current_preset = await db.get_user_setting(user_id, 'encoding_preset') or 'Very Fast 720p30'
    
    keyboard_buttons = []
    for label, preset_value in ENCODING_PRESETS.items():
        # Mark current preset with ✓
        if preset_value == current_preset:
            label = label.replace('(', '(✓ ')
        
        keyboard_buttons.append([
            types.InlineKeyboardButton(text=label, callback_data=f"preset_{preset_value}")
        ])
    
    keyboard_buttons.append([
        types.InlineKeyboardButton(text="← Back", callback_data="back_to_menu")
    ])
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    settings_text = (
        "⚙️ **Encoding Settings**\n\n"
        "**Preset:** Choose encoding speed/quality tradeoff\n"
        "• Very Fast 720p30: Fastest, lower quality (~1-2x real time)\n"
        "• Fast 720p30: Balanced, good quality (~2-3x real time)\n"
        "• Fast 1080p30: Better quality, higher resolution (~3-5x real time)\n"
        "• HQ 720p30: Best quality, slowest (~4-6x real time)\n\n"
        f"**Current:** {current_preset}"
    )
    
    await callback_query.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()


async def preset_selection_handler(callback_query: types.CallbackQuery, state: FSMContext, db: Database):
    """Handle preset selection."""
    user_id = callback_query.from_user.id
    preset = callback_query.data.replace("preset_", "")
    
    # Validate preset
    if preset not in ENCODING_PRESETS.values():
        await callback_query.answer("❌ Invalid preset", show_alert=True)
        return
    
    # Save to database
    await db.set_user_setting(user_id, 'encoding_preset', preset)
    logger.info(f"User {user_id} set encoding preset to: {preset}")
    
    # Show confirmation
    await callback_query.answer(f"✅ Preset set to: {preset}", show_alert=False)
    
    # Refresh settings menu
    await show_settings_menu(callback_query, state, db)


async def register_handlers(dp: Dispatcher, db: Database):
    """Register settings handlers."""
    dp.callback_query.register(
        preset_selection_handler,
        F.data.startswith("preset_")
    )
