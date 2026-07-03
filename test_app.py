import unittest
from unittest.mock import patch, MagicMock
import io
from app import app, extract_journey_bounds
from database import query_transport_system

class TestVoiceTransportSystem(unittest.TestCase):
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_nlp_extraction(self):
        """Test NLP boundary extraction logic for various query formats."""
        test_cases = [
            ("find trains from Delhi to Bangalore", ("New Delhi", "Bangalore")),
            ("find flights from Delhi to Pune", ("New Delhi", "Pune")),
            ("show buses from Pune to Bangalore", ("Pune", "Bangalore")),
            ("trains between Delhi and Bangalore", ("New Delhi", "Bangalore")),
            ("delhi to bangalore", ("New Delhi", "Bangalore")),
            ("to bangalore from delhi", ("New Delhi", "Bangalore")),
            ("find buses from mumbay to delhy", ("Mumbai", "New Delhi")),
        ]
        for query, expected in test_cases:
            with self.subTest(query=query):
                self.assertEqual(extract_journey_bounds(query), expected)

    def test_database_query(self):
        """Test database connection and polyglot query bridge."""
        # Query for a known direct route segment in our database
        result = query_transport_system("New Delhi", "Bangalore")
        self.assertEqual(result.get("status"), "Success")
        self.assertEqual(result.get("origin"), "New Delhi")
        self.assertEqual(result.get("destination"), "Bangalore")
        self.assertIn("schedules", result)
        self.assertTrue(len(result["schedules"]) > 0)
        
        # Query for an unknown route segment
        result_none = query_transport_system("New Delhi", "Pune")
        self.assertEqual(result_none.get("status"), "No routes found")
        self.assertEqual(result_none.get("schedules"), [])

    def test_web_routes(self):
        """Test Flask web app routes and status codes."""
        # Test index route
        response = self.app.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Voice Transport Gateway", response.data)

        # Test service worker route
        response_sw = self.app.get("/service-worker.js")
        self.assertEqual(response_sw.status_code, 200)
        self.assertIn(response_sw.mimetype, ("text/javascript", "application/javascript"))

        # Test search API route (valid query)
        response_search = self.app.post("/search", json={"query": "find trains from Delhi to Bangalore"})
        self.assertEqual(response_search.status_code, 200)
        data = response_search.get_json()
        self.assertEqual(data.get("status"), "Success")
        
        # Test search API route (invalid query - empty)
        response_empty = self.app.post("/search", json={"query": ""})
        self.assertEqual(response_empty.status_code, 400)
        self.assertIn("error", response_empty.get_json())

    @patch('app.AudioSegment.from_file')
    @patch('pydub.effects.normalize')
    def test_search_audio(self, mock_normalize, mock_from_file):
        """Test offline search audio route by mocking Whisper and AudioSegment."""
        # Mock AudioSegment
        mock_audio = MagicMock()
        mock_audio.set_frame_rate.return_value = mock_audio
        mock_audio.set_channels.return_value = mock_audio
        mock_audio.set_sample_width.return_value = mock_audio
        mock_audio.raw_data = b"some raw audio bytes"
        mock_from_file.return_value = mock_audio
        mock_normalize.return_value = mock_audio

        # Mock Whisper model
        mock_whisper = MagicMock()
        mock_whisper.transcribe.return_value = {"text": "find trains from Delhi to Bangalore"}

        with patch('app.whisper_model', mock_whisper):
            # Send post request
            audio_data = (io.BytesIO(b"fake audio data"), "recording.webm")
            response = self.app.post(
                "/search-audio",
                data={"audio": audio_data},
                content_type="multipart/form-data"
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data.get("status"), "Success")
            self.assertEqual(data.get("transcription"), "find trains from Delhi to Bangalore")

    @patch('app.kokoro_tts')
    def test_tts_route(self, mock_kokoro):
        """Test offline TTS route by mocking Kokoro."""
        import numpy as np
        mock_samples = np.zeros(16000, dtype=np.float32)
        mock_kokoro.create.return_value = (mock_samples, 24000)

        response = self.app.get("/tts?text=hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertTrue(len(response.data) > 0)


if __name__ == "__main__":
    unittest.main()
