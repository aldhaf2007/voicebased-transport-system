import unittest
from unittest.mock import patch, MagicMock
import io
import base64
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
        result_none = query_transport_system("Bangalore", "Nonexistentcity")
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

    def test_admin_unauthorized(self):
        """Test that access without credentials redirects to login."""
        response = self.app.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin-login", response.headers["Location"])

        # API requests should return 401 JSON instead of redirecting
        response_post = self.app.post("/admin/add-station", json={"name": "Kolkata"})
        self.assertEqual(response_post.status_code, 401)
        self.assertEqual(response_post.get_json()["error"], "Admin authentication required")

    def test_admin_invalid_credentials(self):
        """Test that access with invalid credentials flashes an error."""
        response = self.app.post("/admin-login", data={"username": "wronguser", "password": "wrongpassword"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid admin credentials.", response.data)

    def test_admin_login_success(self):
        """Test successful admin login redirects to dashboard."""
        response = self.app.post("/admin-login", data={"username": "admin", "password": "admin123"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin", response.headers["Location"])

    @patch('app.get_all_stations')
    @patch('app.get_all_routes')
    @patch('app.get_all_schedules')
    def test_admin_dashboard(self, mock_schedules, mock_routes, mock_stations):
        """Test admin dashboard page render with valid credentials."""
        mock_stations.return_value = ["Delhi", "Mumbai"]
        mock_routes.return_value = [{"route_id": 1, "source": "Delhi", "destination": "Mumbai"}]
        mock_schedules.return_value = []
        
        with self.app.session_transaction() as sess:
            sess["admin_logged_in"] = True
            
        response = self.app.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Administrative Command Center", response.data)

    @patch('app.add_station')
    @patch('app.reload_valid_stations')
    def test_admin_add_station(self, mock_reload, mock_add):
        """Test admin add-station endpoint with valid credentials."""
        mock_add.return_value = (True, "Station added successfully.")
        
        with self.app.session_transaction() as sess:
            sess["admin_logged_in"] = True
            
        response = self.app.post("/admin/add-station", json={"name": "Kolkata"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        mock_reload.assert_called_once()

    @patch('app.delete_station')
    @patch('app.reload_valid_stations')
    def test_admin_delete_station(self, mock_reload, mock_delete):
        """Test admin delete-station endpoint with valid credentials."""
        mock_delete.return_value = (True, "Station deleted successfully.")
        
        with self.app.session_transaction() as sess:
            sess["admin_logged_in"] = True
            
        response = self.app.post("/admin/delete-station", json={"name": "Kolkata"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])

    @patch('app.get_schedule_by_id')
    def test_booking_page(self, mock_get_schedule):
        """Test booking page GET endpoint."""
        # Unauthenticated redirect check
        response_unauth = self.app.get("/book/1")
        self.assertEqual(response_unauth.status_code, 302)
        self.assertIn("/login", response_unauth.headers["Location"])

        # Set authenticated session
        with self.app.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "testuser"

        # Valid schedule
        mock_get_schedule.return_value = {
            "schedule_id": 1,
            "route_id": 1,
            "transport_type": "Train",
            "departure_time": "12:00:00",
            "arrival_time": "16:00:00",
            "available_seats": 50,
            "source": "Delhi",
            "destination": "Mumbai"
        }
        response = self.app.get("/book/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Confirm Booking", response.data)

        # Invalid schedule
        mock_get_schedule.return_value = None
        response_none = self.app.get("/book/999")
        self.assertEqual(response_none.status_code, 404)

    @patch('app.create_booking')
    def test_booking_transaction(self, mock_create):
        """Test booking API POST endpoint."""
        payload = {
            "schedule_id": 1,
            "passenger_name": "John Doe",
            "passenger_email": "john@example.com",
            "seats_booked": 2,
            "travel_date": "2026-07-09"
        }

        # Unauthenticated check (must return 401)
        response_unauth = self.app.post("/book", json=payload)
        self.assertEqual(response_unauth.status_code, 401)

        # Set authenticated session
        with self.app.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "testuser"

        # Success booking
        mock_create.return_value = (True, {
            "booking_id": 42,
            "schedule_id": 1,
            "passenger_name": "John Doe",
            "passenger_email": "john@example.com",
            "seats_booked": 2,
            "travel_date": "2026-07-09"
        })
        response = self.app.post("/book", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "Success")
        self.assertEqual(data["booking"]["booking_id"], 42)

        # Failure booking (insufficient seats)
        mock_create.return_value = (False, "Not enough seats available.")
        response_fail = self.app.post("/book", json=payload)
        self.assertEqual(response_fail.status_code, 400)
        self.assertIn("error", response_fail.get_json())

    @patch('app.get_schedule_by_id')
    def test_transit_booking_page(self, mock_get_schedule):
        """Test transit booking page GET endpoint."""
        # Unauthenticated redirect check
        response_unauth = self.app.get("/book-transit?schedules=1,2")
        self.assertEqual(response_unauth.status_code, 302)
        self.assertIn("/login", response_unauth.headers["Location"])

        # Set authenticated session
        with self.app.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "testuser"

        mock_get_schedule.return_value = {
            "schedule_id": 1,
            "route_id": 1,
            "transport_type": "Train",
            "departure_time": "12:00:00",
            "arrival_time": "16:00:00",
            "available_seats": 50,
            "source": "Delhi",
            "destination": "Mumbai"
        }
        response = self.app.get("/book-transit?schedules=1,2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Book Entire Transit Journey", response.data)

        # No schedules parameter
        response_no_param = self.app.get("/book-transit")
        self.assertEqual(response_no_param.status_code, 400)

    @patch('app.create_transit_bookings')
    def test_transit_booking_transaction(self, mock_create_transit):
        """Test transit booking API POST endpoint."""
        payload = {
            "schedule_ids": [1, 2],
            "passenger_name": "John Doe",
            "passenger_email": "john@example.com",
            "seats_booked": 2,
            "travel_date": "2026-07-09"
        }

        # Unauthenticated check (must return 401)
        response_unauth = self.app.post("/book-transit", json=payload)
        self.assertEqual(response_unauth.status_code, 401)

        # Set authenticated session
        with self.app.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "testuser"

        # Success transit booking
        mock_create_transit.return_value = (True, [
            {
                "booking_id": 100,
                "schedule_id": 1,
                "passenger_name": "John Doe",
                "passenger_email": "john@example.com",
                "seats_booked": 2,
                "travel_date": "2026-07-09",
                "remaining_seats": 48
            },
            {
                "booking_id": 101,
                "schedule_id": 2,
                "passenger_name": "John Doe",
                "passenger_email": "john@example.com",
                "seats_booked": 2,
                "travel_date": "2026-07-09",
                "remaining_seats": 40
            }
        ])
        response = self.app.post("/book-transit", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "Success")
        self.assertEqual(len(data["bookings"]), 2)
        self.assertEqual(data["bookings"][0]["booking_id"], 100)

        # Failure transit booking
        mock_create_transit.return_value = (False, "Not enough seats available on Schedule ID 2.")
        response_fail = self.app.post("/book-transit", json=payload)
        self.assertEqual(response_fail.status_code, 400)
        self.assertIn("error", response_fail.get_json())

    @patch('app.register_user')
    def test_signup(self, mock_register):
        """Test signup endpoint."""
        mock_register.return_value = (True, "User registered successfully.")
        
        # Test GET request
        response_get = self.app.get("/signup")
        self.assertEqual(response_get.status_code, 200)

        # Test POST form redirect
        response_post = self.app.post("/signup", data={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "securepassword"
        })
        self.assertEqual(response_post.status_code, 302)

        # Test POST JSON API
        response_json = self.app.post("/signup", json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "securepassword"
        })
        self.assertEqual(response_json.status_code, 200)
        self.assertTrue(response_json.get_json()["success"])

    @patch('app.authenticate_user')
    def test_login(self, mock_auth):
        """Test login endpoint."""
        mock_auth.return_value = {"user_id": 1, "username": "testuser", "email": "test@example.com"}

        # Test GET request
        response_get = self.app.get("/login")
        self.assertEqual(response_get.status_code, 200)

        # Test POST form redirect
        response_post = self.app.post("/login", data={
            "username": "testuser",
            "password": "correctpassword"
        })
        self.assertEqual(response_post.status_code, 302)

        # Clear session to test JSON API login
        with self.app.session_transaction() as sess:
            sess.clear()

        # Test POST JSON API
        response_json = self.app.post("/login", json={
            "username": "testuser",
            "password": "correctpassword"
        })
        self.assertEqual(response_json.status_code, 200)
        self.assertTrue(response_json.get_json()["success"])

    @patch('app.get_user_bookings')
    def test_my_bookings(self, mock_get_user_bookings):
        """Test the my_bookings route for a logged-in user."""
        mock_get_user_bookings.return_value = [
            {
                "booking_id": 1,
                "passenger_name": "Test User",
                "passenger_email": "test@example.com",
                "seats_booked": 2,
                "travel_date": "10-07-2026",
                "booking_time": "10-07-2026 12:00",
                "source": "New Delhi",
                "destination": "Mumbai",
                "transport_type": "Train",
                "departure_time": "14:00"
            }
        ]

        # Test unauthenticated access (should redirect)
        response_unauth = self.app.get("/my_bookings")
        self.assertEqual(response_unauth.status_code, 302)

        # Test authenticated access
        with self.app.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "testuser"

        response_auth = self.app.get("/my_bookings")
        self.assertEqual(response_auth.status_code, 200)
        self.assertIn(b"My Bookings", response_auth.data)
        self.assertIn(b"Test User", response_auth.data)
        self.assertIn(b"New Delhi \xe2\x9e\x94 Mumbai", response_auth.data)

    @patch('app.cancel_user_booking')
    def test_cancel_booking(self, mock_cancel):
        """Test the cancel booking endpoint."""
        mock_cancel.return_value = (True, "Booking cancelled successfully.")
        
        # Test unauthenticated
        response_unauth = self.app.post("/cancel_booking/1")
        self.assertEqual(response_unauth.status_code, 401)
        
        # Test authenticated
        with self.app.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "testuser"
            
        response_auth = self.app.post("/cancel_booking/1")
        self.assertEqual(response_auth.status_code, 200)
        self.assertTrue(response_auth.get_json()["success"])

if __name__ == "__main__":
    unittest.main()
