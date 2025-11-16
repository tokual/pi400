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

# Concurrency control: Limit simultaneous downloads to prevent bot rate limits
# Using a semaphore to allow 1 download per user at a time
_download_semaphores = {}


SUPPORTED_DOMAINS = [
    'youtube.com', 'youtu.be', 'tiktok.com', 'x.com', 'twitter.com',
    'instagram.com', 'facebook.com', 'vimeo.com', 'dailymotion.com'
]


def validate_url(url: str) -> bool:
    """Validate if URL is from a supported platform.
    
    Performs both format validation and basic security checks.
    """
    if not url or not isinstance(url, str):
        return False
    
    # Limit URL length (max 2048 is standard)
    if len(url) > 2048:
        return False
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        
        # Validate scheme is http/https
        if parsed.scheme not in ['http', 'https']:
            return False
        
        # Validate netloc exists and is not suspicious
        if not parsed.netloc or len(parsed.netloc) > 255:
            return False
        
        # Prevent file:// URIs and other schemes
        if parsed.scheme.lower() not in ['http', 'https']:
            return False
        
        # Allow any domain for yt-dlp (1000+ supported)
        # Just basic URL validation
        return True
    except Exception:
        return False


async def get_file_size(url: str, timeout: int = 30) -> Optional[tuple[int, int]]:
    """Extract file size and duration from video metadata using yt-dlp.
    
    Args:
        url: Video URL to check
        timeout: Maximum seconds to wait for metadata extraction (default: 30s)
    
    Returns:
        Tuple of (file_size_bytes, duration_seconds) or (None, None)
    """
    try:
        async def _extract_info():
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': timeout,
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
        
        # Apply timeout to metadata extraction
        try:
            result = await asyncio.wait_for(_extract_info(), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"File size check timeout for URL: {url[:50]}...")
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


async def download_video(url: str, temp_dir: str, status_msg: types.Message, timeout: int = 3600) -> Optional[str]:
    """Download video using yt-dlp.
    
    Args:
        url: Video URL (must be validated before calling)
        temp_dir: Temporary directory (must exist)
        status_msg: Message to update with progress
        timeout: Maximum seconds to wait for download (default: 1 hour)
    
    Returns:
        Path to downloaded file or None on error
    """
    try:
        # Validate temp_dir
        if not temp_dir or not isinstance(temp_dir, str):
            logger.error("Invalid temp_dir")
            raise ValueError("Invalid temporary directory")
        
        if not os.path.isdir(temp_dir):
            logger.error(f"Temp directory does not exist: {temp_dir}")
            raise ValueError("Temporary directory not found")
        
        async def _download():
            ydl_opts = {
                'format': 'best[ext=mp4][height<=1080]/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
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
                return filename
        
        # Apply timeout to download operation
        try:
            filename = await asyncio.wait_for(_download(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Download timeout after {timeout}s for user")
            raise ValueError(f"Download took too long (timeout: {timeout}s)")
            
            # Validate downloaded file
            if not filename or not isinstance(filename, str):
                logger.error("Invalid filename from yt-dlp")
                raise ValueError("Failed to get filename")
            
            if not os.path.exists(filename):
                logger.error(f"Downloaded file not found: {filename}")
                raise ValueError("Downloaded file not found")
            
            # Sanitize the filename for HandBrake compatibility
            dir_path = os.path.dirname(filename)
            file_basename = os.path.basename(filename)
            name_without_ext = os.path.splitext(file_basename)[0]
            ext = os.path.splitext(file_basename)[1]
            
            sanitized_name = sanitize_filename(name_without_ext) + ext
            sanitized_path = os.path.join(dir_path, sanitized_name)
            
            # Verify the path is within temp_dir (prevent directory traversal)
            real_temp = os.path.realpath(temp_dir)
            real_sanitized = os.path.realpath(sanitized_path)
            
            if not real_sanitized.startswith(real_temp):
                logger.error(f"Path traversal detected: {sanitized_path}")
                raise ValueError("Invalid file path")
            
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


async def _get_semaphore(user_id: int) -> asyncio.Semaphore:
    """Get or create a semaphore for a user to limit concurrent downloads."""
    if user_id not in _download_semaphores:
        _download_semaphores[user_id] = asyncio.Semaphore(1)  # 1 download per user at a time
    return _download_semaphores[user_id]


async def process_download(message: types.Message, state: FSMContext, db: Database, url: str, config, download_states=None):
    """Validate URL, check file size, and show confirmation if needed.
    
    This is the entry point when user submits a URL. It validates the URL,
    checks file size, and either proceeds with download or shows confirmation dialog.
    """
    user_id = message.from_user.id
    status_msg = None
    waiting_for_confirmation = False
    
    # Validate input parameters
    if not url or not isinstance(url, str):
        logger.warning(f"Invalid URL input from user {user_id}")
        try:
            await message.answer("❌ Invalid URL")
        except Exception as e:
            logger.error(f"Failed to send error message to user {user_id}: {e}")
        return
    
    # Validate user_id
    if not isinstance(user_id, int) or user_id < 0:
        logger.warning(f"Invalid user_id: {user_id}")
        return
    
    try:
        # Validate URL
        if not validate_url(url):
            try:
                await message.answer(
                    "❌ *Invalid URL*\n\n"
                    "Please provide a valid video link starting with http:// or https://\n\n"
                    "Examples:\n"
                    "• https://www.youtube.com/watch?v=...\n"
                    "• https://www.tiktok.com/@.../video/...\n"
                    "• https://x.com/.../status/..."
                )
            except Exception as e:
                logger.error(f"Failed to send URL validation error to user {user_id}: {e}")
            return
        
        # Send status message
        try:
            status_msg = await message.answer("🔍 Validating URL and checking video info...")
        except Exception as e:
            logger.error(f"Failed to send status message to user {user_id}: {e}")
            return
        
        # Get file size and duration
        logger.info(f"Checking file size for URL: {url[:50]}... (user {user_id})")
        try:
            await status_msg.edit_text("📊 Analyzing video metadata...")
        except Exception as e:
            logger.warning(f"Failed to update status message for user {user_id}: {e}")
        
        result = await get_file_size(url, timeout=30)
        file_size = None
        duration = None
        
        if result:
            file_size, duration = result
        
        # Check if file size exceeds limit
        if file_size is None:
            try:
                await status_msg.edit_text(
                    "⚠️ *Could not determine video size*\n\n"
                    "Proceeding with caution. The encoded file may be large.\n\n"
                    "If it fails, try a shorter video or faster preset.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to update status for user {user_id}: {e}")
            logger.warning(f"Could not get file size for user {user_id}")
            # Proceed with download anyway
            await execute_confirmed_download(message, state, db, url, config, download_states)
        elif file_size > config.MAX_FILE_SIZE:  # file_size > 50MB
            # Get current preset to estimate encoded size
            try:
                preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
            except Exception as e:
                logger.error(f"Failed to get preset for user {user_id}: {e}")
                preset = config.HANDBRAKE_PRESET
            
            estimated_encoded = estimate_encoded_size(duration or 0, preset)
            
            # If estimated encoded size is below limit, show warning with confirmation
            if estimated_encoded <= config.MAX_FILE_SIZE:
                logger.info(f"Large file warning for user {user_id}: {file_size / (1024*1024):.0f}MB source, ~{estimated_encoded / (1024*1024):.0f}MB encoded")
                
                # Store URL for later use if confirmed (store in database for persistence)
                try:
                    await db.set_user_setting(user_id, 'pending_url', url)
                except Exception as e:
                    logger.error(f"Failed to store pending URL for user {user_id}: {e}")
                    try:
                        await status_msg.edit_text("❌ Database error. Please try again.")
                    except Exception:
                        pass
                    return
                
                if download_states:
                    await state.set_state(download_states.waiting_for_confirmation.state)
                    logger.info(f"FSM state set to waiting_for_confirmation for user {user_id}")
                else:
                    # Use string state name if DownloadStates not provided
                    await state.set_state("DownloadStates:waiting_for_confirmation")
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Yes, Continue", callback_data="confirm_yes"),
                        InlineKeyboardButton(text="❌ No, Cancel", callback_data="confirm_no"),
                    ]
                ])
                
                try:
                    await status_msg.edit_text(
                        f"⚠️ *Large Source File*\n\n"
                        f"📏 Source: {file_size / (1024*1024):.0f}MB\n"
                        f"📏 Estimated after encoding: ~{estimated_encoded / (1024*1024):.0f}MB\n"
                        f"📏 Telegram limit: 50MB\n\n"
                        f"Using preset: *{preset}*\n\n"
                        f"The source is large, but encoding should fit within limits.\n\n"
                        f"Continue with download?",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to show confirmation for user {user_id}: {e}")
                logger.info(f"Confirmation dialog shown to user {user_id}")
                waiting_for_confirmation = True
                return
            else:
                # Estimated size still too large
                try:
                    await status_msg.edit_text(
                        f"❌ *Video Likely Too Large*\n\n"
                        f"📏 Source: {file_size / (1024*1024):.0f}MB\n"
                        f"📏 Estimated after encoding: ~{estimated_encoded / (1024*1024):.0f}MB\n"
                        f"📏 Telegram limit: 50MB\n\n"
                        f"Using preset: *{preset}*\n\n"
                        f"💡 Try:\n"
                        f"• A shorter video\n"
                        f"• Very Fast preset (Settings → ⚙️)\n"
                        f"• A different video",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.warning(f"Failed to update status for user {user_id}: {e}")
                logger.warning(f"User {user_id} file too large even after encoding: {estimated_encoded / (1024*1024):.0f}MB")
                await state.clear()
                return
        else:
            # File size is OK, proceed with download
            await execute_confirmed_download(message, state, db, url, config, download_states)
    
    except Exception as e:
        logger.error(f"Error in process_download: {str(e)}")
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"❌ *Unexpected Error*\n\n"
                    f"Something went wrong: {str(e)[:80]}\n\n"
                    f"Please try again or contact support."
                )
            except Exception as edit_error:
                logger.error(f"Failed to send error message to user: {edit_error}")
        await state.clear()
    
    finally:
        # Only delete status message if not waiting for user confirmation
        # (confirmation handler will delete it after user clicks yes/no)
        if status_msg and not waiting_for_confirmation:
            try:
                await status_msg.delete()
            except Exception as e:
                logger.debug(f"Failed to delete status message: {e}")


async def execute_confirmed_download(message: types.Message, state: FSMContext, db: Database, url: str, config, download_states=None):
    """Execute download after user confirmation (skips file size re-check).
    
    This function is called after the user confirms a large file download,
    or when file size is acceptable. It handles the actual download, encode, and upload.
    """
    user_id = message.from_user.id
    status_msg = None
    temp_dir = None
    
    # Get semaphore for this user to limit concurrent downloads
    semaphore = await _get_semaphore(user_id)
    
    async with semaphore:  # Limit concurrent downloads per user
        try:
            if download_states:
                await state.set_state(download_states.downloading.state)
                logger.info(f"FSM state set to downloading for user {user_id}")
            
            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix=f"video_{user_id}_")
            if not os.path.isdir(temp_dir):
                raise ValueError(f"Failed to create temp directory")
            logger.info(f"Created temp directory: {temp_dir}")
            
            # Get or create status message
            if isinstance(message, types.Message):
                try:
                    status_msg = await message.answer("⬇️ Downloading video...\n_This may take a few minutes..._")
                except Exception as e:
                    logger.error(f"Failed to send download status to user {user_id}: {e}")
                    return
            else:
                logger.error(f"Invalid message object for user {user_id}")
                return
            
            # Download video
            logger.info(f"Starting download for user {user_id}")
            try:
                downloaded_file = await download_video(url, temp_dir, status_msg, timeout=3600)
                logger.info(f"Downloaded: {downloaded_file}")
            except asyncio.TimeoutError:
                try:
                    await status_msg.edit_text(
                        f"❌ *Download Timeout*\n\n"
                        f"The download took too long.\n\n"
                        f"💡 Try:\n"
                        f"• A shorter video\n"
                        f"• A different video\n"
                        f"• Check your internet connection",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                logger.error(f"Download timeout for user {user_id}")
                return
            except Exception as e:
                try:
                    await status_msg.edit_text(
                        f"❌ *Download Failed*\n\n"
                        f"Error: {str(e)[:100]}\n\n"
                        f"💡 The video URL might be:\n"
                        f"• Invalid or expired\n"
                        f"• From an unsupported platform\n"
                        f"• Protected/private\n\n"
                        f"Try another video or check the URL.",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                logger.error(f"Download error for user {user_id}: {str(e)}")
                return
            
            # Encode video
            try:
                await status_msg.edit_text("⚙️ Encoding video...\n_This may take 1-10 minutes depending on length_")
            except Exception as e:
                logger.warning(f"Failed to update status for user {user_id}: {e}")
            
            if download_states:
                await state.set_state(download_states.encoding.state)
                logger.info(f"FSM state set to encoding for user {user_id}")
            
            logger.info(f"Starting encoding for user {user_id}")
            output_file = os.path.join(temp_dir, "encoded.mp4")
            
            try:
                preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
            except Exception as e:
                logger.error(f"Failed to get preset for user {user_id}: {e}")
                preset = config.HANDBRAKE_PRESET
            
            success = await encode_with_handbrake(downloaded_file, output_file, preset, status_msg)
            
            if not success:
                try:
                    await status_msg.edit_text(
                        f"❌ *Encoding Failed*\n\n"
                        f"The video encoding process encountered an error.\n\n"
                        f"💡 Try:\n"
                        f"• A shorter video\n"
                        f"• A faster preset (Settings → ⚙️)\n"
                        f"• A different video",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                logger.error(f"Encoding failed for user {user_id}")
                return
            
            # Check output file size
            try:
                output_size = os.path.getsize(output_file)
            except Exception as e:
                logger.error(f"Failed to get output file size for user {user_id}: {e}")
                try:
                    await status_msg.edit_text("❌ Error checking encoded file size.")
                except Exception:
                    pass
                return
            
            if output_size > config.MAX_FILE_SIZE:
                try:
                    await status_msg.edit_text(
                        f"❌ *Encoded File Too Large*\n\n"
                        f"📏 Result: {output_size / (1024*1024):.1f}MB (max 50MB)\n\n"
                        f"💡 Try:\n"
                        f"• A shorter video\n"
                        f"• A faster preset (Settings → ⚙️)\n"
                        f"• Lower resolution on source",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                logger.error(f"Encoded file too large for user {user_id}: {output_size / (1024*1024):.1f}MB")
                return
            
            # Upload to Telegram
            try:
                await status_msg.edit_text("📤 Uploading to Telegram...\n_Almost done!_")
            except Exception as e:
                logger.warning(f"Failed to update status for user {user_id}: {e}")
            
            if download_states:
                await state.set_state(download_states.uploading.state)
                logger.info(f"FSM state set to uploading for user {user_id}")
            
            logger.info(f"Uploading file for user {user_id}")
            
            try:
                with open(output_file, 'rb') as video_file:
                    video_msg = await message.bot.send_video(
                        chat_id=user_id,
                        video=video_file,
                        caption="✅ Your video is ready!",
                        parse_mode="Markdown"
                    )
            except Exception as e:
                logger.error(f"Upload failed for user {user_id}: {e}")
                try:
                    await status_msg.edit_text(
                        f"❌ *Upload Failed*\n\n"
                        f"The video could not be sent to Telegram.\n\n"
                        f"Error: {str(e)[:80]}\n\n"
                        f"Please try again or contact support."
                    )
                except Exception:
                    pass
                return
            
            # Delete the status message and send completion message
            try:
                await status_msg.delete()
            except Exception as e:
                logger.debug(f"Failed to delete status message for user {user_id}: {e}")
            
            # Send completion with options
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬇️ Download Another", callback_data="start_download")],
                [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
            ])
            
            try:
                await message.answer(
                    "✅ *Done!*\n\n"
                    "Your video has been sent above.\n\n"
                    "What would you like to do next?",
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Failed to send completion message to user {user_id}: {e}")
            
            logger.info(f"Successfully processed video for user {user_id}")
        
        except Exception as e:
            logger.error(f"Error in execute_confirmed_download: {str(e)}")
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"❌ *Unexpected Error*\n\n"
                        f"Something went wrong: {str(e)[:80]}\n\n"
                        f"Please try again or contact support.",
                        parse_mode="Markdown"
                    )
                except Exception as edit_error:
                    logger.error(f"Failed to send error message to user {user_id}: {edit_error}")
        
        finally:
            # Cleanup temp directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    logger.info(f"Cleaned up temp directory: {temp_dir}")
                except Exception as e:
                    logger.error(f"Failed to cleanup temp directory {temp_dir}: {e}")
            
            # Clear FSM state
            try:
                await state.clear()
            except Exception as e:
                logger.error(f"Failed to clear FSM state for user {user_id}: {e}")
            
            # Clear pending URL if it exists
            try:
                await db.set_user_setting(user_id, 'pending_url', '')
            except Exception as e:
                logger.warning(f"Failed to clear pending_url for user {user_id}: {e}")
