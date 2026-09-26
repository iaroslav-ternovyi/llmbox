import os, sys, unittest

TURNS = int(os.environ.get("LLMBOX_TURNS", "5"))
sys.path.insert(0, ".")


def need(n):
    return unittest.skipIf(TURNS < n, f"turn {n} not given")


def server(*responses):
    """responses: (status, text[, headers]); returns get(url) in the shape of the current API and the call log."""
    calls = []

    def get(url):
        calls.append(url)
        r = responses[min(len(calls), len(responses)) - 1]
        status, text = r[0], r[1]
        headers = r[2] if len(r) > 2 else {}
        return (status, headers, text) if TURNS >= 3 else (status, text)
    return get, calls


class Sleeper:
    def __init__(self):
        self.waits = []

    def __call__(self, s):
        self.waits.append(float(s))


def call(get, **kw):
    import fetch
    if TURNS >= 2:
        kw.setdefault("sleep", Sleeper())
    return fetch.fetch_json(get, "https://api.example.com/x", **kw)


class T1_Basic(unittest.TestCase):
    def test_ok(self):
        get, calls = server((200, '{"a": [1, 2]}'))
        self.assertEqual(call(get), {"a": [1, 2]})
        self.assertEqual(calls, ["https://api.example.com/x"])

    def test_error_status(self):
        import fetch
        get, _ = server((404, "not found"))
        with self.assertRaises(fetch.FetchError) as cm:
            call(get)
        self.assertEqual(cm.exception.status, 404)


@need(2)
class T2_Retries(unittest.TestCase):
    def test_recovers(self):
        s = Sleeper()
        get, calls = server((500, "x"), (503, "y"), (200, "[1]"))
        self.assertEqual(call(get, sleep=s), [1])
        self.assertEqual((len(calls), s.waits), (3, [1.0, 2.0]))

    def test_gives_up_after_three(self):
        import fetch
        s = Sleeper()
        get, calls = server((502, "x"))
        with self.assertRaises(fetch.FetchError) as cm:
            call(get, sleep=s)
        self.assertEqual((cm.exception.status, len(calls), s.waits), (502, 3, [1.0, 2.0]))


@need(3)
class T3_RateLimit(unittest.TestCase):
    def test_retry_after(self):
        s = Sleeper()
        get, calls = server((429, "slow down", {"Retry-After": "5"}), (200, '{"ok": true}'))
        self.assertEqual(call(get, sleep=s), {"ok": True})
        self.assertEqual(s.waits, [5.0])

    def test_429_without_header_uses_schedule(self):
        s = Sleeper()
        get, calls = server((429, "slow"), (200, "{}"))
        self.assertEqual(call(get, sleep=s), {})
        self.assertEqual(s.waits, [1.0])


@need(4)
class T4_Errors(unittest.TestCase):
    def test_bad_json(self):
        import fetch
        s = Sleeper()
        get, calls = server((200, "<html>oops</html>"))
        with self.assertRaises(fetch.FetchError) as cm:
            call(get, sleep=s)
        self.assertEqual((cm.exception.status, cm.exception.attempts, len(calls), s.waits), (200, 1, 1, []))

    def test_4xx_no_retry_and_attempts(self):
        import fetch
        s = Sleeper()
        get, calls = server((400, "bad"))
        with self.assertRaises(fetch.FetchError) as cm:
            call(get, sleep=s)
        self.assertEqual((cm.exception.attempts, len(calls)), (1, 1))
        get, calls = server((503, "x"))
        with self.assertRaises(fetch.FetchError) as cm:
            call(get, sleep=Sleeper())
        self.assertEqual(cm.exception.attempts, 3)


@need(5)
class T5_Limits(unittest.TestCase):
    def test_max_wait(self):
        import fetch
        s = Sleeper()
        get, calls = server((429, "slow", {"retry-after": "30"}), (200, "{}"))
        with self.assertRaises(fetch.FetchError) as cm:
            call(get, sleep=s)
        self.assertEqual((cm.exception.status, s.waits, len(calls)), (429, [], 1))

    def test_header_case_and_custom_limit(self):
        s = Sleeper()
        get, calls = server((429, "slow", {"RETRY-AFTER": "3"}), (429, "slow", {"Retry-After": "3"}), (200, "[]"))
        self.assertEqual(call(get, sleep=s, max_wait=6), [])
        self.assertEqual(s.waits, [3.0, 3.0])


if __name__ == "__main__":
    unittest.main()
