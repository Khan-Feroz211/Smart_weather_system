"""
agri_sockets.py
===============
Flask-SocketIO event handlers and real-time broadcasting gateway.
Allows easy attachment from frontend clients.

Events
------
* ``request_agri_analysis``  – existing symptom-based analysis request.
* ``request_image_diagnosis`` – **NEW**: sends a base64-encoded plant image
  for real-time CNN disease diagnosis.  The server decodes, runs inference,
  and emits ``image_diagnosis_response``.
* ``broadcast_weather_update`` – broadcast live weather telemetry.
* ``broadcast_agri_alert``     – broadcast agronomic alerts.
* ``broadcast_connectivity_change`` – broadcast connectivity state changes.
"""

import base64
import io
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

    @socketio.on('request_image_diagnosis')
    def handle_request_image_diagnosis(data):
        """
        Real-time plant disease diagnosis from an uploaded image.

        Expected payload:
        {
            "crop": "wheat",           # optional, defaults to "wheat"
            "image": "<base64 data>",  # required
            "top_k": 3                 # optional
        }

        Emits: ``image_diagnosis_response``
        """
        logger.info("Socket image diagnosis request received")
        from recommendation_engine import RecommendationEngine

        crop = data.get('crop', 'wheat')
        top_k = int(data.get('top_k', 3))
        image_b64 = data.get('image')

        if not image_b64:
            emit('image_diagnosis_response', {
                'success': False,
                'error': 'No image data provided in "image" field (base64).',
                'timestamp': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
            })
            return

        try:
            # Decode base64 image
            image_bytes = base64.b64decode(image_b64)
            engine = RecommendationEngine()
            result = engine.diagnose_from_image(
                image_bytes, crop=crop, top_k=top_k
            )
            emit('image_diagnosis_response', result)
        except Exception as exc:
            logger.error(f"Image diagnosis error: {exc}")
            emit('image_diagnosis_response', {
                'success': False,
                'error': str(exc),
                'timestamp': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
            })


def broadcast_weather_update(weather_data: dict):
    if socketio_instance:
        socketio_instance.emit('weather_update', weather_data)

def broadcast_agri_alert(alert_data: dict):
    if socketio_instance:
        socketio_instance.emit('agri_alert', alert_data)

def broadcast_connectivity_change(connectivity_data: dict):
    if socketio_instance:
        socketio_instance.emit('connectivity_change', connectivity_data)
