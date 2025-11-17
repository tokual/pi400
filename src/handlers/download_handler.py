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
    """Download and optimize video using yt-dlp with native format selection.
    
    Uses dynamic resolution selection based on video duration:
    - ≤60s: 720p H.264 + AAC at 5Mbps (for quality)
    - >60s: 480p H.264 + AAC at 4Mbps (for smaller file size)
    
    Downloads directly as MP4 with H.264/AAC for Telegram compatibility.
    No post-processing/re-encoding needed - stream copy only (remuxing).
    
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
        
        async def _get_duration():
            """Pre-fetch video duration for dynamic format selection."""
            try:
                with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info.get('duration', 120)  # Default 120s if unknown
            except Exception as e:
                logger.warning(f"Could not get duration: {e}. Defaulting to 480p.")
                return 120  # Default to longer duration assumption (480p)
        
        async def _download(duration_seconds: int):
            # Local variable to avoid Python 3.13 scoping issues with nested async functions
            download_url = url
            
            # Dynamically select resolution based on duration
            # Short videos (≤60s): 720p for better quality
            # Long videos (>60s): 480p for reasonable file size
            if duration_seconds <= 60:
                max_height = 720
                bitrate_limit = '2M'  # 2Mbps for short videos (aligned with Telegram iOS)
                logger.info(f"Short video ({duration_seconds}s): using 720p + 2Mbps")
            else:
                max_height = 480
                bitrate_limit = '1.6M'  # 1.6Mbps for longer videos (Telegram 480p standard)
                logger.info(f"Long video ({duration_seconds}s): using 480p + 1.6Mbps")
            
            # Format selection: H.264 video + AAC audio (no VBR filter - too restrictive for YouTube)
            # bestvideo: Best H.264 video track at specified height
            # bestaudio[acodec=aac]: Best AAC audio track
            # /best: Fallback if separate streams not available
            format_str = f'bestvideo[vcodec=h264][height<={max_height}]+bestaudio[acodec=aac]/best[ext=mp4]'
            
            ydl_opts = {
                'format': format_str,
                'remux_video': 'mp4',  # Stream copy to MP4 container (no re-encoding)
                'postprocessor_args': {
                    'ffmpeg': ['-b:v', bitrate_limit]  # Limit video bitrate via ffmpeg
                },
                'quiet': False,
                'no_warnings': False,
                'socket_timeout': 30,
                'playlist_items': '1',  # For quote tweets: take only first video
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'progress_hooks': [lambda d: asyncio.create_task(
                    update_download_progress(status_msg, d)
                )],
                'prefer_free_formats': True,  # Prefer formats without premium/restricted access
                'extractor_args': {
                    'youtube': {
                        'skip': ['hls', 'dash']  # Skip HLS/DASH - use direct formats
                    }
                }
            }
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Download with optimized format selection (no post-processing needed)
                    info = ydl.extract_info(download_url, download=True)
                    filename = ydl.prepare_filename(info)
                    return filename
            except Exception as e:
                # If primary download fails (e.g., age-restricted content), try fallback
                logger.warning(f"Primary download attempt failed: {e}. Retrying with fallback options...")
                
                # Fallback 1: Try without height restrictions
                fallback_opts_1 = ydl_opts.copy()
                fallback_opts_1['quiet'] = True
                fallback_opts_1['format'] = f'bestvideo[vcodec=h264]+bestaudio[acodec=aac]/best[ext=mp4]'
                
                try:
                    with yt_dlp.YoutubeDL(fallback_opts_1) as ydl:
                        info = ydl.extract_info(download_url, download=True)
                        filename = ydl.prepare_filename(info)
                        logger.info(f"Fallback 1 (no height restriction) successful")
                        return filename
                except Exception as e2:
                    logger.warning(f"Fallback 1 failed: {e2}. Trying aggressive fallback...")
                    
                    # Fallback 2: Very aggressive - just get any playable format
                    fallback_opts_2 = {
                        'format': 'best',
                        'quiet': True,
                        'socket_timeout': 30,
                        'playlist_items': '1',
                        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                        'prefer_free_formats': True,
                    }
                    
                    try:
                        with yt_dlp.YoutubeDL(fallback_opts_2) as ydl:
                            info = ydl.extract_info(download_url, download=True)
                            filename = ydl.prepare_filename(info)
                            logger.info(f"Fallback 2 (best format) successful")
                            return filename
                    except Exception as e3:
                        logger.error(f"All download attempts failed: {e3}")
                except Exception as fallback_error:
                    # Both attempts failed
                    logger.error(f"Fallback download also failed: {fallback_error}")
                    raise
        
        # Get video duration first
        duration_seconds = await asyncio.wait_for(_get_duration(), timeout=60)
        
        # Apply timeout to download operation
        try:
            filename = await asyncio.wait_for(_download(duration_seconds), timeout=timeout)
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
        
        # Sanitize the filename for compatibility
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
    
    Downloads video and uploads directly to Telegram. Video is already optimized by yt-dlp
    with dynamic resolution/bitrate selection (720p for ≤60s, 480p for >60s).
    
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
            
            # Upload the optimized video directly
            logger.info(f"Uploading optimized video ({downloaded_size / (1024*1024):.1f}MB) for user {user_id}")
            
            try:
                await status_msg.delete()
            except Exception:
                pass
            
            # File is already optimized by yt-dlp with dynamic resolution/bitrate
            output_file = downloaded_file
            
            # Check file size
            try:
                output_size = os.path.getsize(output_file)
                logger.info(f"Uploading optimized video: {output_size / (1024*1024):.1f}MB for user {user_id}")
            except Exception as e:
                logger.error(f"Failed to get file size for user {user_id}: {e}")
                try:
                    await message.answer("❌ Error checking video file size.")
                except Exception:
                    pass
                await state.clear()
                return
            
            # Check if file is too large
            if output_size > int(config.MAX_FILE_SIZE * 1.1):
                try:
                    await message.answer(
                        f"❌ *Video Too Large*\n\n"
                        f"📏 Size: {output_size / (1024*1024):.1f}MB (limit ~55MB)\n\n"
                        f"💡 Try a shorter video or different source",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                await state.clear()
                return
            
            # Send status
            try:
                status_msg = await message.answer("✅ Download successful!\n📤 Uploading to Telegram...")
            except Exception as e:
                logger.error(f"Failed to send upload status to user {user_id}: {e}")
                return
            
            # Upload video
            try:
                if user_id not in _upload_semaphores:
                    _upload_semaphores[user_id] = asyncio.Semaphore(1)
                
                async with _upload_semaphores[user_id]:
                    async def upload_task():
                        return await message.bot.send_video(
                            chat_id=user_id,
                            video=FSInputFile(output_file),
                            caption=f"✅ *Video Downloaded*\n\n📏 Size: {output_size / (1024*1024):.1f}MB",
                            parse_mode="Markdown"
                        )
                    
                    logger.info(f"Starting upload for optimized file ({output_size / (1024*1024):.1f}MB)")
                    video_msg = await retry_with_backoff(upload_task, max_attempts=3, user_id=user_id)
                    logger.info(f"Successfully uploaded video for user {user_id}")
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
            
            # Send completion
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬇️ Download Another", callback_data="start_download")],
                [InlineKeyboardButton(text="⚙️ Settings", callback_data="show_settings")],
            ])
            
            try:
                await message.answer(
                    "🎬 *Done!*\n\n"
                    "Your video is ready. Download another or return to menu.",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send completion message to user {user_id}: {e}")
            
            await state.clear()
            
            # Cleanup temp directory
            try:
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
            except Exception:
                pass
        
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
            # Cleanup temp directory if we got here with an error
            if temp_dir:
                try:
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


# End of file
