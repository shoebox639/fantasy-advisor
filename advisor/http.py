"""Small JSON-over-HTTP helper (stdlib only, so scheduled runs need no pip install)."""

import json
import time
import urllib.error
import urllib.request

USER_AGENT = "fantasy-advisor/0.1"


def get_json(url, headers=None, cookies=None, retries=3, timeout=30):
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    req_headers.update(headers or {})
    if cookies:
        req_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 4xx won't get better on retry (bad league id, missing cookies, ...).
            if 400 <= e.code < 500:
                raise RuntimeError(f"HTTP {e.code} for {url}") from e
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")
