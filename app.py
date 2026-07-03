from flask import Flask, render_template, request, jsonify, send_file
import spacy
from rapidfuzz import process, fuzz
import io
import json
from pydub import AudioSegment
import whisper
import numpy as np
from kokoro_onnx import Kokoro
import soundfile as sf

# Import your multi-tier polyglot database bridge pipeline
from database import query_transport_system

# Initialize the Whisper model globally for offline speech recognition
try:
    print("Loading Whisper tiny model...")
    whisper_model = whisper.load_model("tiny")
    print("Whisper model loaded successfully!")
except Exception as e:
    print(f"Warning: Failed to load Whisper model: {e}. Offline speech search will be unavailable.")
    whisper_model = None

# Initialize the Kokoro TTS model globally for offline speech synthesis
try:
    print("Loading Kokoro TTS model (kokoro-v1.0.onnx)...")
    kokoro_tts = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
    print("Kokoro TTS model loaded successfully!")
except Exception as e:
    print(f"Warning: Failed to load Kokoro model: {e}. Voice synthesis will be unavailable.")
    kokoro_tts = None

app = Flask(__name__)

# Load the pre-trained English context model into memory globally
nlp = spacy.load("en_core_web_sm")

# Explicitly typed master list of valid database station names
VALID_STATIONS = [
    "New Delhi",
    "Mumbai",
    "Bangalore",
]





def correct_station_name(station_name):
    """
    Checks spelling similarity using Levenshtein Distance metric rules
    and overrides typos if matching confidence is 75% or higher.
    """
    if not station_name:
        return None

    highest_match = process.extractOne(station_name, VALID_STATIONS, scorer=fuzz.WRatio)
    if highest_match and highest_match[1] >= 75:
        return highest_match[0]

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
            speed=1.0, 
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



if __name__ == "__main__":
    # Awake on Port 5000 with debug hot-reloading active
    app.run(host="0.0.0.0", port=5000, debug=True)
