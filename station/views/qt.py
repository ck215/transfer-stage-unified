"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.views.base import Dashboard, PanelView

class SeriesPlot:
    """Pure widget.
    
    Absorbs: SeriesPlot
    """

    def __init__(self, *args, **kwargs):
        """was SeriesPlot.__init__
        """
        raise NotImplementedError

    def paintEvent(self, *args, **kwargs):
        """was SeriesPlot.paintEvent
        """
        raise NotImplementedError

    def set_series(self, *args, **kwargs):
        """was SeriesPlot.set_series
        """
        raise NotImplementedError


class RegionOverlay:
    """Pure widget. Was SelectionOverlay. Qt event-handler names are fixed by
    Qt and exempt from the naming scheme.
    
    Absorbs: SelectionOverlay
    
    MUST SATISFY:
    [BENCH] STILL OPEN. One drag picker in all three views (Tk has only a
    typed prompt today; the Web picker is unreachable). Virtual-desktop and
    display-scaling conversion needs checking on the station PC.
    (REDPERCENT-18, PYSIDE-12)
    """

    def __init__(self, *args, **kwargs):
        """was SelectionOverlay.__init__
        """
        raise NotImplementedError

    def keyPressEvent(self, *args, **kwargs):
        """was SelectionOverlay.keyPressEvent
        """
        raise NotImplementedError

    def mouseMoveEvent(self, *args, **kwargs):
        """was SelectionOverlay.mouseMoveEvent
        """
        raise NotImplementedError

    def mousePressEvent(self, *args, **kwargs):
        """was SelectionOverlay.mousePressEvent
        """
        raise NotImplementedError

    def mouseReleaseEvent(self, *args, **kwargs):
        """was SelectionOverlay.mouseReleaseEvent
        """
        raise NotImplementedError

    def paintEvent(self, *args, **kwargs):
        """was SelectionOverlay.paintEvent
        """
        raise NotImplementedError

    def showEvent(self, *args, **kwargs):
        """was SelectionOverlay.showEvent
        """
        raise NotImplementedError


class DeviceDock:
    """Pure widget.
    
    Absorbs: DeviceDock
    """

    def __init__(self, *args, **kwargs):
        """was DeviceDock.__init__
        """
        raise NotImplementedError

    def closeEvent(self, *args, **kwargs):
        """was DeviceDock.closeEvent
        """
        raise NotImplementedError


class QtPanelView(PanelView):
    """Qt renderer. Was QtDynamicView.
    
    Absorbs: QtDynamicView
    """

    def __init__(self, *args, **kwargs):
        """was QtDynamicView.__init__
        """
        raise NotImplementedError

    def _redraw_plot(self, *args, **kwargs):
        """was QtDynamicView._redraw_plot
        """
        raise NotImplementedError

    def _refresh_log(self, *args, **kwargs):
        """was QtDynamicView._refresh_log
        """
        raise NotImplementedError


class QtDashboard(Dashboard):
    """Qt window.
    
    Absorbs: DashboardWindow, QtEventLogPanel
    """

    def __init__(self, *args, **kwargs):
        """was DashboardWindow.__init__
        """
        raise NotImplementedError

    def changeEvent(self, *args, **kwargs):
        """was DashboardWindow.changeEvent
        """
        raise NotImplementedError

    def closeEvent(self, *args, **kwargs):
        """was DashboardWindow.closeEvent
        """
        raise NotImplementedError

    def _app_has_focus(self, *args, **kwargs):
        """was DashboardWindow._app_has_focus
        """
        raise NotImplementedError

    def _build_event_panel(self, *args, **kwargs):
        """was QtEventLogPanel.__init__
        """
        raise NotImplementedError

    def _build_sidebar(self, *args, **kwargs):
        """was DashboardWindow.populate_sidebar
        """
        raise NotImplementedError

    def _on_dock_closed(self, *args, **kwargs):
        """was DashboardWindow.on_dock_closed
        """
        raise NotImplementedError

    def _on_item_changed(self, *args, **kwargs):
        """was DashboardWindow.on_device_item_changed
        """
        raise NotImplementedError

    def _set_sidebar_checked(self, *args, **kwargs):
        """was DashboardWindow._set_sidebar_checked
        """
        raise NotImplementedError

    def _show_event(self, *args, **kwargs):
        """was QtEventLogPanel.append_event
        """
        raise NotImplementedError
