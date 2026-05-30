"""
check_gemini_api.py -- Probe Gemini grounded-search with the same key + auth path
the pipeline uses.

Run inside the container via a `command:` override on the Container Apps Job —
the bound MI fetches the key from Key Vault, then a direct HTTP POST is sent
to Gemini so the raw response body (status code, error reason, quota metric)
is visible. The engine swallows that body in production; this script does not.

Usage:
    # Build into the image, then PATCH job with command: ["python", "-m", "diagnostics.check_gemini_api"]
    # No CLI args. Uses the same AZURE_KEY_VAULT_URL + AZURE_CLIENT_ID env vars
    # the pipeline already requires.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from market_intel.engine import _resolve_api_key, MODEL


def main() -> int:
    # 1) Fetch the API key via Managed Identity -> Key Vault (same path the pipeline uses).
    try:
        api_key = _resolve_api_key()
    except SystemExit:
        # _resolve_api_key calls sys.exit(1) on missing key — re-raise for visibility.
        return 1
    print(f"[+] Fetched Gemini API key from Key Vault (len={len(api_key)})", flush=True)

    # 2) Send a grounded generateContent call directly via urllib so the raw
    #    HTTP status + body are visible. No SDK exception wrapping.
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"
        f":generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": "What is 2+2? Answer in one word."}]}],
        "tools":    [{"google_search": {}}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"[+] POST {url.split('?')[0]}  (tools=google_search)", flush=True)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        status = resp.status
        body_bytes = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        body_bytes = e.read()
    except Exception as e:
        print(f"[!] Network/transport error: {type(e).__name__}: {e}")
        return 1

    print(f"[+] HTTP {status}")
    try:
        print(json.dumps(json.loads(body_bytes), indent=2))
    except json.JSONDecodeError:
        print(body_bytes.decode(errors="replace"))

    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
