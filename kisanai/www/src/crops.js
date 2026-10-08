/**
 * KisanAI — Offline crop catalogue (src/crops.js).
 *
 * Mirrors the backend `crop_database.py` CROP_DATABASE so the mobile app can
 * render the symptom checklist and growth-stage picker with NO network.
 * (The backend rule-based tier reads the same disease/symptom vocabulary.)
 */

export const CROPS = {
  wheat: {
    name: 'Wheat',
    stages: ['germination', 'tillering', 'jointing', 'heading', 'ripening'],
    symptoms: {
      yellow_pustules: 'Yellow pustules on leaves',
      leaf_lesions: 'Brown leaf lesions',
      powdery_spots: 'Powdery white spots',
      brown_spots: 'Brown spots',
      white_coating: 'White coating on leaves',
    },
    diseases: {
      wheat_rust: { name: 'Wheat Rust', urgency: 'critical' },
      leaf_blight: { name: 'Wheat Leaf Blight', urgency: 'medium' },
      powdery_mildew: { name: 'Powdery Mildew', urgency: 'medium' },
      wheat_healthy: { name: 'Healthy', urgency: 'low' },
    },
  },
  rice: {
    name: 'Rice',
    stages: ['nursery', 'tillering', 'panicle_initiation', 'flowering', 'maturation'],
    symptoms: {
      diamond_spots: 'Diamond-shaped leaf spots',
      leaf_lesions: 'Brown leaf lesions',
      withered_tips: 'Withered leaf tips',
      yellow_waving_margins: 'Yellow waving leaf margins',
      brown_spots: 'Brown spots',
    },
    diseases: {
      rice_blast: { name: 'Rice Blast', urgency: 'critical' },
      bacterial_blight: { name: 'Bacterial Blight', urgency: 'high' },
      rice_brown_spot: { name: 'Rice Brown Spot', urgency: 'high' },
      rice_healthy: { name: 'Healthy', urgency: 'low' },
    },
  },
  cotton: {
    name: 'Cotton',
    stages: ['seedling', 'vegetative', 'squaring', 'boll_formation', 'maturation'],
    symptoms: {
      upward_curling: 'Upward curling of leaves',
      thickened_veins: 'Thickened, discoloured veins',
      stunted_growth: 'Stunted growth',
      yellow_waving_margins: 'Yellow waving leaf margins',
      withered_tips: 'Withered tips',
    },
    diseases: {
      leaf_curl: { name: 'Cotton Leaf Curl Virus', urgency: 'critical' },
      fusarium_wilt: { name: 'Fusarium Wilt', urgency: 'high' },
      cotton_healthy: { name: 'Healthy', urgency: 'low' },
    },
  },
  maize: {
    name: 'Maize',
    stages: ['germination', 'vegetative', 'tasseling', 'silking', 'grain_fill', 'maturation'],
    symptoms: {
      yellow_pustules: 'Yellow pustules on leaves',
      leaf_lesions: 'Brown leaf lesions',
      powdery_spots: 'Powdery white spots',
      brown_spots: 'Brown spots',
      white_coating: 'White coating on leaves',
    },
    diseases: {
      maize_common_rust: { name: 'Maize Common Rust', urgency: 'high' },
      maize_healthy: { name: 'Healthy', urgency: 'low' },
    },
  },
};

/** Flattened symptom → crop map (used by the symptom checker). */
export const SYMPTOMS = Object.keys(CROPS).reduce((acc, crop) => {
  Object.keys(CROPS[crop].symptoms).forEach((s) => { acc[s] = acc[s] || []; acc[s].push(crop); });
  return acc;
}, {});

export default CROPS;
