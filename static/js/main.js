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

// Formats HH:MM:SS time strings into natural spoken format
function formatTimeForSpeech(timeStr) {
    if (!timeStr) return 'N/A';
    const parts = timeStr.split(':');
    if (parts.length >= 2) {
        let hour = parseInt(parts[0], 10);
        const minute = parts[1];
        const ampm = hour >= 12 ? 'PM' : 'AM';
        hour = hour % 12;
        hour = hour ? hour : 12;
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

// Helpers for badges and presentation
function getTransportBadge(type) {
    const t = (type || 'Transit').trim().toLowerCase();
    if (t.includes('flight') || t.includes('plane') || t.includes('air')) {
        return `<span class="transport-badge badge-flight"><i data-lucide="plane" class="btn-icon"></i> Flight</span>`;
    } else if (t.includes('train') || t.includes('rail')) {
        return `<span class="transport-badge badge-train"><i data-lucide="train" class="btn-icon"></i> Train</span>`;
    } else if (t.includes('bus')) {
        return `<span class="transport-badge badge-bus"><i data-lucide="bus" class="btn-icon"></i> Bus</span>`;
    }
    return `<span class="transport-badge badge-train"><i data-lucide="navigation" class="btn-icon"></i> ${type || 'Service'}</span>`;
}

function getSeatsBadge(seats) {
    const num = parseInt(seats, 10);
    if (isNaN(num) || num <= 0) {
        return `<span class="seats-pill seats-soldout"><i data-lucide="x-circle" class="btn-icon" style="width:14px;height:14px;"></i> Sold Out</span>`;
    } else if (num < 10) {
        return `<span class="seats-pill seats-low"><i data-lucide="alert-circle" class="btn-icon" style="width:14px;height:14px;"></i> ${num} seats left</span>`;
    }
    return `<span class="seats-pill seats-available"><i data-lucide="check-circle" class="btn-icon" style="width:14px;height:14px;"></i> ${num} seats</span>`;
}

// Core routine to send text query to Flask and display results
async function performQuerySearch(queryString) {
    transcriptOutput.textContent = `"${queryString}"`;
    transcriptOutput.classList.remove('placeholder-text');
    
    const submitBtn = document.getElementById('submit-btn');
    const originalBtnHtml = submitBtn ? submitBtn.innerHTML : '';
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner"></span> Searching...';
    }

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
        const errorMsg = "Network error: Could not reach transport server. Please check your connection.";
        resultsOutput.innerHTML = `<div class="schedule-card error"><i data-lucide="alert-triangle" class="btn-icon" style="margin-right: 6px; color: var(--error-color);"></i> ${errorMsg}</div>`;
        resultsSection.classList.remove('hidden');
        if (window.lucide) window.lucide.createIcons();
        speakText(errorMsg); 
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalBtnHtml;
            if (window.lucide) window.lucide.createIcons();
        }
    }
}

// Core routine to send offline recorded audio blob to Flask for Whisper transcription
async function performAudioSearch(audioBlob) {
    transcriptOutput.textContent = "Transcribing audio offline (Whisper)...";
    transcriptOutput.classList.add('placeholder-text');
    
    micBtn.classList.remove('recording');
    micBtn.classList.add('processing');
    micStatus.textContent = "Processing...";

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
        const errorMsg = "Audio error: Could not transcribe recording. Please try speaking again.";
        resultsOutput.innerHTML = `<div class="schedule-card error"><i data-lucide="alert-triangle" class="btn-icon" style="margin-right: 6px;"></i> ${errorMsg}</div>`;
        resultsSection.classList.remove('hidden');
        if (window.lucide) window.lucide.createIcons();
        speakText(errorMsg); 
    } finally {
        micBtn.classList.remove('processing');
        micStatus.textContent = "Click to Speak";
        if (window.lucide) window.lucide.createIcons();
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
            transcriptOutput.textContent = "Listening to voice input...";
            resultsSection.classList.add('hidden');
        };
        
        mediaRecorder.onstop = async () => {
            isRecording = false; 
            micBtn.classList.remove('recording');
            
            const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
            audioChunks = [];
            
            await performAudioSearch(audioBlob);
        };
        return true;
    } catch (err) {
        console.error("Microphone access failed:", err);
        const errorMsg = "Microphone access blocked. Please check browser permissions.";
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
    if (micBtn.classList.contains('processing')) return;

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
        if (window.lucide) window.lucide.createIcons();
        if (!isRestore && textToSpeak) {
            speakText(textToSpeak);
        }
        return;
    }

    if (data.is_transit && data.transit_paths && data.transit_paths.length > 0) {
        currentResultsData = data;
        
        data.transit_paths.forEach((path, pathIndex) => {
            const card = document.createElement('div');
            card.className = 'schedule-card';
            
            let pathHtml = `
                <div style="margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                    <div style="font-weight: 800; font-family: var(--font-heading); color: var(--primary-color); font-size: 1.1rem; display: flex; align-items: center; gap: 6px;">
                        <i data-lucide="route" class="btn-icon"></i> Transit Route Option ${pathIndex + 1}
                    </div>
                    <span style="font-size: 0.85rem; color: var(--text-secondary); background: rgba(99,102,241,0.08); padding: 0.25rem 0.6rem; border-radius: 6px;">
                        ${path.legs.length} Connecting Leg${path.legs.length > 1 ? 's' : ''}
                    </span>
                </div>
            `;
            
            path.legs.forEach((leg, legIndex) => {
                const schedule = leg.schedules[0];
                const transportType = schedule.transport_type || 'Service';
                const departureTime = schedule.departure_time || 'N/A';
                const arrivalTime = schedule.arrival_time || 'N/A';
                const seats = schedule.available_seats || 0;
                const isLegSoldOut = seats <= 0;
                
                pathHtml += `
                    <div class="transit-leg-box">
                        <div class="transit-leg-info">
                            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                <span style="background: var(--primary-color); color: white; border-radius: 6px; padding: 2px 7px; font-size: 0.75rem; font-weight: 800;">
                                    LEG ${legIndex + 1}
                                </span>
                                ${getTransportBadge(transportType)}
                                <strong style="font-size: 1rem; color: var(--text-primary);">${leg.source}</strong> 
                                <i data-lucide="arrow-right" class="btn-icon" style="width: 14px; height: 14px; margin: 0 2px;"></i> 
                                <strong style="font-size: 1rem; color: var(--text-primary);">${leg.destination}</strong>
                            </div>
                            <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; font-size: 0.88rem; color: var(--text-secondary); margin-top: 4px;">
                                <span><strong>Dep:</strong> ${departureTime}</span>
                                <span><strong>Arr:</strong> ${arrivalTime}</span>
                                ${getSeatsBadge(seats)}
                            </div>
                        </div>
                        <div>
                            ${isLegSoldOut ? 
                                `<button class="btn-card-action disabled" disabled><i data-lucide="slash" class="btn-icon"></i> Sold Out</button>` : 
                                `<a href="/book/${schedule.schedule_id}" class="btn-card-action" style="font-size: 0.82rem; padding: 0.45rem 0.9rem;"><i data-lucide="ticket" class="btn-icon"></i> Book Leg</a>`
                            }
                        </div>
                    </div>
                `;
            });
            
            const scheduleIds = path.legs.map(leg => leg.schedules[0].schedule_id).join(',');
            pathHtml += `
                <div style="text-align: right; margin-top: 1.25rem; border-top: 1px solid rgba(99,102,241,0.12); padding-top: 1rem; display: flex; justify-content: flex-end;">
                    <a href="/book-transit?schedules=${scheduleIds}" class="btn-primary" style="padding: 0.65rem 1.4rem; font-size: 0.95rem;">
                        <i data-lucide="layers" class="btn-icon"></i> Book Entire Journey
                    </a>
                </div>
            `;
            
            card.innerHTML = pathHtml;
            resultsOutput.appendChild(card);
        });

        // Verbal summary for transit path options
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
            const arrivalTime = schedule.arrival_time || '';
            const seats = schedule.available_seats !== undefined ? schedule.available_seats : 0;
            const isSoldOut = seats <= 0;
            
            card.innerHTML = `
                <div class="schedule-card-layout">
                    <div class="schedule-info-block">
                        <div class="schedule-title-row">
                            ${getTransportBadge(transportType)}
                            <span class="schedule-route-text">
                                ${data.origin} 
                                <i data-lucide="arrow-right" class="btn-icon" style="margin: 0 4px; width: 14px; height: 14px;"></i> 
                                ${data.destination}
                            </span>
                        </div>
                        <div class="schedule-meta-row">
                            <span><strong>Departure:</strong> ${departureTime}</span>
                            ${arrivalTime ? `<span><strong>Arrival:</strong> ${arrivalTime}</span>` : ''}
                            <span><strong>Route:</strong> #${schedule.route_id}</span>
                            ${getSeatsBadge(seats)}
                        </div>
                    </div>
                    <div class="schedule-action-block">
                        ${isSoldOut ? 
                            `<button class="btn-card-action disabled" disabled><i data-lucide="slash" class="btn-icon"></i> Sold Out</button>` : 
                            `<a href="/book/${schedule.schedule_id}" class="btn-card-action"><i data-lucide="ticket" class="btn-icon"></i> Book Ticket</a>`
                        }
                    </div>
                </div>
            `;
            resultsOutput.appendChild(card);
        });
        
        // Concise voice output
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
        resultsOutput.innerHTML = `
            <div class="schedule-card" style="text-align: center; padding: 2rem;">
                <div style="color: var(--text-secondary); display: flex; align-items: center; justify-content: center; gap: 8px; font-size: 1.05rem;">
                    <i data-lucide="info" class="btn-icon" style="color: var(--primary-color); width: 22px; height: 22px;"></i> 
                    No scheduled routes found for this search.
                </div>
            </div>
        `;
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

// Restore on pageshow to handle browser back-button caching
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