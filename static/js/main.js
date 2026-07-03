// Register Service Worker for offline support
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js')
        .then(() => console.log('Service Worker registered'))
        .catch(err => console.log('Service Worker registration failed:', err));
}

// DOM UI Target Handles
const micBtn = document.getElementById('mic-btn');
const micStatus = document.getElementById('mic-status');
const transcriptOutput = document.getElementById('speech-transcript');
const resultsSection = document.getElementById('results-section');
const resultsOutput = document.getElementById('results-output');
const textInputForm = document.getElementById('text-input-form');
const manualQueryInput = document.getElementById('manual-query');

// Track recording states
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];

// --- TEXT-TO-SPEECH VOICE ENGINE (KOKORO OFFLINE AUDIO GENERATOR) ---
const CustomSpeechEngine = {
    audio: null,
    speak: function(text) {
        this.cancel();
        if (!text || !text.trim()) return;
        this.audio = new Audio(`/tts?text=${encodeURIComponent(text)}`);
        this.audio.play().catch(err => console.warn("Failed to play TTS audio:", err));
    },
    cancel: function() {
        if (this.audio) {
            this.audio.pause();
            this.audio = null;
        }
    },
    get speaking() {
        return this.audio && !this.audio.paused && !this.audio.ended;
    }
};

function speakText(textMessage) {
    CustomSpeechEngine.speak(textMessage);
}

// Core routine to send text query to Flask and display results
async function performQuerySearch(queryString) {
    transcriptOutput.textContent = `"${queryString}"`;
    transcriptOutput.classList.remove('placeholder-text');

    try {
        const response = await fetch('/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ query: queryString })
        });

        const data = await response.json();
        displayResults(data);

    } catch (error) {
        console.error("Transmission Error:", error);
        const errorMsg = "Gateway Timeout: Failed to reach backend orchestration router.";
        resultsOutput.innerHTML = `<div class="schedule-card" style="color:red;">${errorMsg}</div>`;
        resultsSection.classList.remove('hidden');
        speakText(errorMsg); 
    }
}

// Core routine to send offline recorded audio blob to Flask for Whisper transcription
async function performAudioSearch(audioBlob) {
    transcriptOutput.textContent = "Transcribing audio locally (offline)...";
    transcriptOutput.classList.add('placeholder-text');

    try {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'recording.webm');

        const response = await fetch('/search-audio', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        
        if (data.transcription) {
            transcriptOutput.textContent = `"${data.transcription}"`;
            transcriptOutput.classList.remove('placeholder-text');
        } else if (data.error && data.error.includes("Could not understand")) {
            transcriptOutput.textContent = "Could not understand speech. Please speak clearly.";
        }
        
        displayResults(data);

    } catch (error) {
        console.error("Audio Processing Error:", error);
        const errorMsg = "Error: Failed to process local speech recognition on the server.";
        resultsOutput.innerHTML = `<div class="schedule-card error">⚠️ ${errorMsg}</div>`;
        resultsSection.classList.remove('hidden');
        speakText(errorMsg); 
    }
}

// Initialize microphone and MediaRecorder
async function initMicrophone() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };
        
        mediaRecorder.onstart = () => {
            isRecording = true; 
            micBtn.classList.add('recording');
            micStatus.textContent = "Click to Stop";
            transcriptOutput.textContent = "Recording voice signal offline...";
            resultsSection.classList.add('hidden');
        };
        
        mediaRecorder.onstop = async () => {
            isRecording = false; 
            micBtn.classList.remove('recording');
            micStatus.textContent = "Click to Speak";
            
            const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
            audioChunks = [];
            
            await performAudioSearch(audioBlob);
        };
        return true;
    } catch (err) {
        console.error("Microphone access failed:", err);
        const errorMsg = "Microphone access blocked. Please check browser and system permissions.";
        transcriptOutput.textContent = errorMsg;
        transcriptOutput.classList.add('placeholder-text');
        resultsOutput.innerHTML = `<div class="schedule-card error">⚠️ ${errorMsg}</div>`;
        resultsSection.classList.remove('hidden');
        speakText(errorMsg);
        return false;
    }
}

// UI Trigger: Click to Toggle Recording
micBtn.addEventListener('click', async () => {
    if (CustomSpeechEngine.speaking) {
        CustomSpeechEngine.cancel();
    }

    if (!mediaRecorder) {
        const success = await initMicrophone();
        if (!success) return;
    }

    if (!isRecording) {
        audioChunks = [];
        mediaRecorder.start();
    } else {
        mediaRecorder.stop();
    }
});

// Handle Manual Text Query Submission
textInputForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const typedQuery = manualQueryInput.value.trim();
    if (typedQuery) {
        if (CustomSpeechEngine.speaking) {
            CustomSpeechEngine.cancel();
        }
        if (isRecording && mediaRecorder) {
            mediaRecorder.stop();
        }
        await performQuerySearch(typedQuery);
        manualQueryInput.value = ""; 
    }
});

// Render returned database layout lists and announce outcomes
function displayResults(data) {
    resultsOutput.innerHTML = "";
    resultsSection.classList.remove('hidden');

    if (data.error) {
        resultsOutput.innerHTML = `<div class="schedule-card error">⚠️ ${data.error}</div>`;
        speakText(data.error); 
        return;
    }

    if (data.schedules && data.schedules.length > 0) {
        let verbalSummary = `Found ${data.schedules.length} schedules from ${data.origin} to ${data.destination}. `;
        
        data.schedules.forEach((schedule, index) => {
            const card = document.createElement('div');
            card.className = 'schedule-card';
            
            const transportType = schedule.transport_type || 'Train';
            const departureTime = schedule.departure_time || 'N/A';
            
            card.innerHTML = `
                <strong>Route ID:</strong> ${schedule.route_id} | 
                <strong>Service:</strong> ${transportType} <br>
                <strong>Path:</strong> ${data.origin} ➔ ${data.destination} <br>
                <strong>Departure:</strong> ${departureTime} | 
                <strong>Status:</strong> Live on Schedule
            `;
            resultsOutput.appendChild(card);
            
            verbalSummary += `Option ${index + 1}: A ${transportType} departing at ${departureTime}. `;
        });
        
        speakText(verbalSummary); 
        
    } else {
        resultsOutput.innerHTML = `<div class="schedule-card">ℹ️ No routes found.</div>`;
        if (data.origin && data.destination) {
            speakText(`No routes found from ${data.origin} to ${data.destination}.`);
        } else {
            speakText("No routes found.");
        }
    }
}