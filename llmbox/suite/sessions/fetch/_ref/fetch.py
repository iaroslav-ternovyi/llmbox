import json
import time


class FetchError(Exception):
    def __init__(self, status, attempts=1):
        super().__init__(f"HTTP {status} after {attempts} attempt(s)")
        self.status, self.attempts = status, attempts


def fetch_json(get, url, sleep=time.sleep, max_wait=10):
    schedule, waited, attempts = [1, 2], 0, 0
    while True:
        attempts += 1
        res = get(url)
        status, headers, text = res if len(res) == 3 else (res[0], {}, res[1])
        if status == 200:
            try:
                return json.loads(text)
            except ValueError:
                raise FetchError(200, attempts) from None
        if not (status >= 500 or status == 429) or attempts >= 3:
            raise FetchError(status, attempts)
        ra = next((v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None)
        wait = float(ra) if (status == 429 and ra is not None) else schedule[attempts - 1]
        if waited + wait > max_wait:
            raise FetchError(status, attempts)
        sleep(wait)
        waited += wait
