"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.views.base import Dashboard, PanelView

class ClosableNotebook:
    """Pure widget. Was DraggableClosableNotebook.
    
    Absorbs: DraggableClosableNotebook
    """

    def __init__(self, *args, **kwargs):
        """was DraggableClosableNotebook.__init__
        """
        raise NotImplementedError

    def close_tab(self, *args, **kwargs):
        """was DraggableClosableNotebook.close_tab
        """
        raise NotImplementedError

    def _on_drag(self, *args, **kwargs):
        """was DraggableClosableNotebook.on_drag
        """
        raise NotImplementedError

    def _on_middle_press(self, *args, **kwargs):
        """was DraggableClosableNotebook.on_middle_press
        """
        raise NotImplementedError

    def _on_press(self, *args, **kwargs):
        """was DraggableClosableNotebook.on_press
        """
        raise NotImplementedError

    def _on_release(self, *args, **kwargs):
        """was DraggableClosableNotebook.on_release
        """
        raise NotImplementedError


class TkPanelView(PanelView):
    """Tk renderer. Was DynamicView.
    
    Absorbs: DynamicView
    """

    def __init__(self, *args, **kwargs):
        """was DynamicView.__init__
        """
        raise NotImplementedError

    def _make_plot(self, *args, **kwargs):
        """was DynamicView._build_plot
        """
        raise NotImplementedError

    def _make_region_select(self, *args, **kwargs):
        """was DynamicView._select_region
        """
        raise NotImplementedError

    def _redraw_plot(self, *args, **kwargs):
        """was DynamicView._redraw_plot
        """
        raise NotImplementedError

    def _refresh_log(self, *args, **kwargs):
        """was DynamicView._refresh_log
        """
        raise NotImplementedError

    def _role_colors(self, *args, **kwargs):
        """was DynamicView._role_colors
        """
        raise NotImplementedError

    def _validate_entry(self, *args, **kwargs):
        """was DynamicView._is_valid_float
        """
        raise NotImplementedError


class TkDashboard(Dashboard):
    """Tk window.
    
    Absorbs: DashboardWindow, TkEventLogPanel
    """

    def __init__(self, *args, **kwargs):
        """was DashboardWindow.__init__
        """
        raise NotImplementedError

    def _build_event_panel(self, *args, **kwargs):
        """was TkEventLogPanel.__init__
        """
        raise NotImplementedError

    def _build_models_menu(self, *args, **kwargs):
        """was DashboardWindow._build_devices_menu
        """
        raise NotImplementedError

    def _on_model_toggled(self, *args, **kwargs):
        """was DashboardWindow._on_device_menu_toggled
        """
        raise NotImplementedError

    def _on_tab_close(self, *args, **kwargs):
        """was DashboardWindow.on_tab_close_requested
        """
        raise NotImplementedError

    def _show_event(self, *args, **kwargs):
        """was TkEventLogPanel.append_event
        """
        raise NotImplementedError
