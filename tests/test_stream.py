#!/usr/bin/env python3
"""The streaming chat path: request shape, meta parity, and no silent failures.

Why this exists as a test rather than only as code: the streaming path was added
to fix a reliability failure (a byte-silent connection killed by a proxy at ~100 s
and reported as "HTTP 524 — sunucu JSON yerine HTML döndürdü"). A streaming
implementation that quietly changes the request, doubles the answer, or stores a
failed call as a real answer would be worse than the bug it replaced -- the user
would get a plausible-looking wrong transcript instead of an error.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis  # noqa: E402


class RequestBodyTest(unittest.TestCase):
    def test_streaming_body_asks_for_stream_and_usage(self):
        b = analysis._request_body("m", [{"role": "user", "content": "x"}],
                                   "off", 1400, stream=True)
        self.assertTrue(b["stream"])
        self.assertEqual(b["stream_options"], {"include_usage": True})
        self.assertEqual(b["reasoning_effort"], "none")

    def test_non_streaming_body_is_unchanged(self):
        b = analysis._request_body("m", [], "high", 8000, stream=False)
        self.assertFalse(b["stream"])
        self.assertNotIn("stream_options", b, "akış yokken usage seçeneği gönderilmemeli")
        self.assertEqual(b["reasoning_effort"], "high")

    def test_both_paths_send_the_same_model_and_budget(self):
        a = analysis._request_body("model-x", [], "low", 8000, stream=True)
        b = analysis._request_body("model-x", [], "low", 8000, stream=False)
        for k in ("model", "max_tokens", "temperature", "reasoning_effort"):
            self.assertEqual(a[k], b[k], k)


class MetaParityTest(unittest.TestCase):
    def _meta(self, **kw):
        base = dict(usage={"completion_tokens": 10, "prompt_tokens": 20,
                           "completion_tokens_details": {"reasoning_tokens": 3}},
                    choice={"finish_reason": "stop"}, budget=1400, level="low",
                    rescued=False, ai={"label": "p"}, model="m", ctx="c",
                    sent=[], usable=[])
        base.update(kw)
        return analysis._answer_meta(**base)

    def test_meta_carries_the_fields_the_panel_shows(self):
        m = self._meta()
        for k in ("level", "completion_tokens", "prompt_tokens", "max_tokens",
                  "truncated", "finish_reason", "provider", "model_used",
                  "context_chars", "history_sent", "history_total"):
            self.assertIn(k, m)

    def test_length_finish_is_reported_as_truncated(self):
        self.assertTrue(self._meta(choice={"finish_reason": "length"})["truncated"])
        self.assertFalse(self._meta(choice={"finish_reason": "stop"})["truncated"])

    def test_rescue_flips_the_level_label(self):
        self.assertIn("bütçe yetmedi", self._meta(rescued=True)["level"])

    def test_missing_usage_does_not_invent_zero_credits(self):
        m = self._meta(usage={})
        self.assertIsNone(m["credits_cr"], "kredi bilinmiyorsa None olmalı, 0 değil")
        self.assertIsNone(m["completion_tokens"])


class NoDoubleAppendTest(unittest.TestCase):
    """`_stream_once` accumulates into the caller's list, so the caller must not.

    Measured failure: deltas totalling 28 characters produced a 56-character
    answer, because both layers appended. The user saw every sentence twice.
    """

    def test_generator_does_not_append_again(self):
        import inspect
        src = inspect.getsource(analysis.llm_chat_stream)
        self.assertNotIn('parts.append(ev["text"])', src,
                         "llm_chat_stream delta'yi tekrar biriktiriyor")

    def test_pump_appends_once(self):
        import inspect
        src = inspect.getsource(analysis._stream_once)
        self.assertEqual(src.count("parts.append(text)"), 1,
                         "_stream_once cevabı tam olarak bir kez biriktirmeli")


class StreamingClientTest(unittest.TestCase):
    """The browser-side SSE parser, exercised against a fake fetch.

    The parser is small but three of its behaviours are load-bearing: heartbeat
    comments must be ignored, a JSON object split across two network reads must
    still parse, and an error event must reach the UI instead of being swallowed.
    """

    def _load(self):
        import pathlib, re
        root = pathlib.Path(__file__).resolve().parent.parent
        src = (root / "static" / "shell.js").read_text(encoding="utf-8")
        m = re.search(r"function chatStream\(url, payload, hooks\)[\s\S]*?\n  \}\n", src)
        if not m:
            raise unittest.SkipTest("chatStream bulunamadı")
        return m.group(0)

    def test_parser_ignores_pings_and_parses_split_chunks(self):
        fn = self._load()
        node = __import__("subprocess").run(["node", "--version"], capture_output=True)
        if node.returncode != 0:
            self.skipTest("node yok")
        script = """
const fn = %s;
function fakeFetch(chunks) {
  let i = 0;
  return Promise.resolve({
    ok: true, status: 200,
    body: { getReader() { return { read() {
      if (i >= chunks.length) return Promise.resolve({done: true});
      return Promise.resolve({done: false, value: chunks[i++]});
    } }; } },
  });
}
const enc = new TextEncoder();
const chunks = [
  enc.encode(': ping\\n\\ndata: {"type":"scope","note":"Veri: Yar\\u0131\\u015f"}\\n\\n'),
  enc.encode('data: {"type":"delta","text":"mer'),
  enc.encode('haba"}\\n\\ndata: {"type":"delta","text":"!"}\\n\\n'),
  enc.encode('data: {"type":"done","answer":"merhaba!","meta":{}}\\n\\n'),
];
global.fetch = () => fakeFetch(chunks);
let out = {scope: null, text: "", done: null, err: null};
fn("api/x", {}, {
  onScope: e => out.scope = e.note,
  onDelta: t => out.text += t,
  onDone: e => out.done = e,
  onError: m => out.err = m,
}).then(() => console.log(JSON.stringify(out)));
""" % fn
        r = __import__("subprocess").run(["node", "-e", script], capture_output=True,
                                         text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[:400])
        import json as _json
        out = _json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["scope"], "Veri: Yarış")
        self.assertEqual(out["text"], "merhaba!", "iki okumaya bölünen JSON kaybolmamalı")
        self.assertIsNotNone(out["done"])
        self.assertIsNone(out["err"])

    def test_error_event_reaches_the_ui(self):
        fn = self._load()
        node = __import__("subprocess").run(["node", "--version"], capture_output=True)
        if node.returncode != 0:
            self.skipTest("node yok")
        script = """
const fn = %s;
const enc = new TextEncoder();
const chunks = [enc.encode('data: {"type":"error","message":"HTTP 524"}\\n\\n')];
global.fetch = () => Promise.resolve({
  ok: true, status: 200,
  body: { getReader() { let i = 0; return { read() {
    if (i >= chunks.length) return Promise.resolve({done: true});
    return Promise.resolve({done: false, value: chunks[i++]});
  } }; } },
});
let err = null;
fn("api/x", {}, { onError: m => err = m }).then(() => console.log(err));
""" % fn
        r = __import__("subprocess").run(["node", "-e", script], capture_output=True,
                                         text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertEqual(r.stdout.strip(), "HTTP 524")


class HeartbeatPolicyTest(unittest.TestCase):
    def test_ping_interval_is_well_under_the_proxy_limit(self):
        """The whole point: a quiet connection must still carry bytes.

        A proxy in front of the app can drop a byte-silent connection at around
        100 s. A heartbeat must therefore be an order of magnitude below that,
        not merely below it.
        """
        import pathlib, re
        root = pathlib.Path(__file__).resolve().parent.parent
        for name in ("server.py", "archive.py"):
            src = (root / name).read_text(encoding="utf-8")
            m = re.search(r"CHAT_PING_SECS\s*=\s*([\d.]+)", src)
            self.assertIsNotNone(m, f"{name}: CHAT_PING_SECS yok")
            self.assertLessEqual(float(m.group(1)), 20.0,
                                 f"{name}: ping aralığı çok uzun")
            self.assertIn("/api/chat/stream", src, f"{name}: akış ucu yok")


if __name__ == "__main__":
    unittest.main()
