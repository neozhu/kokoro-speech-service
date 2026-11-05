import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as service


class FakeModel:
    def __init__(self):
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.fail = False
        self.lock = threading.Lock()

    def create(self, text, **kwargs):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            if self.fail:
                raise RuntimeError("generation failed")
            return [0.0] * 240, 24000
        finally:
            with self.lock:
                self.active -= 1


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.model = FakeModel()
        self.state_patch = patch.multiple(
            service.state,
            model=self.model,
            semaphore=asyncio.Semaphore(2),
            cache_dir=Path(self.directory.name),
        )
        self.state_patch.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.state_patch.stop)

    def test_field_boundaries_do_not_collide(self):
        first = service.SpeechRequest(input="hello", voice="af_sarah")
        second = service.SpeechRequest(input="helloa", voice="f_sarah")
        self.assertNotEqual(service.cache_key(first), service.cache_key(second))

    def test_engine_upgrade_invalidates_cache(self):
        request = service.SpeechRequest(input="hello")
        with patch.object(service, "KOKORO_VERSION", "0.4.9", create=True):
            old_key = service.cache_key(request)
        with patch.object(service, "KOKORO_VERSION", "0.6.1", create=True):
            self.assertNotEqual(old_key, service.cache_key(request))

    async def test_identical_concurrent_requests_generate_once(self):
        request = service.SpeechRequest(input="hello")
        responses = await asyncio.gather(*(service.create_speech(request) for _ in range(8)))
        self.assertEqual(self.model.calls, 1)
        self.assertEqual([r.headers["X-TTS-Cache"] for r in responses].count("MISS"), 1)
        response = await service.create_speech(request)
        self.assertEqual(response.headers["X-TTS-Cache"], "HIT")
        self.assertEqual(self.model.calls, 1)
        self.assertTrue(Path(response.path).read_bytes().startswith(b"RIFF"))

    async def test_different_requests_can_generate_concurrently(self):
        await asyncio.gather(
            service.create_speech(service.SpeechRequest(input="hello")),
            service.create_speech(service.SpeechRequest(input="world")),
        )
        self.assertEqual(self.model.calls, 2)
        self.assertEqual(self.model.max_active, 2)

    async def test_failed_generation_can_be_retried(self):
        request = service.SpeechRequest(input="hello")
        self.model.fail = True
        with self.assertLogs("kokoro_tts", level="ERROR"):
            with self.assertRaises(service.HTTPException):
                await service.create_speech(request)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.model.fail = False
        response = await asyncio.wait_for(service.create_speech(request), timeout=5)
        self.assertEqual(response.headers["X-TTS-Cache"], "MISS")
        self.assertEqual(self.model.calls, 2)


if __name__ == "__main__":
    unittest.main()
