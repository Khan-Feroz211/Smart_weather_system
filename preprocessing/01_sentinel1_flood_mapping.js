// Sentinel-1 Flood Mapping for White Volta Basin
// ========================
// This script maps historical flood extent using Sentinel-1 SAR imagery.
// Designed to run on Google Earth Engine (GEE) Code Editor.
//
// Data sources:
//   - Sentinel-1 C-band SAR (VV, VH polarisations, GRD, 10m resolution)
//   - JRC Global Surface Water (for permanent water masking)
//
// Methodology:
//   The Normalised Difference Flood Index (NDFI) approach is used.
//   A simple threshold-based classification is applied:
//     NDFI = (VV - VH) / (VV + VH)
//   Pixels with NDFI values below a threshold during a flood event period
//   are classified as flooded.

// --- Study area: White Volta Basin (approximate bounding box) ---
var whiteVolta = ee.Geometry.Rectangle(
  [-4.0, 8.0, -1.5, 11.0], 'EPSG:4326', false
);
Map.centerObject(whiteVolta, 9);
Map.addLayer(whiteVolta, {color: 'red'}, 'White Volta Basin');

// --- Parameters ---
var START_DATE = '2019-09-01';   // Flood season start
var END_DATE   = '2019-10-31';   // Flood season end
var VV_THRESHOLD = -17.0;        // dB; pixels below this are likely flooded
var WATER_MASK = true;           // Apply permanent water mask

// --- Load Sentinel-1 collection ---
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterDate(START_DATE, END_DATE)
  .filterBounds(whiteVolta)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
  .filter(ee.Filter.eq('platform_number', 1))
  .select(['VV', 'VH']);

print('Sentinel-1 images found:', s1.size());

// --- Compute NDFI ---
// Use median to reduce speckle
var s1Median = s1.median().clip(whiteVolta);
var vv = s1Median.select('VV');
var vh = s1Median.select('VH');
var ndfi = vv.subtract(vh).divide(vv.add(vh)).rename('NDFI');

// --- Apply permanent water mask ---
var permanentWater = ee.Image('JRC/GSW1_4/GlobalSurfaceWater')
  .select('water');
var waterMask;
if (WATER_MASK) {
  waterMask = permanentWater.gt(0.5).not();
  ndfi = ndfi.updateMask(waterMask);
}

// --- Flood classification: NDFI threshold ---
// Flooded areas tend to have low NDFI values due to double-bounce scattering
var flood = ndfi.lt(-0.1).rename('Flood');

// --- Visualisation ---
var ndfiViz = {min: -0.3, max: 0.3, palette: ['blue', 'white', 'red']};
var floodViz = {palette: ['000000', '0000FF']};  // black=not flood, blue=flood

Map.addLayer(ndfi, ndfiViz, 'NDFI (VV-VH)/(VV+VH)');
Map.addLayer(flood.updateMask(flood), floodViz, 'Flood Extent');

// --- Export ---
Export.image.toDrive({
  image: flood,
  description: 's1_flood_whitenvolta_' + START_DATE.replace(/-/g, ''),
  folder: 'Map',
  region: whiteVolta,
  scale: 10,
  crs: 'EPSG:4326',
  maxPixels: 1e9
});

print('Done. Check Assets / Drive for exported flood map.');
