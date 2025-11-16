"""Download handler for video downloads."""

import asyncio
import os
import shutil
import tempfile
import subprocess
from typing import Optional
from urllib.parse import urlparse
import yt_dlp

from aiogram import types
from aiogram.fsm.context import FSMContext

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


async def get_file_size(url: str) -> Optional[int]:
    """Extract file size from video metadata using yt-dlp."""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Try to get file size
            filesize = info.get('filesize') or info.get('filesize_approx')
            if filesize:
                return int(filesize)
            
            # Estimate from duration and bitrate
            duration = info.get('duration', 0)
            tbr = info.get('tbr', 0)
            if duration and tbr:
                return int(duration * tbr * 125)  # tbr is in kbit/s
            
            return None
    except Exception as e:
        logger.error(f"Error getting file size: {str(e)}")
        return None


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
            return filename
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


async def process_download(message: types.Message, state: FSMContext, db: Database, url: str, config):
    """Main download processing function."""
    user_id = message.from_user.id
    status_msg = None
    temp_dir = None
    
    try:
        # Validate URL
        if not validate_url(url):
            await message.answer("❌ Invalid URL. Please provide a valid video link.")
            return
        
        # Send status message
        status_msg = await message.answer("🔍 Validating URL...")
        
        # Get file size
        logger.info(f"Checking file size for URL: {url[:50]}...")
        await status_msg.edit_text("📊 Checking video size...")
        
        file_size = await get_file_size(url)
        
        if file_size and file_size > config.MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"❌ Video too large!\n\n"
                f"File size: {file_size / (1024*1024):.1f}MB\n"
                f"Max size: {config.MAX_FILE_SIZE / (1024*1024):.0f}MB\n\n"
                f"Please choose a shorter video or lower quality."
            )
            logger.warning(f"User {user_id} attempted to download {file_size / (1024*1024):.1f}MB file")
            return
        
        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix=f"video_{user_id}_")
        logger.info(f"Created temp directory: {temp_dir}")
        
        # Download video
        await status_msg.edit_text("⬇️ Downloading video...")
        logger.info(f"Starting download for user {user_id}")
        
        downloaded_file = await download_video(url, temp_dir, status_msg)
        logger.info(f"Downloaded: {downloaded_file}")
        
        # Encode video
        await status_msg.edit_text("⚙️ Encoding video (this may take a while)...")
        logger.info(f"Starting encoding for user {user_id}")
        
        output_file = os.path.join(temp_dir, "encoded.mp4")
        preset = await db.get_user_setting(user_id, 'encoding_preset') or config.HANDBRAKE_PRESET
        
        success = await encode_with_handbrake(downloaded_file, output_file, preset, status_msg)
        
        if not success:
            await status_msg.edit_text("❌ Encoding failed. Please try another video.")
            logger.error(f"Encoding failed for user {user_id}")
            return
        
        # Check output file size
        output_size = os.path.getsize(output_file)
        if output_size > config.MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"❌ Encoded file is too large for Telegram!\n\n"
                f"Encoded size: {output_size / (1024*1024):.1f}MB (max 50MB)\n\n"
                f"Try a shorter video or lower quality preset."
            )
            return
        
        # Upload to Telegram
        await status_msg.edit_text("📤 Uploading to Telegram...")
        logger.info(f"Uploading file for user {user_id}")
        
        with open(output_file, 'rb') as video_file:
            await message.bot.send_video(
                chat_id=user_id,
                video=video_file,
                caption="✅ Your video is ready!",
                parse_mode="Markdown"
            )
        
        await status_msg.edit_text("✅ Download complete! Check above for your video.")
        logger.info(f"Successfully processed video for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error processing download: {str(e)}")
        if status_msg:
            await status_msg.edit_text(f"❌ Error: {str(e)[:100]}")
    
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
