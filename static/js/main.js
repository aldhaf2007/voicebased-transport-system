const SpeechSynthesis = window.speechSynthesis;

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

// Cache available voices asynchronously to ensure offline compatibility
let offlineVoices = [];
function populateVoices() {
    offlineVoices = SpeechSynthesis.getVoices();
}
populateVoices();
if (SpeechSynthesis.onvoiceschanged !== undefined) {
    SpeechSynthesis.onvoiceschanged = populateVoices;
}

// Selects the absolute best English voice available on the host machine
function getBestEnglishVoice() {
    // Priority 1: Natural neural voices
    let voice = offlineVoices.find(v => v.name.toLowerCase().includes('natural') && v.lang.startsWith('en'));
    if (voice) return voice;

    // Priority 2: Google / Microsoft high-quality voices
    voice = offlineVoices.find(v => (v.name.toLowerCase().includes('google') || v.name.toLowerCase().includes('microsoft')) && v.lang.startsWith('en'));
    if (voice) return voice;

    // Priority 3: MBROLA high-quality Linux offline voices
    voice = offlineVoices.find(v => v.name.toLowerCase().includes('mbrola') && v.lang.startsWith('en'));
    if (voice) return voice;

    // Priority 4: Festival English voices (Linux offline)
    voice = offlineVoices.find(v => v.name.toLowerCase().includes('festival') && v.lang.startsWith('en'));
    if (voice) return voice;

    // Priority 5: Flite / CMU English voices (Linux offline)
    voice = offlineVoices.find(v => (v.name.toLowerCase().includes('flite') || v.name.toLowerCase().includes('cmu')) && v.lang.startsWith('en'));
    if (voice) return voice;

    // Priority 6: Non-robotic English voices (excludes standard basic eSpeak)
    voice = offlineVoices.find(v => v.lang.startsWith('en') && !v.name.toLowerCase().includes('espeak'));
    if (voice) return voice;

    // Priority 7: Fallback to any English voice
    voice = offlineVoices.find(v => v.lang.startsWith('en'));
    if (voice) return voice;

    return null;
}

// Store active utterances in a global reference to prevent browser garbage collection bugs
window.speechUtancesRef = [];

// --- TEXT-TO-SPEECH VOICE ENGINE ---
function speakText(textMessage) {
    if (SpeechSynthesis.speaking) {
        SpeechSynthesis.cancel();
    }
    const utterance = new SpeechSynthesisUtterance(textMessage);
    
    // Automatically select the highest-quality English voice
    const bestVoice = getBestEnglishVoice();
    if (bestVoice) {
        utterance.voice = bestVoice;
        console.log(`Using voice: ${bestVoice.name} (Local: ${bestVoice.localService})`);
    }
    
    utterance.lang = 'en-US';
    // Slower speech (0.8x) sounds significantly more natural/clear for local synthesizers
    utterance.rate = 0.8;  
    // Slightly lower pitch (0.95) reduces robotic high-pitched resonance
    utterance.pitch = 0.95; 

    // Save strong reference to prevent garbage collection
    window.speechUtancesRef.push(utterance);
    utterance.onend = () => {
        window.speechUtancesRef = window.speechUtancesRef.filter(u => u !== utterance);
    };
    utterance.onerror = () => {
        window.speechUtancesRef = window.speechUtancesRef.filter(u => u !== utterance);
    };

    SpeechSynthesis.speak(utterance);
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
    if (SpeechSynthesis.speaking) {
        SpeechSynthesis.cancel();
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
        if (SpeechSynthesis.speaking) {
            SpeechSynthesis.cancel();
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