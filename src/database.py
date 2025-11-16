"""Database management using SQLite and aiosqlite."""

import aiosqlite
import os
from datetime import datetime, timedelta
from typing import Optional, List

DB_PATH = os.getenv('DATABASE_FILE', '/opt/video-bot/bot.db')


class Database:
    """SQLite database handler with async support."""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.db = None
    
    async def initialize(self):
        """Initialize database and create tables if needed."""
        self.db = await aiosqlite.connect(self.db_path)
        
        # Create tables
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_whitelisted BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, key)
            )
        ''')
        
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                message TEXT
            )
        ''')
        
        await self.db.commit()
        
        # Cleanup old logs (>48h)
        await self.cleanup_old_logs()
    
    async def close(self):
        """Close database connection."""
        if self.db:
            await self.db.close()
    
    async def add_user(self, user_id: int, is_whitelisted: bool = False):
        """Add a user to the database."""
        await self.db.execute(
            'INSERT OR IGNORE INTO users (user_id, is_whitelisted) VALUES (?, ?)',
            (user_id, is_whitelisted)
        )
        await self.db.commit()
    
    async def is_user_whitelisted(self, user_id: int) -> bool:
        """Check if user is whitelisted."""
        cursor = await self.db.execute(
            'SELECT is_whitelisted FROM users WHERE user_id = ?',
            (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else False
    
    async def set_user_setting(self, user_id: int, key: str, value: str):
        """Set a user setting."""
        # Ensure user exists
        await self.add_user(user_id)
        
        await self.db.execute(
            '''INSERT INTO settings (user_id, key, value) 
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, key) DO UPDATE SET value=?, updated_at=CURRENT_TIMESTAMP''',
            (user_id, key, value, value)
        )
        await self.db.commit()
    
    async def get_user_setting(self, user_id: int, key: str) -> Optional[str]:
        """Get a user setting."""
        cursor = await self.db.execute(
            'SELECT value FROM settings WHERE user_id = ? AND key = ?',
            (user_id, key)
        )
        row = await cursor.fetchone()
        return row[0] if row else None
    
    async def log_action(self, level: str, message: str):
        """Log an action to the database."""
        await self.db.execute(
            'INSERT INTO logs (level, message) VALUES (?, ?)',
            (level, message)
        )
        await self.db.commit()
    
    async def cleanup_old_logs(self):
        """Delete logs older than 48 hours."""
        cutoff_time = datetime.utcnow() - timedelta(hours=48)
        await self.db.execute(
            'DELETE FROM logs WHERE timestamp < ?',
            (cutoff_time.isoformat(),)
        )
        await self.db.commit()
