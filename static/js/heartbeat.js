/* Presence heartbeat: tells the server this signed-in user is active.
   Every ~45 s while the tab is visible (never every second); stops on 401. */
(function () {
  var interval = (window.SW_HEARTBEAT_SECONDS || 45) * 1000, timer = null, stopped = false;
  function beat() {
    if (stopped || document.hidden) return;
    fetch('/api/heartbeat', { method: 'POST', credentials: 'same-origin', keepalive: true })
      .then(function (r) { if (r.status === 401) stop(); })
      .catch(function () { /* offline: try again next tick */ });
  }
  function start() { if (!timer) { beat(); timer = setInterval(beat, interval); } }
  function stop() { stopped = true; if (timer) clearInterval(timer); timer = null; }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) beat(); });
  start();
})();
