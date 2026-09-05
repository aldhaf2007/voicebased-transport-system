// Unregister Service Workers to bypass caching and prevent network stream blocks
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations().then(registrations => {
        for (let registration of registrations) {
            registration.unregister();
        }
    });
}

// DOM UI Target Handles
const micBtn = document.getElementById('mic-btn');
const micStatus = document.getElementById('mic-status');
const transcriptOutput = document.getElementById('speech-transcript');
const resultsSection = document.getElementById('results-section');
const resultsOutput = document.getElementById('results-output');
const textInputForm = document.getElementById('text-input-form');
const manualQueryInput = document.getElementById('manual-query');
const readAllBtn = document.getElementById('read-all-btn');

// Track state
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];
let currentResultsData = null;

// --- TEXT-TO-SPEECH VOICE ENGINE (KOKORO OFFLINE AUDIO GENERATOR) ---
const CustomSpeechEngine = {
    audio: null,
    speak: function(text) {
        this.cancel();
        if (!text || !text.trim()) return;
        const audioElement = document.getElementById('tts-audio');
        if (audioElement) {
            audioElement.src = `/tts?text=${encodeURIComponent(text)}`;
            audioElement.load();
            audioElement.play().catch(err => console.warn("Failed to play TTS audio:", err));
            this.audio = audioElement;
        }
    },
    playBase64: function(base64Data) {
        this.cancel();
        if (!base64Data) return;
        const audioElement = document.getElementById('tts-audio');
        if (audioElement) {
            audioElement.src = `data:audio/wav;base64,${base64Data}`;
            audioElement.load();
            audioElement.play().catch(err => console.warn("Failed to play base64 TTS audio:", err));
            this.audio = audioElement;
        }
    },
    cancel: function() {
        if (this.audio) {
            this.audio.pause();
            this.audio.currentTime = 0;
            this.audio = null;
        }
    },
    get speaking() {
        return this.audio && !this.audio.paused && !this.audio.ended;
    }
};

// Formats HH:MM:SS time strings into a highly natural spoken format (e.g. 16:30:00 -> 4:30 PM, 08:00:00 -> 8 AM)
function formatTimeForSpeech(timeStr) {
    if (!timeStr) return 'N/A';
    const parts = timeStr.split(':');
    if (parts.length >= 2) {
        let hour = parseInt(parts[0], 10);
        const minute = parts[1];
        const ampm = hour >= 12 ? 'PM' : 'AM';
        hour = hour % 12;
        hour = hour ? hour : 12; // 0 hour becomes 12
        if (minute === '00') {
            return `${hour} ${ampm}`;
        }
        return `${hour}:${minute} ${ampm}`;
    }
    return timeStr;
}

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
        resultsOutput.innerHTML = `<div class="schedule-card error"><i data-lucide="alert-triangle" class="btn-icon" style="margin-right: 6px; color: var(--error-color);"></i> ${errorMsg}</div>`;
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
        resultsOutput.innerHTML = `<div class="schedule-card error"><i data-lucide="alert-triangle" class="btn-icon" style="margin-right: 6px;"></i> ${errorMsg}</div>`;
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
        resultsOutput.innerHTML = `<div class="schedule-card error"><i data-lucide="alert-triangle" class="btn-icon" style="margin-right: 6px;"></i> ${errorMsg}</div>`;
        resultsSection.classList.remove('hidden');
        if (window.lucide) window.lucide.createIcons();
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
function displayResults(data, isRestore=false) {
    resultsOutput.innerHTML = "";
    resultsSection.classList.remove('hidden');
    currentResultsData = null;
    let textToSpeak = null;

    if (data.error) {
        resultsOutput.innerHTML = `<div class="schedule-card error"><i data-lucide="alert-triangle" class="btn-icon" style="margin-right: 6px;"></i> ${data.error}</div>`;
        textToSpeak = data.error;
        return;
    }

    if (data.is_transit && data.transit_paths && data.transit_paths.length > 0) {
        currentResultsData = data;
        
        data.transit_paths.forEach((path, pathIndex) => {
            const card = document.createElement('div');
            card.className = 'schedule-card';
            
            let pathHtml = `<strong style="color: var(--primary-color); font-size: 1.05rem;"><i data-lucide="route" class="btn-icon" style="margin-right: 6px;"></i> Transit Route Option ${pathIndex + 1}:</strong><br>`;
            
            path.legs.forEach((leg, legIndex) => {
                const schedule = leg.schedules[0];
                const transportType = schedule.transport_type || 'service';
                const departureTime = schedule.departure_time || 'N/A';
                const arrivalTime = schedule.arrival_time || 'N/A';
                
                pathHtml += `
                    <div style="margin-left: 10px; border-left: 2px solid var(--primary-color); padding-left: 12px; margin-top: 10px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; gap: 10px;">
                        <div>
                            <strong>Leg ${legIndex + 1}:</strong> ${leg.source} <i data-lucide="arrow-right" class="btn-icon" style="margin: 0 4px; width: 14px; height: 14px; vertical-align: middle;"></i> ${leg.destination} <br>
                            <strong>Service:</strong> ${transportType} | 
                            <strong>Departure:</strong> ${departureTime} | 
                            <strong>Arrival:</strong> ${arrivalTime} |
                            <strong>Seats:</strong> ${schedule.available_seats || 0}
                        </div>
                        <div>
                            <a href="/book/${schedule.schedule_id}" class="read-all-button" style="text-decoration: none; padding: 0.4rem 0.8rem; border-radius: 8px; font-size: 0.85rem; font-weight: bold; background: linear-gradient(135deg, var(--primary-color) 0%, #4f46e5 100%); display: inline-block; white-space: nowrap;"><i data-lucide="ticket" class="btn-icon" style="margin-right: 4px;"></i> Book Leg</a>
                        </div>
                    </div>
                `;
            });
            
            const scheduleIds = path.legs.map(leg => leg.schedules[0].schedule_id).join(',');
            pathHtml += `
                <div style="text-align: right; margin-top: 1.25rem; border-top: 1px solid rgba(99,102,241,0.1); padding-top: 1rem;">
                    <a href="/book-transit?schedules=${scheduleIds}" class="read-all-button" style="text-decoration: none; padding: 0.5rem 1.25rem; border-radius: 8px; font-size: 0.9rem; font-weight: bold; background: linear-gradient(135deg, var(--primary-color) 0%, #4f46e5 100%); display: inline-block;"><i data-lucide="ticket" class="btn-icon" style="margin-right: 6px;"></i> Book Entire Journey</a>
                </div>
            `;
            
            card.innerHTML = pathHtml;
            resultsOutput.appendChild(card);
        });

        // Compile verbal summary for transit path options
        let verbalSummary = `No direct route found from ${data.origin} to ${data.destination}. However, you can travel `;
        const firstPath = data.transit_paths[0];
        firstPath.legs.forEach((leg, index) => {
            const transport = leg.schedules[0].transport_type || 'service';
            if (index > 0) {
                verbalSummary += `, and then from ${leg.source} to ${leg.destination} via ${transport}`;
            } else {
                verbalSummary += `from ${leg.source} to ${leg.destination} via ${transport}`;
            }
        });
        verbalSummary += `. Click Read All to hear full schedules.`;
        textToSpeak = verbalSummary;

    } else if (data.schedules && data.schedules.length > 0) {
        currentResultsData = data;
        let verbalSummary = `Found ${data.schedules.length} options from ${data.origin} to ${data.destination}. `;
        
        data.schedules.forEach((schedule) => {
            const card = document.createElement('div');
            card.className = 'schedule-card';
            
            const transportType = schedule.transport_type || 'Train';
            const departureTime = schedule.departure_time || 'N/A';
            
            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; gap: 10px;">
                    <div>
                        <strong>Route ID:</strong> ${schedule.route_id} | 
                        <strong>Service:</strong> ${transportType} <br>
                        <strong>Path:</strong> ${data.origin} <i data-lucide="arrow-right" class="btn-icon" style="margin: 0 4px; width: 14px; height: 14px; vertical-align: middle;"></i> ${data.destination} <br>
                        <strong>Departure:</strong> ${departureTime} | 
                        <strong>Seats:</strong> ${schedule.available_seats || 0}
                    </div>
                    <div>
                        <a href="/book/${schedule.schedule_id}" class="read-all-button" style="text-decoration: none; padding: 0.5rem 1rem; border-radius: 8px; font-size: 0.9rem; font-weight: bold; background: linear-gradient(135deg, var(--primary-color) 0%, #4f46e5 100%); display: inline-block; white-space: nowrap;"><i data-lucide="ticket" class="btn-icon" style="margin-right: 6px;"></i> Book Ticket</a>
                    </div>
                </div>
            `;
            resultsOutput.appendChild(card);
        });
        
        // Build concise voice output to keep speech short and natural
        if (data.schedules.length === 1) {
            verbalSummary += `It is a ${data.schedules[0].transport_type || 'service'} departing at ${formatTimeForSpeech(data.schedules[0].departure_time)}.`;
        } else {
            verbalSummary += `The first option is a ${data.schedules[0].transport_type || 'service'} departing at ${formatTimeForSpeech(data.schedules[0].departure_time)}. `;
            if (data.schedules.length > 1) {
                verbalSummary += `We also have a ${data.schedules[1].transport_type || 'service'} departing at ${formatTimeForSpeech(data.schedules[1].departure_time)}. `;
            }
            verbalSummary += `Click Read All to hear all options.`;
        }
        
        textToSpeak = verbalSummary;
        
    } else {
        resultsOutput.innerHTML = `<div class="schedule-card"><i data-lucide="info" class="btn-icon" style="margin-right: 6px; color: var(--primary-color);"></i> No routes found.</div>`;
        if (data.origin && data.destination) {
            textToSpeak = `No routes found from ${data.origin} to ${data.destination}.`;
        } else {
            textToSpeak = "No routes found.";
        }
    }

    // Play pre-generated base64 audio instantly, or fallback to HTTP TTS synthesis
    if (!isRestore) {
        if (data.audio_base64) {
            CustomSpeechEngine.playBase64(data.audio_base64);
        } else if (textToSpeak) {
            speakText(textToSpeak);
        }
    }
    
    // Save state for back navigation
    sessionStorage.setItem('lastSearchResults', JSON.stringify(data));
    sessionStorage.setItem('lastSearchTranscript', transcriptOutput.textContent);
    
    if (window.lucide) {
        window.lucide.createIcons();
    }
}

// State restoration logic
function restoreSearchState() {
    // If the user manually reloads the page, wipe the saved state so they get a fresh search interface
    const navEntries = performance.getEntriesByType("navigation");
    if (navEntries.length > 0 && navEntries[0].type === "reload") {
        sessionStorage.removeItem('lastSearchResults');
        sessionStorage.removeItem('lastSearchTranscript');
        return;
    }

    const savedData = sessionStorage.getItem('lastSearchResults');
    const savedTranscript = sessionStorage.getItem('lastSearchTranscript');
    if (savedData && savedTranscript) {
        transcriptOutput.textContent = savedTranscript;
        transcriptOutput.classList.remove('placeholder-text');
        try {
            const data = JSON.parse(savedData);
            displayResults(data, true);
        } catch (e) {
            console.error('Error restoring session state:', e);
        }
    }
}

// Restore results from sessionStorage on page load
window.addEventListener('DOMContentLoaded', restoreSearchState);

// Also restore on pageshow to handle browser back-button caching
window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
        restoreSearchState();
    }
});

// Click handler for Read All button to voice all results on demand
readAllBtn.addEventListener('click', () => {
    if (!currentResultsData) return;
    
    if (currentResultsData.is_transit) {
        if (!currentResultsData.transit_paths || currentResultsData.transit_paths.length === 0) return;
        
        let fullVerbalSummary = `Here are the transit route options from ${currentResultsData.origin} to ${currentResultsData.destination}. `;
        
        currentResultsData.transit_paths.forEach((path, pathIndex) => {
            fullVerbalSummary += `Option ${pathIndex + 1}: `;
            path.legs.forEach((leg, legIndex) => {
                const transport = leg.schedules[0].transport_type || 'service';
                const time = formatTimeForSpeech(leg.schedules[0].departure_time);
                if (legIndex > 0) {
                    fullVerbalSummary += `, followed by a ${transport} from ${leg.source} to ${leg.destination} departing at ${time}`;
                } else {
                    fullVerbalSummary += `Take a ${transport} from ${leg.source} to ${leg.destination} departing at ${time}`;
                }
            });
            fullVerbalSummary += `. `;
        });
        speakText(fullVerbalSummary);
    } else {
        if (!currentResultsData.schedules || currentResultsData.schedules.length === 0) return;
        
        let fullVerbalSummary = `Here are all ${currentResultsData.schedules.length} options from ${currentResultsData.origin} to ${currentResultsData.destination}. `;
        
        currentResultsData.schedules.forEach((schedule, index) => {
            const transportType = schedule.transport_type || 'service';
            const departureTime = formatTimeForSpeech(schedule.departure_time);
            fullVerbalSummary += `Option ${index + 1}: A ${transportType} departing at ${departureTime}. `;
        });
        
        speakText(fullVerbalSummary);
    }
});