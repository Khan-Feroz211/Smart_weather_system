# KisanAI Android — Build TODO

Status: In progress. Working dir: `D:\SWS\Smart_weather_system`. Git remote: `Khan-Feroz211/Smart_weather_system` on `main`.

## Environment constraints (verified)
- Node v24 + npm available. npm registry reachable.
- Java/JDK **NOT** installed; Android SDK has cmdline-tools/emulator/platform-tools but **no platforms or build-tools**.
  -> APK build cannot run in this environment. Setup + build instructions are documented; user builds on their machine.
- `kisanai.html` prototype **not present** anywhere (disk + git history). `rainConfidence()`/`localSkill()`/`T` reconstructed from backend contracts + feature spec (flagged in deliverables).

## Backend contract verification (from code, NOT README)
Core: api_routes.py (v6.2-hybrid), app_clean.py hazard routes, auth.py, totp_auth.py, multi_hazard.py, recommendation_engine.py, edge_case_hardening_v2.py.

### Verified endpoints
- `GET  /api/agri/status`  -> {engine_version, accuracy_monitor, auth_enabled, deep_learning{available,torch_installed,model_trained,classes}, weather{provider,openweather_key_configured}}
- `POST/GET /api/agri/analyze` -> crop, symptoms{}, site{temp,humidity,rainfall,wind_speed,pressure}, stage, location, connectivity_state(ONLINE/CACHE/OFFLINE), data_age_hours, compact, image(base64|multipart|raw), top_k
  -> {status_header, generated_at, engine_version, crop, growth_stage, connectivity_state, diagnoses[](confidence_calibrated,confidence_raw,disease,urgency,explanation,evidence,disclaimer,suggested_next_question,source, ...), image_analysis, geovis_satellite, yield_estimate, crop_suitability}
- `POST /api/agri/disease-diagnose` -> image(+crop,top_k) -> predictions[].confidence (top-1); <0.7 => "Not sure, retake"
- `POST /api/agri/feedback` -> {crop, predicted_disease, actual_disease, is_simulated, is_offline} -> {status,message,note}
- `POST /api/agri/sync-feedback` -> {synced_count}
- `GET  /api/agri/openmeteo` -> satellite/flood/climate/seasonal
- `GET  /api/hazards/predict` -> {success, location, alert{overall_risk_level, active_hazards, confidence, confidence_label, confidence_penalty_reasons, recommended_actions, hazard_probabilities, degradation_info{mode,data_source,data_age_hours,stale_data_warning,api_error}}, offline_sms}
- `GET  /api/hazards/alerts` -> {success, alerts[], count}
- `GET  /api/hazards/dashboard` -> {success, dashboard}

### README vs CODE mismatches (trust code)
1. README says POST /api/hazards/predict (line 521) but code defines GET-only (app_clean.py:3733). test_endpoints.py uses GET. FIX RECOMMENDED.
2. Ports: README Quick Start + start_app.py + Dockerfile + docker-compose = 8000. But README lines 1065/1078/2328 reference 5000. FIX RECOMMENDED (document 8000 as canonical).
3. README AgriAdvisor API table omits: /api/agri/disease-diagnose (POST), /api/agri/sync-feedback (POST), /api/agri/openmeteo (GET), /api/agri/train-disease-model (POST). FIX RECOMMENDED.
4. README omits /api/hazards/alerts entirely. FIX RECOMMENDED.
5. README auth section emphasizes email/password only; phone_number (E.164) is stored in metadata but NOT supported as a login identifier (Supabase email/password). Mobile phone-as-login requires new token endpoint. (Backend change #1)
6. /api/hazards/predict reads ONLY query params (request.args) — contradicts "POST with body" intent. (route fix candidate)

## Mobile auth design (proposed token endpoint — backend change needed)
- New Flask endpoints (to be added server-side):
  - `POST /api/mobile/login` {phone_number|E.164, password?, email?} -> issues bearer token + {user, requires_2fa}. Verifies via Supabase + TOTP.
  - `POST /api/mobile/verify-totp` {temp_token, code} -> finalizes token when 2FA enabled.
  - `POST /api/mobile/send-otp` {phone_number} -> (future) SMS OTP for passwordless.
  - `before_request` hook: accept `Authorization: Bearer <token>` (validate vs token store) as alternative to X-API-Key + session cookie; do NOT require CSRF for stateless bearer calls.
- Mobile client: never stores passwords. Token kept in Android Keystore via @capawesome/capacitor-preferences (encrypted). TOTP 2FA supported; phone login (E.164) supported.
- If backend token endpoint absent (404) -> app degrades to offline mode with cached rule-based data.

## Directory layout (new /kisanai/ keeps mobile app separate from Flask frontend)
kisanai/{ package.json, capacitor.config.ts, src/, www/{ index.html, styles.css, app.js, i18n.js, config.js, sw.js, manifest.json, src/{api.js,auth.js,storage.js,location.js,camera.js,notifications.js,ui.js,router.js}, assets/{fonts,icons,img} }, scripts/{gen-config.js,tests.mjs,fetch-fonts.js}, README.md, android/(generated) }

## Build order (commit per feature)
- [x] Scaffolding: package.json, capacitor.config.ts, config.js, scripts/gen-config.js, manifest.json
- [x] i18n.js (T table, EN+UR, RTL)
- [x] src/api.js (sole network module: 10s timeout, backoff, bearer token, connectivity badge, all verified contracts)
- [x] src/auth.js (Keystore token, TOTP 2FA verified vs RFC 6238, phone login, 404→offline degradation)
- [x] Connectivity badge mapping (ONLINE/CACHE/OFFLINE + penalty, EN+UR)
- [x] F1 Home: rainConfidence(), localSkill(), 5-day strip, decision card, Open-Meteo fallback
- [x] F2 Location: GPS + district picker, permission-denied handling, no hard-coded Chakwal (Lahore default)
- [x] F3 Crop scan: camera, resize<=800px JPEG, diagnose, <70% -> "Not sure, retake"
- [x] F4 Symptom checker: /api/agri/analyze UI, fully offline rule-based cached
- [x] F5 Fields & soil: SQLite repository (add/edit fields + soil test values) — getSQLite() returns {SQLiteConnection, CapacitorSQLite}
- [x] F6 Rain feedback loop: rain/no-rain by forecast date, queue + backoff retry (api.submitFeedback + syncFeedback drain)
- [x] F7 Alerts: local notifications (daily prompt + hazards), dedup + severity colors (green/yellow/orange/red)
- [x] F8 Offline: service worker caching + data-age labels (stale-while-revalidate + x-data-age-hours)
- [x] UX: RTL logical CSS, 48dp targets, Noto Nastaliq font + line-height, TTS (Urdu), back button, rotation, skeletons
- [x] Native: network_security_config (no cleartext), permissions (loc/cam/notif), back-button
- [x] README + debug/signed build guides + test checklist + backend-changes list
- [x] Smoke-test pure logic (rainConfidence/localSkill/connectivity/feedback/TOTP/soil/ui/camera/notifications) with Node — 91 assertions pass
- [ ] Commit per feature + push to GitHub
