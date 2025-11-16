"""Download handler for video downloads."""

import asyncio
import os
import shutil
import tempfile
import subprocess
import re
from typing import Optional, Callable, Any
from urllib.parse import urlparse
import yt_dlp

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

from src.database import Database
from src.utils import logger

# Concurrency control: Limit simultaneous downloads to prevent bot rate limits
# Using a semaphore to allow 1 download per user at a time
_download_semaphores = {}
_upload_semaphores = {}  # Limit simultaneous uploads per user


SUPPORTED_DOMAINS = [
    'youtube.com', 'youtu.be', 'tiktok.com', 'x.com', 'twitter.com',
    'instagram.com', 'facebook.com', 'vimeo.com', 'dailymotion.com'
]

# Quality settings for different resolutions (quality value, description)
QUALITY_SETTINGS = {
    '720p': {'quality': '28', 'label': '720p (smaller, slower)'},
    '480p': {'quality': '28', 'label': '480p (recommended)'},
    '360p': {'quality': '30', 'label': '360p (tiny, slowest)'},
}

# Estimated file size per minute of video (in MB) for different qualities
# Based on empirical results: aggressive compression for mobile/Telegram compatibility
EST_SIZE_PER_MINUTE = {
    '720p': 1.5,    # ~1.5 MB per minute (720p, quality=28, aac 96kbps)
    '480p': 0.8,    # ~0.8 MB per minute (480p, quality=28, aac 96kbps) - empirically 17MB for 20min
    '360p': 0.4,    # ~0.4 MB per minute (360p, quality=30, aac 96kbps)
}


def is_retryable_error(error: Exception) -> bool:
    """Classify if an error is retryable or not.
    
    Retryable: Transport, timeout, connection errors
    Non-retryable: Auth, file not found, permission errors
    """
    error_str = str(error).lower()
    
    # Retryable errors
    retryable_keywords = [
        'closing transport',
        'connection reset',
        'connection refused',
        'timeout',
        'timed out',
        'temporary failure',
        'network unreachable',
        'no address associated',
        'broken pipe',
        'cannot write',
        'read timeout',
        'write timeout',
        'connection aborted',
    ]
    
    for keyword in retryable_keywords:
        if keyword in error_str:
            return True
    
    # Non-retryable errors
    non_retryable_keywords = [
        'unauthorized',
        'forbidden',
        'not found',
        'permission denied',
        'invalid token',
        'bad request',
        'invalid file',
    ]
    
    for keyword in non_retryable_keywords:
        if keyword in error_str:
            return False
    
    # Default to retryable for upload errors to be safe
    return True


async def retry_with_backoff(
    func: Callable,
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    user_id: int = None
) -> Any:
    """Retry a function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_attempts: Maximum number of attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        user_id: User ID for logging
    
    Returns:
        Result of function call
        
    Raises:
        Last exception if all attempts fail
    """
    last_error = None
    delay = initial_delay
    
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except Exception as e:
            last_error = e
            
            # Check if error is retryable
            if not is_retryable_error(e):
                logger.warning(f"Non-retryable error for user {user_id}: {e}")
                raise
            
            # Don't retry on last attempt
            if attempt == max_attempts:
                logger.error(f"All {max_attempts} upload attempts failed for user {user_id}: {e}")
                raise
            
            logger.warning(f"Upload attempt {attempt}/{max_attempts} failed for user {user_id}: {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)  # Exponential backoff with cap
    
    # Should never reach here
    raise last_error


def estimate_encoding_sizes(duration_seconds: int) -> dict:
    """Estimate encoded file sizes for different quality levels with 10% safety buffer.
    
    Args:
        duration_seconds: Video duration in seconds
    
    Returns:
        Dict with quality -> estimated_size_mb, or empty dict if duration invalid
    """
    if not duration_seconds or duration_seconds <= 0:
        return {}
    
    duration_minutes = duration_seconds / 60.0
    estimates = {}
    
    for quality, size_per_min in EST_SIZE_PER_MINUTE.items():
        # Calculate base estimate and add 10% safety buffer
        base_estimate = duration_minutes * size_per_min
        estimated_size = base_estimate * 1.1
        estimates[quality] = estimated_size
    
    return estimates


async def show_encoding_choices_keyboard(duration_seconds: int) -> tuple:
    """Generate inline keyboard with encoding quality options.
    
    Filters out options where estimate exceeds 50MB threshold.
    
    Args:
        duration_seconds: Video duration in seconds for size estimation
    
    Returns:
        Tuple of (InlineKeyboardMarkup, dict of available estimates)
    """
    estimates = estimate_encoding_sizes(duration_seconds)
    MAX_SIZE_MB = 50
    
    available_options = []
    available_estimates = {}
    
    for quality in ['720p', '480p', '360p']:
        estimated_size = estimates.get(quality, 0)
        if estimated_size <= MAX_SIZE_MB:
            available_options.append(quality)
            available_estimates[quality] = estimated_size
    
    # Build keyboard buttons
    buttons = []
    for quality in available_options:
        estimated_size = available_estimates[quality]
        label = QUALITY_SETTINGS[quality]['label']
        buttons.append(
            InlineKeyboardButton(
                text=f"{label} (~{estimated_size:.1f}MB)",
                callback_data=f"encode_quality:{quality}"
            )
        )
    
    # Add skip button
    buttons.append(
        InlineKeyboardButton(
            text="⏭️ Skip Encoding",
            callback_data="encode_skip"
        )
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn] for btn in buttons])
    return keyboard, available_estimates


async def upload_original_and_ask_encoding(user_id: int, message: types.Message, state: FSMContext, original_file: str, duration_seconds: int, temp_dir: str) -> bool:
    """Upload original video and show encoding choice keyboard.
    
    Args:
        user_id: User ID
        message: Message object for sending
        state: FSM context
        original_file: Path to original video file
        duration_seconds: Video duration in seconds
        temp_dir: Temporary directory path
    
    Returns:
        True if upload successful, False otherwise
    """
    try:
        # Check file exists
        if not os.path.exists(original_file):
            logger.error(f"Original file not found: {original_file}")
            try:
                await message.answer("❌ Original file not found.")
            except Exception:
                pass
            return False
        
        # Get file size
        file_size_mb = os.path.getsize(original_file) / (1024 * 1024)
        
        # Upload original video with retry logic
        try:
            upload_msg = await message.answer("📤 Uploading original video...")
            
            # Get or create semaphore for this user to limit concurrent uploads
            if user_id not in _upload_semaphores:
                _upload_semaphores[user_id] = asyncio.Semaphore(1)
            
            async with _upload_semaphores[user_id]:
                async def upload_task():
                    return await message.bot.send_video(
                        chat_id=user_id,
                        video=FSInputFile(original_file),
                        caption=f"📹 *Original Video* ({file_size_mb:.1f}MB)\\n\\nChoose encoding quality below or skip.",
                        parse_mode="Markdown"
                    )
                
                video_msg = await retry_with_backoff(upload_task, max_attempts=3, user_id=user_id)
            
            await upload_msg.delete()
        except Exception as e:
            logger.error(f"Failed to upload original for user {user_id}: {e}")
            try:
                await message.answer(f"❌ Upload failed: {str(e)[:100]}")
            except Exception:
                pass
            return False
        
        # Save state data for encoding handler
        try:
            await state.update_data(
                original_file=original_file,
                temp_dir=temp_dir,
                duration_seconds=duration_seconds,
                original_uploaded=True
            )
        except Exception as e:
            logger.error(f"Failed to save state for user {user_id}: {e}")
        
        # Show encoding choices
        keyboard, estimates = await show_encoding_choices_keyboard(duration_seconds)
        
        try:
            await message.answer(
                "⚙️ *Encoding Options*\n\n"
                "Would you like to encode the video to a smaller size?\n\n"
                "_Estimates include 10% safety buffer_",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to show encoding choices for user {user_id}: {e}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error in upload_original_and_ask_encoding for user {user_id}: {e}")
        return False


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
                # Format: Prefer 720p max, mp4 container, prioritize balanced quality/size
                # Fallback chain: 720p+audio > 480p+audio > 360p+audio > best available
                'format': 'best[ext=mp4][height<=720]/bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[height<=720]',
                'quiet': False,
                'no_warnings': False,
                'socket_timeout': 30,
                'noplaylist': True,
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'progress_hooks': [lambda d: asyncio.create_task(
                    update_download_progress(status_msg, d)
                )],
                # Additional optimizations for smaller downloads
                'prefer_free_formats': True,  # Prefer formats without premium/restricted access
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # For Twitter/X URLs, we need to extract only the main tweet, not quoted/replied tweets
                # Use skip_download=True first to get metadata and filter out playlists
                if 'twitter.com' in url or 'x.com' in url:
                    # Extract info without downloading to check if it's a playlist
                    extract_opts = ydl_opts.copy()
                    extract_opts['skip_download'] = True
                    extract_opts['quiet'] = True
                    
                    with yt_dlp.YoutubeDL(extract_opts) as extract_ydl:
                        info = extract_ydl.extract_info(url, download=False)
                    
                    # If it's a playlist, only take the first entry (main tweet)
                    if info.get('_type') == 'playlist' and 'entries' in info:
                        # Use only the first entry's ID
                        main_video_id = info['entries'][0].get('id')
                        if main_video_id:
                            # Construct URL for just the main tweet
                            if 'twitter.com' in url:
                                url = f"https://twitter.com/i/web/status/{main_video_id}"
                            else:
                                url = f"https://x.com/i/web/status/{main_video_id}"
                    elif isinstance(info, dict) and 'id' in info:
                        # Single video, update URL with proper format
                        video_id = info['id']
                        if 'twitter.com' in url:
                            url = f"https://twitter.com/i/web/status/{video_id}"
                        else:
                            url = f"https://x.com/i/web/status/{video_id}"
                
                # Now download with the filtered URL
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
        
        # Fix missing or incorrect extension
        if not ext or ext == '.NA':
            ext = '.mp4'
        
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
            # Only show size estimate once to reduce message updates
            if data.get('_total_bytes_estimate') and not hasattr(update_download_progress, '_size_shown'):
                total_mb = data['_total_bytes_estimate'] / (1024 * 1024)
                try:
                    await message.edit_text(f"⬇️ Downloading video...\n({total_mb:.0f}MB estimated)")
                    update_download_progress._size_shown = True
                except Exception:
                    pass
        elif data['status'] == 'finished':
            # Clear flag for next download
            if hasattr(update_download_progress, '_size_shown'):
                delattr(update_download_progress, '_size_shown')
    except Exception as e:
        logger.debug(f"Progress update error: {str(e)}")


async def encode_with_handbrake(input_file: str, output_file: str, preset: str, status_msg: types.Message, quality: str = '24') -> bool:
    """Encode video using HandBrake CLI for aggressive compression.
    
    Optimized for Raspberry Pi 4 performance and mobile device compatibility.
    Uses lower bitrates and higher quality settings for minimal file size.
    
    Args:
        input_file: Path to input video file
        output_file: Path to output video file
        preset: HandBrake preset (Fast, Balanced, Quality)
        status_msg: Message to update with progress
        quality: Quality setting (e.g., '24', '28', '30'). Higher number = faster/smaller but lower quality.
    """
    try:
        cmd = [
            'HandBrakeCLI',
            '-i', input_file,
            '-o', output_file,
            '--preset', preset,
            '-q', quality,
            '-e', 'x264',
            '--encoder-preset', 'veryfast',  # Faster encoding on Pi
            '-a', '1',
            '-E', 'aac',
            '-B', '96',  # Reduced from 128kbps to 96kbps (sufficient for web video)
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
            await execute_confirmed_download(user_id, message, state, db, url, config, download_states)
        elif file_size > config.MAX_FILE_SIZE:  # file_size > 50MB
            # Get current preset to estimate encoded size
            try:
                preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
            except Exception as e:
                logger.error(f"Failed to get preset for user {user_id}: {e}")
                preset = config.HANDBRAKE_PRESET
            
            estimated_encoded = estimate_encoded_size(duration or 0, preset)
            
            # If estimated encoded size is below limit, skip confirmation and proceed directly to download
            if estimated_encoded <= config.MAX_FILE_SIZE:
                logger.info(f"Large file (source {file_size / (1024*1024):.0f}MB, estimated {estimated_encoded / (1024*1024):.0f}MB encoded) for user {user_id}: proceeding directly to download")
                
                try:
                    await status_msg.edit_text("✅ File size acceptable after encoding.\n\n⬇️ Starting download...")
                except Exception as e:
                    logger.warning(f"Failed to update status for user {user_id}: {e}")
                
                # Proceed directly with download (will ask for quality afterward)
                await execute_confirmed_download(user_id, message, state, db, url, config, download_states)
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
            await execute_confirmed_download(user_id, message, state, db, url, config, download_states)
    
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


async def execute_confirmed_download(user_id: int, message: types.Message, state: FSMContext, db: Database, url: str, config, download_states=None):
    """Execute download after user confirmation (skips file size re-check).
    
    Implements two-stage flow:
    - Small files (≤50MB): Download original → Upload original → Ask for encoding quality
    - Large files (>50MB): Download original → Ask for encoding quality from start (no original upload)
    
    Args:
        user_id: The ID of the user who initiated the download (not from message.from_user)
        message: The message to use for status updates
        state: FSM context
        db: Database instance
        url: The video URL to download
        config: Bot configuration
        download_states: FSM states (optional)
    """
    status_msg = None
    temp_dir = None
    downloaded_file = None
    duration_seconds = 0
    
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
            
            # Get downloaded file size and duration
            try:
                downloaded_size = os.path.getsize(downloaded_file)
                # Try to get duration from state data if available
                state_data = await state.get_data()
                duration_seconds = state_data.get('duration_seconds', 0)
                if not duration_seconds:
                    # If no duration, try to estimate from file size
                    # Rough estimate: 1 minute of video ≈ 2-3 MB
                    duration_seconds = int((downloaded_size / (1024 * 1024)) / 2.5 * 60)
            except Exception as e:
                logger.warning(f"Could not get downloaded file size or duration for user {user_id}: {e}")
                downloaded_size = 0
                duration_seconds = 0
            
            # Two-stage flow decision
            if downloaded_size <= config.MAX_FILE_SIZE:
                # Small file: Upload original first, then ask about encoding
                logger.info(f"Small file ({downloaded_size / (1024*1024):.1f}MB) for user {user_id}: uploading original and asking for encoding")
                
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                
                # Upload original and show encoding choices
                success = await upload_original_and_ask_encoding(user_id, message, state, downloaded_file, duration_seconds, temp_dir)
                if success:
                    if download_states:
                        await state.set_state(download_states.waiting_for_encoding_choice.state)
                        logger.info(f"FSM state set to waiting_for_encoding_choice for user {user_id}")
                    return
                else:
                    logger.error(f"Failed to upload original for user {user_id}")
                    return
            else:
                # Large file: Ask about encoding quality from the start (show keyboard with no original upload)
                logger.info(f"Large file ({downloaded_size / (1024*1024):.1f}MB) for user {user_id}: asking for encoding quality")
                
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                
                # Show encoding choices keyboard
                keyboard, estimates = await show_encoding_choices_keyboard(duration_seconds)
                
                try:
                    await message.answer(
                        "⚙️ *Encoding Required*\n\n"
                        f"📏 Source file: {downloaded_size / (1024*1024):.1f}MB (too large to upload)\n\n"
                        "Choose encoding quality to reduce file size:\n\n"
                        "_Estimates include 10% safety buffer_",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to show encoding choices for user {user_id}: {e}")
                    return
                
                # Save state data for encoding handler
                try:
                    await state.update_data(
                        downloaded_file=downloaded_file,
                        temp_dir=temp_dir,
                        duration_seconds=duration_seconds,
                        original_uploaded=False,
                        url=url
                    )
                except Exception as e:
                    logger.error(f"Failed to save state for user {user_id}: {e}")
                
                if download_states:
                    await state.set_state(download_states.waiting_for_encoding_choice.state)
                    logger.info(f"FSM state set to waiting_for_encoding_choice for user {user_id}")
                return
        
        except Exception as e:
            logger.error(f"Error in execute_confirmed_download: {str(e)}")
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"❌ *Unexpected Error*\n\n"
                        f"Something went wrong: {str(e)[:80]}\n\n"
                        f"Please try again or contact support."
                    )
                except Exception:
                    pass
            await state.clear()
        
        finally:
            # Cleanup temp directory if we got here with an error and haven't saved state
            if temp_dir and downloaded_file:
                try:
                    state_data = await state.get_data()
                    # Only cleanup if state doesn't have the files saved (error case)
                    if 'downloaded_file' not in state_data and 'original_file' not in state_data:
                        import threading
                        def cleanup():
                            try:
                                if os.path.isdir(temp_dir):
                                    shutil.rmtree(temp_dir)
                            except Exception:
                                pass
                        thread = threading.Thread(target=cleanup, daemon=True)
                        thread.start()
                except Exception:
                    pass


async def handle_encoding_choice(user_id: int, message: types.Message, state: FSMContext, db: Database, chosen_quality: str, config, download_states=None):
    """Handle user's encoding quality choice and perform encoding + upload.
    
    Args:
        user_id: User ID
        message: Message object for status updates
        state: FSM context
        db: Database instance
        chosen_quality: Quality choice ('720p', '480p', '360p')
        config: Bot configuration
        download_states: FSM states (optional)
    """
    status_msg = None
    
    try:
        # Get state data
        state_data = await state.get_data()
        downloaded_file = state_data.get('downloaded_file') or state_data.get('original_file')
        temp_dir = state_data.get('temp_dir')
        duration_seconds = state_data.get('duration_seconds', 0)
        original_uploaded = state_data.get('original_uploaded', False)
        
        if not downloaded_file or not temp_dir:
            logger.error(f"Missing state data for user {user_id}: downloaded_file={bool(downloaded_file)}, temp_dir={bool(temp_dir)}")
            try:
                await message.answer("❌ Error: State data missing. Please start again.")
            except Exception:
                pass
            await state.clear()
            return
        
        # Send status message
        try:
            status_msg = await message.answer("⚙️ Encoding started...\n_Processing video, please wait_")
        except Exception as e:
            logger.error(f"Failed to send encoding status to user {user_id}: {e}")
            return
        
        if download_states:
            await state.set_state(download_states.encoding.state)
            logger.info(f"FSM state set to encoding for user {user_id}")
        
        # Get preset
        try:
            preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
        except Exception as e:
            logger.error(f"Failed to get preset for user {user_id}: {e}")
            preset = config.HANDBRAKE_PRESET
        
        # Get quality setting for chosen resolution
        quality_setting = QUALITY_SETTINGS.get(chosen_quality, {}).get('quality', '24')
        
        # Encode with appropriate quality
        output_file = os.path.join(temp_dir, "encoded.mp4")
        logger.info(f"Starting {chosen_quality} encoding for user {user_id} with quality={quality_setting}")
        
        success = await encode_with_handbrake(downloaded_file, output_file, preset, status_msg, quality=quality_setting)
        
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
            await state.clear()
            return
        
        # Check output file size
        try:
            output_size = os.path.getsize(output_file)
            logger.info(f"Encoded {chosen_quality} file size: {output_size / (1024*1024):.1f}MB for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to get output file size for user {user_id}: {e}")
            try:
                await status_msg.edit_text("❌ Error checking encoded file size.")
            except Exception:
                pass
            await state.clear()
            return
        
        # Check if file is too large (allow slight overage, ~55MB instead of 50MB)
        MAX_SIZE_WITH_TOLERANCE = int(config.MAX_FILE_SIZE * 1.1)  # 10% tolerance (55MB)
        if output_size > MAX_SIZE_WITH_TOLERANCE:
            try:
                await status_msg.edit_text(
                    f"❌ *Encoded File Too Large*\n\n"
                    f"📏 Result: {output_size / (1024*1024):.1f}MB (limit ~55MB)\n\n"
                    f"💡 Try:\n"
                    f"• A shorter video\n"
                    f"• A faster preset (Settings → ⚙️)\n"
                    f"• Lower resolution source",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            logger.error(f"Encoded file too large for user {user_id}: {output_size / (1024*1024):.1f}MB")
            await state.clear()
            return
        
        # Uploading
        try:
            await status_msg.edit_text("✅ Encoding done!\n📤 Uploading to Telegram...")
        except Exception as e:
            logger.warning(f"Failed to update status for user {user_id}: {e}")
        
        if download_states:
            await state.set_state(download_states.uploading.state)
            logger.info(f"FSM state set to uploading for user {user_id}")
        
        logger.info(f"Uploading {chosen_quality} encoded file for user {user_id}")
        
        # Defensive check: ensure output file exists before upload
        if not os.path.exists(output_file):
            logger.error(f"Output file does not exist: {output_file}")
            try:
                await status_msg.edit_text(
                    f"❌ *File Error*\n\n"
                    f"The encoded video file was not found.\n\n"
                    f"Please try again or contact support."
                )
            except Exception:
                pass
            await state.clear()
            return
        
        try:
            # Get or create semaphore for this user to limit concurrent uploads
            if user_id not in _upload_semaphores:
                _upload_semaphores[user_id] = asyncio.Semaphore(1)
            
            async with _upload_semaphores[user_id]:
                async def upload_task():
                    return await message.bot.send_video(
                        chat_id=user_id,
                        video=FSInputFile(output_file),
                        caption=f"✅ *Your {chosen_quality} Video*!\n\n📏 Size: {output_size / (1024*1024):.1f}MB",
                        parse_mode="Markdown"
                    )
                
                video_msg = await retry_with_backoff(upload_task, max_attempts=3, user_id=user_id)
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
            await state.clear()
            return
        
        # Success! Delete status message
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
                "🎬 *Done!*\n\n"
                "Your video is ready. You can download another or adjust settings.",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send completion message to user {user_id}: {e}")
        
        await state.clear()
        logger.info(f"Download complete for user {user_id}: {chosen_quality}")
        
    except Exception as e:
        logger.error(f"Error in handle_encoding_choice: {str(e)}")
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"❌ *Unexpected Error*\n\n"
                    f"Something went wrong: {str(e)[:80]}\n\n"
                    f"Please try again or contact support."
                )
            except Exception:
                pass
        await state.clear()
    
    finally:
        # Ensure temp directory cleanup on all paths (success or error)
        try:
            state_data = await state.get_data()
            temp_dir = state_data.get('temp_dir')
            
            if temp_dir and os.path.isdir(temp_dir):
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
        except Exception as e:
            logger.warning(f"Error in finally cleanup for user {user_id}: {e}")


# End of file
