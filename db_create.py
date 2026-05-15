"""Create the SQLite database with sample users and messages.

This script is rerunnable: it drops existing tables and rebuilds them
with the extended schema required by the lab extension:

- users  : adds password_hash and profile_description
- messages: adds edited_at (NULL when never edited)
- messages_fts: an FTS5 virtual table kept in sync via triggers
- 200 users x 200 messages = 40,000 randomly generated messages,
  including at least one message containing both a single quote (')
  and a double quote (") to satisfy required task 1.v.
"""
import random
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt

from avatar import download_all_avatars

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = str(BASE_DIR / "site.db")
AVATAR_DIR = BASE_DIR / "static" / "avatars"


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt (72-byte safe)."""
    # bcrypt only looks at the first 72 bytes; truncate explicitly so
    # newer bcrypt releases don't raise on long input.
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")

# Reproducible runs while still looking varied.
random.seed(42)

NUM_USERS = 200
MESSAGES_PER_USER = 200

# Four fixed accounts (handy for manual testing) + 196 generated ones.
FIXED_USERS = [
    {
        "username": "alice",
        "age": 22,
        "profile_description": "Hi, I'm Alice. I love FastAPI and good coffee.",
    },
    {
        "username": "bob",
        "age": 25,
        "profile_description": "Bob here. I post about books and bikes.",
    },
    {
        "username": "charlie",
        "age": 19,
        "profile_description": "Charlie - student, gamer, occasional poet.",
    },
    {
        "username": "quote_tester",
        "age": 30,
        "profile_description": "Account used to verify quote handling.",
    },
]

# Pieces used to randomly compose messages.
SUBJECTS = [
    "FastAPI", "SQLite", "Python", "Markdown", "the labs", "this homework",
    "the new feature", "my project", "the deployment", "the database",
    "this bug", "the docs", "the search box", "the profile page",
]
VERBS = [
    "is great because", "feels easier with", "needs work on", "works fine for",
    "surprised me with", "broke when I tried", "shines at",
    "could improve on", "really helps with", "turned out fine for",
]
OBJECTS = [
    "pagination", "Markdown rendering", "user profiles", "the search index",
    "URL auto-linking", "edit history", "delete buttons", "the JSON endpoint",
    "the avatar service", "FTS5 queries", "session cookies",
    "form validation",
]
URL_TEMPLATES = [
    "https://fastapi.tiangolo.com/",
    "https://docs.python.org/3/library/sqlite3.html",
    "https://robohash.org/example",
    "https://www.sqlite.org/fts5.html",
    "https://daringfireball.net/projects/markdown/",
]
MARKDOWN_SNIPPETS = [
    "**bold idea**", "*emphasis*", "`inline code`",
    "> quoted thought", "- a quick bullet point",
]


def random_message_text() -> str:
    """Return a randomly composed message; sometimes contains a URL or markdown."""
    parts = [
        f"{random.choice(SUBJECTS)} {random.choice(VERBS)} {random.choice(OBJECTS)}."
    ]
    roll = random.random()
    if roll < 0.20:
        parts.append(f"See {random.choice(URL_TEMPLATES)} for details.")
    elif roll < 0.35:
        parts.append(f"Note: {random.choice(MARKDOWN_SNIPPETS)}.")
    return " ".join(parts)


def random_timestamp(now: datetime) -> str:
    """A random datetime within the last 60 days, formatted YYYY-MM-DD HH:MM:SS."""
    delta_seconds = random.randint(0, 60 * 24 * 60 * 60)
    return (now - timedelta(seconds=delta_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def create_schema(cursor: sqlite3.Cursor) -> None:
    """Drop and recreate every table / virtual table / trigger we own."""
    cursor.execute("DROP TRIGGER IF EXISTS messages_ai")
    cursor.execute("DROP TRIGGER IF EXISTS messages_ad")
    cursor.execute("DROP TRIGGER IF EXISTS messages_au")
    cursor.execute("DROP TABLE IF EXISTS messages_fts")
    cursor.execute("DROP TABLE IF EXISTS messages")
    cursor.execute("DROP TABLE IF EXISTS users")

    cursor.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            age INTEGER NOT NULL,
            password_hash TEXT NOT NULL,
            profile_description TEXT NOT NULL DEFAULT ''
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            edited_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX idx_messages_timestamp ON messages(timestamp DESC)"
    )
    cursor.execute(
        "CREATE INDEX idx_messages_user_id ON messages(user_id)"
    )

    # FTS5 virtual table mirrors messages.text so /search can use MATCH.
    cursor.execute(
        """
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            text,
            content='messages',
            content_rowid='id'
        )
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
        END
        """
    )
    cursor.execute(
        """
        CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )


def insert_users(cursor: sqlite3.Cursor) -> list:
    """Insert the fixed test accounts and 196 generated users; return user ids."""
    default_hash = hash_password("password")

    rows = []
    for fixed in FIXED_USERS:
        rows.append((
            fixed["username"],
            fixed["age"],
            default_hash,
            fixed["profile_description"],
        ))

    needed = NUM_USERS - len(FIXED_USERS)
    for i in range(1, needed + 1):
        rows.append((
            f"user{i:03d}",
            random.randint(18, 60),
            default_hash,
            "",
        ))

    cursor.executemany(
        """
        INSERT INTO users (username, age, password_hash, profile_description)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )

    cursor.execute("SELECT id FROM users ORDER BY id")
    return [row[0] for row in cursor.fetchall()]


def insert_messages(cursor: sqlite3.Cursor, user_ids: list) -> None:
    """Insert MESSAGES_PER_USER messages per user, plus a few special fixtures."""
    now = datetime.now()

    # Fixture #1 (required task 1.v): one message containing BOTH ' and ".
    # We attribute this to the dedicated quote_tester account.
    cursor.execute(
        "SELECT id FROM users WHERE username = ?",
        ("quote_tester",),
    )
    quote_tester_id = cursor.fetchone()[0]

    # Build the bulk batch.
    rows = []
    for user_id in user_ids:
        for _ in range(MESSAGES_PER_USER):
            rows.append((
                random_message_text(),
                random_timestamp(now),
                user_id,
            ))

    # Replace ONE of quote_tester's slots with the special quote message.
    quote_message = (
        """It's a "must read": Bob said 'hi' and I said "hello"."""
        " Quotes ' and \" should both render safely."
    )
    for idx in range(len(rows) - 1, -1, -1):
        if rows[idx][2] == quote_tester_id:
            rows[idx] = (quote_message, now.strftime("%Y-%m-%d %H:%M:%S"), quote_tester_id)
            break

    cursor.executemany(
        "INSERT INTO messages (text, timestamp, user_id) VALUES (?, ?, ?)",
        rows,
    )


def create_database() -> None:
    """Build the database from scratch and grab every user's kitten avatar."""
    start = time.perf_counter()
    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.cursor()
        # Speed up the bulk insert; we do a single commit at the end.
        cursor.execute("PRAGMA journal_mode = MEMORY")
        cursor.execute("PRAGMA synchronous = OFF")

        create_schema(cursor)
        user_ids = insert_users(cursor)
        insert_messages(cursor, user_ids)

        # Collect every username we just inserted so we can pre-download
        # a unique kitten avatar for each one.
        cursor.execute("SELECT username FROM users")
        all_usernames = [row[0] for row in cursor.fetchall()]

        conn.commit()
    finally:
        conn.close()

    db_elapsed = time.perf_counter() - start
    total_messages = NUM_USERS * MESSAGES_PER_USER
    print(
        f"Database '{DB_NAME}' created with {NUM_USERS} users and "
        f"{total_messages} messages in {db_elapsed:.1f} s."
    )

    print(f"Downloading {len(all_usernames)} kitten avatars to '{AVATAR_DIR}'...")
    avatar_start = time.perf_counter()
    summary = download_all_avatars(all_usernames, AVATAR_DIR, max_workers=12)
    avatar_elapsed = time.perf_counter() - avatar_start
    print(
        f"  ok={summary['ok']} fallback={summary['fallback']} "
        f"missing={summary['missing']} (in {avatar_elapsed:.1f} s)"
    )
    print("All accounts use the password: password")


if __name__ == "__main__":
    create_database()
