-- =============================================================================
-- Smart Weather System -- OPTIONAL demo seed (safe to re-run)
-- =============================================================================
-- The Flask app already seeds the same demo rows at start-up when
-- SEED_DEMO_DATA=true (default), so you normally do NOT need this file.
-- Use it if you prefer to seed manually and set SEED_DEMO_DATA=false.
-- Contains NO credentials and NO real personal data.
-- Do not run it if you import your SQLite data (migration script) -- the ids overlap.

INSERT INTO public.users (username, email, location, preferences)
VALUES ('farm_owner', 'owner@agri.pk', 'Lahore', '{"preferred_activities": ["farming", "irrigation"]}')
ON CONFLICT DO NOTHING;

INSERT INTO public.farms (farm_id, name, owner_name, location, total_area_ha) VALUES
    (1, 'Green Valley Farm',    'Ahmed Khan',   'Karachi', 15.5),
    (2, 'Punjab Wheat Estate',  'Muhammad Ali', 'Lahore',  25.0)
ON CONFLICT DO NOTHING;

INSERT INTO public.fields (field_id, farm_id, name, area_ha, soil_type, irrigation_type) VALUES
    (1, 1, 'North Field',  5.5, 'Loamy',      'Drip'),
    (2, 1, 'South Field',  4.0, 'Clay',       'Furrow'),
    (3, 1, 'West Field',   6.0, 'Sandy Loam', 'Sprinkler'),
    (4, 2, 'Alpha Field', 12.0, 'Clay Loam',  'Flood'),
    (5, 2, 'Beta Field',  13.0, 'Silty Clay', 'Drip')
ON CONFLICT DO NOTHING;

INSERT INTO public.crop_census (census_id, field_id, crop_type, area_ha, growth_stage, planted_date, expected_harvest_date, target_yield_ton_ha, season) VALUES
    (1, 1, 'Wheat',  5.5, 'Tillering',         '2025-11-01', '2026-04-15', 3.5, 'Rabi 2025-26'),
    (2, 2, 'Wheat',  4.0, 'Heading',           '2025-10-15', '2026-03-30', 3.2, 'Rabi 2025-26'),
    (3, 3, 'Cotton', 6.0, 'Boll Formation',    '2025-05-01', '2025-10-31', 2.8, 'Kharif 2025'),
    (4, 4, 'Wheat', 12.0, 'Jointing',          '2025-11-10', '2026-04-20', 4.0, 'Rabi 2025-26'),
    (5, 5, 'Rice',  13.0, 'Panicle Initiation','2025-06-15', '2025-11-15', 5.0, 'Kharif 2025')
ON CONFLICT DO NOTHING;

INSERT INTO public.crop_health (health_id, field_id, health_score, heat_stress, frost_risk, drought_stress, excess_moisture) VALUES
    (1, 1, 82.5, 0.10, 0.00, 0.05, 0.08),
    (2, 2, 71.0, 0.20, 0.00, 0.00, 0.22),
    (3, 3, 91.0, 0.05, 0.00, 0.02, 0.05),
    (4, 4, 68.0, 0.25, 0.00, 0.10, 0.00),
    (5, 5, 78.5, 0.15, 0.00, 0.00, 0.12)
ON CONFLICT DO NOTHING;

INSERT INTO public.pest_risks (risk_id, field_id, pest_type, risk_level, warning_message) VALUES
    (1, 1, 'Wheat Rust', 'high',   'Conditions ideal for wheat rust spread – monitor closely'),
    (2, 1, 'Aphids',     'medium', 'Warm calm conditions favour aphid colonies'),
    (3, 2, 'Blight',     'medium', 'High humidity increases blight risk'),
    (4, 4, 'Wheat Rust', 'high',   'Jointing stage – rust risk elevated')
ON CONFLICT DO NOTHING;

INSERT INTO public.irrigation_recommendations (rec_id, field_id, recommended_date, volume_mm, reason, is_done) VALUES
    (1, 2, '2026-05-11', 25.0, 'Soil moisture below threshold - irrigation recommended', false),
    (2, 4, '2026-05-11', 30.0, 'High evapotranspiration rate detected', false),
    (3, 3, '2026-05-12', 18.0, 'Crop water stress indicator active', false)
ON CONFLICT DO NOTHING;

INSERT INTO public.yield_forecasts (forecast_id, field_id, expected_yield_ton_ha, target_yield_ton_ha, confidence) VALUES
    (1, 1, 3.1, 3.5, 0.78), (2, 2, 2.4, 3.2, 0.72), (3, 3, 2.6, 2.8, 0.85),
    (4, 4, 2.8, 4.0, 0.69), (5, 5, 4.2, 5.0, 0.74)
ON CONFLICT DO NOTHING;

INSERT INTO public.agri_alerts (alert_id, field_id, farm_id, alert_type, severity, message, is_active) VALUES
    (1, 1, 1,    'Pest Risk',      'high',     '🌾 High wheat rust risk in North Field – apply fungicide', true),
    (2, 2, 1,    'Irrigation Due', 'medium',   '💧 South Field needs irrigation – 25 mm deficit',           true),
    (3, 4, 2,    'Irrigation Due', 'medium',   '💧 Alpha Field needs 30 mm irrigation',                     true),
    (4, 4, 2,    'Pest Risk',      'high',     '🌾 Jointing stage rust risk elevated in Alpha Field',       true),
    (5, NULL, 1, 'Heatwave',       'critical', '🌡️ Heatwave forecast – prepare shade nets for nurseries',  true)
ON CONFLICT DO NOTHING;

-- explicit ids were used above: move every identity sequence past the highest id
SELECT setval(pg_get_serial_sequence('public.farms','farm_id'),                       (SELECT COALESCE(MAX(farm_id),1)     FROM public.farms));
SELECT setval(pg_get_serial_sequence('public.fields','field_id'),                     (SELECT COALESCE(MAX(field_id),1)    FROM public.fields));
SELECT setval(pg_get_serial_sequence('public.crop_census','census_id'),               (SELECT COALESCE(MAX(census_id),1)   FROM public.crop_census));
SELECT setval(pg_get_serial_sequence('public.crop_health','health_id'),               (SELECT COALESCE(MAX(health_id),1)   FROM public.crop_health));
SELECT setval(pg_get_serial_sequence('public.pest_risks','risk_id'),                  (SELECT COALESCE(MAX(risk_id),1)     FROM public.pest_risks));
SELECT setval(pg_get_serial_sequence('public.irrigation_recommendations','rec_id'),   (SELECT COALESCE(MAX(rec_id),1)      FROM public.irrigation_recommendations));
SELECT setval(pg_get_serial_sequence('public.yield_forecasts','forecast_id'),         (SELECT COALESCE(MAX(forecast_id),1) FROM public.yield_forecasts));
SELECT setval(pg_get_serial_sequence('public.agri_alerts','alert_id'),                (SELECT COALESCE(MAX(alert_id),1)    FROM public.agri_alerts));
