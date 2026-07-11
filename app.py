from flask import Flask, render_template, request, jsonify, send_file, Response, session, redirect, url_for, flash
import os
import spacy
from rapidfuzz import process, fuzz
import io
import json
import base64
from pydub import AudioSegment
import whisper
import numpy as np
from kokoro_onnx import Kokoro
import soundfile as sf
import onnxruntime as ort

# Import your multi-tier polyglot database bridge pipeline and admin CRUD interfaces
from database import (
    query_transport_system,
    get_all_stations,
    get_all_routes,
    get_all_schedules,
    add_station,
    rename_station,
    delete_station,
    add_route,
    delete_route,
    add_schedule,
    update_schedule,
    delete_schedule,
    get_schedule_by_id,
    create_booking,
    create_transit_bookings,
    register_user,
    authenticate_user,
    get_all_users,
    get_all_bookings,
    get_user_bookings,
    cancel_user_booking
)

# Initialize the Whisper model globally for offline speech recognition
try:
    print("Loading Whisper tiny model...")
    whisper_model = whisper.load_model("tiny")
    print("Whisper model loaded successfully!")
except Exception as e:
    print(f"Warning: Failed to load Whisper model: {e}. Offline speech search will be unavailable.")
    whisper_model = None

# Initialize the Kokoro TTS model globally for offline speech synthesis with optimized SessionOptions
try:
    print("Loading Kokoro TTS model (kokoro-v1.0.onnx) with optimized SessionOptions...")
    sess_options = ort.SessionOptions()
    # Optimize threads to avoid scheduler overhead and minimize latency
    sess_options.intra_op_num_threads = 4
    sess_options.inter_op_num_threads = 1
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sess_options.enable_mem_pattern = True
    sess_options.enable_cpu_mem_arena = True
    
    onnx_session = ort.InferenceSession("kokoro-v1.0.onnx", sess_options, providers=["CPUExecutionProvider"])
    kokoro_tts = Kokoro.from_session(onnx_session, "voices-v1.0.bin")
    print("Kokoro TTS model loaded successfully with SessionOptions!")
except Exception as e:
    print(f"Warning: Failed to load Kokoro model with SessionOptions: {e}. Falling back to default...")
    try:
        kokoro_tts = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
    except Exception as ex:
        print(f"Warning: Failed to load Kokoro model: {ex}. Voice synthesis will be unavailable.")
        kokoro_tts = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "voice-transport-system-secret-key-129847")

# Load the pre-trained English context model into memory globally
nlp = spacy.load("en_core_web_sm")

# Master list of valid database station names, loaded dynamically from get_all_stations
VALID_STATIONS = []

def reload_valid_stations():
    global VALID_STATIONS
    try:
        stations = get_all_stations()
        if stations:
            VALID_STATIONS = stations
        else:
            VALID_STATIONS = ["New Delhi", "Mumbai", "Bangalore", "Pune"]
    except Exception:
        VALID_STATIONS = ["New Delhi", "Mumbai", "Bangalore", "Pune"]

reload_valid_stations()





def correct_station_name(station_name):
    """
    Checks spelling similarity using Levenshtein Distance metric rules
    and overrides typos if matching confidence is 75% or higher.
    """
    if not station_name:
        return None

    station_name_lower = station_name.lower().strip()
    stations_lower = [s.lower() for s in VALID_STATIONS]
    highest_match = process.extractOne(station_name_lower, stations_lower, scorer=fuzz.WRatio)
    if highest_match and highest_match[1] >= 75:
        match_index = highest_match[2]
        return VALID_STATIONS[match_index]

    return station_name.strip().title()


def extract_journey_bounds(text):
    """
    Parses conversational text through a hybrid extraction pipeline:
    1. Direct substring matching of valid stations.
    2. Case-insensitive token fuzzy matching to catch spelling typos.
    3. Falling back to spaCy NER classification (GPE/LOC).
    4. Determining source/destination via prepositions (from/to) and positional order.
    5. Preposition-following fallback (extract words immediately after 'from'/'to' if parsing fails).
    """
    if not text:
        return None, None

    text_lower = text.lower()
    matches = []
    
    # 1. Try exact/substring match first (highly accurate, avoids overlap/consuming errors)
    for station in VALID_STATIONS:
        station_lower = station.lower()
        if station_lower in text_lower:
            idx = text_lower.find(station_lower)
            matches.append((station, idx, idx + len(station_lower)))
        elif station_lower == "new delhi" and "delhi" in text_lower:
            idx = text_lower.find("delhi")
            matches.append(("New Delhi", idx, idx + len("delhi")))
            
    matches = sorted(list(set(matches)), key=lambda x: x[1])
    
    # 2. If we don't have 2 matches, use token-by-token fuzzy matching to catch typos
    if len(matches) < 2:
        stations_map = {s.lower(): s for s in VALID_STATIONS}
        doc = nlp(text)
        tokens = [token.text.lower() for token in doc]
        
        # Match single tokens first, then n-grams
        for n in [1, 2]:
            for i in range(len(tokens) - n + 1):
                ngram_text = " ".join(tokens[i:i+n])
                ngram_start_char = text_lower.find(ngram_text)
                if ngram_start_char == -1:
                    continue
                ngram_end_char = ngram_start_char + len(ngram_text)
                
                # Check for overlap with already matched stations
                overlap = False
                for name, start, end in matches:
                    if not (ngram_end_char <= start or ngram_start_char >= end):
                        overlap = True
                        break
                if overlap:
                    continue
                    
                highest_match = process.extractOne(ngram_text, list(stations_map.keys()), scorer=fuzz.WRatio)
                if highest_match and highest_match[1] >= 75:
                    # Skip common utility/preposition words to avoid false positive fuzzy matches
                    if ngram_text in ("to", "from", "and", "between", "in", "at", "the", "a", "show", "find", "trains", "train", "bus", "buses", "flight", "flights"):
                        continue
                    matches.append((stations_map[highest_match[0]], ngram_start_char, ngram_end_char))
                    
        matches = sorted(list(set(matches)), key=lambda x: x[1])
        
    # 3. Fallback to spaCy GPE/LOC entities for unknown cities
    if len(matches) < 2:
        doc = nlp(text)
        for ent in doc.ents:
            if ent.label_ in ("GPE", "LOC"):
                ent_lower = ent.text.lower()
                ent_start = text_lower.find(ent_lower)
                if ent_start == -1:
                    continue
                ent_end = ent_start + len(ent_lower)
                
                # Check for overlap
                overlap = False
                for name, start, end in matches:
                    if not (ent_end <= start or ent_start >= end):
                        overlap = True
                        break
                if not overlap:
                    matches.append((ent.text.strip().title(), ent_start, ent_end))
                    
        matches = sorted(list(set(matches)), key=lambda x: x[1])
        
    source = None
    destination = None
    
    # Check prepositions preceding matches
    for name, start, end in matches:
        prefix = text_lower[max(0, start-8):start]
        if "from" in prefix:
            source = name
        elif "to" in prefix:
            destination = name
            
    # Fallback positional sequence deduction (First matched = Source, Second matched = Destination)
    extracted_names = [m[0] for m in matches]
    if not source and not destination and len(extracted_names) >= 2:
        source = extracted_names[0]
        destination = extracted_names[1]
    elif len(extracted_names) == 2:
        if source and not destination:
            remaining = [name for name in extracted_names if name != source]
            if remaining:
                destination = remaining[0]
        elif destination and not source:
            remaining = [name for name in extracted_names if name != destination]
            if remaining:
                source = remaining[0]
                
    # 5. Preposition-following fallback (extract tokens following prepositions directly if still missing)
    if not source or not destination:
        doc = nlp(text)
        for i, token in enumerate(doc):
            if token.text.lower() == "from" and i + 1 < len(doc) and not source:
                word = doc[i+1].text
                if word.lower() not in ("to", "the", "a", "in", "at", "from", "between", "and", "show", "find", "trains", "train", "bus", "buses", "flight", "flights"):
                    source = word.strip().title()
                    j = i + 2
                    while j < len(doc) and doc[j].tag_ in ("NNP", "NNPS") and doc[j].text.lower() != "to":
                        source += " " + doc[j].text.strip().title()
                        j += 1
            elif token.text.lower() == "to" and i + 1 < len(doc) and not destination:
                word = doc[i+1].text
                if word.lower() not in ("from", "the", "a", "in", "at", "to", "between", "and", "show", "find", "trains", "train", "bus", "buses", "flight", "flights"):
                    destination = word.strip().title()
                    j = i + 2
                    while j < len(doc) and doc[j].tag_ in ("NNP", "NNPS") and doc[j].text.lower() != "from":
                        destination += " " + doc[j].text.strip().title()
                        j += 1
                        
    return source, destination


# Route to serve the clean web dashboard interface
@app.route("/")
def index():
    return render_template("index.html")


# Route to serve the service worker with root scope
@app.route("/service-worker.js")
def service_worker():
    return app.send_static_file("service-worker.js")


# Core network orchestration endpoint accepting HTTP POST voice query transcripts
@app.route("/search", methods=["POST"])
def search_transport():
    try:
        # Extract the JSON payload from the request body
        payload = request.get_json() or {}
        raw_query = payload.get("query", "")

        # Intercept empty text streams
        if not raw_query.strip():
            return jsonify({"error": "Empty search query received."}), 400

        # Debug trace log streamed directly to local terminal console
        print(f"\n[Incoming Raw Speech]: {raw_query}")

        # Pipeline Phase 1: Machine learning processing to target contextual nodes
        source_raw, dest_raw = extract_journey_bounds(raw_query)

        # Semantic fallback validation check
        if not source_raw or not dest_raw:
            return jsonify(
                {
                    "error": "Could not identify both origin and destination cities. Please say something like: 'Find trains from Delhi to Mumbai'."
                }
            ), 400

        # Pipeline Phase 2: Lexical spelling metric correction against master catalogs
        source_clean = correct_station_name(source_raw)
        dest_clean = correct_station_name(dest_raw)

        print(f"[Normalized Route]: From '{source_clean}' To '{dest_clean}'")

        # Pipeline Phase 3: Execute polyglot database bridge routine (Neo4j paths + MySQL properties)
        search_results = query_transport_system(source_clean, dest_clean)

        # Build verbal summary for TTS engine
        def build_verbal_summary(data):
            if not data or data.get("error"):
                return ""
            origin = data.get("origin", "Unknown")
            destination = data.get("destination", "Unknown")
            
            if data.get("is_transit"):
                paths = data.get("transit_paths", [])
                if not paths:
                    return f"No routes found from {origin} to {destination}."
                summary = f"No direct route found from {origin} to {destination}. However, you can travel "
                first_path = paths[0]
                for idx, leg in enumerate(first_path["legs"]):
                    transport = leg["schedules"][0]["transport_type"] if leg["schedules"] else "service"
                    if idx > 0:
                        summary += f", and then from {leg['source']} to {leg['destination']} via {transport}"
                    else:
                        summary += f"from {leg['source']} to {leg['destination']} via {transport}"
                summary += ". Click Read All to hear full schedules."
                return summary
            else:
                schedules = data.get("schedules", [])
                if not schedules:
                    return f"No routes found from {origin} to {destination}."
                
                summary = f"Found {len(schedules)} options from {origin} to {destination}. "
                def format_time_speech(time_str):
                    if not time_str:
                        return 'N/A'
                    parts = time_str.split(':')
                    if len(parts) >= 2:
                        try:
                            hour = int(parts[0])
                            minute = parts[1]
                            ampm = 'PM' if hour >= 12 else 'AM'
                            hour = hour % 12
                            hour = hour if hour else 12
                            if minute == '00':
                                return f"{hour} {ampm}"
                            return f"{hour}:{minute} {ampm}"
                        except ValueError:
                            return time_str
                    return time_str

                if len(schedules) == 1:
                    summary += f"It is a {schedules[0]['transport_type']} departing at {format_time_speech(schedules[0]['departure_time'])}."
                else:
                    summary += f"The first option is a {schedules[0]['transport_type']} departing at {format_time_speech(schedules[0]['departure_time'])}. "
                    if len(schedules) > 1:
                        summary += f"We also have a {schedules[1]['transport_type']} departing at {format_time_speech(schedules[1]['departure_time'])}. "
                    summary += "Click Read All to hear all options."
                return summary

        verbal_summary = build_verbal_summary(search_results)
        search_results["verbal_summary"] = verbal_summary

        # Pre-synthesize the voice summary into base64 audio to enable instantaneous playback
        audio_base64 = ""
        if kokoro_tts and verbal_summary:
            try:
                samples, sample_rate = kokoro_tts.create(
                    verbal_summary, 
                    voice="af_sarah", 
                    speed=1.3, 
                    lang="en-us"
                )
                wav_io = io.BytesIO()
                sf.write(wav_io, samples, sample_rate, format="WAV")
                wav_io.seek(0)
                audio_base64 = base64.b64encode(wav_io.read()).decode("utf-8")
            except Exception as e:
                print(f"⚠️ Pre-TTS generation error: {e}")

        search_results["audio_base64"] = audio_base64

        # Send unified response array back to browser client web panel
        return jsonify(search_results)

    except Exception as e:
        # Global backend safety handler block to absorb unhandled application crashes
        print(f"[System Error]: {str(e)}")
        return jsonify(
            {
                "error": "An internal server error occurred while processing your request."
            }
        ), 500


# ==========================================
# ADMIN DASHBOARD ROUTINGS & ENDPOINTS
# ==========================================

@app.before_request
def check_admin_auth():
    if request.path == "/admin" or request.path.startswith("/admin/"):
        if not session.get("admin_logged_in"):
            # For API routes starting with /admin/, return JSON error instead of redirect
            if request.path != "/admin" and request.headers.get("Content-Type") == "application/json":
                return jsonify({"error": "Admin authentication required"}), 401
            return redirect(url_for("admin_login"))

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    """Dedicated login route for the administrator."""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        expected_username = os.environ.get("ADMIN_USERNAME", "admin")
        expected_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        
        if username == expected_username and password == expected_password:
            session["admin_logged_in"] = True
            flash("Successfully logged into the admin dashboard.", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid admin credentials.", "error")
            
    return render_template("admin_login.html")

@app.route("/admin-logout")
def admin_logout():
    """Logs out the administrator."""
    session.pop("admin_logged_in", None)
    flash("You have been logged out of the admin panel.", "success")
    return redirect(url_for("index"))


@app.route("/admin")
def admin_dashboard():
    """Renders the interactive web panel for administrative data management."""
    stations = get_all_stations()
    routes = get_all_routes()
    schedules = get_all_schedules()
    users = get_all_users()
    bookings = get_all_bookings()
    
    from datetime import datetime
    active_bookings = []
    expired_bookings = []
    cancelled_bookings = []
    today = datetime.now().date()
    for b in bookings:
        if b.get('status') == 'CANCELLED':
            cancelled_bookings.append(b)
            continue
            
        try:
            b_date = datetime.strptime(b['travel_date'], "%d-%m-%Y").date()
            if b_date >= today:
                active_bookings.append(b)
            else:
                expired_bookings.append(b)
        except ValueError:
            active_bookings.append(b) # Fallback

    return render_template("admin.html", stations=stations, routes=routes, schedules=schedules, users=users, active_bookings=active_bookings, expired_bookings=expired_bookings, cancelled_bookings=cancelled_bookings)


@app.route("/admin/add-station", methods=["POST"])
def admin_add_station():
    data = request.get_json() or {}
    name = data.get("name", "")
    success, msg = add_station(name)
    if success:
        reload_valid_stations()
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/admin/rename-station", methods=["POST"])
def admin_rename_station():
    data = request.get_json() or {}
    old_name = data.get("old_name", "")
    new_name = data.get("new_name", "")
    success, msg = rename_station(old_name, new_name)
    if success:
        reload_valid_stations()
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/admin/delete-station", methods=["POST"])
def admin_delete_station():
    data = request.get_json() or {}
    name = data.get("name", "")
    success, msg = delete_station(name)
    if success:
        reload_valid_stations()
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/admin/add-route", methods=["POST"])
def admin_add_route():
    data = request.get_json() or {}
    source = data.get("source", "")
    destination = data.get("destination", "")
    success, res = add_route(source, destination)
    if success:
        return jsonify({"success": True, "message": res["message"], "route_id": res["route_id"]})
    return jsonify({"success": False, "message": res}), 400


@app.route("/admin/delete-route", methods=["POST"])
def admin_delete_route():
    data = request.get_json() or {}
    route_id = data.get("route_id")
    success, msg = delete_route(route_id)
    if success:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/admin/add-schedule", methods=["POST"])
def admin_add_schedule():
    data = request.get_json() or {}
    route_id = data.get("route_id")
    transport_type = data.get("transport_type")
    departure_time = data.get("departure_time")
    arrival_time = data.get("arrival_time")
    available_seats = data.get("available_seats")
    success, msg = add_schedule(route_id, transport_type, departure_time, arrival_time, available_seats)
    if success:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/admin/update-schedule", methods=["POST"])
def admin_update_schedule():
    data = request.get_json() or {}
    schedule_id = data.get("schedule_id")
    transport_type = data.get("transport_type")
    departure_time = data.get("departure_time")
    arrival_time = data.get("arrival_time")
    available_seats = data.get("available_seats")
    success, msg = update_schedule(schedule_id, transport_type, departure_time, arrival_time, available_seats)
    if success:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/admin/delete-schedule", methods=["POST"])
def admin_delete_schedule():
    data = request.get_json() or {}
    schedule_id = data.get("schedule_id")
    success, msg = delete_schedule(schedule_id)
    if success:
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/tts", methods=["GET"])
def text_to_speech():
    """
    Synthesizes the requested text into high-quality audio using Kokoro-82M offline.
    """
    text = request.args.get("text", "")
    if not text.strip():
        return jsonify({"error": "No text provided for synthesis."}), 400

    if not kokoro_tts:
        return jsonify({"error": "Kokoro TTS engine is not available on the server."}), 503

    try:
        # Generate audio samples and sample rate from Kokoro using the natural female voice "af_sarah"
        samples, sample_rate = kokoro_tts.create(
            text, 
            voice="af_sarah", 
            speed=1.3, 
            lang="en-us"
        )
        
        # Write to an in-memory buffer as a WAV file
        wav_io = io.BytesIO()
        sf.write(wav_io, samples, sample_rate, format="WAV")
        wav_io.seek(0)
        
        return send_file(
            wav_io,
            mimetype="audio/wav",
            as_attachment=False,
            download_name="speech.wav"
        )
        
    except Exception as e:
        print(f"[TTS Error]: {str(e)}")
        return jsonify({"error": f"Failed to synthesize speech: {str(e)}"}), 500


@app.route("/search-audio", methods=["POST"])
def search_audio():
    try:
        if not whisper_model:
            return jsonify({"error": "Whisper offline speech recognition model is not loaded on the server."}), 503

        if 'audio' not in request.files:
            return jsonify({"error": "No audio file provided in request."}), 400

        audio_file = request.files['audio']
        audio_bytes = audio_file.read()

        if not audio_bytes:
            return jsonify({"error": "Audio file is empty."}), 400

        # Load and convert audio to mono, 16000Hz, 16-bit PCM using pydub
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
        
        # Normalize audio levels to maximum gain without clipping
        from pydub.effects import normalize
        audio = normalize(audio)
        
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)

        # Convert to float32 numpy array as Whisper expects
        audio_np = np.frombuffer(audio.raw_data, dtype=np.int16).astype(np.float32) / 32768.0

        # Transcribe audio using Whisper tiny model offline
        initial_prompt = "New Delhi, Mumbai, Bangalore, Pune, trains, flights, buses, from, to"
        result = whisper_model.transcribe(audio_np, fp16=False, initial_prompt=initial_prompt)
        raw_query = result.get("text", "")

        print(f"\n[Incoming Offline Speech (Whisper)]: {raw_query}")

        # Intercept empty text streams
        if not raw_query.strip():
            return jsonify({"error": "Could not understand speech. Please speak clearly."}), 400

        # Pipeline Phase 1: Machine learning processing to target contextual nodes
        source_raw, dest_raw = extract_journey_bounds(raw_query)

        # Semantic fallback validation check
        if not source_raw or not dest_raw:
            return jsonify(
                {
                    "error": f"Understood: '{raw_query}', but could not identify both origin and destination cities. Please say something like: 'Find trains from Delhi to Mumbai'."
                }
            ), 400

        # Pipeline Phase 2: Lexical spelling metric correction against master catalogs
        source_clean = correct_station_name(source_raw)
        dest_clean = correct_station_name(dest_raw)

        print(f"[Normalized Route]: From '{source_clean}' To '{dest_clean}'")

        # Pipeline Phase 3: Execute polyglot database bridge routine
        search_results = query_transport_system(source_clean, dest_clean)

        # Include the transcription in the response so the frontend can display it
        search_results["transcription"] = raw_query
        return jsonify(search_results)

    except Exception as e:
        print(f"[System Audio Error]: {str(e)}")
        return jsonify(
            {
                "error": f"An error occurred while processing offline audio on the server: {str(e)}"
            }
        ), 500



# ==========================================
# USER SIGN UP, SIGN IN & SIGN OUT ROUTES
# ==========================================

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        if request.is_json:
            data = request.get_json() or {}
            username = data.get("username", "")
            email = data.get("email", "")
            password = data.get("password", "")
            success, msg = register_user(username, email, password)
            if success:
                return jsonify({"success": True, "message": msg})
            return jsonify({"success": False, "message": msg}), 400
        else:
            username = request.form.get("username", "")
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            success, msg = register_user(username, email, password)
            if success:
                flash("Registration successful! Please log in.", "success")
                return redirect(url_for("login"))
            flash(msg, "error")

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    next_url = request.args.get("next", "")

    if request.method == "POST":
        if request.is_json:
            data = request.get_json() or {}
            username = data.get("username", "")
            password = data.get("password", "")
            user = authenticate_user(username, password)
            if user:
                session["user_id"] = user["user_id"]
                session["username"] = user["username"]
                return jsonify({"success": True, "message": "Login successful!"})
            return jsonify({"success": False, "message": "Invalid username or password."}), 400
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            user = authenticate_user(username, password)
            if user:
                session["user_id"] = user["user_id"]
                session["username"] = user["username"]
                flash("Logged in successfully!", "success")
                if next_url:
                    return redirect(next_url)
                return redirect(url_for("index"))
            flash("Invalid username or password.", "error")

    return render_template("login.html", next=next_url)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/my_bookings")
def my_bookings():
    """
    Renders the page for users to view their own bookings.
    """
    if "user_id" not in session:
        flash("Please log in to view your bookings.", "error")
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    bookings = get_user_bookings(user_id)
    return render_template("my_bookings.html", bookings=bookings)

@app.route("/cancel_booking/<int:booking_id>", methods=["POST"])
def cancel_booking_route(booking_id):
    """Cancels a specific booking for the logged in user."""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    success, message = cancel_user_booking(booking_id, user_id)
    return jsonify({"success": success, "message": message})

@app.route("/book/<int:schedule_id>", methods=["GET"])
def book_ticket_page(schedule_id):
    """
    Renders the ticket booking page for the specified schedule.
    """
    if "user_id" not in session:
        return redirect(url_for("login", next=request.full_path))
    schedule = get_schedule_by_id(schedule_id)
    if not schedule:
        return render_template("index.html"), 404
    return render_template("booking.html", schedule=schedule)


@app.route("/book", methods=["POST"])
def perform_booking():
    """
    Executes the ticket booking transaction.
    """
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized: Please log in first."}), 401
    try:
        data = request.get_json() or {}
        schedule_id = data.get("schedule_id")
        passenger_name = data.get("passenger_name", "").strip()
        passenger_email = data.get("passenger_email", "").strip()
        seats_booked = data.get("seats_booked")
        travel_date = data.get("travel_date")

        if not schedule_id or not passenger_name or not passenger_email or not seats_booked or not travel_date:
            return jsonify({"error": "Missing required booking details."}), 400

        success, booking_or_error = create_booking(
            schedule_id, passenger_name, passenger_email, seats_booked, travel_date, user_id=session["user_id"]
        )
        if success:
            return jsonify({"status": "Success", "booking": booking_or_error})
        return jsonify({"error": booking_or_error}), 400
    except Exception as e:
        print(f"[Booking Route Error]: {str(e)}")
        return jsonify({"error": "Failed to process booking."}), 500


@app.route("/book-transit", methods=["GET"])
def book_transit_page():
    """
    Renders the multi-leg transit journey booking page.
    Expects comma-separated schedule IDs in query parameters (e.g. ?schedules=1,2).
    """
    if "user_id" not in session:
        return redirect(url_for("login", next=request.full_path))
    schedules_str = request.args.get("schedules", "")
    if not schedules_str:
        return render_template("index.html"), 400

    try:
        schedule_ids = [int(id.strip()) for id in schedules_str.split(",") if id.strip()]
    except ValueError:
        return render_template("index.html"), 400

    schedules = []
    min_available_seats = 99999
    for s_id in schedule_ids:
        schedule = get_schedule_by_id(s_id)
        if not schedule:
            return render_template("index.html"), 404
        schedules.append(schedule)
        if schedule["available_seats"] < min_available_seats:
            min_available_seats = schedule["available_seats"]

    return render_template(
        "booking_transit.html",
        schedules=schedules,
        schedule_ids_str=schedules_str,
        min_available_seats=min_available_seats
    )


@app.route("/book-transit", methods=["POST"])
def perform_transit_booking():
    """
    Executes the multi-leg transit booking inside a single database transaction.
    """
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized: Please log in first."}), 401
    try:
        data = request.get_json() or {}
        schedule_ids = data.get("schedule_ids")
        passenger_name = data.get("passenger_name", "").strip()
        passenger_email = data.get("passenger_email", "").strip()
        seats_booked = data.get("seats_booked")
        travel_date = data.get("travel_date")

        if not schedule_ids or not passenger_name or not passenger_email or not seats_booked or not travel_date:
            return jsonify({"error": "Missing required transit booking details."}), 400

        success, bookings_or_error = create_transit_bookings(
            schedule_ids, passenger_name, passenger_email, seats_booked, travel_date, user_id=session["user_id"]
        )
        if success:
            return jsonify({"status": "Success", "bookings": bookings_or_error})
        return jsonify({"error": bookings_or_error}), 400
    except Exception as e:
        print(f"[Transit Booking Route Error]: {str(e)}")
        return jsonify({"error": "Failed to process transit booking."}), 500


if __name__ == "__main__":
    # Awake on Port 5000 with debug hot-reloading active
    app.run(host="0.0.0.0", port=5000, debug=True)
