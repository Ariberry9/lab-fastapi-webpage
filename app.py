"""A simple FastAPI web application with 5 routes."""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = str(BASE_DIR / "site.db")

app = FastAPI()
# Secret key is required for the session middleware (used for login and flash messages).
app.add_middleware(SessionMiddleware, secret_key="lab-secret-key")

# Mount the /static folder so files inside `static/` are served at /static/<file>.
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

# Configure Jinja2 templates (used via templates.TemplateResponse).
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_db_connection() -> sqlite3.Connection:
    """Open a connection to the SQLite database."""
    conn = sqlite3.connect(DB_NAME)
    # Allow accessing columns by name (like a dictionary)
    conn.row_factory = sqlite3.Row
    return conn


def flash(request: Request, message: str) -> None:
    """Store a one-shot message in the session (FastAPI has no built-in flash)."""
    flashes = request.session.get("_flashes", [])
    flashes.append(message)
    request.session["_flashes"] = flashes


def pop_flashes(request: Request) -> list:
    """Return all flash messages and clear them from the session."""
    return request.session.pop("_flashes", [])


def render(request: Request, template_name: str, context: Optional[dict] = None) -> Response:
    """Render a template with the standard context (request + flashes)."""
    ctx = {"request": request, "flashes": pop_flashes(request)}
    if context:
        ctx.update(context)
    return templates.TemplateResponse(template_name, ctx)


@app.get("/", name="index")
def index(request: Request) -> Response:
    """Display all messages sorted with the most recent message at the top."""
    conn = get_db_connection()
    cursor = conn.cursor()
    # Join messages with users so we can show username and age for each message
    cursor.execute(
        """
        SELECT messages.text, messages.timestamp, users.username, users.age
        FROM messages
        JOIN users ON messages.user_id = users.id
        ORDER BY messages.timestamp DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    # Build a list of dictionaries to pass to the template
    messages = [
        {
            "text": row["text"],
            "timestamp": row["timestamp"],
            "username": row["username"],
            "age": row["age"],
        }
        for row in rows
    ]
    return render(request, "index.html", {"messages": messages})


@app.get("/login", name="login")
def login_get(request: Request) -> Response:
    """Show the login form."""
    return render(request, "login.html")


@app.post("/login")
def login_post(request: Request, username: str = Form("")) -> Response:
    """Log in by storing the username in the session."""
    username = username.strip()
    if not username:
        flash(request, "Please enter a username.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    # Check that the user exists in the database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()

    if user is None:
        flash(request, "User does not exist. Please create one first.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    request.session["username"] = username
    flash(request, f"Welcome, {username}!")
    return RedirectResponse(request.url_for("index"), status_code=303)


@app.get("/logout", name="logout")
def logout(request: Request) -> Response:
    """Log the user out by clearing the session."""
    request.session.pop("username", None)
    return render(request, "logout.html")


@app.get("/create_message", name="create_message")
def create_message_get(request: Request) -> Response:
    """Show the create-message form (must be logged in)."""
    if "username" not in request.session:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    return render(request, "create_message.html")


@app.post("/create_message")
def create_message_post(request: Request, text: str = Form("")) -> Response:
    """Allow a logged-in user to create a new message."""
    if "username" not in request.session:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    text = text.strip()
    if not text:
        flash(request, "Message cannot be empty.")
        return RedirectResponse(request.url_for("create_message"), status_code=303)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM users WHERE username = ?",
        (request.session["username"],),
    )
    user = cursor.fetchone()
    if user is None:
        conn.close()
        flash(request, "Logged-in user not found.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO messages (text, timestamp, user_id) VALUES (?, ?, ?)",
        (text, timestamp, user["id"]),
    )
    conn.commit()
    conn.close()
    flash(request, "Message posted.")
    return RedirectResponse(request.url_for("index"), status_code=303)


@app.get("/create_user", name="create_user")
def create_user_get(request: Request) -> Response:
    """Show the create-user form."""
    return render(request, "create_user.html")


@app.post("/create_user")
def create_user_post(
    request: Request,
    username: str = Form(""),
    age: str = Form(""),
) -> Response:
    """Create a new user with a username and age."""
    username = username.strip()
    age_str = age.strip()

    if not username or not age_str:
        flash(request, "Please fill in both fields.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    try:
        age_int = int(age_str)
    except ValueError:
        flash(request, "Age must be a number.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, age) VALUES (?, ?)",
            (username, age_int),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        flash(request, "Username already exists.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)
    conn.close()

    flash(request, f"User '{username}' created. You can now log in.")
    return RedirectResponse(request.url_for("login"), status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
