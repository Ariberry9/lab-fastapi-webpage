"""FastAPI lab project (extended).

Implements:
- Required tasks 1-5 (home, login, logout, create user, create message)
- 3-point optional tasks 1, 2, 3, 4, 5, 7, 9, 10
- 6-point optional tasks 1, 2, 4, 8 (FTS5 search for the +2 EC)

Security stance:
- Every SQL statement is parameterised (no string concatenation).
- Jinja2 autoescape is on by default; user input is never marked |safe
  unless it has been sanitised by `render_message_html` (markdown -> bleach).
- Edit / delete routes verify that the logged-in user owns the message.
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import bcrypt
import bleach
import markdown as md
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from avatar import ensure_avatar

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = str(BASE_DIR / "site.db")
AVATAR_DIR = BASE_DIR / "static" / "avatars"
PAGE_SIZE = 50

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

# bcrypt only inspects the first 72 bytes, so we truncate explicitly
# to stay compatible with bcrypt 5.x which raises on longer input.

# Markdown converter: only the "safe" extensions we actually want.
_md = md.Markdown(extensions=["fenced_code", "tables", "sane_lists"])

# HTML tags / attributes we allow after markdown rendering.
ALLOWED_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "em", "i",
    "li", "ol", "p", "pre", "strong", "ul",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "abbr": ["title"],
}
# bleach.linkify auto-converts plain URLs in messages to <a> tags
# (covers 3-point optional task 2).


def get_db_connection() -> sqlite3.Connection:
    """Open a connection to the SQLite database with row-as-dict access."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


# --- Password helpers ---------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    try:
        pwd_bytes = password.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd_bytes, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash in the DB: treat as a failed login rather than 500.
        return False


# --- Flash / current user helpers --------------------------------------

def flash(request: Request, message: str) -> None:
    """Store a one-shot message in the session (FastAPI has no built-in flash)."""
    flashes = request.session.get("_flashes", [])
    flashes.append(message)
    request.session["_flashes"] = flashes


def pop_flashes(request: Request) -> list:
    """Return all flash messages and clear them from the session."""
    return request.session.pop("_flashes", [])


def get_current_user(request: Request) -> Optional[sqlite3.Row]:
    """Return the logged-in user row (id/username/age/profile_description) or None."""
    username = request.session.get("username")
    if not username:
        return None
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, age, profile_description
            FROM users WHERE username = ?
            """,
            (username,),
        )
        return cursor.fetchone()
    finally:
        conn.close()


# --- Markdown rendering pipeline ---------------------------------------

def render_message_html(text: str) -> str:
    """Convert message text -> sanitized HTML.

    Pipeline:
      1. Render markdown -> HTML
      2. bleach.clean with a strict allow-list (drops <script>, etc.)
      3. bleach Linker auto-converts bare URLs to <a> tags
    """
    _md.reset()
    raw_html = _md.convert(text or "")
    cleaned = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        strip=True,
    )
    return bleach.linkify(cleaned)


def message_row_to_dict(row: sqlite3.Row, current_user_id: Optional[int]) -> dict:
    """Convert a joined messages+users row into the dict our templates expect."""
    return {
        "id": row["id"],
        "text": row["text"],
        "html": render_message_html(row["text"]),
        "timestamp": row["timestamp"],
        "edited_at": row["edited_at"],
        "username": row["username"],
        "age": row["age"],
        "user_id": row["user_id"],
        "is_owner": current_user_id is not None and row["user_id"] == current_user_id,
    }


# --- Template rendering -------------------------------------------------

def render(request: Request, template_name: str, context: Optional[dict] = None) -> Response:
    """Render a template with the standard context (request + flashes + current user)."""
    ctx = {
        "request": request,
        "flashes": pop_flashes(request),
        "current_user": get_current_user(request),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(template_name, ctx)


# --- Validation helpers -------------------------------------------------

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def validate_username(username: str) -> Optional[str]:
    """Return None if valid; otherwise an error message."""
    if not USERNAME_RE.match(username):
        return (
            "Username must be 3-32 characters and contain only letters, "
            "digits, or underscores."
        )
    return None


def validate_password(password: str) -> Optional[str]:
    """Return None if valid; otherwise an error message."""
    if len(password) < 6:
        return "Password must be at least 6 characters long."
    return None


# =======================================================================
# Required tasks
# =======================================================================

@app.get("/", name="index")
def index(request: Request, page: str = "1") -> Response:
    """Display 50 messages per page, newest first; supports ?page=N navigation."""
    # Accept "page" as a string so bad input never triggers a 422 from
    # FastAPI's int validator. Any unparseable value falls back to page 1.
    try:
        page_num = int(page)
    except (TypeError, ValueError):
        page_num = 1
    if page_num < 1:
        page_num = 1
    page = page_num
    offset = (page - 1) * PAGE_SIZE

    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM messages")
        total_messages = cursor.fetchone()["cnt"]

        # Use LIMIT + OFFSET so SQLite paginates efficiently (6-point task 1).
        cursor.execute(
            """
            SELECT messages.id, messages.text, messages.timestamp,
                   messages.edited_at, messages.user_id,
                   users.username, users.age
            FROM messages
            JOIN users ON messages.user_id = users.id
            ORDER BY messages.timestamp DESC, messages.id DESC
            LIMIT ? OFFSET ?
            """,
            (PAGE_SIZE, offset),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    messages = [message_row_to_dict(row, current_user_id) for row in rows]
    total_pages = max(1, (total_messages + PAGE_SIZE - 1) // PAGE_SIZE)

    return render(request, "index.html", {
        "messages": messages,
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "total_messages": total_messages,
    })


@app.get("/login", name="login")
def login_get(request: Request) -> Response:
    """Show the login form."""
    if get_current_user(request):
        return RedirectResponse(request.url_for("index"), status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
) -> Response:
    """Authenticate a user using username + password."""
    username = username.strip()

    if not username or not password:
        flash(request, "Please enter both your username and password.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        )
        user = cursor.fetchone()
    finally:
        conn.close()

    # Identical error message for both cases keeps us from leaking which
    # half (username vs password) is wrong.
    if user is None or not verify_password(password, user["password_hash"]):
        flash(request, "Invalid username or password.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    request.session["username"] = user["username"]
    flash(request, f"Welcome, {user['username']}!")
    return RedirectResponse(request.url_for("index"), status_code=303)


@app.get("/logout", name="logout")
def logout(request: Request) -> Response:
    """Log the user out by clearing the session cookie data."""
    request.session.pop("username", None)
    return render(request, "logout.html")


@app.get("/create_user", name="create_user")
def create_user_get(request: Request) -> Response:
    """Show the create-user form."""
    if get_current_user(request):
        return RedirectResponse(request.url_for("index"), status_code=303)
    return render(request, "create_user.html")


@app.post("/create_user")
def create_user_post(
    request: Request,
    username: str = Form(""),
    age: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
) -> Response:
    """Create a new user, then log them in and redirect to the home page."""
    username = username.strip()
    age_str = age.strip()

    if not username or not age_str or not password or not password_confirm:
        flash(request, "Please fill in every field.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    err = validate_username(username)
    if err:
        flash(request, err)
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    try:
        age_int = int(age_str)
    except ValueError:
        flash(request, "Age must be a whole number.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)
    if age_int < 1 or age_int > 150:
        flash(request, "Age must be between 1 and 150.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    if password != password_confirm:
        flash(request, "The two password fields do not match.")
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    err = validate_password(password)
    if err:
        flash(request, err)
        return RedirectResponse(request.url_for("create_user"), status_code=303)

    password_hash = hash_password(password)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO users (username, age, password_hash, profile_description)
                VALUES (?, ?, ?, '')
                """,
                (username, age_int, password_hash),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash(request, f"The username '{username}' is already taken.")
            return RedirectResponse(request.url_for("create_user"), status_code=303)
    finally:
        conn.close()

    # Download this user's robohash kitten so the avatar is served locally
    # from now on. Falls back to the local _default.png if the download
    # fails, so the request never blocks for long.
    ensure_avatar(username, AVATAR_DIR)

    # 3-point task 7: auto-login after sign-up and redirect to the home page.
    request.session["username"] = username
    flash(request, f"Account created. Welcome, {username}!")
    return RedirectResponse(request.url_for("index"), status_code=303)


@app.get("/create_message", name="create_message")
def create_message_get(request: Request) -> Response:
    """Show the create-message form (must be logged in)."""
    if not get_current_user(request):
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    return render(request, "create_message.html")


@app.post("/create_message")
def create_message_post(request: Request, text: str = Form("")) -> Response:
    """Allow a logged-in user to create a new message."""
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    text = text.strip()
    if not text:
        flash(request, "Message cannot be empty.")
        return RedirectResponse(request.url_for("create_message"), status_code=303)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (text, timestamp, user_id) VALUES (?, ?, ?)",
            (text, timestamp, user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    flash(request, "Message posted.")
    return RedirectResponse(request.url_for("index"), status_code=303)


# =======================================================================
# Optional 3-point tasks
# =======================================================================

# --- Task 4 + 5: edit & delete a message --------------------------------

def _parse_message_id(raw: str) -> Optional[int]:
    """Convert a path-parameter string to a positive int, or None."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _load_owned_message(message_id: int, user_id: int) -> Optional[sqlite3.Row]:
    """Return the message row only if it exists AND belongs to user_id."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, text, user_id FROM messages WHERE id = ?",
            (message_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if row is None or row["user_id"] != user_id:
        return None
    return row


@app.get("/messages/{message_id}/edit", name="edit_message")
def edit_message_get(request: Request, message_id: str) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    mid = _parse_message_id(message_id)
    if mid is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    message = _load_owned_message(mid, user["id"])
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return render(request, "edit_message.html", {"message": message})


@app.post("/messages/{message_id}/edit")
def edit_message_post(
    request: Request,
    message_id: str,
    text: str = Form(""),
) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    mid = _parse_message_id(message_id)
    if mid is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    message = _load_owned_message(mid, user["id"])
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")

    text = text.strip()
    if not text:
        flash(request, "Message cannot be empty.")
        return RedirectResponse(
            request.url_for("edit_message", message_id=mid),
            status_code=303,
        )

    edited_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE messages SET text = ?, edited_at = ? WHERE id = ?",
            (text, edited_at, mid),
        )
        conn.commit()
    finally:
        conn.close()

    flash(request, "Message updated.")
    return RedirectResponse(request.url_for("index"), status_code=303)


@app.post("/messages/{message_id}/delete", name="delete_message")
def delete_message_post(request: Request, message_id: str) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    mid = _parse_message_id(message_id)
    if mid is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    message = _load_owned_message(mid, user["id"])
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()

    flash(request, "Message deleted.")
    return RedirectResponse(request.url_for("index"), status_code=303)


# --- Task 10: JSON endpoint --------------------------------------------

@app.get("/messages.json", name="messages_json")
def messages_json(request: Request, page: str = "1") -> Response:
    """Same data the home page renders, but as JSON."""
    try:
        page_num = int(page)
    except (TypeError, ValueError):
        page_num = 1
    if page_num < 1:
        page_num = 1
    page = page_num
    offset = (page - 1) * PAGE_SIZE
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM messages")
        total = cursor.fetchone()["cnt"]
        cursor.execute(
            """
            SELECT messages.id, messages.text, messages.timestamp,
                   messages.edited_at, messages.user_id,
                   users.username, users.age
            FROM messages
            JOIN users ON messages.user_id = users.id
            ORDER BY messages.timestamp DESC, messages.id DESC
            LIMIT ? OFFSET ?
            """,
            (PAGE_SIZE, offset),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    payload = {
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
        "messages": [
            {
                "id": row["id"],
                "text": row["text"],
                "timestamp": row["timestamp"],
                "edited_at": row["edited_at"],
                "username": row["username"],
                "age": row["age"],
            }
            for row in rows
        ],
    }
    return JSONResponse(payload)


# =======================================================================
# Optional 6-point tasks
# =======================================================================

# --- Task 2: user profile pages -----------------------------------------

@app.get("/users/{username}", name="user_profile")
def user_profile(request: Request, username: str) -> Response:
    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, age, profile_description
            FROM users WHERE username = ?
            """,
            (username,),
        )
        profile = cursor.fetchone()
        if profile is None:
            raise HTTPException(status_code=404, detail="User not found.")

        # 50 most recent messages from this user (per the 6-point task 2 spec).
        cursor.execute(
            """
            SELECT messages.id, messages.text, messages.timestamp,
                   messages.edited_at, messages.user_id,
                   users.username, users.age
            FROM messages
            JOIN users ON messages.user_id = users.id
            WHERE users.id = ?
            ORDER BY messages.timestamp DESC, messages.id DESC
            LIMIT 50
            """,
            (profile["id"],),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    messages = [message_row_to_dict(row, current_user_id) for row in rows]
    return render(request, "user_profile.html", {
        "profile": profile,
        "messages": messages,
        "is_self": current_user is not None and current_user["id"] == profile["id"],
    })


@app.get("/profile/edit", name="profile_edit")
def profile_edit_get(request: Request) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    return render(request, "profile_edit.html", {"profile": user})


@app.post("/profile/edit")
def profile_edit_post(
    request: Request,
    profile_description: str = Form(""),
) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    description = profile_description.strip()
    if len(description) > 1000:
        flash(request, "Profile description must be at most 1000 characters.")
        return RedirectResponse(request.url_for("profile_edit"), status_code=303)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET profile_description = ? WHERE id = ?",
            (description, user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    flash(request, "Profile updated.")
    return RedirectResponse(
        request.url_for("user_profile", username=user["username"]),
        status_code=303,
    )


# --- Task 4: search (FTS5) ----------------------------------------------

def _fts_match_query(raw: str) -> str:
    """Build a safe FTS5 MATCH expression from arbitrary user input.

    FTS5 has its own query syntax; passing untrusted input directly causes
    syntax errors (and 500s). We split into word tokens and quote each one,
    which gives a forgiving "all words must match" search.
    """
    tokens = re.findall(r"[A-Za-z0-9_]+", raw)
    if not tokens:
        return ""
    return " ".join(f'"{tok}"' for tok in tokens)


@app.get("/search", name="search")
def search(request: Request, q: str = "") -> Response:
    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    query = q.strip()
    messages = []
    error = None

    if query:
        match_expr = _fts_match_query(query)
        if not match_expr:
            error = "Please enter at least one searchable word."
        else:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT messages.id, messages.text, messages.timestamp,
                           messages.edited_at, messages.user_id,
                           users.username, users.age
                    FROM messages_fts
                    JOIN messages ON messages.id = messages_fts.rowid
                    JOIN users ON messages.user_id = users.id
                    WHERE messages_fts MATCH ?
                    ORDER BY messages.timestamp DESC, messages.id DESC
                    LIMIT 100
                    """,
                    (match_expr,),
                )
                rows = cursor.fetchall()
            finally:
                conn.close()
            messages = [message_row_to_dict(row, current_user_id) for row in rows]

    return render(request, "search.html", {
        "query": query,
        "messages": messages,
        "error": error,
    })


# --- Task 8 (3pt): change password --------------------------------------

@app.get("/account/password", name="change_password")
def change_password_get(request: Request) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    return render(request, "change_password.html")


@app.post("/account/password")
def change_password_post(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    if not old_password or not new_password or not new_password_confirm:
        flash(request, "Please fill in every field.")
        return RedirectResponse(request.url_for("change_password"), status_code=303)

    # Look up the current hash for the logged-in user.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (user["id"],),
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(old_password, row["password_hash"]):
        flash(request, "Your current password is incorrect.")
        return RedirectResponse(request.url_for("change_password"), status_code=303)

    if new_password != new_password_confirm:
        flash(request, "The two new password fields do not match.")
        return RedirectResponse(request.url_for("change_password"), status_code=303)

    err = validate_password(new_password)
    if err:
        flash(request, err)
        return RedirectResponse(request.url_for("change_password"), status_code=303)

    if new_password == old_password:
        flash(request, "Your new password must be different from the old one.")
        return RedirectResponse(request.url_for("change_password"), status_code=303)

    new_hash = hash_password(new_password)
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    flash(request, "Password updated.")
    return RedirectResponse(
        request.url_for("user_profile", username=user["username"]),
        status_code=303,
    )


# --- Task 6 (3pt): delete user account ----------------------------------

@app.get("/account/delete", name="delete_account")
def delete_account_get(request: Request) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)
    return render(request, "delete_account.html", {"profile": user})


@app.post("/account/delete")
def delete_account_post(
    request: Request,
    password: str = Form(""),
    confirm: str = Form(""),
) -> Response:
    user = get_current_user(request)
    if user is None:
        flash(request, "Please log in first.")
        return RedirectResponse(request.url_for("login"), status_code=303)

    if confirm != "DELETE":
        flash(request, 'Please type DELETE in the confirmation field.')
        return RedirectResponse(request.url_for("delete_account"), status_code=303)

    # Re-verify the password before destroying the account.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (user["id"],),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if row is None or not verify_password(password, row["password_hash"]):
        flash(request, "Your password is incorrect.")
        return RedirectResponse(request.url_for("delete_account"), status_code=303)

    # Cascade-delete: remove this user's messages (the messages_ad FTS5
    # trigger will keep the search index in sync), then the user row
    # itself, all in a single transaction so we can never leave a half
    # deleted account behind.
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        cursor.execute("DELETE FROM messages WHERE user_id = ?", (user["id"],))
        cursor.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        conn.commit()
    finally:
        conn.close()

    # Best-effort: remove the per-user avatar PNG so re-registrations
    # later re-download a fresh one.
    avatar_path = AVATAR_DIR / f"{user['username']}.png"
    try:
        if avatar_path.exists() and avatar_path.name != "_default.png":
            avatar_path.unlink()
    except OSError:
        # Don't 500 just because we couldn't unlink a file.
        pass

    request.session.pop("username", None)
    flash(request, "Your account and all of your messages have been deleted.")
    return RedirectResponse(request.url_for("index"), status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
