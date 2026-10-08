/**
 * KisanAI — Camera / image capture (src/camera.js).
 *
 * F3: Crop scan.
 *   - capturePhoto({ source:'camera'|'gallery' }) → base64 JPEG
 *   - resizeImageBase64(base64, maxSize) → base64 JPEG scaled to <= maxSize px
 *     (uses a <canvas>; the dimension math lives in computeResizeDimensions
 *      which is pure and unit-tested).
 *   - DIAGNOSE_THRESHOLD = 0.7  — confidence < 70% => "Not sure, retake"
 *     (matches /api/agri/disease-diagnose: top-1 confidence < 0.7 is
 *      treated as low-confidence per api_routes.py §diagnose_from_image).
 *
 * Import-safe in Node: Capacitor Camera is lazy-imported behind isNative();
 * only ../config.js is imported at module scope.
 */
import CONFIG from '../config.js';
import { getCamera } from './caps.js';

export const MAX_IMAGE_PX = CONFIG.MAX_IMAGE_PX;   // 800
export const MAX_IMAGE_BYTES = CONFIG.MAX_IMAGE_BYTES; // 2 MB
export const DIAGNOSE_THRESHOLD = 0.70;             // < 70% => "Not sure, retake"

export const CAMERA_SOURCE = { CAMERA: 'camera', GALLERY: 'gallery' };

/**
 * Compute the resized (width, height) so the longer side == maxSize and
 * the aspect ratio is preserved.  Pure — used by resizeImageBase64 and by
 * tests.  Never exceeds maxSize on either axis.
 */
export function computeResizeDimensions(width, height, maxSize = MAX_IMAGE_PX) {
  const w = Number(width) || 0;
  const h = Number(height) || 0;
  if (!w || !h) return { width: 0, height: 0, scale: 1 };
  const scale = Math.min(1, maxSize / Math.max(w, h));
  const rw = Math.round(w * scale) || 1;
  const rh = Math.round(h * scale) || 1;
  return { width: rw, height: rh, scale };
}

/** Convert a data-URL `data:image/...;base64,XXXX` → raw base64 string. */
export function dataUrlToBase64(dataUrl) {
  if (!dataUrl) return '';
  const m = dataUrl.match(/^data:image\/[^;]+;base64,(.*)$/);
  return m ? m[1] : dataUrl;
}

/** Convert raw base64 → data-URL (PNG unless jpeg requested). */
export function base64ToDataUrl(base64, type = 'image/jpeg') {
  const b = (base64 || '').replace(/^data:image\/[^;]+;base64,/, '');
  return `data:${type};base64,${b}`;
}

/**
 * Resize an image (given as a data-URL) to <= maxSize px on a canvas,
 * re-encoded as JPEG at quality 0.82.  Returns a base64 JPEG string.
 * Browser-only (needs <canvas> + createImageBitmap).
 */
export async function resizeImageBase64(dataUrl, maxSize = MAX_IMAGE_PX, quality = 0.82) {
  if (!dataUrl) return '';
  const img = await createImageBitmap(new Blob([dataUrlToBase64(dataUrl)]));
  const { width, height } = computeResizeDimensions(img.width, img.height, maxSize);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  // Keep EXIF-free orientation: draw straight.
  ctx.drawImage(img, 0, 0, width, height);
  // toDataURL returns a data-URL; strip to bare base64 for the API.
  return dataUrlToBase64(canvas.toDataURL('image/jpeg', quality));
}

/**
 * Estimate the byte length of a base64 string (for the 2MB guard).  Pure.
 */
export function base64ByteLength(base64) {
  const b = dataUrlToBase64(base64);
  // base64 encodes 3 bytes per 4 chars (ignoring padding).
  return Math.floor((b.length * 3) / 4);
}

/** Returns true if the image would fit under the backend 2MB limit. */
export function meetsSizeLimit(base64, limit = MAX_IMAGE_BYTES) {
  return base64ByteLength(base64) <= limit;
}

/**
 * Capture an image from the camera or gallery and return a base64 JPEG
 * <= MAX_IMAGE_PX with JPEG quality tuned to stay under MAX_IMAGE_BYTES.
 */
export async function capturePhoto(opts = {}) {
  const source = opts.source === 'gallery' ? 'PHOTOLIBRARY' : 'CAMERA';
  const preferred = opts.source === 'gallery' ? 2 : 1; // CameraSource enum values
  const Camera = await getCamera();
  if (!Camera) {
    // Web fallback: use a plain <input type=file>.  Returns a data-URL.
    return _webFilePicker(opts.accept || 'image/*');
  }
  const photo = await Camera.getPhoto({
    quality: 90,
    allowEditing: false,
    resultType: 0 /* Base64 */,   // CameraResultType.Base64 = 0 in v7
    source: preferred,            // CameraSource.Camera(1)/PhotoLibrary(2)
    width: MAX_IMAGE_PX,
  });
  // Capacitor may return base64 without a data-URL prefix.
  const raw = photo.base64String || photo.webPath || '';
  const dataUrl = raw.startsWith('data:') ? raw : base64ToDataUrl(raw);
  return resizeImageBase64(dataUrl, MAX_IMAGE_PX, 0.82);
}

/** Resolve image capture entirely via the api diagnose endpoint. */
export async function diagnoseImage(photoBase64, api, opts = {}) {
  const crop = opts.crop || 'wheat';
  const top_k = opts.top_k || 3;
  const res = await api.diagnoseImage(photoBase64, { crop, top_k });
  if (res.offline) return { offline: true, message: 'Diagnosis unavailable offline — showing cached rules.' };
  const data = res.data || {};
  const predictions = data.predictions || (Array.isArray(data) ? data : []);
  const top = predictions[0];
  const confidence = Number(top && (top.confidence != null ? top.confidence : top.confidence_calibrated));
  const sure = confidence >= DIAGNOSE_THRESHOLD;
  return {
    success: !!data.success || !!top,
    confidence: isNaN(confidence) ? 0 : confidence,
    confidenceLabel: sure ? 'confident' : 'low',
    sure,
    disease: top ? (top.disease || top.name || 'unknown') : 'unknown',
    explanation: top ? (top.explanation || '') : '',
    evidence: top ? (top.evidence || ['leaf_image_analysis']) : [],
    treatment: top ? (top.treatment || '') : '',
    urgency: top ? (top.urgency || 'medium') : 'medium',
    message: sure ? null : 'Not enough confidence — retake the photo.',
    note: data.message || data.error || '',
    raw: data,
  };
}

function _webFilePicker(accept) {
  return new Promise((resolve, reject) => {
    if (typeof document === 'undefined') return reject(new Error('No file picker in this environment'));
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept || 'image/*';
    input.onchange = () => {
      const file = input.files && input.files[0];
      if (!file) return reject(new Error('No file selected'));
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result); // data-URL
      fr.readAsDataURL(file);
    };
    input.oncancel = () => reject(new Error('Selection cancelled'));
    input.click();
  });
}

export default { capturePhoto, resizeImageBase64, diagnoseImage, computeResizeDimensions, meetsSizeLimit, MAX_IMAGE_PX, DIAGNOSE_THRESHOLD };
