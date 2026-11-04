#!/usr/bin/env python3
"""AI provider profiles: precedence, secret handling, and never leaking a key.

The three things tested here are the three that would hurt:

  1. **A key must never leave the server.** The first implementation returned the
     last four characters of the key as a "hint" so a user could tell two keys
     apart. That is a fragment of the secret: it lands in screenshots, in logs,
     in a proxied response. The hint is now a SHA-256 prefix, and this test fails
     if any part of the key appears in the public state.
  2. **Precedence must be predictable.** profile > env > nothing, in that order,
     because the whole point of the UI is that a chosen provider wins.
  3. **Editing without re-typing the key must not wipe it.** A form that posts an
     empty password field is the most common way a stored credential vanishes.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_config  # noqa: E402
import store  # noqa: E402

# A fixture, not a credential: the "test-" marker keeps secret scanners quiet.
SECRET = "sk-test-0123456789abcdef0123456789"


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = (store.DATA_DIR, store.DB_PATH)
        store.DATA_DIR = pathlib.Path(self._tmp.name)
        store.DB_PATH = os.path.join(self._tmp.name, "t.db")
        store.init_db()
        # Environment is part of the precedence chain, so a developer running the
        # tests with a real key exported would change the outcome. Pin it.
        self._env = {k: os.environ.pop(k, None)
                     for k in ai_config.ENV_KEY_NAMES}
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name          # dotfiles in HOME must not matter

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        store.DATA_DIR, store.DB_PATH = self._old
        self._tmp.cleanup()


class PrecedenceTest(_TempDB):
    def test_no_profile_no_env_resolves_to_nothing(self):
        r = ai_config.resolve()
        self.assertIsNone(r["api_key"])
        self.assertIsNone(r["source"])
        self.assertEqual(r["base_url"], ai_config.DEFAULT_BASE.rstrip("/"))

    def test_env_key_is_used_when_no_profile(self):
        os.environ["F1_AI_KEY"] = SECRET
        r = ai_config.resolve()
        self.assertEqual(r["api_key"], SECRET)
        self.assertEqual(r["source"], "env")

    def test_saved_profile_wins_over_env(self):
        os.environ["F1_AI_KEY"] = "env-key-000000"
        ai_config.save_profile(label="mine", base_url="https://api.example.com/v1",
                               model="my-model", api_key=SECRET)
        r = ai_config.resolve()
        self.assertEqual(r["api_key"], SECRET)
        self.assertEqual(r["model"], "my-model")
        self.assertEqual(r["base_url"], "https://api.example.com/v1")
        self.assertEqual(r["source"], "profile")

    def test_profile_without_key_falls_back_to_env(self):
        # A profile carrying only a model name (no key yet) must not disable the
        # assistant: fall through to the environment instead of failing closed.
        os.environ["F1_AI_KEY"] = "env-key-000000"
        ai_config.save_profile(label="no-key", base_url="https://api.example.com/v1",
                               model="m", api_key=None)
        r = ai_config.resolve()
        self.assertEqual(r["source"], "env")
        self.assertEqual(r["api_key"], "env-key-000000")


class SecretHandlingTest(_TempDB):
    def test_public_state_never_contains_the_key(self):
        ai_config.save_profile(label="mine", base_url="https://x/v1",
                               model="m", api_key=SECRET)
        blob = repr(ai_config.public_state())
        self.assertNotIn(SECRET, blob)
        # Not even a tail: the previous implementation leaked the last 4 chars.
        self.assertNotIn(SECRET[-4:], blob)
        self.assertNotIn(SECRET[:8], blob)

    def test_hint_is_a_fingerprint_not_a_fragment(self):
        h = ai_config._hint(SECRET)
        self.assertTrue(h.startswith("sha:"))
        self.assertEqual(h, ai_config._hint(SECRET), "aynı anahtar aynı etiketi vermeli")
        self.assertNotEqual(h, ai_config._hint(SECRET + "x"), "farklı anahtar farklı etiket")
        for i in range(len(SECRET) - 3):
            self.assertNotIn(SECRET[i:i + 4], h)

    def test_short_or_empty_key_has_no_hint(self):
        self.assertEqual(ai_config._hint(""), "—")
        self.assertEqual(ai_config._hint("short"), "—")

    def test_db_file_is_tightened_after_a_key_is_stored(self):
        ai_config.save_profile(label="mine", base_url="https://x/v1",
                               model="m", api_key=SECRET)
        mode = os.stat(store.DB_PATH).st_mode & 0o777
        self.assertEqual(mode, 0o600, oct(mode))


class EditSemanticsTest(_TempDB):
    def test_blank_key_keeps_the_stored_one(self):
        p = ai_config.save_profile(label="a", base_url="https://x/v1",
                                   model="m1", api_key=SECRET)
        ai_config.save_profile(profile_id=p["id"], label="a", base_url="https://x/v1",
                               model="m2", api_key=None)
        kept = [x for x in ai_config.profiles() if x["id"] == p["id"]][0]
        self.assertEqual(kept["model"], "m2")
        self.assertTrue(kept["has_key"], "boş anahtar kayıtlıyı silmemeli")
        self.assertEqual(ai_config.resolve()["api_key"], SECRET)

    def test_new_key_replaces_the_old_one(self):
        p = ai_config.save_profile(label="a", base_url="https://x/v1",
                                   model="m", api_key=SECRET)
        ai_config.save_profile(profile_id=p["id"], label="a", base_url="https://x/v1",
                               model="m", api_key="sk-new-key-1111111111")
        self.assertEqual(ai_config.resolve()["api_key"], "sk-new-key-1111111111")

    def test_only_one_default_at_a_time(self):
        a = ai_config.save_profile(label="a", base_url="https://x/v1", model="m", api_key="k1" * 5)
        b = ai_config.save_profile(label="b", base_url="https://y/v1", model="n", api_key="k2" * 5)
        defaults = [p for p in ai_config.profiles() if p["is_default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["id"], b["id"])
        ai_config.set_default(a["id"])
        defaults = [p for p in ai_config.profiles() if p["is_default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["id"], a["id"])

    def test_deleting_the_default_promotes_another(self):
        a = ai_config.save_profile(label="a", base_url="https://x/v1", model="m", api_key="k1" * 5)
        b = ai_config.save_profile(label="b", base_url="https://y/v1", model="n", api_key="k2" * 5)
        self.assertTrue(ai_config.delete_profile(b["id"]))
        defaults = [p for p in ai_config.profiles() if p["is_default"]]
        self.assertEqual(len(defaults), 1, "varsayılansız kalmamalı")
        self.assertEqual(defaults[0]["id"], a["id"])

    def test_bad_input_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            ai_config.save_profile(label="x", base_url="ftp://nope", model="m", api_key="k" * 9)
        with self.assertRaises(ValueError):
            ai_config.save_profile(label="x", base_url="https://x/v1", model="", api_key="k" * 9)
        with self.assertRaises(ValueError):
            ai_config.save_profile(profile_id=9999, label="x", base_url="https://x/v1",
                                   model="m", api_key="k" * 9)


class BaseUrlGuardTest(_TempDB):
    """`base_url` is user input and the server fetches it: the SSRF surface.

    The settings dialog is reachable by anyone who can open the page, so a URL
    the server will happily call is a capability, not a preference. Loopback
    stays allowed -- a local Ollama or llama.cpp is the expected local-first
    target -- but cloud metadata, link-local and (unless opted in) private
    ranges must be refused before any request is made.
    """

    def _rejected(self, url):
        with self.assertRaises(ValueError):
            ai_config.check_base_url(url)

    def test_metadata_and_link_local_are_always_refused(self):
        for url in ("http://169.254.169.254/latest/meta-data/",
                    "http://169.254.169.254:80/v1",
                    "http://metadata.google.internal/v1",
                    "http://[fe80::1]/v1",
                    "http://[ff02::1]/v1"):
            self._rejected(url)

    def test_private_network_needs_an_explicit_opt_in(self):
        self._rejected("http://10.0.0.5:8000/v1")
        self._rejected("http://192.0.2.10/v1")
        self._rejected("http://172.16.5.4/v1")
        os.environ["F1_AI_ALLOW_PRIVATE"] = "1"
        try:
            self.assertEqual(ai_config.check_base_url("http://10.0.0.5:8000/v1"),
                             "http://10.0.0.5:8000/v1")
        finally:
            os.environ.pop("F1_AI_ALLOW_PRIVATE", None)

    def test_loopback_is_allowed_without_opt_in(self):
        # The whole point of local-first: your own machine is a valid target.
        for url in ("http://127.0.0.1:11434/v1", "http://localhost:8080/v1"):
            self.assertEqual(ai_config.check_base_url(url), url)

    def test_non_http_schemes_are_refused(self):
        for url in ("file:///etc/passwd", "ftp://x/v1", "gopher://x/1",
                    "http:///novhost", ""):
            self._rejected(url)

    def test_public_https_passes_through(self):
        self.assertEqual(ai_config.check_base_url("https://api.openai.com/v1/"),
                         "https://api.openai.com/v1")

    def test_saving_a_blocked_url_stores_nothing(self):
        with self.assertRaises(ValueError):
            ai_config.save_profile(label="x", base_url="http://169.254.169.254/v1",
                                   model="m", api_key=SECRET)
        self.assertEqual(ai_config.profiles(), [])


class ScrubTest(unittest.TestCase):
    """A provider can echo the key back; nothing that leaves may contain it."""

    def test_key_is_replaced_in_provider_error_text(self):
        text = f'invalid api key provided: {SECRET}'
        out = ai_config.scrub(text, SECRET)
        self.assertNotIn(SECRET, out)
        self.assertIn("gizli", out)

    def test_text_without_a_key_is_unchanged(self):
        self.assertEqual(ai_config.scrub("HTTP 500", None), "HTTP 500")
        self.assertEqual(ai_config.scrub("HTTP 500", ""), "HTTP 500")


if __name__ == "__main__":
    unittest.main()
