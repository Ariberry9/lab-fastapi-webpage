"""Create the SQLite database with sample users and messages."""
import sqlite3
from datetime import datetime, timedelta

DB_NAME = "site.db"


def create_database():
    """Drop existing tables and create fresh users and messages tables with sample data."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Drop existing tables so we can re-run this script safely
    cursor.execute("DROP TABLE IF EXISTS messages")
    cursor.execute("DROP TABLE IF EXISTS users")

    # Create users table
    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            age INTEGER NOT NULL
        )
    """)

    # Create messages table (linked to users by user_id)
    cursor.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Insert sample users
    sample_users = [
        ("alice", 22),
        ("bob", 25),
        ("charlie", 19),
    ]
    cursor.executemany(
        "INSERT INTO users (username, age) VALUES (?, ?)",
        sample_users,
    )

    # Insert sample messages with different timestamps so we can see sorting
    now = datetime.now()
    sample_messages = [
        ("Hello, this is my first message!",
         (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"), 1),
        ("Flask is fun to learn.",
         (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), 2),
        ("Anyone working on the lab?",
         (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), 3),
        ("Almost done with my homework.",
         now.strftime("%Y-%m-%d %H:%M:%S"), 1),
    ]
    cursor.executemany(
        "INSERT INTO messages (text, timestamp, user_id) VALUES (?, ?, ?)",
        sample_messages,
    )

    conn.commit()
    conn.close()
    print(f"Database '{DB_NAME}' created successfully.")


if __name__ == "__main__":
    create_database()
