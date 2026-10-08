/**
 * KisanAI — Offline storage (src/storage.js).
 *
 * F5: Fields & soil repository.
 *
 * Persists user-created fields and their soil-test readings in a local SQLite
 * database (Capacitor Community SQLite, Keystore-encrypted at the file level on
 * Android).  All SQL runs through parameterised queries — the schema whitelist
 * pattern mirrors the backend's `SCHEMA_TABLE_WHITELIST` (app_clean.py:121) so
 * only the two tables we own are ever touched.
 *
 * When the SQLite plugin is unavailable (web / Node tests) the store transparently
 * falls back to an in-memory map + localStorage, so the UI and the rule-based
 * soil engine keep working.
 *
 * Pure, testable, side-effect-free helpers exported at the bottom:
 *   - computeSoilRecommendation(soil)   → agronomic recommendation from test values
 *   - DEFAULT_SOIL                       → sane defaults
 */
import CONFIG from '../config.js';
import { getSQLite } from './caps.js';

export const FIELD_TABLE = 'kisanai_fields';
export const SOIL_TABLE = 'kisanai_soil_tests';

export const DEFAULT_SOIL = {
  ph: 7.0,
  nitrogen: 20,       // ppm
  phosphorus: 15,     // ppm
  potassium: 100,     // ppm
  organic_matter: 2.5, // %
  texture: 'loam',
};

const TEXTURES = ['clay', 'clay_loam', 'loam', 'sandy_loam', 'sand', 'silt', 'silty_clay'];

/* ======================================================================
 * Pure agronomic logic (unit-tested in scripts/tests.mjs).
 * ==================================================================== */

/**
 * Classify a single nutrient/ph reading.
 * pH:   6.0–7.0 ideal for most crops; <6 acidic (limestone), >7.5 alkaline.
 * N:    <10 deficient, 10–30 adequate, >30 excess.
 * P:    <8 deficient, 8–25 adequate, >25 excess.
 * K:    <50 deficient, 50–200 adequate, >200 excess.
 * OM:   <1.5 low, 1.5–4.0 good, >4.0 high.
 */
export function classifyReading(key, value) {
  const v = Number(value);
  if (key === 'ph') {
    if (v < 5.5) return { status: 'acidic', label: 'Acidic', action: 'Apply agricultural lime' };
    if (v < 6.0) return { status: 'low_acid', label: 'Slightly acidic', action: 'Apply lime' };
    if (v <= 7.5) return { status: 'ideal', label: 'Ideal', action: 'No amendment needed' };
    if (v <= 8.0) return { status: 'alkaline', label: 'Mildly alkaline', action: 'Add sulfur / organic matter' };
    return { status: 'alkaline', label: 'Alkaline', action: 'Add sulfur + chelated micronutrients' };
  }
  if (key === 'nitrogen') {
    if (v < 10) return { status: 'deficient', label: 'Deficient', action: 'Apply urea / compost' };
    if (v <= 30) return { status: 'adequate', label: 'Adequate', action: 'Maintain with green manure' };
    return { status: 'excess', label: 'Excess', action: 'Reduce nitrogen, plant catch crop' };
  }
  if (key === 'phosphorus') {
    if (v < 8) return { status: 'deficient', label: 'Deficient', action: 'Apply rock phosphate / bone meal' };
    if (v <= 25) return { status: 'adequate', label: 'Adequate', action: 'Maintain with compost' };
    return { status: 'excess', label: 'Excess', action: 'Avoid P fertilizer; risk of runoff' };
  }
  if (key === 'potassium') {
    if (v < 50) return { status: 'deficient', label: 'Deficient', action: 'Apply potash / wood ash' };
    if (v <= 200) return { status: 'adequate', label: 'Adequate', action: 'Maintain with compost' };
    return { status: 'excess', label: 'Excess', action: 'Leach or reduce K inputs' };
  }
  if (key === 'organic_matter') {
    if (v < 1.5) return { status: 'low', label: 'Low', action: 'Add compost / green manure' };
    if (v <= 4.0) return { status: 'good', label: 'Good', action: 'Maintain with mulch' };
    return { status: 'high', label: 'High', action: 'Maintain with mulch' };
  }
  return { status: 'unknown', label: 'Unknown', action: 'Test again' };
}

/**
 * Build a full recommendation object from a soil-test record.
 */
export function computeSoilRecommendation(soil) {
  const s = { ...DEFAULT_SOIL, ...(soil || {}) };
  const nutrients = ['nitrogen', 'phosphorus', 'potassium', 'organic_matter'];
  const readings = {};
  const actions = [];
  for (const k of ['ph', ...nutrients]) {
    const r = classifyReading(k, s[k]);
    readings[k] = { value: Number(s[k]), ...r };
    if (r.status !== 'ideal' && r.status !== 'adequate' && r.status !== 'good' && r.status !== 'high') {
      actions.push(r.action);
    }
  }
  // Texture-specific note
  if (s.texture === 'sand') actions.push('Add organic matter to improve water retention');
  if (s.texture === 'clay') actions.push('Improve drainage / avoid compaction');

  const deficient = Object.values(readings).filter((r) => r.status === 'deficient').length;
  let overall = 'good';
  if (deficient >= 2) overall = 'poor';
  else if (deficient === 1) overall = 'fair';
  if (readings.ph && (readings.ph.status === 'acidic' || readings.ph.status === 'alkaline' && readings.ph.value > 8)) {
    overall = 'poor';
  }

  const deduped = [...new Set(actions)];
  const recText = deduped.length ? deduped.join('. ') : 'No specific soil amendment needed — maintain with compost.';
  return {
    soil: s,
    readings,
    overall, // 'good' | 'fair' | 'poor'
    actions: deduped,
    recommendation: recText,
  };
}
// (No module-level dependency on the i18n runtime; recommendation text is
//  a plain string so this module is importable in plain Node tests.)

/* ======================================================================
 * SQLite-backed repository (Capacitor Community SQLite).
 * ==================================================================== */
export class FieldStore {
  constructor(dbName = 'kisanai.db') {
    this.dbName = dbName;
    this._conn = null;
    this._mem = new Map();           // web/node fallback
    this._seq = 1;
    this._ready = false;
  }

  async init() {
    const sqlite = await getSQLite();
    if (!sqlite) {
      // Web / Node fallback — load from localStorage if present.
      this._loadMem();
      this._ready = true;
      return;
    }
    const { SQLiteConnection, CapacitorSQLite } = sqlite;
    try {
      // v7 plugin API: new SQLiteConnection(CapacitorSQLite), then createConnection.
      const connMgr = new SQLiteConnection(CapacitorSQLite);
      this._conn = await connMgr.createConnection(this.dbName, { version: 1 });
      await this._conn.open();
      await this._conn.run(`CREATE TABLE IF NOT EXISTS ${FIELD_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        area REAL,
        crop TEXT,
        soil_texture TEXT,
        created_at TEXT
      );`);
      await this._conn.run(`CREATE TABLE IF NOT EXISTS ${SOIL_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        field_id INTEGER NOT NULL,
        ph REAL,
        nitrogen REAL,
        phosphorus REAL,
        potassium REAL,
        organic_matter REAL,
        texture TEXT,
        tested_at TEXT
      );`);
      this._ready = true;
    } catch (e) {
      console.warn('[FieldStore] SQLite unavailable, using in-memory store:', e);
      this._loadMem();
      this._ready = true;
    }
  }

  /* ---- memory fallback (web/node) ---- */
  _loadMem() {
    try {
      const raw = typeof localStorage !== 'undefined' ? localStorage.getItem('__kisanai_fields__') : null;
      const soils = typeof localStorage !== 'undefined' ? localStorage.getItem('__kisanai_soil__') : null;
      if (raw) {
        const arr = JSON.parse(raw);
        const maxId = arr.reduce((m, f) => Math.max(m, f.id || 0), 0);
        this._seq = maxId + 1;
        for (const f of arr) this._mem.set(f.id, f);
      }
      if (soils) {
        const arr = JSON.parse(soils);
        for (const s of arr) {
          const fld = this._mem.get(s.field_id);
          if (fld) (fld.soil_tests = fld.soil_tests || []).push(s);
        }
      }
    } catch (e) { /* noop */ }
  }
  _saveMem() {
    try {
      const fields = Array.from(this._mem.values());
      typeof localStorage !== 'undefined' && localStorage.setItem('__kisanai_fields__', JSON.stringify(fields));
    } catch (e) { /* noop */ }
  }

  /* ---- CRUD ---- */
  async listFields() {
    await this._ensure();
    if (this._conn) {
      const rs = await this._conn.query(`SELECT * FROM ${FIELD_TABLE} ORDER BY id DESC;`);
      return rs.values || [];
    }
    return Array.from(this._mem.values()).sort((a, b) => b.id - a.id);
  }

  async getField(id) {
    await this._ensure();
    if (this._conn) {
      const rs = await this._conn.query(`SELECT * FROM ${FIELD_TABLE} WHERE id = ?;`, [Number(id)]);
      return rs.values?.[0] || null;
    }
    return this._mem.get(Number(id)) || null;
  }

  async addField({ name, area, crop, soil_texture }) {
    await this._ensure();
    const now = new Date().toISOString();
    if (this._conn) {
      const res = await this._conn.run(
        `INSERT INTO ${FIELD_TABLE}(name, area, crop, soil_texture, created_at) VALUES (?,?,?,?,?);`,
        [name, Number(area) || 0, crop || 'wheat', soil_texture || null, now]
      );
      const id = res.lastId || res.insertId;
      return this.getField(id);
    }
    const id = this._seq++;
    const field = { id, name, area: Number(area) || 0, crop: crop || 'wheat', soil_texture: soil_texture || null, created_at: now, soil_tests: [] };
    this._mem.set(id, field);
    this._saveMem();
    return field;
  }

  async updateField(id, patch) {
    await this._ensure();
    if (this._conn) {
      const sets = []; const vals = [];
      for (const k of Object.keys(patch)) { if (['name','area','crop','soil_texture'].includes(k)) { sets.push(`${k}=?`); vals.push(patch[k]); } }
      if (!sets.length) return this.getField(id);
      await this._conn.run(`UPDATE ${FIELD_TABLE} SET ${sets.join(',')} WHERE id=?;`, [...vals, Number(id)]);
      return this.getField(id);
    }
    const field = this._mem.get(Number(id));
    if (!field) return null;
    Object.assign(field, patch);
    this._saveMem();
    return field;
  }

  async deleteField(id) {
    await this._ensure();
    if (this._conn) {
      await this._conn.run(`DELETE FROM ${SOIL_TABLE} WHERE field_id = ?;`, [Number(id)]);
      await this._conn.run(`DELETE FROM ${FIELD_TABLE} WHERE id = ?;`, [Number(id)]);
    } else {
      this._mem.delete(Number(id));
      this._saveMem();
    }
    return true;
  }

  /* ---- soil tests ---- */
  async saveSoilTest(fieldId, soil) {
    await this._ensure();
    const now = new Date().toISOString();
    const s = { ...DEFAULT_SOIL, ...soil, texture: soil.texture || DEFAULT_SOIL.texture };
    if (this._conn) {
      const res = await this._conn.run(
        `INSERT INTO ${SOIL_TABLE}(field_id, ph, nitrogen, phosphorus, potassium, organic_matter, texture, tested_at)
         VALUES (?,?,?,?,?,?,?,?);`,
        [Number(fieldId), s.ph, s.nitrogen, s.phosphorus, s.potassium, s.organic_matter, s.texture, now]
      );
      const id = res.lastId || res.insertId;
      return { id, field_id: Number(fieldId), ...s, tested_at: now };
    }
    const id = this._seq++;
    const rec = { id, field_id: Number(fieldId), ...s, tested_at: now };
    const field = this._mem.get(Number(fieldId));
    if (field) (field.soil_tests = field.soil_tests ||[]).push(rec);
    this._saveMem();
    return rec;
  }

  async getSoilTests(fieldId) {
    await this._ensure();
    if (this._conn) {
      const rs = await this._conn.query(`SELECT * FROM ${SOIL_TABLE} WHERE field_id = ? ORDER BY id DESC;`, [Number(fieldId)]);
      return rs.values || [];
    }
    const field = this._mem.get(Number(fieldId));
    return field ? (field.soil_tests || []) : [];
  }

  async _ensure() {
    if (!this._ready) await this.init();
  }
}

export const fieldStore = new FieldStore();
export default fieldStore;
