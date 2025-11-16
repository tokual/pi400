"""Download handler for video downloads."""

import asyncio
import os
import shutil
import tempfile
import subprocess
import re
from typing import Optional
from urllib.parse import urlparse
import yt_dlp

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.database import Database
from src.utils import logger


SUPPORTED_DOMAINS = [
    'youtube.com', 'youtu.be', 'tiktok.com', 'x.com', 'twitter.com',
    'instagram.com', 'facebook.com', 'vimeo.com', 'dailymotion.com'
]


def validate_url(url: str) -> bool:
    """Validate if URL is from a supported platform."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        
        # Allow any domain for yt-dlp (1000+ supported)
        # Just basic URL validation
        return parsed.scheme in ['http', 'https'] and parsed.netloc
    except Exception:
        return False


async def get_file_size(url: str) -> Optional[tuple[int, int]]:
    """Extract file size and duration from video metadata using yt-dlp.
    
    Returns:
        Tuple of (file_size_bytes, duration_seconds) or (None, None)
    """
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Get file size
            filesize = info.get('filesize') or info.get('filesize_approx')
            if not filesize:
                # Estimate from duration and bitrate
                duration = info.get('duration', 0)
                tbr = info.get('tbr', 0)
                if duration and tbr:
                    filesize = int(duration * tbr * 125)  # tbr is in kbit/s
            
            # Get duration - required for size estimation
            duration = info.get('duration')
            
            # Return filesize if available (even if 0), use duration for estimation
            if filesize is not None and duration:
                return int(filesize), int(duration)
            
            # If we have duration but no filesize, still return it for estimation
            if duration:
                return None, int(duration)
            
            return None, None
    except Exception as e:
        logger.error(f"Error getting file size: {str(e)}")
        return None, None


def estimate_encoded_size(duration_seconds: int, preset: str) -> int:
    """Estimate encoded file size based on preset and duration.
    
    Encoding presets produce different bitrates:
    - Very Fast 720p30: ~1.5 Mbps (lower bitrate)
    - Fast 720p30: ~2.0 Mbps (balanced)
    - Fast 1080p30: ~3.0 Mbps (higher resolution)
    - HQ 720p30 Surround: ~2.5 Mbps (includes audio overhead)
    """
    bitrate_map = {
        'Very Fast 720p30': 1.5,  # Mbps
        'Fast 720p30': 2.0,
        'Fast 1080p30': 3.0,
        'HQ 720p30 Surround': 2.5,
    }
    
    # Get bitrate for preset (default to 2.0 if unknown)
    mbps = bitrate_map.get(preset, 2.0)
    
    # Calculate: duration * bitrate + overhead for audio and metadata (~5%)
    encoded_size = (duration_seconds * mbps * 1024 * 1024) / 8
    return int(encoded_size * 1.05)  # Add 5% overhead


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to be compatible with HandBrake and filesystems.
    
    Removes or replaces problematic characters:
    - Colons (:) - not allowed in Windows filenames
    - Slashes (/ and backslash) - directory separators
    - Other special characters that cause issues
    """
    # Remove or replace problematic characters
    filename = filename.replace(':', '-')  # Replace colons with dashes
    filename = filename.replace('/', '-')  # Replace forward slashes with dashes
    filename = filename.replace('\\', '-')  # Replace backslashes with dashes
    filename = filename.replace('?', '')   # Remove question marks
    filename = filename.replace('"', '')   # Remove quotes
    filename = filename.replace('<', '')   # Remove angle brackets
    filename = filename.replace('>', '')
    filename = filename.replace('|', '-')  # Replace pipes with dashes
    filename = filename.replace('*', '')   # Remove asterisks
    
    # Also handle unicode special characters that look like punctuation
    filename = re.sub(r'[^\w\s\-\.]', '', filename)  # Keep only word chars, spaces, dashes, dots
    
    # Remove leading/trailing spaces and dots
    filename = filename.strip('. ')
    
    # Limit length to 200 chars (leave room for extension)
    if len(filename) > 200:
        filename = filename[:200]
    
    # Ensure filename is not empty
    if not filename or filename.isspace():
        filename = 'video'
    
    return filename


async def download_video(url: str, temp_dir: str, status_msg: types.Message) -> Optional[str]:
    """Download video using yt-dlp."""
    try:
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'quiet': False,
            'no_warnings': False,
            'socket_timeout': 30,
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [lambda d: asyncio.create_task(
                update_download_progress(status_msg, d)
            )],
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Sanitize the filename for HandBrake compatibility
            dir_path = os.path.dirname(filename)
            file_basename = os.path.basename(filename)
            name_without_ext = os.path.splitext(file_basename)[0]
            ext = os.path.splitext(file_basename)[1]
            
            sanitized_name = sanitize_filename(name_without_ext) + ext
            sanitized_path = os.path.join(dir_path, sanitized_name)
            
            # Rename the file if necessary
            if filename != sanitized_path:
                os.rename(filename, sanitized_path)
                logger.info(f"Renamed: {file_basename} -> {sanitized_name}")
            
            return sanitized_path
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise


async def update_download_progress(message: types.Message, data: dict):
    """Update message with download progress."""
    try:
        if data['status'] == 'downloading':
            percent = data.get('_percent_str', 'N/A').strip()
            speed = data.get('_speed_str', 'N/A').strip()
            eta = data.get('_eta_str', 'N/A').strip()
            
            progress_text = f"⬇️ Downloading...\n{percent} | Speed: {speed} | ETA: {eta}"
            
            # Update every 5 seconds to avoid rate limits
            if not hasattr(update_download_progress, '_last_update'):
                update_download_progress._last_update = 0
            
            import time
            if time.time() - update_download_progress._last_update > 5:
                try:
                    await message.edit_text(progress_text)
                    update_download_progress._last_update = time.time()
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Progress update error: {str(e)}")


async def encode_with_handbrake(input_file: str, output_file: str, preset: str, status_msg: types.Message) -> bool:
    """Encode video using HandBrake CLI."""
    try:
        cmd = [
            'HandBrakeCLI',
            '-i', input_file,
            '-o', output_file,
            '--preset', preset,
            '-q', '22',
            '-e', 'x264',
            '-b', '2000',
            '-a', '1',
            '-E', 'aac',
            '-B', '128',
            '--format', 'av_mp4',
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3600)
        
        if process.returncode != 0:
            logger.error(f"HandBrake error: {stderr.decode()}")
            return False
        
        await status_msg.edit_text("⚙️ Encoding complete!")
        return True
    except asyncio.TimeoutError:
        logger.error("HandBrake encoding timeout")
        return False
    except Exception as e:
        logger.error(f"HandBrake error: {str(e)}")
        return False


async def process_download(message: types.Message, state: FSMContext, db: Database, url: str, config, download_states=None):
    """Main download processing function."""
    user_id = message.from_user.id
    status_msg = None
    temp_dir = None
    
    try:
        # Validate URL
        if not validate_url(url):
            await message.answer(
                "❌ *Invalid URL*\n\n"
                "Please provide a valid video link starting with http:// or https://\n\n"
                "Examples:\n"
                "• https://www.youtube.com/watch?v=...\n"
                "• https://www.tiktok.com/@.../video/...\n"
                "• https://x.com/.../status/..."
            )
            return
        
        # Send status message
        status_msg = await message.answer("🔍 Validating URL and checking video info...")
        
        # Get file size and duration
        logger.info(f"Checking file size for URL: {url[:50]}...")
        await status_msg.edit_text("📊 Analyzing video metadata...")
        
        result = await get_file_size(url)
        file_size = None
        duration = None
        
        if result:
            file_size, duration = result
        
        # Check if file size exceeds limit
        if file_size is None:
            await status_msg.edit_text(
                "⚠️ *Could not determine video size*\n\n"
                "Proceeding with caution. The encoded file may be large.\n\n"
                "If it fails, try a shorter video or faster preset."
            )
            logger.warning(f"Could not get file size for user {user_id}")
        elif file_size > config.MAX_FILE_SIZE:  # file_size > 50MB
            # Get current preset to estimate encoded size
            preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
            estimated_encoded = estimate_encoded_size(duration or 0, preset)
            
            # If estimated encoded size is below limit, show warning with confirmation
            if estimated_encoded <= config.MAX_FILE_SIZE:
                logger.info(f"Large file warning for user {user_id}: {file_size / (1024*1024):.0f}MB source, ~{estimated_encoded / (1024*1024):.0f}MB encoded")
                
                # Store URL for later use if confirmed
                await state.update_data(pending_url=url, pending_message_id=status_msg.message_id)
                if download_states:
                    await state.set_state(download_states.waiting_for_confirmation.state)
                else:
                    # Use string state name if DownloadStates not provided
                    await state.set_state("DownloadStates:waiting_for_confirmation")
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Yes, Continue", callback_data="confirm_yes"),
                        InlineKeyboardButton(text="❌ No, Cancel", callback_data="confirm_no"),
                    ]
                ])
                
                await status_msg.edit_text(
                    f"⚠️ *Large Source File*\n\n"
                    f"📏 Source: {file_size / (1024*1024):.0f}MB\n"
                    f"📏 Estimated after encoding: ~{estimated_encoded / (1024*1024):.0f}MB\n"
                    f"📏 Telegram limit: 50MB\n\n"
                    f"Using preset: *{preset}*\n\n"
                    f"The source is large, but encoding should fit within limits.\n\n"
                    f"Continue with download?",
                    reply_markup=keyboard
                )
                return
            else:
                # Estimated size still too large
                await status_msg.edit_text(
                    f"❌ *Video Likely Too Large*\n\n"
                    f"📏 Source: {file_size / (1024*1024):.0f}MB\n"
                    f"📏 Estimated after encoding: ~{estimated_encoded / (1024*1024):.0f}MB\n"
                    f"📏 Telegram limit: 50MB\n\n"
                    f"Using preset: *{preset}*\n\n"
                    f"💡 Try:\n"
                    f"• A shorter video\n"
                    f"• Very Fast preset (Settings → ⚙️)\n"
                    f"• A different video"
                )
                logger.warning(f"User {user_id} file too large even after encoding: {estimated_encoded / (1024*1024):.0f}MB")
                await state.clear()
                return
        
        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix=f"video_{user_id}_")
        logger.info(f"Created temp directory: {temp_dir}")
        
        # Download video
        await status_msg.edit_text("⬇️ Downloading video...\n_This may take a few minutes..._")
        logger.info(f"Starting download for user {user_id}")
        
        try:
            downloaded_file = await download_video(url, temp_dir, status_msg)
            logger.info(f"Downloaded: {downloaded_file}")
        except Exception as e:
            await status_msg.edit_text(
                f"❌ *Download Failed*\n\n"
                f"Error: {str(e)[:100]}\n\n"
                f"💡 The video URL might be:\n"
                f"• Invalid or expired\n"
                f"• From an unsupported platform\n"
                f"• Protected/private\n\n"
                f"Try another video or check the URL."
            )
            logger.error(f"Download error for user {user_id}: {str(e)}")
            return
        
        # Encode video
        await status_msg.edit_text("⚙️ Encoding video...\n_This may take 1-10 minutes depending on length_")
        logger.info(f"Starting encoding for user {user_id}")
        
        output_file = os.path.join(temp_dir, "encoded.mp4")
        preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
        
        success = await encode_with_handbrake(downloaded_file, output_file, preset, status_msg)
        
        if not success:
            await status_msg.edit_text(
                f"❌ *Encoding Failed*\n\n"
                f"The video encoding process encountered an error.\n\n"
                f"💡 Try:\n"
                f"• A shorter video\n"
                f"• A faster preset (Settings → ⚙️)\n"
                f"• A different video"
            )
            logger.error(f"Encoding failed for user {user_id}")
            return
        
        # Check output file size
        output_size = os.path.getsize(output_file)
        if output_size > config.MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"❌ *Encoded File Too Large*\n\n"
                f"📏 Result: {output_size / (1024*1024):.1f}MB (max 50MB)\n\n"
                f"💡 Try:\n"
                f"• A shorter video\n"
                f"• A faster preset (Settings → ⚙️)\n"
                f"• Lower resolution on source"
            )
            return
        
        # Upload to Telegram
        await status_msg.edit_text("📤 Uploading to Telegram...\n_Almost done!_")
        logger.info(f"Uploading file for user {user_id}")
        
        with open(output_file, 'rb') as video_file:
            video_msg = await message.bot.send_video(
                chat_id=user_id,
                video=video_file,
                caption="✅ Your video is ready!",
                parse_mode="Markdown"
            )
        
        # Delete the status message and send completion message
        try:
            await status_msg.delete()
        except Exception:
            pass  # Message may already be deleted
        
        # Send completion with options
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬇️ Download Another", callback_data="start_download")],
            [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
        ])
        
        await message.answer(
            "✅ *Done!*\n\n"
            "Your video has been sent above.\n\n"
            "What would you like to do next?",
            reply_markup=keyboard
        )
        logger.info(f"Successfully processed video for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error processing download: {str(e)}")
        if status_msg:
            await status_msg.edit_text(
                f"❌ *Unexpected Error*\n\n"
                f"Something went wrong: {str(e)[:80]}\n\n"
                f"Please try again or contact support."
            )
    
    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.error(f"Failed to cleanup temp directory: {str(e)}")
        
        # Clear FSM state
        await state.clear()
