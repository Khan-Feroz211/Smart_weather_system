// Smart Weather System - Real-time JavaScript
class WeatherSystem {
    constructor() {
        this.socket = io();
        this.initializeSocket();
        this.initializeApp();
    }

    initializeSocket() {
        // Connection events
        this.socket.on('connect', () => {
            this.showNotification('✅ Connected to weather service', 'success');
            this.updateConnectionStatus(true);
        });

        this.socket.on('disconnect', () => {
            this.showNotification('❌ Disconnected from weather service', 'danger');
            this.updateConnectionStatus(false);
        });

        this.socket.on('connection_response', (data) => {
            console.log('Server:', data.message);
        });

        // Real-time weather updates
        this.socket.on('weather_update', (data) => {
            this.updateWeatherDisplay(data);
            this.checkAlerts(data);
        });

        this.socket.on('weather_response', (data) => {
            this.displayWeatherData(data);
        });

        // AI prediction updates
        this.socket.on('prediction_update', (data) => {
            this.updatePredictions(data);
        });

        // AgriAdvisor v6 WebSockets Gateway Event Listeners
        this.socket.on('agri_alert', (data) => {
            this.showNotification(`🌾 AGRI ALERT: ${data.disease || 'Hazard'} - ${data.message || 'Urgent crop action required'}`, 'danger');
        });

        this.socket.on('connectivity_change', (data) => {
            this.updateAgriStatusBanner(data.status_header, data.mode);
        });

        this.socket.on('analysis_response', (data) => {
            if (data.status_header) {
                this.updateAgriStatusBanner(data.status_header, data.status_header.includes('OFFLINE') ? 'OFFLINE' : (data.status_header.includes('CACHE') ? 'CACHE' : 'ONLINE'));
            }
        });
    }

    initializeApp() {
        // Initialize real-time clock
        this.startRealTimeClock();
        
        // Initialize animations
        this.initializeAnimations();
        
        // Load initial weather data
        this.loadInitialWeather();
        
        // Set up periodic updates
        this.setupPeriodicUpdates();

        // Initialize AgriAdvisor Mobile Draft Autosave (Gap 2d)
        this.initMobileDraftAutosave();

        // Check and sync offline feedback queue (Gap 1e)
        this.syncOfflineFeedbackQueue();
    }

    updateConnectionStatus(isConnected) {
        const badge = document.getElementById('socketStatusBadge');
        const text = document.getElementById('socketStatusText');
        if (badge && text) {
            if (isConnected) {
                badge.className = 'badge bg-success';
                text.textContent = 'Socket Connected';
                this.syncOfflineFeedbackQueue();
            } else {
                badge.className = 'badge bg-danger';
                text.textContent = 'Socket Disconnected';
            }
        }
    }

    updateAgriStatusBanner(statusHeader, mode) {
        const headerEl = document.getElementById('agriStatusHeader');
        const iconEl = document.getElementById('agriStatusIcon');
        const bannerEl = document.getElementById('agriStatusBanner');
        const detailEl = document.getElementById('agriStatusDetail');

        if (headerEl) headerEl.textContent = statusHeader;
        if (!bannerEl) return;

        if (mode === 'OFFLINE' || statusHeader.includes('OFFLINE')) {
            bannerEl.className = 'alert alert-danger d-flex align-items-center justify-content-between shadow-sm py-2 px-3 mb-0';
            if (iconEl) iconEl.className = 'fas fa-wifi-slash text-danger';
            if (detailEl) detailEl.textContent = 'Weather and satellite telemetry withheld offline';
        } else if (mode === 'CACHE' || statusHeader.includes('CACHE')) {
            bannerEl.className = 'alert alert-warning d-flex align-items-center justify-content-between shadow-sm py-2 px-3 mb-0';
            if (iconEl) iconEl.className = 'fas fa-history text-warning';
            if (detailEl) detailEl.textContent = 'Using cached telemetry (Confidence penalty applied)';
        } else {
            bannerEl.className = 'alert alert-success d-flex align-items-center justify-content-between shadow-sm py-2 px-3 mb-0';
            if (iconEl) iconEl.className = 'fas fa-wifi text-success';
            if (detailEl) detailEl.textContent = 'Fresh telemetry available via Geovis & Weather Gateway';
        }
    }

    initMobileDraftAutosave() {
        const symptomForm = document.getElementById('symptomForm') || document.querySelector('form.symptom-form');
        if (!symptomForm) return;

        const DRAFT_KEY = 'agri_symptom_form_draft';

        // Restore saved draft
        const savedDraft = localStorage.getItem(DRAFT_KEY);
        if (savedDraft) {
            try {
                const formData = JSON.parse(savedDraft);
                Object.keys(formData).forEach(key => {
                    const input = symptomForm.elements[key];
                    if (input) {
                        if (input.type === 'checkbox') input.checked = formData[key];
                        else input.value = formData[key];
                    }
                });
                console.log('Restored mobile symptom form draft from localStorage');
            } catch (e) {
                console.error('Failed to restore symptom draft', e);
            }
        }

        // Save on input change
        symptomForm.addEventListener('input', () => {
            const formData = {};
            Array.from(symptomForm.elements).forEach(el => {
                if (el.name) {
                    formData[el.name] = el.type === 'checkbox' ? el.checked : el.value;
                }
            });
            localStorage.setItem(DRAFT_KEY, JSON.stringify(formData));
        });

        // Clear draft on successful submit
        symptomForm.addEventListener('submit', () => {
            localStorage.removeItem(DRAFT_KEY);
        });
    }

    async syncOfflineFeedbackQueue() {
        const QUEUE_KEY = 'agri_offline_feedback_queue';
        const queueRaw = localStorage.getItem(QUEUE_KEY);
        if (!queueRaw) return;

        try {
            const queue = JSON.parse(queueRaw);
            if (!Array.isArray(queue) || queue.length === 0) return;

            console.log(`Syncing ${queue.length} offline feedback items...`);
            for (const item of queue) {
                await fetch('/api/agri/feedback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...item, is_offline: true })
                });
            }

            // Sync with backend queue
            await fetch('/api/agri/sync-feedback', { method: 'POST' });
            localStorage.removeItem(QUEUE_KEY);
            console.log('Successfully flushed and synced offline feedback queue');
        } catch (e) {
            console.error('Failed to sync offline feedback queue', e);
        }
    }

    startRealTimeClock() {
        const updateClock = () => {
            const now = new Date();
            const timeString = now.toLocaleTimeString('en-US', { 
                hour12: true,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
            const dateString = now.toLocaleDateString('en-US', {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric'
            });
            
            const clockElement = document.getElementById('live-clock');
            const dateElement = document.getElementById('live-date');
            
            if (clockElement) clockElement.textContent = timeString;
            if (dateElement) dateElement.textContent = dateString;
        };

        updateClock();
        setInterval(updateClock, 1000);
    }

    initializeAnimations() {
        // Simple fade-in animation for cards
        const observerOptions = {
            threshold: 0.1,
            rootMargin: '0px 0px -50px 0px'
        };

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.style.opacity = '1';
                    entry.target.style.transform = 'translateY(0)';
                }
            });
        }, observerOptions);

        // Observe cards for fade-in
        document.querySelectorAll('.card-futuristic').forEach(card => {
            card.style.opacity = '0';
            card.style.transform = 'translateY(20px)';
            card.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
            observer.observe(card);
        });

        // Auto-dismiss alerts after 5 seconds
        this.setupAutoDismissAlerts();
    }

    setupAutoDismissAlerts() {
        const alerts = document.querySelectorAll('.alert-dismissible');
        alerts.forEach(alert => {
            setTimeout(() => {
                if (alert.parentNode) {
                    const bsAlert = new bootstrap.Alert(alert);
                    setTimeout(() => bsAlert.close(), 5000);
                }
            }, 5000);
        });
    }

    loadInitialWeather() {
        // Request initial weather data for default location
        this.socket.emit('request_weather', { location: 'Lahore' });
        
        // Load user locations if available
        const userLocations = this.getUserLocations();
        userLocations.forEach(location => {
            this.socket.emit('request_weather', { location: location });
        });
    }

    getUserLocations() {
        // This would typically come from your user data
        return ['Lahore', 'Islamabad', 'Karachi', 'Peshawar', 'Quetta'];
    }

    setupPeriodicUpdates() {
        // Refresh weather every 2 minutes
        setInterval(() => {
            const userLocations = this.getUserLocations();
            userLocations.forEach(location => {
                this.socket.emit('request_weather', { location: location });
            });
        }, 120000);

        // Simulate data updates for demo
        this.simulateLiveUpdates();
    }

    simulateLiveUpdates() {
        // Simulate temperature fluctuations for demo
        setInterval(() => {
            this.updateDemoTemperatures();
        }, 30000);
    }

    updateDemoTemperatures() {
        const tempElements = document.querySelectorAll('.temperature');
        tempElements.forEach(element => {
            if (element.dataset.originalTemp === undefined) {
                element.dataset.originalTemp = parseFloat(element.textContent);
            }
            
            const originalTemp = parseFloat(element.dataset.originalTemp);
            const variation = (Math.random() - 0.5) * 2; // -1 to +1
            const newTemp = (originalTemp + variation).toFixed(1);
            
            // Add visual feedback
            element.classList.add('temp-updating');
            setTimeout(() => {
                element.textContent = newTemp + '°C';
                element.classList.remove('temp-updating');
                
                // Update temperature color class
                this.updateTemperatureColor(element, newTemp);
            }, 300);
        });
    }

    updateTemperatureColor(element, temperature) {
        const temp = parseFloat(temperature);
        element.classList.remove('temp-cold', 'temp-mild', 'temp-warm', 'temp-hot');
        
        if (temp < 10) element.classList.add('temp-cold');
        else if (temp < 20) element.classList.add('temp-mild');
        else if (temp < 30) element.classList.add('temp-warm');
        else element.classList.add('temp-hot');
    }

    updateWeatherDisplay(data) {
        // Update weather cards with new data
        const weatherCard = document.querySelector(`[data-location="${data.location}"]`);
        if (weatherCard) {
            this.animateWeatherUpdate(weatherCard, data);
        }
        
        // Update dashboard if this is the primary location
        if (data.location === 'Lahore') {
            this.updateDashboardWeather(data);
        }
    }

    animateWeatherUpdate(card, data) {
        card.classList.add('weather-updating');
        
        // Update temperature with animation
        const tempElement = card.querySelector('.temperature');
        if (tempElement) {
            tempElement.textContent = data.data.temperature.toFixed(1) + '°C';
            this.updateTemperatureColor(tempElement, data.data.temperature);
        }
        
        // Update condition
        const conditionElement = card.querySelector('.weather-condition');
        if (conditionElement) {
            conditionElement.textContent = data.data.condition;
            conditionElement.className = 'weather-condition ' + this.getWeatherClass(data.data.condition);
        }
        
        setTimeout(() => {
            card.classList.remove('weather-updating');
        }, 500);
    }

    getWeatherClass(condition) {
        const conditionMap = {
            'Clear': 'weather-sunny',
            'Clouds': 'weather-cloudy',
            'Rain': 'weather-rainy',
            'Thunderstorm': 'weather-stormy',
            'Snow': 'weather-cold',
            'Sunny': 'weather-sunny',
            'Cloudy': 'weather-cloudy',
            'Rainy': 'weather-rainy',
            'Stormy': 'weather-stormy'
        };
        return conditionMap[condition] || 'weather-cloudy';
    }

    updateDashboardWeather(data) {
        // Update main dashboard weather display
        const elements = {
            'current-temp': data.data.temperature.toFixed(1) + '°C',
            'current-condition': data.data.condition,
            'current-humidity': data.data.humidity + '%',
            'current-wind': data.data.wind_speed + ' km/h',
            'current-pressure': data.data.pressure + ' hPa'
        };

        Object.keys(elements).forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                element.textContent = elements[id];
            }
        });

        // Update prediction if available
        if (data.prediction) {
            this.updatePredictionDisplay(data.prediction);
        }

        if (data.quality) {
            const qualityElement = document.getElementById('ai-quality');
            if (qualityElement) {
                qualityElement.textContent = `Quality: ${data.quality.quality_label} (${(data.quality.confidence * 100).toFixed(0)}% confidence)`;
            }
        }

        if (data.decision) {
            const decisionElement = document.getElementById('ai-decision');
            if (decisionElement) {
                decisionElement.textContent = `Decision: ${data.decision.severity.toUpperCase()} - ${data.decision.recommended_action}`;
            }
        }
    }

    updatePredictionDisplay(prediction) {
        const predictionElement = document.getElementById('ai-prediction');
        if (predictionElement) {
            predictionElement.innerHTML = `
                <strong>AI Prediction:</strong> ${prediction.predicted_temperature}°C in 1 hour
                <small class="text-muted">(${(prediction.confidence * 100).toFixed(0)}% confidence)</small>
            `;
        }
    }

    displayWeatherData(data) {
        console.log('Weather data received:', data);
        this.updateDashboardWeather({
            location: data.location,
            data: data.current,
            prediction: data.prediction,
            quality: data.quality,
            decision: data.decision
        });
    }

    updatePredictions(data) {
        // Update AI prediction displays
        console.log('Prediction update:', data);
    }

    checkAlerts(data) {
        // Check if weather conditions trigger any alerts
        // This would integrate with your alert system
        if (data.data.temperature > 35) {
            this.showAlert('Heat Warning', 'Extreme temperature detected!', 'warning');
        }
        
        if (data.data.wind_speed > 50) {
            this.showAlert('Wind Warning', 'High wind conditions!', 'danger');
        }
    }

    showAlert(title, message, type = 'warning') {
        this.showNotification(`⚠️ ${title}: ${message}`, type);
    }

    showNotification(message, type = 'info') {
        // Create toast notification
        const toastContainer = document.getElementById('toast-container') || this.createToastContainer();
        
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-bg-${type} border-0`;
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        
        toastContainer.appendChild(toast);
        const bsToast = new bootstrap.Toast(toast);
        bsToast.show();
        
        // Remove toast after hide
        toast.addEventListener('hidden.bs.toast', () => {
            toast.remove();
        });
    }

    createToastContainer() {
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container position-fixed top-0 end-0 p-3';
        container.style.zIndex = '9999';
        document.body.appendChild(container);
        return container;
    }

    updateConnectionStatus(connected) {
        const indicator = document.getElementById('connection-indicator');
        if (indicator) {
            indicator.className = connected ? 'live-indicator' : 'live-indicator bg-danger';
            indicator.title = connected ? 'Connected' : 'Disconnected';
        }
    }

    // Method to request weather for specific location
    requestWeather(location) {
        this.socket.emit('request_weather', { location: location });
    }

    // Method to simulate AI training completion
    simulateAITraining() {
        this.showNotification('🤖 AI model training completed!', 'success');
        
        const aiStatus = document.querySelector('.ai-status');
        if (aiStatus) {
            aiStatus.textContent = 'Trained';
            aiStatus.className = 'ai-status trained';
        }
    }
}

// Initialize the weather system when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    window.weatherSystem = new WeatherSystem();
    
    // Add CSS for animations
    const style = document.createElement('style');
    style.textContent = `
        .weather-updating {
            animation: pulseUpdate 0.5s ease-in-out;
        }
        
        .temp-updating {
            color: #fbbf24 !important;
            transition: color 0.3s ease;
        }
        
        @keyframes pulseUpdate {
            0% { transform: scale(1); }
            50% { transform: scale(1.02); }
            100% { transform: scale(1); }
        }
        
        .toast {
            backdrop-filter: blur(10px);
            background: rgba(var(--bs-dark-rgb), 0.9) !important;
        }
    `;
    document.head.appendChild(style);
});

// Utility functions
function formatTemperature(temp) {
    return temp.toFixed(1) + '°C';
}

function getWeatherIcon(condition) {
    const icons = {
        'Clear': 'fa-sun',
        'Clouds': 'fa-cloud',
        'Rain': 'fa-cloud-rain',
        'Thunderstorm': 'fa-bolt',
        'Snow': 'fa-snowflake',
        'Sunny': 'fa-sun',
        'Cloudy': 'fa-cloud',
        'Rainy': 'fa-cloud-rain',
        'Stormy': 'fa-bolt'
    };
    return icons[condition] || 'fa-cloud';
}

// ============================================================
// AGRICULTURE MODULE JS
// ============================================================

/**
 * Fetch live health data for a field and render it into the page
 * (used on field_detail.html when the user clicks "Live Refresh")
 */
function loadLiveFieldHealth(fieldId) {
    const url = `/api/agri/health/${fieldId}`;
    fetch(url)
        .then(r => r.json())
        .then(data => {
            const h = data.health;
            if (!h) return;

            // Health score bar
            const bar = document.getElementById('live-health-bar');
            const label = document.getElementById('live-health-label');
            if (bar) {
                bar.style.width = h.health_score + '%';
                bar.className = 'progress-bar ' +
                    (h.health_score >= 75 ? 'bg-success' :
                     h.health_score >= 50 ? 'bg-warning' : 'bg-danger');
            }
            if (label) {
                label.textContent = h.health_score.toFixed(0) + '%';
                label.className = 'fw-bold ' +
                    (h.health_score >= 75 ? 'text-success' :
                     h.health_score >= 50 ? 'text-warning' : 'text-danger');
            }

            // Stress indicators
            _setText('live-heat-stress',     (h.heat_stress * 100).toFixed(0) + '%');
            _setText('live-frost-risk',      (h.frost_risk * 100).toFixed(0) + '%');
            _setText('live-drought-stress',  (h.drought_stress * 100).toFixed(0) + '%');
            _setText('live-excess-moisture', (h.excess_moisture * 100).toFixed(0) + '%');

            // Pest risks list
            const pestContainer = document.getElementById('live-pest-risks');
            if (pestContainer && data.pest_risks) {
                pestContainer.innerHTML = data.pest_risks.length
                    ? data.pest_risks.map(r =>
                        `<div class="d-flex align-items-start mb-2">
                           <span class="pest-pill pest-pill-${r.risk_level} me-2">${r.risk_level.toUpperCase()}</span>
                           <div><strong>${r.pest_type}</strong><br>
                                <small class="text-muted">${r.warning_message}</small></div>
                         </div>`).join('')
                    : '<p class="text-muted">No pest risks detected.</p>';
            }

            // Irrigation
            const irrEl = document.getElementById('live-irrigation');
            if (irrEl) {
                irrEl.innerHTML = data.irrigation
                    ? `<span class="irrigation-badge">💧 ${data.irrigation.volume_mm} mm needed</span>
                       <div class="text-muted small mt-1">${data.irrigation.reason}</div>`
                    : '<span class="text-success small">No irrigation deficit.</span>';
            }

            // Yield forecast
            const yf = data.yield_forecast;
            if (yf) {
                _setText('live-yield-expected',  yf.expected_yield_ton_ha + ' t/ha');
                _setText('live-yield-gap',       yf.gap_ton_ha + ' t/ha gap');
                _setText('live-yield-confidence', (yf.confidence * 100).toFixed(0) + '% confidence');
            }
        })
        .catch(err => console.error('Live health fetch error:', err));
}

function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

/**
 * Colour-code all health-score cells on the page for the agri dashboard
 */
function colourHealthCells() {
    document.querySelectorAll('[data-health-score]').forEach(el => {
        const score = parseFloat(el.dataset.healthScore);
        el.classList.remove('health-excellent', 'health-good', 'health-fair', 'health-poor');
        if (score >= 80)      el.classList.add('health-excellent');
        else if (score >= 65) el.classList.add('health-good');
        else if (score >= 45) el.classList.add('health-fair');
        else                  el.classList.add('health-poor');
    });
}

document.addEventListener('DOMContentLoaded', function () {
    colourHealthCells();

    // Auto-refresh live health if we are on a field detail page
    const fieldId = document.body.dataset.fieldId;
    if (fieldId) {
        loadLiveFieldHealth(fieldId);
        // Refresh every 5 minutes
        setInterval(() => loadLiveFieldHealth(fieldId), 300000);
    }
});
