# Architecture and data model

## Components

| Component | Responsibility |
| --- | --- |
| Flask (`app.py`) | Serves pages and JSON/audio endpoints; coordinates search, authentication, and bookings. |
| spaCy + RapidFuzz | Extracts origin/destination text and maps it to known station names. |
| Whisper | Transcribes uploaded browser audio for `/search-audio`. |
| Neo4j | Stores `Station` nodes and directed `CONNECTS_TO` route relationships. |
| MySQL | Stores transport types, schedules, users, and bookings. |
| Kokoro | Produces WAV audio for textual route summaries. |
| Browser client | Records audio, renders search results, and uses service-worker caching for static assets. |

## Search flow

1. The browser posts a typed query to `POST /search` or WebM audio to `POST /search-audio`.
2. Audio requests are normalized to 16 kHz mono PCM and transcribed with Whisper.
3. The application identifies cities by exact station matching, fuzzy token matching, spaCy entities, and `from`/`to` parsing.
4. The graph query searches directed paths from origin to destination with one to five hops.
5. For a direct route, MySQL returns schedules with remaining seats. If direct schedules are unavailable, the app attempts to resolve viable multi-leg paths.
6. The browser renders results. For text searches it may request an asynchronous WAV summary from `GET /tts`.

## Graph model (Neo4j)

```cypher
(:Station {name: 'New Delhi'})
  -[:CONNECTS_TO {route_id: 1}]->
(:Station {name: 'Mumbai'})
```

`route_id` is the join key shared with `Schedules.route_id`; Neo4j does not enforce that cross-database relationship. Route search follows only the directed relationships, though the admin interface creates both directions for a newly added route.

## Relational model (MySQL)

| Table | Purpose | Important fields |
| --- | --- | --- |
| `Transport_Details` | Transport categories | `transport_id`, `type` |
| `Schedules` | Timetabled service inventory | `schedule_id`, `route_id`, `transport_id`, `departure_time`, `arrival_time`, `available_seats` |
| `Users` | Registered accounts | `user_id`, `username`, `email`, `password_hash` |
| `Bookings` | Ticket records | `booking_id`, `schedule_id`, `user_id`, `seats_booked`, `travel_date`, `status` |

The app initializes `Users` and `Bookings` when `database.py` loads. `Transport_Details` and `Schedules` must already exist.

## Booking consistency

Single-leg and transit booking operations start a MySQL transaction, lock each affected schedule row with `FOR UPDATE`, verify capacity, decrement capacity, and insert booking records before committing. A multi-leg booking rolls back the whole operation if any leg is unavailable. Cancellation restores the booked seat count and changes the booking status to `CANCELLED`.

## Configuration

| Setting | Purpose | Default |
| --- | --- | --- |
| `NEO4J_URI` | Neo4j Bolt endpoint | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j account | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | Read from application fallback; set explicitly in your environment. |
| `FLASK_SECRET_KEY` | Signs Flask sessions | Development fallback; set explicitly. |
| `ADMIN_USERNAME` | Admin login name | `admin` |
| `ADMIN_PASSWORD` | Admin login password | `admin123` |

MySQL values are defined in `MYSQL_CONFIG` in `database.py`: host, user, password, and database name. Change them for your local environment and do not commit real secrets.
