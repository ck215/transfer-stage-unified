"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""

class ApiHandler:
    """Thin JSON wrapper over the same Controller calls the desktop views make.
    Was WebAPIHandler + WebModelAdapter.
    
    Absorbs: WebAPIHandler
    
    MUST SATISFY:
    [CARRY] Localhost bind plus Origin/Host check on every state-changing
    route; JSON content type required.  (MANAGER-15, WEB-10)
    """

    def do_GET(self, *args, **kwargs):
        """was WebAPIHandler.do_GET, WebAPIHandler._do_GET_impl
        """
        raise NotImplementedError

    def do_POST(self, *args, **kwargs):
        """was WebAPIHandler.do_POST, WebAPIHandler._do_POST_impl
        """
        raise NotImplementedError

    def _is_local(self, *args, **kwargs):
        """was WebAPIHandler._bound_hosts, WebAPIHandler._origin_ok,
        WebAPIHandler._token_ok, WebAPIHandler._authorize
        owner ruling 2026-09-21: localhost only. Bind 127.0.0.1, check
        client address and Origin (the Origin check stays: a web page open
        in the same browser can still POST to localhost). The session token
        is purged
        """
        raise NotImplementedError

    def _send_json(self, *args, **kwargs):
        """was WebAPIHandler._send_json
        """
        raise NotImplementedError

    def _serve_static(self, *args, **kwargs):
        """was WebAPIHandler._serve_static
        """
        raise NotImplementedError


class WebView:
    """Was WebDashboardWindow + WebDashboardServer. Owns the HTTP server AND
    the browser-heartbeat watchdog, which calls Controller.estop_all. Client
    liveness no longer exists in any model.
    
    Absorbs: BaseProbe, ClientLivenessGate, WebDashboardServer,
    WebDashboardWindow, WebModelAdapter
    """

    @property
    def heartbeat_age(self):
        """was WebModelAdapter.client_heartbeat_age_seconds
        """
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        """was WebDashboardServer.__init__, WebDashboardWindow.__init__
        """
        raise NotImplementedError

    def beat(self, *args, **kwargs):
        """was WebModelAdapter.record_client_heartbeat,
        ClientLivenessGate.touch_client_liveness
        """
        raise NotImplementedError

    def close(self, *args, **kwargs):
        """was WebDashboardServer.stop, WebDashboardWindow.close
        """
        raise NotImplementedError

    def open(self, *args, **kwargs):
        """was WebDashboardServer.start, WebDashboardWindow.show
        """
        raise NotImplementedError

    def _check_heartbeat(self, *args, **kwargs):
        """was ClientLivenessGate._check_client_liveness,
        BaseProbe.touch_client_liveness, BaseProbe._check_client_liveness
        on timeout: Controller.estop_all. One watchdog for the station
        instead of one per model
        the probe's private copy of client liveness
        """
        raise NotImplementedError

    def _watch_loop(self, *args, **kwargs):
        """was ClientLivenessGate._client_liveness_loop
        """
        raise NotImplementedError
