(function () {
    'use strict';

    // ---- Named particle limits (configurable cap for the wind overlay) ----
    const MAX_PARTICLES = 3000;
    const MAX_AGE = 90;

    const config = window.SWSMapConfig || {};
    const geojsonUrl = config.geojsonUrl || '/api/agri/map-data';
    const map = L.map(config.mapId || 'satelliteMap').setView([30.3769, 69.3478], 6);
    const tiles = {
        esri: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Tiles &copy; Esri', maxZoom: 19, maxNativeZoom: 17
        }),
        'esri-labels': L.layerGroup()
            .addLayer(L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                attribution: 'Tiles &copy; Esri', maxZoom: 19, maxNativeZoom: 17
            }))
            .addLayer(L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
                transparent: true, attribution: ''
            })),
        osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors', maxZoom: 19
        }),
        dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; OpenStreetMap contributors &copy; CARTO', maxZoom: 19
        })
    };

    // ---- State ----
    let windLayer = null;
    let windParticles = [];
    let windFrame = null;
    let windGrid = null;
    let windLastTime = 0;
    let geoJsonLayer = null;
    let userLocationMarker = null;
    let watchId = null;
    let selectedFeature = null;     // currently selected GeoJSON feature (for popup)
    let satCache = {};              // location name -> parsed satellite summary

    const t = (key) => (window.SWSI18n ? window.SWSI18n.t(key) : key);

    function windVector(u, v) {
        return { speed: Math.sqrt((u * u) + (v * v)), angle: Math.atan2(v, u) };
    }

    function interpolateWind(lat, lon) {
        if (!windGrid || !windGrid.points.length) return { u: 0, v: 0 };
        let best = windGrid.points[0];
        let distance = Infinity;
        windGrid.points.forEach((point) => {
            const nextDistance = Math.abs(point.latitude - lat) + Math.abs(point.longitude - lon);
            if (nextDistance < distance) {
                distance = nextDistance;
                best = point;
            }
        });
        return best;
    }

    function createWindLayer() {
        const canvas = L.DomUtil.create('canvas', 'wind-particle-canvas');
        canvas.style.pointerEvents = 'none';
        const layer = L.Layer.extend({
            onAdd() {
                const pane = map.getPane('overlayPane');
                pane.appendChild(canvas);
                this._resize();
                map.on('move resize zoom', this._resize, this);
                windLastTime = performance.now();
                windFrame = requestAnimationFrame(animateWind);
            },
            onRemove() {
                map.off('move resize zoom', this._resize, this);
                if (windFrame) cancelAnimationFrame(windFrame);
                if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
            },
            _resize() {
                const size = map.getSize();
                const pixelRatio = window.devicePixelRatio || 1;
                canvas.width = size.x * pixelRatio;
                canvas.height = size.y * pixelRatio;
                canvas.style.width = `${size.x}px`;
                canvas.style.height = `${size.y}px`;
                canvas.getContext('2d').setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
                windParticles = [];
            }
        });
        return new layer();
    }

    function seedParticles(width, height) {
        while (windParticles.length < MAX_PARTICLES) {
            windParticles.push({
                x: Math.random() * width,
                y: Math.random() * height,
                age: Math.floor(Math.random() * MAX_AGE),
                maxAge: MAX_AGE
            });
        }
    }

    function animateWind(timestamp) {
        if (!windLayer || !windLayer._map) return;
        const canvas = document.querySelector('.wind-particle-canvas');
        if (!canvas) return;
        const context = canvas.getContext('2d');
        const width = map.getSize().x;
        const height = map.getSize().y;
        seedParticles(width, height);
        const delta = Math.min((timestamp - windLastTime) / 16.67, 3);
        windLastTime = timestamp;
        context.globalCompositeOperation = 'destination-in';
        context.fillStyle = 'rgba(0, 0, 0, 0.92)';
        context.fillRect(0, 0, width, height);
        context.globalCompositeOperation = 'source-over';
        context.strokeStyle = 'rgba(77, 208, 225, 0.7)';
        context.lineWidth = 1;
        windParticles.forEach((particle) => {
            const before = { x: particle.x, y: particle.y };
            const point = map.containerPointToLatLng([particle.y, particle.x]);
            const vector = windVector(...Object.values(interpolateWind(point.lat, point.lng)));
            particle.x += Math.cos(vector.angle) * Math.max(vector.speed, 1) * 0.45 * delta;
            particle.y -= Math.sin(vector.angle) * Math.max(vector.speed, 1) * 0.45 * delta;
            particle.age += delta;
            if (particle.age >= MAX_AGE || particle.x < 0 || particle.y < 0 || particle.x > width || particle.y > height) {
                particle.x = Math.random() * width;
                particle.y = Math.random() * height;
                particle.age = 0;
                return;
            }
            context.beginPath();
            context.moveTo(before.x, before.y);
            context.lineTo(particle.x, particle.y);
            context.stroke();
        });
        windFrame = requestAnimationFrame(animateWind);
    }

    // Load the wind grid for the current map centre so the particle
    // overlay always reflects the visible region instead of a hardcoded point.
    async function loadWind() {
        const badge = document.getElementById('mapStatusBadge');
        try {
            if (badge) badge.textContent = t('map.wind_loading');
            const center = map.getCenter();
            const response = await fetch(`/api/weather/grid?latitude=${center.lat}&longitude=${center.lng}`);
            if (!response.ok) throw new Error(`wind grid ${response.status}`);
            windGrid = await response.json();
            if (window.__windLayerCreated) {
                // re-bind the canvas to the new grid
                windParticles = [];
            }
            if (!windLayer) {
                windLayer = createWindLayer();
                window.__windLayerCreated = true;
                const toggle = document.getElementById('windOverlayToggle');
                if (toggle && toggle.checked) windLayer.addTo(map);
            }
            document.dispatchEvent(new CustomEvent('sws:wind-ready'));
            if (badge) badge.textContent = t('map_ready') || 'Ready';
        } catch (error) {
            console.warn('Wind particle overlay unavailable:', error);
            document.dispatchEvent(new CustomEvent('sws:wind-unavailable'));
            if (badge) badge.textContent = t('map.wind_unavailable');
        }
    }

    // ------------------------------------------------------------------
    // Satellite data — fetched live from the Open-Meteo-backed API and
    // rendered inside farm/field popups as well as the dedicated panel.
    // ------------------------------------------------------------------
    async function fetchSatelliteData(location) {
        if (!location) return null;
        if (satCache[location]) return satCache[location];
        try {
            const response = await fetch(`/api/agri/openmeteo?location=${encodeURIComponent(location)}&datasets=satellite`);
            if (!response.ok) throw new Error(`satellite ${response.status}`);
            const json = await response.json();
            const sat = json.satellite;
            satCache[location] = sat || null;
            return sat || null;
        } catch (error) {
            console.warn('Satellite data unavailable for', location, error);
            satCache[location] = null;
            return null;
        }
    }

    function satelliteSummaryHtml(sat) {
        if (!sat || !sat.available) {
            return `<div class="small text-muted fst-italic"><i class="fas fa-cloud-slash me-1"></i>${t('map.satellite_unavailable')}</div>`;
        }
        const summary = sat.summary || {};
        const swr = summary.mean_shortwave_radiation ?? '—';
        const clr = summary.mean_clear_sky_radiation ?? '—';
        const pts = summary.data_points ?? '—';
        const ts = (sat.timestamp || '').split('T')[0] || '—';
        return `<div class="small">
            <div class="d-flex justify-content-between py-1"><span>SW Radiation (W/m²)</span><strong>${swr}</strong></div>
            <div class="d-flex justify-content-between py-1"><span>Clear-Sky (W/m²)</span><strong>${clr}</strong></div>
            <div class="d-flex justify-content-between py-1"><span>Data points (7-day)</span><strong>${pts}</strong></div>
            <div class="d-flex justify-content-between py-1"><span>Last update</span><small class="text-muted">${ts}</small></div>
            ${sat.attribution ? `<div class="mt-1"><small class="text-muted fst-italic">${sat.attribution}</small></div>` : ''}
        </div>`;
    }

    async function updateSatellitePanel(location) {
        const panel = document.getElementById('satelliteLiveData');
        if (!panel || !location) return;
        panel.innerHTML = `<div class="small text-muted"><i class="fas fa-spinner fa-pulse me-1"></i>${t('map.wind_loading')}</div>`;
        const sat = await fetchSatelliteData(location);
        panel.innerHTML = sat ? satelliteSummaryHtml(sat) : satelliteSummaryHtml(null);
    }

    // ------------------------------------------------------------------
    // GeoJSON — farms & fields rendered with REAL geocoded coordinates
    // from /api/agri/map-data (server-side geocode_location), replacing the
    // brittle cityCoords fallback that piled every marker on one point.
    // ------------------------------------------------------------------
    function iconFor(type) {
        if (type === 'farm') {
            return L.divIcon({ className: 'farm-marker', html: '<i class="fas fa-map-marker-alt fa-lg text-danger drop-shadow"></i>', iconSize: [24, 24], iconAnchor: [12, 24] });
        }
        return L.divIcon({ className: 'field-marker', html: '<i class="fas fa-circle-notch fa-lg text-teal"></i>', iconSize: [18, 18], iconAnchor: [9, 18] });
    }

    function popupFor(feature) {
        const f = feature.properties;
        const title = f.type === 'farm' ? f.name : `${f.name} (${f.farm_name})`;
        const typeLabel = f.type === 'farm' ? t('farm') : t('field');
        const location = f.location || '';
        const area = f.area_ha || (f.type === 'farm' ? f.area_ha : f.area_ha);
        let actions = `<a href="/agri" class="btn btn-xs btn-outline-secondary">${t('dashboard')}</a>`;
        if (f.farm_id) {
            actions = `<a href="/farms/${f.farm_id}" class="btn btn-xs btn-outline-primary me-1">${t('view_fields')}</a>` + actions;
        }
        return `<div class="p-2">
            <div class="fw-bold text-primary mb-1">🚜 ${title}</div>
            <div class="small mb-1"><i class="fas fa-layer-group me-1"></i>${typeLabel}</div>
            <div class="small mb-1"><i class="fas fa-user me-1"></i>${t('owner')}: ${f.owner || f.farm_name || ' — '}</div>
            <div class="small mb-1"><i class="fas fa-map-marker-alt me-1"></i>${t('location')}: ${location || ' — '}</div>
            <div class="small mb-1"><i class="fas fa-tag me-1"></i>${t('area')}: ${area != null ? area + ' ha' : ' — '}</div>
            <div class="mt-2" id="satPopup_${f.type}_${f[f.type + '_id']}"><i class="fas fa-spinner fa-pulse me-1"></i>${t('map.wind_loading')}</div>
            <div class="mt-2">${actions}</div>
        </div>`;
    }

    async function refreshPopupSatellite(feature) {
        const f = feature.properties;
        const elId = `satPopup_${f.type}_${f[f.type + '_id']}`;
        const el = document.getElementById(elId);
        if (!el) return;
        const location = f.location || '';
        const sat = await fetchSatelliteData(location);
        el.innerHTML = satelliteSummaryHtml(sat);
    }

    function onFeature(feature, layer) {
        layer.bindPopup(popupFor(feature));
        layer.on('popupopen', () => {
            selectedFeature = feature;
            const loc = feature.properties.location || '';
            updateSatellitePanel(loc);
            refreshPopupSatellite(feature);
        });
    }

    function pointToLayer(feature, latlng) {
        return L.marker(latlng, { icon: iconFor(feature.properties.type) });
    }

    async function loadMapData() {
        try {
            const response = await fetch(geojsonUrl);
            if (!response.ok) throw new Error(`geojson ${response.status}`);
            const data = await response.json();
            if (geoJsonLayer) map.removeLayer(geoJsonLayer);
            geoJsonLayer = L.geoJSON(data, {
                pointToLayer: pointToLayer,
                onEachFeature: onFeature,
            }).addTo(map);
            const bounds = geoJsonLayer.getBounds();
            if (bounds.isValid()) map.fitBounds(bounds.pad(0.3));
            document.getElementById('mapStatusBadge').textContent = t('map_ready') || 'Ready';
        } catch (error) {
            console.error('Failed to load map data:', error);
            document.getElementById('mapStatusBadge').textContent = t('map.wind_unavailable');
        }
    }

    // ------------------------------------------------------------------
    // Real-time user geolocation marker
    // ------------------------------------------------------------------
    function locateUser() {
        const btn = document.getElementById('locateMeBtn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-pulse"></i>';
        }
        if (!navigator.geolocation) {
            console.warn('Geolocation not supported by this browser');
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-location-dot"></i>'; }
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const { latitude, longitude } = pos.coords;
                if (userLocationMarker) map.removeLayer(userLocationMarker);
                userLocationMarker = L.circleMarker([latitude, longitude], {
                    radius: 9, fillColor: '#2196f3', color: '#ffffff', weight: 2.5,
                    opacity: 1, fillOpacity: 0.9, className: 'user-location-marker'
                })
                    .addTo(map)
                    .bindPopup(t('map.user_location') || 'Your location')
                    .openPopup();
                map.setView([latitude, longitude], 11);
                reverseGeocodeAndSet(latitude, longitude);
                // Watch position for real-time movement tracking
                watchId = navigator.geolocation.watchPosition(
                    (p) => {
                        if (userLocationMarker && window.SWSMapConfig.trackUser) {
                            userLocationMarker.setLatLng([p.coords.latitude, p.coords.longitude]);
                        }
                    },
                    () => {},
                    { enableHighAccuracy: false, maximumAge: 10000, timeout: 8000 }
                );
            },
            (err) => {
                console.warn('Geolocation denied:', err);
                if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-location-dot"></i>'; }
            },
            { enableHighAccuracy: true, timeout: 5000 }
        );
    }

    // Reverse-geocode coordinates to a location name for satellite-data fetch.
    async function reverseGeocodeAndSet(lat, lon) {
        try {
            const response = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=4`, {
                headers: { 'User-Agent': 'SmartAgriWeather/1.0' }
            });
            if (response.ok) {
                const data = await response.json();
                const addr = data.address || {};
                const locName = addr.city || addr.town || addr.village || addr.county || addr.state || 'Lahore';
                updateSatellitePanel(locName);
                return;
            }
        } catch (error) {
            console.warn('Reverse geocoding failed:', error);
        }
        // Fallback
        fetchSatelliteData('Lahore');
        updateSatellitePanel('Lahore');
    }

    function stopWatching() {
        if (watchId !== null) {
            navigator.geolocation.clearWatch(watchId);
            watchId = null;
        }
    }

    // ------------------------------------------------------------------
    // i18n: refresh popups / panel text when locale changes
    // ------------------------------------------------------------------
    function refreshPopups() {
        if (!geoJsonLayer) return;
        geoJsonLayer.eachLayer((layer) => {
            if (layer.getPopup && layer.getPopup() && layer.feature) {
                layer.setPopupContent(popupFor(layer.feature));
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        tiles.esri.addTo(map);
        loadMapData();
        loadWind();
        document.getElementById('tileLayerSelect')?.addEventListener('change', (event) => {
            Object.values(tiles).forEach((layer) => { if (map.hasLayer(layer)) map.removeLayer(layer); });
            tiles[event.target.value].addTo(map);
        });
        document.getElementById('windOverlayToggle')?.addEventListener('change', (event) => {
            if (event.target.checked && windLayer) windLayer.addTo(map);
            if (!event.target.checked && windLayer) map.removeLayer(windLayer);
        });
        const locateBtn = document.getElementById('locateMeBtn');
        if (locateBtn) locateBtn.addEventListener('click', locateUser);

        // Re-fetch wind grid when the map settles on a new centre
        map.on('moveend', () => {
            if (windGrid) loadWind();
        });

        document.addEventListener('sws:locale-changed', () => {
            refreshPopups();
            // refresh the live panel header / content
            const header = document.getElementById('satellitePanelHeader');
            if (header) header.textContent = t('map.live_data');
        });
    });
})();
