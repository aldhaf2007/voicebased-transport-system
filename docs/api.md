# HTTP API reference

All endpoints are served by the Flask app at `http://localhost:5000` by default. JSON request endpoints expect `Content-Type: application/json` unless noted otherwise. Session-protected routes use the browser's Flask session cookie.

## Search and speech

| Method and path | Request | Response |
| --- | --- | --- |
| `POST /search` | `{ "query": "find trains from Delhi to Mumbai" }` | Direct schedules or a transit path result. |
| `POST /search-audio` | Multipart form field `audio` containing browser-recorded audio | Search result plus `transcription`. |
| `GET /tts?text=...` | URL-encoded summary text | `audio/wav`; returns `503` when Kokoro is unavailable. |

Example:

```bash
curl -X POST http://localhost:5000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"find trains from Delhi to Mumbai"}'
```

A direct search response has this shape:

```json
{
  "status": "Success",
  "origin": "New Delhi",
  "destination": "Mumbai",
  "is_transit": false,
  "schedules": [
    {
      "schedule_id": 1,
      "route_id": 1,
      "transport_type": "Train",
      "departure_time": "09:00:00",
      "arrival_time": "13:00:00",
      "available_seats": 42
    }
  ],
  "verbal_summary": "Found 1 options from New Delhi to Mumbai.",
  "audio_base64": null
}
```

Transit responses set `is_transit` to `true` and return `transit_paths`, where each path has `legs`; every leg includes `source`, `destination`, `route_id`, and its available `schedules`.

## Account and booking

| Method and path | Authentication | Request |
| --- | --- | --- |
| `POST /signup` | No | `{ "username", "email", "password" }` |
| `POST /login` | No | `{ "username", "password" }` |
| `GET /logout` | No | Clears the session and redirects home. |
| `GET /my_bookings` | User | Renders booking history. |
| `POST /book` | User | `{ "schedule_id", "passenger_name", "passenger_email", "seats_booked", "travel_date" }` |
| `GET /book/<schedule_id>` | User | Renders the direct-booking page. |
| `GET /book-transit?schedules=1,2` | User | Renders the multi-leg booking page. |
| `POST /book-transit` | User | `{ "schedule_ids", "passenger_name", "passenger_email", "seats_booked", "travel_date" }` |
| `POST /cancel_booking/<booking_id>` | User | Cancels the caller's booking and restores seats. |

`/signup` and `/login` also accept regular form submissions for their HTML pages. A successful direct booking responds with `{ "status": "Success", "booking": { ... } }`; a successful transit booking uses `bookings`, an array of records.

## Administration

Admin endpoints require an authenticated admin session obtained through `GET`/`POST /admin-login`. For JSON API requests without that session, the app returns `401` with `{"error":"Admin authentication required"}`.

| Method and path | JSON body |
| --- | --- |
| `POST /admin/add-station` | `{ "name": "Kolkata" }` |
| `POST /admin/rename-station` | `{ "old_name": "Delhi", "new_name": "New Delhi" }` |
| `POST /admin/delete-station` | `{ "name": "Kolkata" }` |
| `POST /admin/add-route` | `{ "source": "New Delhi", "destination": "Mumbai" }` |
| `POST /admin/delete-route` | `{ "route_id": 1 }` |
| `POST /admin/add-schedule` | `{ "route_id": 1, "transport_type": "Train", "departure_time": "09:00", "arrival_time": "13:00", "available_seats": 50 }` |
| `POST /admin/update-schedule` | `{ "schedule_id": 1, "transport_type": "Train", "departure_time": "09:30", "arrival_time": "13:30", "available_seats": 50 }` |
| `POST /admin/delete-schedule` | `{ "schedule_id": 1 }` |

`GET /admin` renders the dashboard and `GET /admin-logout` ends the admin session. Station and route deletion also removes the related schedule records, so use them carefully.

## Status codes

| Code | Meaning |
| --- | --- |
| `200` | Request succeeded. |
| `302` | Browser route redirects to a login page. |
| `400` | Missing/invalid input, unrecognized trip bounds, or failed business validation. |
| `401` | A JSON booking/admin request has no appropriate session. |
| `404` | Requested schedule was not found. |
| `500` | Unexpected server failure. |
| `503` | A requested speech model is not currently available. |
