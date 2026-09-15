"""Entry point: ``python -m app serve`` starts the API server; ``python -m app login``
opens NotebookLM for a manual sign-in; ``python -m app import-session`` imports
a session file into the browser profile."""

from __future__ import annotations

import logging
import os
import sys

from app import browser
from app import auth
from app.config import Settings
from app.models import JobStore
from app.runner import JobRunner
from app.server import serve

LOGGER = logging.getLogger("notebooklm")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_server(settings: Settings) -> int:
    store = JobStore(os.path.join(settings.data_dir, "jobs.json"))
    runner = JobRunner(settings, store)
    runner.start()
    httpd = serve(settings, store, runner)
    LOGGER.info(
        "NotebookLM worker listening on %s:%s (session mode %s)",
        settings.bind_ip,
        settings.port,
        settings.session_mode,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping the NotebookLM worker")
    finally:
        httpd.server_close()
        runner.stop()
    return 0


def _login_google(settings) -> int:
    """Run the automated Google credential sign-in flow."""
    from app.browser import _persistent_page, configure_page, signed_in

    if not settings.google_email or not settings.google_password:
        print("Google email and password are required. Set NOTEBOOKLM_GOOGLE_EMAIL"
              " and NOTEBOOKLM_GOOGLE_PASSWORD.", flush=True)
        return 2

    profile_dir = settings.browser_profile or os.path.join(
        settings.data_dir, "notebooklm-browser-profile"
    )
    os.makedirs(profile_dir, exist_ok=True)

    with _persistent_page(settings) as page:
        configure_page(page, settings)
        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        if signed_in(page):
            print("Already signed in to NotebookLM.", flush=True)
            return 0

        result = auth.login_with_credentials(
            page,
            email=settings.google_email,
            password=settings.google_password,
            totp_secret=settings.google_totp_secret,
            interactive=False,
        )

        if result == 0:
            print("Sign-in successful.", flush=True)
            return 0
        else:
            print(
                f"Sign-in needs manual steps (result={result}).\n"
                f"URL: {page.url}\n"
                "Complete the sign-in in the browser, then press Enter here.",
                flush=True,
            )
            input()
            if signed_in(page):
                print("Sign-in confirmed after manual steps.", flush=True)
                return 0
            print("Still not signed in.", flush=True)
            return 1

def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    command = args[0] if args else "serve"
    if command in {"login", "notebooklm-login"}:
        wait = 600
        if len(args) > 1 and str(args[1]).isdigit():
            wait = int(args[1])
        LOGGER.info(
            "Opening NotebookLM for a manual sign-in (mode %s)", settings.session_mode
        )
        return browser.login(settings, wait_seconds=wait)
    if command in {"import-session", "import_session"}:
        if len(args) < 2:
            print("Usage: python -m app import-session <session.json>", file=sys.stderr)
            return 2
        session_path = args[1]
        profile_dir = settings.browser_profile or os.path.join(
            settings.data_dir, "notebooklm-browser-profile"
        )
        LOGGER.info("Importing session from %s into %s", session_path, profile_dir)
        return browser.import_session(profile_dir, session_path)
    if command in {"serve", "run"}:
        return run_server(settings)
    print("Usage: python -m app serve|login [seconds]|import-session <path>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
