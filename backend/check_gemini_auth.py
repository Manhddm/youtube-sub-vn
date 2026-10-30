r"""Safe Gemini API key diagnosis for the Gemini Progressive V2 backend.

Place this file in backend/ beside .env, then run:
  .\.venv\Scripts\python.exe check_gemini_auth.py

Never prints the API key. Makes one metadata request, and (if successful)
one short inference request to the official Gemini API.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import dotenv_values

API = "https://generativelanguage.googleapis.com/v1beta"
ROOT = Path(__file__).resolve().parent


def label(key: str) -> str:
    if key.startswith("AQ."):
        return "Auth key (AQ.)"
    if key.startswith("AIza"):
        return "Standard key (AIza)"
    if not key:
        return "MISSING"
    return "Unrecognized prefix"


def short_error(response: requests.Response, key: str) -> None:
    try:
        root = response.json()
        error = root.get("error", root) if isinstance(root, dict) else {}
        if not isinstance(error, dict):
            error = {}
        reasons = [
            entry.get("reason")
            for entry in error.get("details", [])
            if isinstance(entry, dict) and entry.get("reason")
        ]
        message = str(error.get("message", ""))[:300].replace(key, "[KEY REDACTED]")
        print("  Status:", error.get("status", "unknown"))
        print("  Reason:", ", ".join(reasons) or "not provided")
        print("  Message:", message or "not provided")
    except ValueError:
        print("  Response was not JSON (not displaying raw response).")


def main() -> None:
    print("Gemini Progressive V2 - credential diagnostic")
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("ERROR: No .env found next to this script:", env_file)
        return

    cfg = dotenv_values(env_file, encoding="utf-8-sig")
    file_key = str(cfg.get("GEMINI_API_KEY") or "").strip()
    os_key = os.environ.get("GEMINI_API_KEY")
    # Match backend's load_dotenv(..., override=False) behavior exactly.
    active_key = (os_key if os_key is not None else file_key).strip()
    print(".env key type:", label(file_key), "| length:", len(file_key))
    print("Key selected by backend:", label(active_key), "| length:", len(active_key))
    if os_key is not None:
        if os_key.strip() != file_key:
            print("WARNING: Windows/PowerShell GEMINI_API_KEY overrides .env with a DIFFERENT value.")
            print("Fix: close the server, remove the old environment variable or use")
            print("     load_dotenv(ROOT / '.env', override=True) in app.py.")
        else:
            print("Windows/PowerShell GEMINI_API_KEY matches the .env key.")
    if not active_key or active_key.startswith("PASTE_"):
        print("ERROR: Key is empty or still the template placeholder.")
        return
    if file_key != file_key.strip('"\''):
        print("WARNING: .env key contains a literal quotation mark at the edge.")

    headers = {"x-goog-api-key": active_key}
    try:
        print("\nTest 1: GET /v1beta/models (checks credential recognition)")
        response = requests.get(f"{API}/models", headers=headers, timeout=25)
        print("  HTTP:", response.status_code)
        if response.status_code != 200:
            short_error(response, active_key)
            print("\nRESULT: Authentication fails before model generation. Verify key/project in AI Studio.")
            return
        print("  Models endpoint accepted the key; inference permissions not yet verified.")

        print("\nTest 2: POST /v1beta/interactions (minimal real model request)")
        response = requests.post(
            f"{API}/interactions",
            headers={**headers, "Content-Type": "application/json"},
            json={"model": "gemini-3.8-flash", "input": "Reply with the word OK only."},
            timeout=60,
        )
        print("  HTTP:", response.status_code)
        if response.status_code != 200:
            short_error(response, active_key)
            print("\nTest 3: POST /models/gemini-3.8-flash:generateContent")
            other = requests.post(
                f"{API}/models/gemini-3.8-flash:generateContent",
                headers={**headers, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": "Reply OK."}]}]},
                timeout=60,
            )
            print("  HTTP:", other.status_code)
            if other.status_code != 200:
                short_error(other, active_key)
                print("\nRESULT: A model inference request also failed. Check key/project access.")
            else:
                print("\nRESULT: generateContent works but Interactions does not.")
                print("This narrows the problem to Interactions endpoint/access or SDK config.")
            return
        print("\nRESULT: The key works through the Interactions API.")
        print("If the extension still fails, restart the backend and inspect SDK/environment setup.")
    except requests.RequestException as exc:
        # The request errors should not include credentials (key is in header),
        # but redact defensively if it somehow appears in the exception text.
        print("Network error:", str(exc).replace(active_key, "[KEY REDACTED]")[:400])


if __name__ == "__main__":
    main()
