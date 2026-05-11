"""
Trend Visualization Module - Research-Grade Data Visualization
Generates past/present/future trend charts with uncertainty bands
Using Plotly for interactive visualizations
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Any
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import structlog

logger = structlog.get_logger()


class TrendVisualizer:
    """Generate trend visualizations for sensor data and predictions"""
    
    def __init__(self, db_path='smart_weather.db'):
        self.db_path = db_path
    
    def get_sensor_trend_data(self, sensor_type: str, field_id: int = None, 
                             hours: int = 720) -> pd.DataFrame:
        """Fetch sensor data for trend visualization"""
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
        
        if len(df) > 0:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
    
    def generate_historical_trend(self, sensor_type: str, field_id: int = None, 
                                  hours: int = 720) -> Dict[str, Any]:
        """
        Generate historical trend chart (past data)
        Returns: {'plotly_json': str, 'statistics': dict}
        """
        df = self.get_sensor_trend_data(sensor_type, field_id, hours)
        
        if len(df) == 0:
            return {'success': False, 'message': 'No data available'}
        
        # Create figure
        fig = go.Figure()
        
        # Add historical data line
        fig.add_trace(go.Scatter(
            x=df['timestamp'],
            y=df['value'],
            mode='lines+markers',
            name='Historical Data',
            line=dict(color='#3B82F6', width=2),
            marker=dict(size=4),
            hovertemplate='Time: %{x}<br>Value: %{y:.2f}<extra></extra>'
        ))
        
        # Add quality score as color indicator
        if 'quality_score' in df.columns:
            fig.add_trace(go.Scatter(
                x=df['timestamp'],
                y=df['value'],
                mode='markers',
                name='Quality Score',
                marker=dict(
                    size=8,
                    color=df['quality_score'],
                    colorscale='RdYlGn',
                    showscale=True,
                    colorbar=dict(title='Quality Score'),
                    opacity=0.3
                ),
                hovertemplate='Time: %{x}<br>Quality: %{marker.color:.2f}<extra></extra>'
            ))
        
        # Calculate statistics
        statistics = {
            'mean': float(df['value'].mean()),
            'std': float(df['value'].std()),
            'min': float(df['value'].min()),
            'max': float(df['value'].max()),
            'median': float(df['value'].median()),
            'data_points': len(df),
            'time_range': f"{df['timestamp'].min()} to {df['timestamp'].max()}"
        }
        
        # Add statistics annotations
        fig.add_annotation(
            text=f"Mean: {statistics['mean']:.2f}<br>Std: {statistics['std']:.2f}<br>Range: [{statistics['min']:.2f}, {statistics['max']:.2f}]",
            xref="paper", yref="paper",
            x=0.02, y=0.98,
            showarrow=False,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="#3B82F6",
            borderwidth=1
        )
        
        fig.update_layout(
            title=f'{sensor_type.replace("_", " ").title()} - Historical Trend',
            xaxis_title='Timestamp',
            yaxis_title='Value',
            hovermode='x unified',
            template='plotly_white',
            height=400
        )
        
        return {
            'success': True,
            'plotly_json': fig.to_json(),
            'statistics': statistics
        }
    
    def generate_forecast_trend(self, sensor_type: str, field_id: int = None,
                               forecast_hours: int = 168) -> Dict[str, Any]:
        """
        Generate forecast trend chart with uncertainty bands
        Returns: {'plotly_json': str, 'forecast_data': dict}
        """
        # Get historical data
        df = self.get_sensor_trend_data(sensor_type, field_id, hours=168)
        
        if len(df) < 10:
            return {'success': False, 'message': 'Insufficient historical data for forecasting'}
        
        # Simple forecast using moving average (placeholder for ML forecast)
        recent_mean = df['value'].tail(24).mean() if len(df) >= 24 else df['value'].mean()
        recent_std = df['value'].tail(24).std() if len(df) >= 24 else df['value'].std()
        
        # Generate forecast timestamps
        last_timestamp = df['timestamp'].max()
        forecast_timestamps = [last_timestamp + timedelta(hours=i) for i in range(1, forecast_hours + 1)]
        
        # Generate forecast values with uncertainty
        np.random.seed(42)
        forecast_values = []
        forecast_lower = []
        forecast_upper = []
        
        for i, ts in enumerate(forecast_timestamps):
            # Add some random variation
            variation = np.random.normal(0, recent_std * 0.1)
            forecast_value = recent_mean + variation
            uncertainty = recent_std * (1 + i / forecast_hours)  # Increasing uncertainty
            
            forecast_values.append(forecast_value)
            forecast_lower.append(forecast_value - 1.96 * uncertainty)
            forecast_upper.append(forecast_value + 1.96 * uncertainty)
        
        # Create figure
        fig = go.Figure()
        
        # Add historical data
        fig.add_trace(go.Scatter(
            x=df['timestamp'],
            y=df['value'],
            mode='lines',
            name='Historical',
            line=dict(color='#3B82F6', width=2),
            hovertemplate='Time: %{x}<br>Value: %{y:.2f}<extra></extra>'
        ))
        
        # Add forecast line
        fig.add_trace(go.Scatter(
            x=forecast_timestamps,
            y=forecast_values,
            mode='lines',
            name='Forecast',
            line=dict(color='#10B981', width=2, dash='dash'),
            hovertemplate='Time: %{x}<br>Forecast: %{y:.2f}<extra></extra>'
        ))
        
        # Add uncertainty band
        fig.add_trace(go.Scatter(
            x=forecast_timestamps + forecast_timestamps[::-1],
            y=forecast_upper + forecast_lower[::-1],
            fill='toself',
            fillcolor='rgba(16, 185, 129, 0.2)',
            line=dict(color='rgba(255,255,255,0)'),
            name='95% Confidence Interval',
            hoverinfo='skip'
        ))
        
        fig.update_layout(
            title=f'{sensor_type.replace("_", " ").title()} - Forecast Trend ({forecast_hours} hours)',
            xaxis_title='Timestamp',
            yaxis_title='Value',
            hovermode='x unified',
            template='plotly_white',
            height=400
        )
        
        return {
            'success': True,
            'plotly_json': fig.to_json(),
            'forecast_data': {
                'forecast_values': forecast_values,
                'forecast_lower': forecast_lower,
                'forecast_upper': forecast_upper,
                'forecast_timestamps': [ts.isoformat() for ts in forecast_timestamps]
            }
        }
    
    def generate_comparative_trend(self, sensor_types: List[str], field_id: int = None,
                                   hours: int = 168) -> Dict[str, Any]:
        """
        Generate comparative trend chart for multiple sensor types
        Returns: {'plotly_json': str}
        """
        fig = make_subplots(
            rows=len(sensor_types), cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            subplot_titles=[st.replace('_', ' ').title() for st in sensor_types]
        )
        
        colors = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6']
        
        for i, sensor_type in enumerate(sensor_types):
            df = self.get_sensor_trend_data(sensor_type, field_id, hours)
            
            if len(df) > 0:
                fig.add_trace(
                    go.Scatter(
                        x=df['timestamp'],
                        y=df['value'],
                        mode='lines',
                        name=sensor_type,
                        line=dict(color=colors[i % len(colors)], width=2),
                        hovertemplate='Time: %{x}<br>Value: %{y:.2f}<extra></extra>'
                    ),
                    row=i+1, col=1
                )
        
        fig.update_layout(
            title='Comparative Sensor Trends',
            height=200 * len(sensor_types),
            hovermode='x unified',
            template='plotly_white'
        )
        
        return {
            'success': True,
            'plotly_json': fig.to_json()
        }
    
    def generate_health_trend(self, field_id: int, hours: int = 720) -> Dict[str, Any]:
        """
        Generate crop health trend chart
        Returns: {'plotly_json': str, 'health_data': dict}
        """
        conn = sqlite3.connect(self.db_path)
        
        query = '''
            SELECT ch.*, f.name AS field_name
            FROM crop_health ch
            JOIN fields f ON ch.field_id = f.field_id
            WHERE ch.field_id = ?
            AND recorded_at >= datetime('now', '-{} hours')
            ORDER BY recorded_at ASC
        '''.format(hours)
        
        df = pd.read_sql_query(query, conn, params=[field_id])
        conn.close()
        
        if len(df) == 0:
            return {'success': False, 'message': 'No health data available'}
        
        df['recorded_at'] = pd.to_datetime(df['recorded_at'])
        
        # Create figure with multiple traces
        fig = go.Figure()
        
        # Health score
        fig.add_trace(go.Scatter(
            x=df['recorded_at'],
            y=df['health_score'],
            mode='lines+markers',
            name='Health Score',
            line=dict(color='#10B981', width=3),
            yaxis='y',
            hovertemplate='Time: %{x}<br>Health: %{y:.1f}%<extra></extra>'
        ))
        
        # Stress indices on secondary axis
        fig.add_trace(go.Scatter(
            x=df['recorded_at'],
            y=df['heat_stress'] * 100,
            mode='lines',
            name='Heat Stress (%)',
            line=dict(color='#EF4444', width=2, dash='dot'),
            yaxis='y2',
            hovertemplate='Time: %{x}<br>Heat Stress: %{y:.1f}%<extra></extra>'
        ))
        
        fig.add_trace(go.Scatter(
            x=df['recorded_at'],
            y=df['drought_stress'] * 100,
            mode='lines',
            name='Drought Stress (%)',
            line=dict(color='#F59E0B', width=2, dash='dot'),
            yaxis='y2',
            hovertemplate='Time: %{x}<br>Drought Stress: %{y:.1f}%<extra></extra>'
        ))
        
        fig.update_layout(
            title=f'Crop Health Trend - Field {field_id}',
            xaxis_title='Timestamp',
            yaxis=dict(title='Health Score (%)', range=[0, 100]),
            yaxis2=dict(title='Stress (%)', overlaying='y', side='right', showgrid=False),
            hovermode='x unified',
            template='plotly_white',
            height=400,
            legend=dict(x=0.02, y=0.98)
        )
        
        health_data = {
            'latest_health': float(df['health_score'].iloc[-1]),
            'health_trend': 'improving' if df['health_score'].iloc[-1] > df['health_score'].iloc[0] else 'declining',
            'average_health': float(df['health_score'].mean()),
            'stress_summary': {
                'heat_stress_avg': float(df['heat_stress'].mean() * 100),
                'drought_stress_avg': float(df['drought_stress'].mean() * 100),
                'frost_risk_avg': float(df['frost_risk'].mean() * 100)
            }
        }
        
        return {
            'success': True,
            'plotly_json': fig.to_json(),
            'health_data': health_data
        }
    
    def generate_yield_forecast_trend(self, field_id: int) -> Dict[str, Any]:
        """
        Generate yield forecast trend with uncertainty
        Returns: {'plotly_json': str}
        """
        conn = sqlite3.connect(self.db_path)
        
        query = '''
            SELECT yf.*, cc.crop_type, cc.target_yield_ton_ha
            FROM yield_forecasts yf
            JOIN crop_census cc ON yf.field_id = cc.field_id
            WHERE yf.field_id = ?
            ORDER BY forecast_date ASC
        '''
        
        df = pd.read_sql_query(query, conn, params=[field_id])
        conn.close()
        
        if len(df) == 0:
            return {'success': False, 'message': 'No yield forecast data available'}
        
        df['forecast_date'] = pd.to_datetime(df['forecast_date'])
        
        fig = go.Figure()
        
        # Expected yield
        fig.add_trace(go.Scatter(
            x=df['forecast_date'],
            y=df['expected_yield_ton_ha'],
            mode='lines+markers',
            name='Expected Yield',
            line=dict(color='#3B82F6', width=3),
            error_y=dict(
                type='data',
                array=df['expected_yield_ton_ha'] * (1 - df['confidence']),
                visible=True
            ),
            hovertemplate='Date: %{x}<br>Yield: %{y:.2f} t/ha<br>Confidence: %{customdata[0]:.1%}<extra></extra>',
            customdata=df[['confidence']].values
        ))
        
        # Target yield line
        target_yield = df['target_yield_ton_ha'].iloc[0]
        fig.add_hline(
            y=target_yield,
            line_dash='dash',
            line_color='#EF4444',
            annotation_text=f'Target: {target_yield} t/ha',
            annotation_position='right'
        )
        
        fig.update_layout(
            title=f'Yield Forecast Trend - Field {field_id}',
            xaxis_title='Forecast Date',
            yaxis_title='Yield (t/ha)',
            hovermode='x unified',
            template='plotly_white',
            height=400
        )
        
        return {
            'success': True,
            'plotly_json': fig.to_json()
        }


if __name__ == '__main__':
    logger.info("trend_visualizer_start")
    visualizer = TrendVisualizer()
    
    # Example: Generate historical trend for soil moisture
    result = visualizer.generate_historical_trend('soil_moisture', field_id=1, hours=168)
    print("Historical trend result:", result['success'])
