/**
 * Smart Weather System - Frontend Entry Point
 * 
 * This module initializes the frontend application.
 * The actual UI is served by Flask templates at /dashboard
 * This entry point provides a lightweight SPA wrapper that
 * loads the Flask-rendered pages and enhances them with
 * real-time Socket.IO updates.
 */

// Redirect to the Flask dashboard
window.addEventListener('DOMContentLoaded', () => {
    // If we're at the root, redirect to dashboard
    if (window.location.pathname === '/' || window.location.pathname === '/index.html') {
        window.location.href = '/dashboard'
    }
})

// Export for module system
export { }
