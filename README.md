# FastAPI Lab Project

A simple FastAPI web application built for the lab assignment.

## Features

- Five routes: `/`, `/login`, `/logout`, `/create_message`, `/create_user`
- All page templates extend a common `base.html`
- SQLite database for users and messages (`db_create.py`)
- Home page (`/`) lists all messages, sorted with the most recent first
- Each message shows: text, timestamp, username, and the user's age
- Static folder contains an image (`logo.svg`) and a stylesheet (`style.css`)
- The base template loads the stylesheet so every page is styled

## Project Structure

```
fastapi_website/
├── app.py
├── db_create.py
├── requirements.txt
├── README.md
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── logout.html
│   ├── create_message.html
│   └── create_user.html
└── static/
    ├── style.css
    └── logo.svg
```

## How to Run

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create the database (only needs to be done once):

   ```bash
   python db_create.py
   ```

3. Start the FastAPI development server (either works):

   ```bash
   # Option A: run via uvicorn (recommended)
   uvicorn app:app --reload

   # Option B: run the script directly
   python app.py
   ```

4. Open your browser and visit `http://127.0.0.1:8000/`.

## Usage

- Visit `/create_user` to create a new user (e.g. username `alice`, age `22`).
- Visit `/login` to log in with that username.
- Visit `/create_message` to post a message.
- Go back to `/` to see your message at the top of the list.
- Click `Logout` in the navigation to log out.

The database is pre-populated with sample users (`alice`, `bob`, `charlie`)
and a few example messages, so you can see the home page working right away.

## Screenshot

> Add a screenshot of the `/` route here once the app is running.

## Submission Notes

1. Add a screenshot of the working `/` route to this README.
2. Create a new branch named `lab-submission` containing all of the lab code.
3. Submit the link to that branch on Canvas.
4. Do **not** modify the `lab-submission` branch after submitting; continue
   any further work on the `main` / `master` branch.
