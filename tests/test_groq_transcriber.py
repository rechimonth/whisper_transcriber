import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.groq_transcriber import GroqTranscriber


class TestGroqRetryAndCheckpointing(unittest.TestCase):
    def test_retryable_statuses(self):
        for status in (408, 429, 500, 502, 503):
            exc = SimpleNamespace(status_code=status)
            self.assertTrue(GroqTranscriber._is_retryable(exc))
        self.assertFalse(
            GroqTranscriber._is_retryable(SimpleNamespace(status_code=400))
        )

    def test_timeout_is_retryable(self):
        self.assertTrue(GroqTranscriber._is_retryable(TimeoutError("timeout")))

    def test_retry_after_header(self):
        exc = SimpleNamespace(
            response=SimpleNamespace(headers={"retry-after": "4.5"})
        )
        self.assertEqual(GroqTranscriber._extract_retry_after(exc), 4.5)

    def test_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temp_transcript_test.json"
            results = {0: [{"start": 0.0, "end": 1.0, "text": "hola"}]}
            GroqTranscriber._save_checkpoint(
                str(path),
                signature="abc",
                duration=60.0,
                language="es",
                chunk_count=2,
                results_by_chunk=results,
            )
            loaded = GroqTranscriber._load_checkpoint(
                str(path),
                signature="abc",
                duration=60.0,
                language="es",
                chunk_count=2,
            )
            self.assertEqual(loaded, results)

    def test_invalid_checkpoint_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temp_transcript_test.json"
            path.write_text("{not-json", encoding="utf-8")
            loaded = GroqTranscriber._load_checkpoint(
                str(path),
                signature="abc",
                duration=60.0,
                language=None,
                chunk_count=1,
            )
            self.assertEqual(loaded, {})


if __name__ == "__main__":
    unittest.main()
