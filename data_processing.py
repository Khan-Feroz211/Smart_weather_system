"""
Data Processing Pipeline - Research-Grade Data Cleaning & Feature Engineering
Handles anomaly detection, missing value imputation, sensor calibration,
feature engineering (GDD, ET, stress indices), and data quality scoring
"""
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.impute import KNNImputer
import structlog

logger = structlog.get_logger()


class DataCleaner:
    """Advanced data cleaning with anomaly detection and imputation"""
    
    def __init__(self, contamination=0.05):
        self.contamination = contamination
        self.anomaly_detectors = {}
        self.scalers = {}
    
    def detect_anomalies_isolation_forest(self, df: pd.DataFrame, sensor_type: str) -> pd.Series:
        """
        Detect anomalies using Isolation Forest
        Returns boolean series where True = anomaly
        """
        if len(df) < 30:
            return pd.Series([False] * len(df), index=df.index)
        
        # Get or create detector for this sensor type
        if sensor_type not in self.anomaly_detectors:
            self.anomaly_detectors[sensor_type] = IsolationForest(
                contamination=self.contamination,
                random_state=42,
                n_estimators=100
            )
        
        detector = self.anomaly_detectors[sensor_type]
        
        # Fit and predict
        try:
            anomalies = detector.fit_predict(df[['value']].values)
            return pd.Series(anomalies == -1, index=df.index)
        except Exception as e:
            logger.error("anomaly_detection_error", sensor_type=sensor_type, error=str(e))
            return pd.Series([False] * len(df), index=df.index)
    
    def impute_missing_values(self, df: pd.DataFrame, method='knn') -> pd.DataFrame:
        """
        Impute missing values using KNN or linear interpolation
        """
        df_clean = df.copy()
        
        if method == 'knn' and len(df_clean) >= 5:
            try:
                imputer = KNNImputer(n_neighbors=min(5, len(df_clean)))
                df_clean['value'] = imputer.fit_transform(df_clean[['value']])
            except Exception as e:
                logger.warning("knn_imputation_failed", error=str(e))
                # Fallback to interpolation
                df_clean['value'] = df_clean['value'].interpolate(method='linear')
        else:
            df_clean['value'] = df_clean['value'].interpolate(method='linear')
        
        # Fill remaining NaN with forward fill then backward fill
        df_clean['value'] = df_clean['value'].fillna(method='ffill').fillna(method='bfill')
        
        return df_clean
    
    def apply_sensor_calibration(self, df: pd.DataFrame, calibration_params: Dict[str, float]) -> pd.DataFrame:
        """
        Apply linear calibration: calibrated = a * raw + b
        """
        df_calibrated = df.copy()
        
        sensor_type = df['sensor_type'].iloc[0] if 'sensor_type' in df.columns else 'unknown'
        params = calibration_params.get(sensor_type, {'a': 1.0, 'b': 0.0})
        
        df_calibrated['value'] = params['a'] * df_calibrated['value'] + params['b']
        
        return df_calibrated
    
    def clean_sensor_data(self, df: pd.DataFrame, sensor_type: str, 
                          calibration_params: Dict[str, float] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Complete cleaning pipeline for sensor data
        Returns (cleaned_df, cleaning_report)
        """
        cleaning_report = {
            'original_count': len(df),
            'anomalies_detected': 0,
            'missing_values': 0,
            'imputed_count': 0,
            'calibration_applied': False
        }
        
        # Detect anomalies
        anomaly_mask = self.detect_anomalies_isolation_forest(df, sensor_type)
        cleaning_report['anomalies_detected'] = anomaly_mask.sum()
        
        # Remove anomalies (mark for review instead of deleting)
        df_clean = df.copy()
        df_clean['is_anomaly'] = anomaly_mask
        df_clean.loc[anomaly_mask, 'value'] = np.nan  # Mark anomalies as missing
        
        # Count missing values
        missing_before = df_clean['value'].isna().sum()
        cleaning_report['missing_values'] = missing_before
        
        # Impute missing values
        df_clean = self.impute_missing_values(df_clean)
        missing_after = df_clean['value'].isna().sum()
        cleaning_report['imputed_count'] = missing_before - missing_after
        
        # Apply calibration if params provided
        if calibration_params:
            df_clean = self.apply_sensor_calibration(df_clean, calibration_params)
            cleaning_report['calibration_applied'] = True
        
        return df_clean, cleaning_report


class FeatureEngineer:
    """Compute agricultural features from sensor data"""
    
    @staticmethod
    def calculate_growing_degree_days(temperatures: List[float], base_temp: float = 10.0) -> float:
        """
        Calculate Growing Degree Days (GDD)
        GDD = max(0, (T_max + T_min) / 2 - T_base)
        """
        if not temperatures:
            return 0.0
        
        gdd_sum = 0.0
        for temp in temperatures:
            gdd = max(0.0, temp - base_temp)
            gdd_sum += gdd
        
        return gdd_sum
    
    @staticmethod
    def calculate_evapotranspiration_penman_monteith(
        temperature: float, humidity: float, wind_speed: float, 
        solar_radiation: float, pressure: float = 1013.0
    ) -> float:
        """
        Simplified Penman-Monteith equation for reference evapotranspiration (ET0)
        Returns ET0 in mm/day
        """
        try:
            # Constants
            gamma = 0.665e-3 * pressure  # Psychrometric constant
            delta = 4098 * (0.6108 * np.exp(17.27 * temperature / (temperature + 237.3))) / (temperature + 237.3) ** 2  # Slope of saturation vapor pressure curve
            
            # Saturation vapor pressure
            es = 0.6108 * np.exp(17.27 * temperature / (temperature + 237.3))
            
            # Actual vapor pressure
            ea = es * (humidity / 100.0)
            
            # Net radiation (simplified from solar radiation)
            rn = 0.77 * solar_radiation  # W/m2 to MJ/m2/day approximation
            
            # Soil heat flux (assumed negligible for daily)
            g = 0.0
            
            # Air density
            rho = 1.225  # kg/m3 at sea level
            
            # Reference crop height
            z = 0.12  # m
            d = 0.667 * z  # Zero plane displacement
            zom = 0.123 * z  # Roughness length for momentum
            zoh = 0.0123 * z  # Roughness length for heat
            ra = (np.log((2 - d) / zom) * np.log((2 - d) / zoh)) / (0.41 ** 2)  # Aerodynamic resistance
            rs = 70 / (0.41 ** 2) * (2 / z)  # Surface resistance
            
            # Penman-Monteith
            et0 = (delta * (rn - g) + (rho * 1005 * (es - ea) / ra)) / (delta + gamma * (1 + rs / ra))
            
            # Convert to mm/day (1 W/m2 ≈ 0.0864 mm/day)
            et0_mm = et0 * 0.0864 * 24
            
            return max(0.0, et0_mm)
        except Exception as e:
            logger.error("et_calculation_error", error=str(e))
            return 0.0
    
    @staticmethod
    def calculate_stress_indices(sensor_data: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate various stress indices from sensor data
        """
        stress_indices = {}
        
        # Heat stress index (based on temperature)
        temp = sensor_data.get('temperature', 20.0)
        if temp > 35:
            stress_indices['heat_stress'] = min(1.0, (temp - 35) / 15.0)
        else:
            stress_indices['heat_stress'] = 0.0
        
        # Drought stress (based on soil moisture)
        soil_moisture = sensor_data.get('soil_moisture', 50.0)
        if soil_moisture < 30:
            stress_indices['drought_stress'] = min(1.0, (30 - soil_moisture) / 30.0)
        else:
            stress_indices['drought_stress'] = 0.0
        
        # Frost risk (based on temperature)
        if temp < 5:
            stress_indices['frost_risk'] = min(1.0, (5 - temp) / 10.0)
        else:
            stress_indices['frost_risk'] = 0.0
        
        # Excess moisture stress
        if soil_moisture > 80:
            stress_indices['excess_moisture'] = min(1.0, (soil_moisture - 80) / 20.0)
        else:
            stress_indices['excess_moisture'] = 0.0
        
        # Overall stress score (maximum of individual stresses)
        stress_indices['overall_stress'] = max(stress_indices.values())
        
        return stress_indices
    
    @staticmethod
    def calculate_vegetation_indices(ndvi: float) -> Dict[str, float]:
        """
        Calculate vegetation health indices from NDVI
        """
        if ndvi is None:
            return {'ndvi': 0.0, 'health_status': 'unknown'}
        
        # NDVI interpretation
        if ndvi < 0.1:
            health_status = 'barren'
        elif ndvi < 0.3:
            health_status = 'sparse'
        elif ndvi < 0.6:
            health_status = 'moderate'
        else:
            health_status = 'healthy'
        
        return {
            'ndvi': ndvi,
            'health_status': health_status,
            'biomass_estimate': ndvi * 5.0  # Simplified biomass estimate in t/ha
        }


class DataQualityScorer:
    """Compute data quality scores for research-grade assessment"""
    
    @staticmethod
    def compute_completeness_score(df: pd.DataFrame) -> float:
        """
        Compute completeness score (percentage of non-null values)
        """
        if len(df) == 0:
            return 0.0
        
        total_cells = len(df) * len(df.columns)
        non_null_cells = df.count().sum()
        
        return non_null_cells / total_cells
    
    @staticmethod
    def compute_temporal_coverage_score(df: pd.DataFrame, expected_interval_minutes: int = 60) -> float:
        """
        Compute temporal coverage score (how well readings cover expected time intervals)
        """
        if len(df) < 2:
            return 0.0
        
        df_sorted = df.sort_values('timestamp')
        df_sorted['timestamp'] = pd.to_datetime(df_sorted['timestamp'])
        
        # Calculate actual intervals
        intervals = df_sorted['timestamp'].diff().dt.total_seconds() / 60  # minutes
        
        # Expected interval tolerance (±50%)
        expected_min = expected_interval_minutes * 0.5
        expected_max = expected_interval_minutes * 1.5
        
        # Count intervals within tolerance
        valid_intervals = ((intervals >= expected_min) & (intervals <= expected_max)).sum()
        
        return valid_intervals / len(intervals) if len(intervals) > 0 else 0.0
    
    @staticmethod
    def compute_consistency_score(df: pd.DataFrame, sensor_type: str) -> float:
        """
        Compute consistency score (how consistent values are over time)
        Uses coefficient of variation (CV)
        """
        if len(df) < 10:
            return 0.5  # Low confidence with few data points
        
        values = df['value'].dropna()
        if len(values) == 0:
            return 0.0
        
        mean = values.mean()
        std = values.std()
        
        if mean == 0:
            return 0.0
        
        cv = std / mean
        
        # Lower CV = higher consistency score
        # CV < 0.1 = excellent, CV < 0.3 = good, CV < 0.5 = fair
        if cv < 0.1:
            return 1.0
        elif cv < 0.3:
            return 0.8
        elif cv < 0.5:
            return 0.6
        elif cv < 1.0:
            return 0.4
        else:
            return 0.2
    
    @staticmethod
    def compute_overall_quality_score(df: pd.DataFrame, sensor_type: str, 
                                     expected_interval_minutes: int = 60) -> Dict[str, float]:
        """
        Compute overall data quality score with component breakdown
        """
        completeness = DataQualityScorer.compute_completeness_score(df)
        temporal_coverage = DataQualityScorer.compute_temporal_coverage_score(df, expected_interval_minutes)
        consistency = DataQualityScorer.compute_consistency_score(df, sensor_type)
        
        # Weighted average (completeness 40%, temporal 30%, consistency 30%)
        overall = 0.4 * completeness + 0.3 * temporal_coverage + 0.3 * consistency
        
        return {
            'overall_quality_score': round(overall, 3),
            'completeness_score': round(completeness, 3),
            'temporal_coverage_score': round(temporal_coverage, 3),
            'consistency_score': round(consistency, 3),
            'data_count': len(df)
        }


class DataProcessingPipeline:
    """Complete data processing pipeline for research-grade analysis"""
    
    def __init__(self, db_path='smart_weather.db'):
        self.db_path = db_path
        self.cleaner = DataCleaner()
        self.feature_engineer = FeatureEngineer()
        self.quality_scorer = DataQualityScorer()
    
    def get_validated_sensor_data(self, sensor_type: str, field_id: int = None, 
                                  hours: int = 168) -> pd.DataFrame:
        """Fetch validated sensor data from database"""
        conn = sqlite3.connect(self.db_path)
        
        query = '''
            SELECT * FROM validated_sensor_readings
            WHERE sensor_type = ?
            AND timestamp >= datetime('now', '-{} hours')
        '''.format(hours)
        
        params = [sensor_type]
        if field_id:
            query += ' AND field_id = ?'
            params.append(field_id)
        
        query += ' ORDER BY timestamp ASC'
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        return df
    
    def process_sensor_data(self, sensor_type: str, field_id: int = None, 
                           hours: int = 168) -> Dict[str, Any]:
        """
        Complete processing pipeline for sensor data
        Returns processed data, cleaning report, quality score, and engineered features
        """
        # Fetch raw validated data
        df = self.get_validated_sensor_data(sensor_type, field_id, hours)
        
        if len(df) == 0:
            return {
                'success': False,
                'message': 'No data available for processing',
                'data': None
            }
        
        # Clean data
        df_clean, cleaning_report = self.cleaner.clean_sensor_data(df, sensor_type)
        
        # Compute quality score
        quality_score = self.quality_scorer.compute_overall_quality_score(df_clean, sensor_type)
        
        # Engineer features
        features = {}
        
        if sensor_type == 'soil_temperature' or sensor_type == 'air_temperature':
            temperatures = df_clean['value'].tolist()
            features['growing_degree_days'] = self.feature_engineer.calculate_growing_degree_days(temperatures)
        
        # Get aggregated sensor data for stress indices
        if field_id:
            conn = sqlite3.connect(self.db_path)
            try:
                # Fetch latest readings from all sensor types for this field
                latest_readings = conn.execute('''
                    SELECT sensor_type, value FROM validated_sensor_readings
                    WHERE field_id = ?
                    AND timestamp >= datetime('now', '-1 hour')
                    GROUP BY sensor_type
                ''', (field_id,)).fetchall()
                
                sensor_dict = {row['sensor_type']: row['value'] for row in latest_readings}
                features['stress_indices'] = self.feature_engineer.calculate_stress_indices(sensor_dict)
                
                if 'ndvi' in sensor_dict:
                    features['vegetation_indices'] = self.feature_engineer.calculate_vegetation_indices(
                        sensor_dict['ndvi']
                    )
            finally:
                conn.close()
        
        return {
            'success': True,
            'sensor_type': sensor_type,
            'field_id': field_id,
            'data': df_clean.to_dict('records'),
            'cleaning_report': cleaning_report,
            'quality_score': quality_score,
            'engineered_features': features
        }
    
    def process_all_field_sensors(self, field_id: int, hours: int = 168) -> Dict[str, Any]:
        """
        Process all sensor types for a field
        Returns comprehensive analysis
        """
        sensor_types = [
            'soil_moisture', 'soil_temperature', 'soil_ph', 'soil_ec',
            'air_temperature', 'air_humidity', 'rainfall', 'wind_speed',
            'solar_radiation', 'ndvi'
        ]
        
        results = {}
        for sensor_type in sensor_types:
            try:
                result = self.process_sensor_data(sensor_type, field_id, hours)
                if result['success']:
                    results[sensor_type] = result
            except Exception as e:
                logger.error("sensor_processing_error", sensor_type=sensor_type, error=str(e))
        
        return results


if __name__ == '__main__':
    logger.info("data_processing_pipeline_start")
    pipeline = DataProcessingPipeline()
    
    # Example: Process soil moisture for field 1
    result = pipeline.process_sensor_data('soil_moisture', field_id=1, hours=24)
    print("Processing result:", result)
