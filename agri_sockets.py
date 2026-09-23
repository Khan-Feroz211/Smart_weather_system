"""
agri_sockets.py
===============
Flask-SocketIO event handlers and real-time broadcasting gateway.
Allows easy attachment from frontend clients.
"""

import logging
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

socketio_instance: SocketIO = None

def register_agri_sockets(socketio: SocketIO):
    global socketio_instance
    socketio_instance = socketio

    @socketio.on('connect')
    def handle_connect():
        logger.info("Agri client connected to Socket.IO")
        emit('connection_response', {
            'status': 'connected',
            'message': 'Connected to AgriAdvisor real-time telemetry stream.'
        })

    @socketio.on('disconnect')
    def handle_disconnect():
        logger.info("Agri client disconnected from Socket.IO")

    @socketio.on('request_agri_analysis')
    def handle_request_analysis(data):
        logger.info(f"Socket analysis request received: {data}")
        from recommendation_engine import RecommendationEngine
        engine = RecommendationEngine()
        
        crop = data.get('crop', 'wheat')
        symptoms = data.get('symptoms', {})
        site = data.get('site', {})
        conn_state = data.get('connectivity_state', 'ONLINE')
        compact = data.get('compact', False)

        result = engine.analyze(
            crop=crop,
            symptoms=symptoms,
            site=site,
            connectivity_state=conn_state,
            compact=compact
        )
        emit('analysis_response', result)

def broadcast_weather_update(weather_data: dict):
    if socketio_instance:
        socketio_instance.emit('weather_update', weather_data)

def broadcast_agri_alert(alert_data: dict):
    if socketio_instance:
        socketio_instance.emit('agri_alert', alert_data)

def broadcast_connectivity_change(connectivity_data: dict):
    if socketio_instance:
        socketio_instance.emit('connectivity_change', connectivity_data)

