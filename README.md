# Instagram Scraper (Python)

Monolithic Python project that scrapes Instagram profile data from a profile URL. It uses Instaloader (robust and optimized for Instagram scraping) and Playwright for Facebook OAuth login. The codebase follows clean coding practices and a standard folder structure.

## Features
- Extracts core profile fields: `username`, `full_name`, `biography`, `external_url`, `is_verified`, `is_private`, `profile_pic_url`, `followers`, `following`, `posts_count`.
- Retrieves the latest N posts (default 5): `shortcode`, `url`, `date`, `caption`.
- Works without login for public profiles; supports optional login via environment variables to expand data and reduce blocks.
- Simple CLI to run scraping from the terminal and export JSON.

## Requirements
- Python 3.10+
- Dependencies: `instaloader`, `python-dotenv`, `playwright`, `cryptography`

## Installation

```bash
# Create and activate a virtual environment (recommended)
python -m venv .venv
# Windows
.\.venv\Scripts\activate

# Install dependencies
pip install instaloader python-dotenv playwright cryptography

# Install Playwright browser (Chromium)
python -m playwright install chromium
```

Optional: if you prefer `pyproject.toml`, you can install with:

```bash
pip install -e .
```

> Note: Editable mode (`-e`) requires a build backend; this project includes a minimal `pyproject.toml`.

## Usage

### Facebook Authentication (OAuth)

1) Define variables in `.env`:

```
FB_EMAIL=your_fb_email
FB_PASSWORD=your_fb_password
FB_2FA_CODE=123456                # optional if you have 2FA
AUTH_SECRET_KEY=base64_fernet_key # optional to encrypt storage
HEADLESS=true                     # or false to see the browser
AUTH_STORAGE_PATH=storage/auth_state.enc
AUTH_STORAGE_PLAIN_PATH=storage/storage_state.json
LOG_LEVEL=INFO
```

2) Run authentication:

```bash
python main.py auth --headless false
```

This opens the browser, clicks “Log in with Facebook”, fills credentials, handles 2FA if `FB_2FA_CODE` is set, and saves the session state (cookies and storage). If you set `AUTH_SECRET_KEY`, the file is encrypted.

Recent improvements:
- Handles Instagram “Continue” / “Use another profile” screens automatically and accepts cookie banners.
- If a valid session is detected after “Continue”, it saves storage without Facebook login.
- On Facebook, it accepts cookie banners, waits for input visibility, uses alternate selectors, and applies JS fallbacks when overlays block interaction.

### Scraping with Playwright Session

```bash
python main.py scrape --url https://www.instagram.com/<username>/ --posts 5 --output profile.json
```

Uses the authenticated session to query the `web_profile_info` API and fetch profile data and recent posts.

### Alternative Scraping with Instaloader

```bash
python main.py legacy --url https://www.instagram.com/<username>/ --posts 5 --output profile.json --login
```

With `--login` you will use `IG_USERNAME`/`IG_PASSWORD` (and `IG_2FA_CODE` if applicable) from `.env`.

Parameters:
- `--url` (required): Instagram profile URL.
- `--posts` (optional): number of recent posts to fetch (default 5).
- `--output` (optional): output file path to save the JSON.
- `--login` (optional, legacy): attempts login if credentials are provided.

### Environment Variables (full)
Create a `.env` at the project root:

```
# Instagram (Instaloader)
IG_USERNAME=your_username
IG_PASSWORD=your_password
IG_2FA_CODE=123456

# Default posts limit
POSTS_LIMIT=5

# Facebook OAuth (Playwright)
FB_EMAIL=your_fb_email
FB_PASSWORD=your_fb_password
FB_2FA_CODE=123456

# Browser and storage
HEADLESS=true
AUTH_STORAGE_PATH=storage/auth_state.enc
AUTH_STORAGE_PLAIN_PATH=storage/storage_state.json
AUTH_SECRET_KEY=base64_fernet_key
LOG_LEVEL=INFO
```

> Private profiles require login and viewing permissions.

## Project Structure

```
instagram-scraper/
├─ .gitignore
├─ README.md
├─ pyproject.toml
├─ main.py
└─ src/
   └─ instagram_scraper/
      ├─ __init__.py
      ├─ config.py
      ├─ utils.py
      ├─ scraper.py
      ├─ browser_scraper.py
      ├─ auth.py
      └─ cli.py
```

## Libraries and Rationale
- `Playwright` (Python):
  - Manages interactive OAuth flows (redirects to Facebook and returns to Instagram).
  - Supports multi-page, multi-domain navigation with isolated contexts.
  - Enables advanced scraping, including authenticated `fetch` from the browser context.
  - Persists sessions by saving `storage_state` (cookies and localStorage), with encryption support.
- `Instaloader`:
  - Optimized alternative for scraping via Instagram’s internal APIs using direct credentials.
  - Useful when OAuth is not needed.

## Implementation Flow
1. `auth`: open Instagram login, handle “Continue” / cookie banners, click “Log in with Facebook”, fill credentials and 2FA (if applicable), return to Instagram authenticated and save `storage_state` (cookies and storage). If `AUTH_SECRET_KEY` is set, the file is encrypted.
2. `scrape`: decrypt and load `storage_state` if needed, create a context and call `https://www.instagram.com/api/v1/users/web_profile_info/?username=<user>` to fetch profile and posts.
3. Export JSON with standard fields and a post list limited by `--posts` or `POSTS_LIMIT`.

## Error Handling and Logs
- Clear errors when required variables are missing (`FB_EMAIL`, `FB_PASSWORD`).
- Validate `sessionid` cookie after login to confirm authentication.
- Detailed logs with configurable `LOG_LEVEL`.
- Explicit messages when 2FA is required; run with `HEADLESS=false` to manually intervene if needed.

## Credential Security
- Credentials in `.env` (not versioned due to `.gitignore`).
- `AUTH_SECRET_KEY` enables Fernet encryption of the state file (`auth_state.enc`).
- Session files in `storage/` are excluded by `.gitignore`.

## Deployment Notes
- Requires installing Playwright’s browser in the deployment environment (`python -m playwright install chromium`).
- Set environment variables correctly and run `auth` before `scrape`.

## Responsible Scraping
- Respect Instagram and Facebook Terms of Service.
- Use login only on accounts you control.
- Avoid high request rates to prevent blocking.

## License
No specific license; adapt to your needs.