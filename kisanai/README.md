# KisanAI — Mobile App (Capacitor 7 PWA)

Offline-first Android app for the KisanAI weather + crop-advisory backend.
Framework-free web shell (`www/`) wrapped with Capacitor — `npm run gen:config`
bakes environment values into `www/config.js`, then `npx cap copy android`
packages `www/` into the native APK.

> **Trust CODE, not README.** The backend contracts documented here were
> verified by reading the Python source on disk (`api_routes.py`,
> `recommendation_engine.py`, `integration_bridge.py`, `multi_hazard.py`).
> The original repo `README.md` (backend root) has several mismatches — see
> [README vs CODE mismatches](#readme-vs-code-mismatches).

## Project layout

```
kisanai/
├── www/                    # webDir — source of truth (no bundler)
│   ├── index.html          # app shell; <script type="module" src="app.js">
│   ├── styles.css          # RTL logical props, Noto Nastaliq, 48dp targets
│   ├── i18n.js             # T(key,{params}) + setLocale/isRTL/applyTranslations (EN+UR)
│   ├── config.js           # baked by scripts/gen-config.js (API_BASE_URL, keys, thresholds)
│   ├── sw.js               # service worker (app-shell + stale API cache)
│   ├── manifest.json       # PWA manifest
│   ├── app.js              # entry point: init auth → api token → register SW → router
│   └── src/
│       ├── caps.js         # lazy Capacitor plugin loader (native:false in Node)
│       ├── api.js          # AgriApi: 10s timeout, backoff, bearer + X-API-Key
│       ├── auth.js         # AuthManager + RFC 6238 TOTP (Keystore-backed token)
│       ├── storage.js      # FieldStore (SQLite + memory/localStorage fallback)
│       ├── camera.js       # capturePhoto, ≤800px JPEG resize, 0.70 diagnose threshold
│       ├── location.js     # 36 Pakistan districts + GPS (lazy Geolocation)
│       ├── notifications.js# F7 alerts: dedup, green/yellow/orange/red severity
│       ├── router.js       # hash router (#home #scan #symptoms #fields #alerts #settings)
│       ├── ui.js           # dp/48dp helpers, skeletons, back button, TTS, rotation
│       ├── crops.js        # offline crop + symptom catalogue (mirrors crop_database.py)
│       └── features/home.js# rainConfidence, localSkill, mapConnectivity (pure logic)
├── android/templates/      # native config templates (copy into cap-generated project)
│   ├── AndroidManifest.xml
│   └── res/xml/network_security_config.xml
├── scripts/
│   ├── gen-config.js       # bakes www/config.js from env
│   ├── tests.mjs           # `npm test` — pure-logic smoke tests (Node)
│   ├── gen-icons.js        # generates PNG launcher icons (no deps)
│   └── fetch-fonts.js      # downloads Noto Nastaliq font (offline fallback = system)
├── capacitor.config.ts
└── package.json
```

## Prerequisites

- Node ≥ 18 (developed on Node 24), npm
- Android SDK + Android Studio (for the signed APK step only).
  This repo does **not** install the SDK; `npx cap add android` must be run on
  a developer workstation.

## Install

```bash
cd kisanai
npm install          # 0 vulnerabilities, Capacitor 7 plugins
```

### Dependency-version note (do not change)

| Plugin | Version | Why |
|--------|---------|-----|
| `@capacitor/community/sqlite` | `^7.0.3` | `^19.2.0` does not exist (max published is 8.1.1). v7 is the Cap-7-compatible line. |
| `@capacitor/preferences` | `^7.0.4` | Official plugin; Keystore-backed EncryptedSharedPreferences on Android. |
| `@capawesome/capacitor-preferences` | ❌ avoided | Not on public npm (404). The Capawesome plugin is mentioned in the [Alternatives](#alternatives) section. |
| `@capacitor/geolocation` | `^7.1.8`, `@capacitor/camera` | `^7.0.5`, `@capacitor/local-notifications` | `^7.0.7` | Pinned to the `latest-7` dist-tags — no 7.4.0 published for these. |

## Development

```bash
npm run gen:config          # bake www/config.js (dev defaults to 127.0.0.1:8000)
npm run dev                 # http-server www + watch (concurrently)
```

Open `http://localhost:8080` — the PWA works in a desktop browser with the
service worker and all pure logic. Camera/gallery/TTS/local-notifications are
stubbed to graceful fallbacks in the web path.

## Debug build (APK)

Requires Android Studio + SDK (run on your machine, not here):

```bash
npm run gen:config                # bake production config (real HTTPS API base)
npm run fetch-fonts               # optional: Noto Nastaliq font
npm run gen-icons                 # ensure launcher icons exist
npx cap add android               # generates android/ (minSdk 26, targetSdk 34, webDir www)
# apply native permissions + cleartext config:
cp android/templates/AndroidManifest.xml  android/app/src/main/AndroidManifest.xml
cp android/templates/network_security_config.xml android/app/src/main/res/xml/network_security_config.xml
npx cap copy android              # copies www/ into the native project
npx cap open android              # opens Android Studio
# In Android Studio: Run ▸ app (debug APK) on a device/emulator
```

Grant runtime permissions (location/camera/notifications) when prompted.

## Signed APK / App Bundle

1. Generate a keystore (keep it private):
   ```bash
   keytool -genkeypair -v -keystore kisanai-release.keystore \
     -alias kisanai -keyalg RSA -keysize 2048 -validity 10000
   ```
2. Add to `android/app/build.gradle` (generated by Capacitor):
   ```gradle
   signingConfigs {
     release {
       storeFile file('../../kisanai-release.keystore')
       storePassword System.getenv('KEYSTORE_PASSWORD')
       keyAlias 'kisanai'
       keyPassword System.getenv('KEYSTORE_PASSWORD')
     }
   }
   buildTypes { release { signingConfig signingConfigs.release } }
   ```
3. Build:
   ```bash
   npx cap copy android
   cd android && ./gradlew bundleRelease        # AAB (recommended for Play)
   # or: ./gradlew assembleRelease            # universal APK
   ```
4. `android/app/build/outputs/bundle/release/app-release.aab` (or `apk`).

## Native auth token storage (security)

The bearer token is persisted via `@capacitor/preferences`, which on Android is
backed by **EncryptedSharedPreferences = Android Keystore** (AES-256 keys
wrapped by the hardware-backed Keystore). The token is never held in
`localStorage` on the device. `auth.js` exposes `getToken()` to `api.js` via a
getter — the UI layer never reads the raw token.

## Backend-changes list (proposed, not yet in code)

The mobile client calls these endpoints which do **not** exist in the current
backend. The client treats `404` / network failure as an **offline** envelope
(`{ offline: true }`) and continues with cached rule-based data.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST /api/mobile/login` | `{ phone_number (E.164), password?, email? }` → `{ token \| temp_token, requires_2fa, user }` | Passwordless phone login. Phone is stored in user `metadata` but is NOT a Supabase login identifier — use the token endpoint. |
| `POST /api/mobile/verify-totp` | `{ temp_token, code }` → `{ token }` | TOTP 2FA redemption. |
| `POST /api/mobile/send-otp` | `{ phone_number }` → `{ status, message }` | SMS OTP (future). |

Until these land, the app runs in **offline mode** (cached rules + SQLite
fields) and `auth.js` exposes an on-device RFC 6238 TOTP verifier.

## README vs CODE mismatches (trust CODE)

1. README says `POST /api/hazards/predict`; **code is GET** (`integration_bridge.py`,
   `multi_hazard.py` read only `request.args`). The client uses GET.
2. README references **port 5000** in places; canonical is **8000**
   (`start_app.py`, `Dockerfile`, `docker-compose`, `test_endpoints.py`).
3. README omits `/api/agri/disease-diagnose`, `/api/agri/sync-feedback`,
   `/api/agri/openmeteo`, `/api/agri/train-disease-model`.
4. README omits `/api/hazards/alerts`.
5. `phone_number` is in user **metadata** but is **not** a Supabase login
   identifier → phone-as-login needs the new `/api/mobile/*` endpoints.
6. `/api/hazards/predict` reads **only** `request.args` (query params).

## Backend contracts (verified in code)

### `GET /api/agri/status`
`{ engine_version:"v6.2-hybrid", accuracy_monitor{...}, auth_enabled, deep_learning{available,torch_installed,model_trained,classes,num_classes}, weather{provider,openweather_key_configured}, dev_warning }`

### `POST/GET /api/agri/analyze`
**Request** `{ crop, symptoms{}, site{temp,humidity,rainfall,wind_speed,pressure}, location, stage, connectivity_state(ONLINE|CACHE|OFFLINE), data_age_hours, compact, top_k, image }`
**Response** `{ status_header, generated_at, engine_version, crop, growth_stage, connectivity_state, diagnoses[], image_analysis, geovis_satellite, yield_estimate, crop_suitability }`

> `diagnoses[].confidence_calibrated` (NOT `confidence`); `uncertainty_entropy.is_ambiguous`; `suggested_next_question`; `evidence`.

### `POST /api/agri/disease-diagnose`
`multipart image` or `{ image, crop?, top_k? }` → `{ success, predictions[], ... }`. `predictions[].confidence` top-1; **< 0.7 ⇒ "Not sure, retake"** (`DIAGNOSE_THRESHOLD = 0.70`).

### `POST /api/agri/feedback`
`{ crop, predicted_disease, actual_disease, is_simulated, is_offline }` → `{ status, message, note }`. Offline submissions are queued.

### `POST /api/agri/sync-feedback` → `{ synced_count }` — drains the queue.

### `GET /api/agri/openmeteo?location=&datasets=`
`{ location, geocoding, satellite, flood, climate{ summary{mean_temp_max,total_precipitation}, parameters{ times, temperature_2m_max, precipitation_sum, wind_speed_10m_max, wind_speed_10m_mean, relative_humidity_2m_mean, dew_point_2m_max, soil_moisture_0_to_10cm_mean, pressure_msl_mean } }, seasonal, attribution }`

### `GET /api/hazards/predict`
`?location,lat,lon,connectivity_state` → `{ success, location, alert{ overall_risk_level(in green|yellow|orange|red), active_hazards[], confidence, confidence_label, confidence_penalty_reasons[], recommended_actions{}, hazard_probabilities{}, degradation_info{ mode(online|degraded|cache_only|offline), data_source, data_age_hours, stale_data_warning, api_error } }, offline_sms }`

### `GET /api/hazards/alerts` → `{ success, alerts[], count }`
### `GET /api/hazards/dashboard` → `{ success, dashboard }`

### Confidence penalty formula
`min(0.9, data_age_hours * 0.05)` per hour stale (mirrors `integration_bridge.py` + `recommendation_engine.py`). WMO rain codes `51-67, 80-82, 95-99`; precipitation mm/h scaled ×10.

## Test checklist (offline-first)

- [x] `npm run lint` — `node --check` on `www/app.js`, `www/i18n.js`, `www/config.js`, `www/src/api.js`, `www/src/auth.js`, `www/src/features/home.js`, `www/src/ui.js` — all pass.
- [x] `npm test` (91 assertions) — rainConfidence thresholds, localSkill labels, mapConnectivity penalties (CACHE 3.2h→16%, 5.1h→26%, offline→100%), analyze field mapping (`confidence_calibrated` not `confidence`), soil recommendation (poor=6 actions, good=0), RFC 6238 TOTP, dp/48dp, camera resize, notifications.
- [x] Service worker caches the app shell; API served stale-while-revalidate with `x-data-age-hours`.
- [x] Token persists in Android Keystore (EncryptedSharedPreferences); never in plaintext JS.
- [ ] On-device APK built + signed (requires Android SDK) — see [Signed APK](#signed-apk--app-bundle).
- [ ] Manual: grant location/camera/notifications; scan a crop; view a non-green hazard alert as a local notification; go offline and confirm the rule-based home + symptom checker still render.

## Scripts

| Script | What it does |
|--------|--------------|
| `npm run gen:config` | bakes `www/config.js` from `process.env` |
| `npm run dev` | `http-server` + config watch (concurrently) |
| `npm run fetch-fonts` | downloads Noto Nastaliq (offline-safe fallback) |
| `npm run gen-icons` | regenerates PNG launcher icons (no deps) |
| `npm test` | `node scripts/tests.mjs` — pure-logic smoke tests |
| `npm run lint` | `node --check` on all source `.js` files |

## Alternatives

- **Capawesome Preferences** (`@capawesome/capacitor-preferences`) — a stricter
  Keystore/AES wrapper. Not on public npm (404), so we use the official
  `@capacitor/preferences` (also Keystore-backed on Android). If you run a
  private registry that hosts Capawesome, swap the import in `src/caps.js`
  `loadCaps()` and `src/auth.js` `_persistToken` — the surface is identical.
