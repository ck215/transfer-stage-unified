"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.model import Model

class RunLog:
    """Samples of one run. Was RedPercentDataLog.
    
    Absorbs: RedPercentDataLog
    
    MUST SATISFY:
    [REOPEN] Ledger says closed; today's audit says NOT fit for force
    approximation. Velocity is differenced across red events against a 10 Hz
    position cache, and rows are still change-triggered. Needs the owner's
    two answers (uniform sampling? is 10 Hz a firmware limit?).
    (REDPERCENT-16)
    """

    def __init__(self, *args, **kwargs):
        """was RedPercentDataLog.__init__
        """
        raise NotImplementedError

    def add(self, *args, **kwargs):
        """was RedPercentDataLog.add_entry
        """
        raise NotImplementedError

    def save(self, *args, **kwargs):
        """was RedPercentDataLog.save_to_csv
        """
        raise NotImplementedError


class MonitorRun:
    """One monitoring run. Was MonitoringRun.
    
    Absorbs: MonitoringRun, RedPercentSystem
    
    MUST SATISFY:
    [CARRY] Each run freezes its own configuration and owns its log and stop
    event. Start refuses without a focus area. Thread failure ends the run
    and reports. Red and change published as one pair. Run identity, output
    root, sidecar JSON and annotations kept.  (REDPERCENT-1, REDPERCENT-2,
    REDPERCENT-3, REDPERCENT-4, REDPERCENT-5, REDPERCENT-9, REDPERCENT-21,
    REDPERCENT-22, REDPERCENT-23, VIEW-TKINTER-15)
    """

    @property
    def is_active(self):
        """was MonitoringRun.active, RedPercentSystem._new_monitor_generation,
        RedPercentSystem._monitor_generation_is_current
        each run owns its stop_event; a second, global generation counter is
        redundant once MonitorRun exists (verify against S13 tests during
        rebuild)
        """
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        """was MonitoringRun.__init__
        """
        raise NotImplementedError

    def end(self, *args, **kwargs):
        """was MonitoringRun.mark_stopped
        """
        raise NotImplementedError


class RedMonitor(Model):
    """Was RedPercentSystem. Owns a Screen. Follows a Probe for position.
    
    Absorbs: <model.redpercent_system>, PlotDialog, RedPercentSystem
    
    MUST SATISFY:
    [CARRY] Each run freezes its own configuration and owns its log and stop
    event. Start refuses without a focus area. Thread failure ends the run
    and reports. Red and change published as one pair. Run identity, output
    root, sidecar JSON and annotations kept.  (REDPERCENT-1, REDPERCENT-2,
    REDPERCENT-3, REDPERCENT-4, REDPERCENT-5, REDPERCENT-9, REDPERCENT-21,
    REDPERCENT-22, REDPERCENT-23, VIEW-TKINTER-15)
    [OUT] No Red Percent view subclasses. One Position Source control wired
    to set_source. One Save. The probe list follows on_model_added /
    on_model_removed. remove() autosaves or asks before discarding unsaved
    data. The live series comes from the model only.  (REDPERCENT-6,
    REDPERCENT-8, REDPERCENT-10, REDPERCENT-11, REDPERCENT-12,
    REDPERCENT-13, REDPERCENT-17, PYSIDE-3, PYSIDE-4, PYSIDE-7, PYSIDE-8,
    PYSIDE-11, PYSIDE-18, STEPPER-13, WEB-6, WEB-7, WEB-13, VIEW-TKINTER-14)
    [REOPEN] Ledger says closed; today's audit says NOT fit for force
    approximation. Velocity is differenced across red events against a 10 Hz
    position cache, and rows are still change-triggered. Needs the owner's
    two answers (uniform sampling? is 10 Hz a firmware limit?).
    (REDPERCENT-16)
    """

    @property
    def current_red(self):
        """was RedPercentSystem.current_red
        """
        raise NotImplementedError

    @property
    def figure(self):
        """new
        PNG bytes of the analysis plot, rendered once by
        plot_data.render_figure and displayed by every view.
        """
        raise NotImplementedError

    @property
    def has_unsaved_data(self):
        """was RedPercentSystem.has_unsaved_data
        """
        raise NotImplementedError

    @property
    def is_running(self):
        """was RedPercentSystem.monitoring
        the setter is purged; start_run/end_run are the only writers
        """
        raise NotImplementedError

    @property
    def red_change(self):
        """was RedPercentSystem.red_change
        """
        raise NotImplementedError

    @property
    def run_dir(self):
        """was RedPercentSystem.run_dir
        """
        raise NotImplementedError

    @property
    def run_id(self):
        """was RedPercentSystem.effective_run_id
        """
        raise NotImplementedError

    @property
    def series(self):
        """was RedPercentSystem.plot_series
        """
        raise NotImplementedError

    @property
    def set_sync(self):
        """was RedPercentSystem.sync_x, RedPercentSystem.sync_y,
        RedPercentSystem.sync_z, RedPercentSystem.toggle_sync_x,
        RedPercentSystem.toggle_sync_y, RedPercentSystem.toggle_sync_z
        nine members (three properties, three setters, three toggles) become
        one sync_axes Param and one command
        """
        raise NotImplementedError

    @property
    def source_options(self):
        """was RedPercentSystem.get_available_probe_names
        """
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        """was RedPercentSystem.__init__
        """
        raise NotImplementedError

    def end_run(self, *args, **kwargs):
        """was RedPercentSystem.stop_monitoring
        """
        raise NotImplementedError

    def load_run(self, *args, **kwargs):
        """was PlotDialog.load_csv
        """
        raise NotImplementedError

    def on_model_added(self, *args, **kwargs):
        """was RedPercentSystem.probe_registered
        """
        raise NotImplementedError

    def on_model_removed(self, *args, **kwargs):
        """was RedPercentSystem.probe_released
        """
        raise NotImplementedError

    def reset_baseline(self, *args, **kwargs):
        """was RedPercentSystem.reset_baseline
        """
        raise NotImplementedError

    def save(self, *args, **kwargs):
        """was RedPercentSystem.save_run, RedPercentSystem.save_log,
        RedPercentSystem.autosave_log
        always writes under output_root; the view then copies or downloads
        the file. No view passes a path to the server (designs out finding
        9)
        """
        raise NotImplementedError

    def set_plot_dims(self, *args, **kwargs):
        """was PlotDialog.select_plot_type
        """
        raise NotImplementedError

    def set_region(self, *args, **kwargs):
        """was RedPercentSystem.set_focus_area
        """
        raise NotImplementedError

    def set_source(self, *args, **kwargs):
        """was RedPercentSystem.set_stepper_model
        """
        raise NotImplementedError

    def start_run(self, *args, **kwargs):
        """was RedPercentSystem.start_monitoring
        """
        raise NotImplementedError

    def _default_output_root(self, *args, **kwargs):
        """was <model.redpercent_system>._default_output_root
        """
        raise NotImplementedError

    def _measure_red(self, *args, **kwargs):
        """was RedPercentSystem.detect_red
        """
        raise NotImplementedError

    def _publish_red(self, *args, **kwargs):
        """was RedPercentSystem._publish_red
        """
        raise NotImplementedError

    def _read_dim(self, *args, **kwargs):
        """was RedPercentSystem._read_dim
        """
        raise NotImplementedError

    def _reselect(self, *args, **kwargs):
        """was RedPercentSystem._reselect
        """
        raise NotImplementedError

    def _run_loop(self, *args, **kwargs):
        """was RedPercentSystem._monitor_colors
        """
        raise NotImplementedError

    def _station_meta(self, *args, **kwargs):
        """was RedPercentSystem.station_meta
        """
        raise NotImplementedError
