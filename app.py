"""A simple Flask web application with 5 routes."""
import sqlite3
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)

app = Flask(__name__)
# Secret key is required for session and flash messages.
app.secret_key = "lab-secret-key"

DB_NAME = "site.db"


def get_db_connection():
    """Open a connection to the SQLite database."""
    conn = sqlite3.connect(DB_NAME)
    # Allow accessing columns by name (like a dictionary)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    """Display all messages sorted with the most recent message at the top."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # Join messages with users so we can show username and age for each message
    cursor.execute("""
        SELECT messages.text, messages.timestamp, users.username, users.age
        FROM messages
        JOIN users ON messages.user_id = users.id
        ORDER BY messages.timestamp DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    # Build a list of dictionaries to pass to the template
    messages = []
    for row in rows:
        messages.append({
            "text": row["text"],
            "timestamp": row["timestamp"],
            "username": row["username"],
            "age": row["age"],
        })

    return render_template("index.html", messages=messages)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log in by storing the username in the session."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if not username:
            flash("Please enter a username.")
            return redirect(url_for("login"))

        # Check that the user exists in the database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()

        if user is None:
            flash("User does not exist. Please create one first.")
            return redirect(url_for("login"))

        session["username"] = username
        flash(f"Welcome, {username}!")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Log the user out by clearing the session."""
    session.pop("username", None)
    return render_template("logout.html")


@app.route("/create_message", methods=["GET", "POST"])
def create_message():
    """Allow a logged-in user to create a new message."""
    if "username" not in session:
        flash("Please log in first.")
        return redirect(url_for("login"))

    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if not text:
            flash("Message cannot be empty.")
            return redirect(url_for("create_message"))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM users WHERE username = ?",
            (session["username"],),
        )
        user = cursor.fetchone()
        if user is None:
            conn.close()
            flash("Logged-in user not found.")
            return redirect(url_for("login"))

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO messages (text, timestamp, user_id) VALUES (?, ?, ?)",
            (text, timestamp, user["id"]),
        )
        conn.commit()
        conn.close()
        flash("Message posted.")
        return redirect(url_for("index"))

    return render_template("create_message.html")


@app.route("/create_user", methods=["GET", "POST"])
def create_user():
    """Create a new user with a username and age."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        age_str = request.form.get("age", "").strip()

        if not username or not age_str:
            flash("Please fill in both fields.")
            return redirect(url_for("create_user"))

        try:
            age = int(age_str)
        except ValueError:
            flash("Age must be a number.")
            return redirect(url_for("create_user"))

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, age) VALUES (?, ?)",
                (username, age),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("Username already exists.")
            return redirect(url_for("create_user"))
        conn.close()

        flash(f"User '{username}' created. You can now log in.")
        return redirect(url_for("login"))

    return render_template("create_user.html")


if __name__ == "__main__":
    app.run(debug=True)
