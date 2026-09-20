/**
 * Transfer Stage Unified Control - Web Client Architecture (Vanilla JS)
 * Handles schema-driven UI construction, state polling, telemetry readouts,
 * command dispatch, and interactive modal dialogs.
 */

// Session token (RC-10). The server injects this into the HTML it serves, so
// this page has it and a cross-site page does not — a cross-site page cannot
// read our HTML. Every same-origin /api/ call carries it, so the server can
// refuse requests that did not come from the dashboard it served. Wrapping
// fetch here rather than editing 13 call sites means a new call site cannot
// forget it.
(function attachSessionToken() {
  const meta = document.querySelector('meta[name="stage-token"]');
  const token = meta ? meta.getAttribute('content') : '';
  const originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isOwnApi = url.startsWith('/api/') || url.startsWith(window.location.origin + '/api/');
    if (!isOwnApi || !token) {
      return originalFetch(input, init);
    }
    const opts = Object.assign({}, init);
    opts.headers = new Headers((init && init.headers) || (typeof input !== 'string' && input.headers) || {});
    opts.headers.set('X-Stage-Token', token);
    return originalFetch(input, opts);
  };
})();

class TransferStageApp {
  constructor() {
    this.devices = {};          // device_name -> schema
    this.deviceState = {};      // device_name -> { attr: val }
    this.activeFilter = 'all';  // 'all' or device name

    // Initialization
    this.pollTimer = null;
    this.pollIntervalMs = 20;
    this.isPolling = false;
    this.logs = [];
    this.autoScrollLogs = true;

    // Hardware Setup Wizard State
    this.systemStatus = 'setup'; // 'setup' | 'running'
    this.scannedPorts = ['SIM'];
    this.scannedControllers = ['None'];
    this.setupDeviceConfigs = [
      { id: 'Stepper Probe', name: 'Stepper Probe', desc: 'X/Y/Z Stepper Stage with Joystick', defaultPort: 'SIM', defaultCtrl: 'None', hasController: true, enabled: true },
      { id: 'DC Probe', name: 'DC Probe', desc: 'X/Y/Z DC Motor Probe Positioner', defaultPort: 'SIM', defaultCtrl: 'None', hasController: true, enabled: true },
      { id: 'Chuck Positioner', name: 'Chuck Positioner', desc: 'Motorized Substrate Chuck Stage', defaultPort: 'SIM', defaultCtrl: 'None', hasController: true, enabled: true },
      { id: 'Temperature Controller', name: 'Temperature Controller', desc: 'Thermal Stage Sensor & Heater', defaultPort: 'SIM', defaultCtrl: 'None', hasController: false, enabled: true },
      { id: 'SMC100 Rotator', name: 'SMC100 Rotator', desc: 'Newport Precision Single-Axis Stage', defaultPort: 'SIM', defaultCtrl: 'None', hasController: false, enabled: true },
      { id: 'Red Percent Window', name: 'Red Percent Window', desc: 'ToupCam Optical Flake Monitor', defaultPort: 'SIM', defaultCtrl: 'None', hasController: false, enabled: true }
    ];

    // Optical Plotter state
    this.plotterData = {
      timestamps: [],
      currentRed: [],
      deltaRed: []
    };
    this.maxPlotPoints = 60;
    this.baselineRed = 0.0;
    this.peakRed = 0.0;

    // Camera Focus ROI state
    this.focusRoi = { active: false, x: 0, y: 0, w: 0, h: 0 };
    this.hostOriginalWidth = 1920;
    this.hostOriginalHeight = 1080;
    this.hostMonitorLeft = 0;
    this.hostMonitorTop = 0;
    this.lastFocusImage = null;
    this.focusStreamInterval = null;

    // Cache DOM Elements
    this.dom = {
      deviceGrid: document.getElementById('device-grid'),
      sidebarNav: document.getElementById('sidebar-device-list'),
      tabAllCount: document.getElementById('tab-count-all'),
      currentViewTitle: document.getElementById('current-view-title'),
      viewDeviceCount: document.getElementById('view-device-count'),
      summaryChips: document.getElementById('summary-chips'),
      connectionBeacon: document.getElementById('system-status-beacon'),
      connectionStateText: document.getElementById('connection-state-text'),
      syncStatusIndicator: document.getElementById('sync-status-indicator'),
      pollingFrequencyLabel: document.getElementById('polling-frequency-label'),
      pollIntervalSelect: document.getElementById('poll-interval-select'),
      btnOpenSettings: document.getElementById('btn-open-settings'),
      btnCloseSettings: document.getElementById('btn-close-settings'),
      btnSettingsDone: document.getElementById('btn-settings-done'),
      settingsModal: document.getElementById('settings-modal'),
      btnRefreshDevices: document.getElementById('btn-refresh-devices'),
      btnGlobalHalt: document.getElementById('btn-global-halt'),
      btnCardExpandAll: document.getElementById('btn-card-expand-all'),
      toastContainer: document.getElementById('toast-container'),

      // Logs Modal
      btnToggleLogs: document.getElementById('btn-toggle-logs'),
      logModal: document.getElementById('controller-log-modal'),
      btnCloseLogs: document.getElementById('btn-close-logs'),
      logConsole: document.getElementById('controller-log-console'),
      logCounterBadge: document.getElementById('log-counter-badge'),
      chkAutoScroll: document.getElementById('chk-autoscroll'),
      btnClearLogs: document.getElementById('btn-clear-logs'),
      btnExportLogs: document.getElementById('btn-export-logs'),
      formLogCmd: document.getElementById('form-log-command'),
      inputLogCmd: document.getElementById('input-log-cmd'),

      // Plotter Modal
      btnTogglePlotter: document.getElementById('btn-toggle-plotter'),
      plotterModal: document.getElementById('redpercent-modal'),
      btnClosePlotter: document.getElementById('btn-close-plotter'),
      redCanvas: document.getElementById('redpercent-canvas'),
      plotterCurrentRed: document.getElementById('plotter-current-red'),
      plotterBaselineRed: document.getElementById('plotter-baseline-red'),
      plotterDeltaRed: document.getElementById('plotter-delta-red'),
      plotterPeakRed: document.getElementById('plotter-peak-red'),
      btnPlotterStart: document.getElementById('btn-plotter-start'),
      btnPlotterReset: document.getElementById('btn-plotter-reset'),
      btnPlotterStop: document.getElementById('btn-plotter-stop'),
      focusRoiCanvas: document.getElementById('focus-roi-canvas'),
      roiStatusBadge: document.getElementById('roi-status-badge'),

      // File Picker Modal
      fileModal: document.getElementById('file-picker-modal'),
      btnCloseFileModal: document.getElementById('btn-close-file-modal'),
      btnCancelFile: document.getElementById('btn-cancel-file'),
      btnConfirmFile: document.getElementById('btn-confirm-file'),
      scriptFileInput: document.getElementById('script-file-input'),
      btnBrowseFile: document.getElementById('btn-browse-file'),
      selectedFileDisplay: document.getElementById('selected-file-display'),
      scriptPathText: document.getElementById('script-path-text'),

      // Setup Wizard Modal
      setupModal: document.getElementById('setup-wizard-modal'),
      btnOpenSetup: document.getElementById('btn-open-setup'),
      btnCloseSetupModal: document.getElementById('btn-close-setup-modal'),
      btnSetupCancel: document.getElementById('btn-setup-cancel'),
      btnSetupScan: document.getElementById('btn-setup-scan'),
      btnSetupAllSim: document.getElementById('btn-setup-all-sim'),
      btnSetupDisableAll: document.getElementById('btn-setup-disable-all'),
      btnSetupLaunch: document.getElementById('btn-setup-launch'),
      setupScanSpinner: document.getElementById('setup-scan-spinner'),
      setupScanBtnText: document.getElementById('setup-scan-btn-text'),
      setupScanIndicator: document.getElementById('setup-scan-indicator'),
      setupScanMeta: document.getElementById('setup-scan-meta'),
      setupDeviceRows: document.getElementById('setup-device-rows'),
      setupAlertBox: document.getElementById('setup-alert-box')
    };

    this.initEventListeners();
  }

  // =========================================================================
  // Bootstrap & Event Listeners
  // =========================================================================
  async init() {
    await this.checkSystemStatus();
    this.setupPolling(this.pollIntervalMs);
  }

  async checkSystemStatus() {
    try {
      const res = await fetch('/api/system/status');
      if (res.ok) {
        const data = await res.json();
        this.systemStatus = data.status || 'running';
      }
    } catch (e) {
      console.warn('[WebDashboard] Could not fetch system status:', e);
      this.systemStatus = 'running';
    }

    if (this.systemStatus === 'setup') {
      this.openSetupWizard();
      this.scanHardware();
    } else {
      await this.fetchDevices();
    }
  }

  initEventListeners() {
    // Nav tabs filter
    const allTab = document.querySelector('[data-device-tab="all"]');
    if (allTab) {
      allTab.addEventListener('click', () => this.setDeviceFilter('all'));
    }

    if (this.dom.btnRefreshDevices) {
      this.dom.btnRefreshDevices.addEventListener('click', () => {
        this.fetchDevices();
        this.showToast('Rescanning hardware schemas...', 'info');
      });
    }

    if (this.dom.btnGlobalHalt) {
      this.dom.btnGlobalHalt.addEventListener('click', () => this.triggerGlobalEmergencyStop());
    }

    
    if (this.dom.btnOpenSettings) {
      this.dom.btnOpenSettings.addEventListener('click', () => {
        this.toggleModal(this.dom.settingsModal, true);
      });
    }
    if (this.dom.btnCloseSettings) {
      this.dom.btnCloseSettings.addEventListener('click', () => {
        this.toggleModal(this.dom.settingsModal, false);
      });
    }
    if (this.dom.btnSettingsDone) {
      this.dom.btnSettingsDone.addEventListener('click', () => {
        this.toggleModal(this.dom.settingsModal, false);
      });
    }

    if (this.dom.pollIntervalSelect) {
      this.dom.pollIntervalSelect.addEventListener('change', (e) => {
        const val = parseInt(e.target.value, 10);
        this.setupPolling(val);
      });
    }

    if (this.dom.btnCardExpandAll) {
      this.dom.btnCardExpandAll.addEventListener('click', () => {
        this.dom.deviceGrid.classList.toggle('compact-mode');
      });
    }

    // Modal Logs
    if (this.dom.btnToggleLogs) {
      this.dom.btnToggleLogs.addEventListener('click', () => this.toggleModal(this.dom.logModal, true));
    }
    if (this.dom.btnCloseLogs) {
      this.dom.btnCloseLogs.addEventListener('click', () => this.toggleModal(this.dom.logModal, false));
    }
    if (this.dom.btnClearLogs) {
      this.dom.btnClearLogs.addEventListener('click', () => {
        this.logs = [];
        if (this.dom.logConsole) this.dom.logConsole.innerHTML = '';
        if (this.dom.logCounterBadge) this.dom.logCounterBadge.innerText = '0';
      });
    }
    if (this.dom.btnExportLogs) {
      this.dom.btnExportLogs.addEventListener('click', () => this.exportLogsToFile());
    }
    if (this.dom.formLogCmd) {
      this.dom.formLogCmd.addEventListener('submit', (e) => {
        e.preventDefault();
        this.handleLogConsoleCommand();
      });
    }
    if (this.dom.chkAutoScroll) {
      this.dom.chkAutoScroll.addEventListener('change', (e) => {
        this.autoScrollLogs = e.target.checked;
      });
    }

    // Modal Plotter
    if (this.dom.btnTogglePlotter) {
      this.dom.btnTogglePlotter.addEventListener('click', () => {
        this.toggleModal(this.dom.plotterModal, true);
        this.renderPlotterCanvas();
        
      });
    }
    if (this.dom.btnClosePlotter) {
      this.dom.btnClosePlotter.addEventListener('click', () => {
        this.toggleModal(this.dom.plotterModal, false);
        
      });
    }
    if (this.dom.btnPlotterReset) {
      this.dom.btnPlotterReset.addEventListener('click', () => {
        const curr = this.plotterData.currentRed.slice(-1)[0] || 0;
        this.baselineRed = curr;
        this.plotterData.deltaRed = [];
        if (this.dom.plotterBaselineRed) {
          this.dom.plotterBaselineRed.innerText = curr.toFixed(2) + '%';
        }
        this.showToast(`Baseline optical red reset to ${curr.toFixed(2)}%`, 'info');
      });
    }
    if (this.dom.btnPlotterStart) {
      this.dom.btnPlotterStart.addEventListener('click', () => {
        this.dispatchCommand('Red Percent Window', 'start_monitoring');
      });
    }
    if (this.dom.btnPlotterStop) {
      this.dom.btnPlotterStop.addEventListener('click', () => {
        this.dispatchCommand('Red Percent Window', 'stop_monitoring');
      });
    }

    // File picker modal
    if (this.dom.btnCloseFileModal) {
      this.dom.btnCloseFileModal.addEventListener('click', () => this.toggleModal(this.dom.fileModal, false));
    }
    if (this.dom.btnCancelFile) {
      this.dom.btnCancelFile.addEventListener('click', () => this.toggleModal(this.dom.fileModal, false));
    }
    if (this.dom.btnBrowseFile && this.dom.scriptFileInput) {
      this.dom.btnBrowseFile.addEventListener('click', () => this.dom.scriptFileInput.click());
      this.dom.scriptFileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
          if (this.dom.selectedFileDisplay) this.dom.selectedFileDisplay.innerText = file.name;
          if (this.dom.scriptPathText) this.dom.scriptPathText.value = file.name;
        }
      });
    }
    if (this.dom.btnConfirmFile) {
      this.dom.btnConfirmFile.addEventListener('click', () => {
        const path = this.dom.scriptPathText ? this.dom.scriptPathText.value.trim() : '';
        if (path) {
          this.executeScriptFile(path);
          this.toggleModal(this.dom.fileModal, false);
        } else {
          this.showToast('Please select or specify a script file path.', 'warning');
        }
      });
    }

    // Setup Wizard Modal listeners
    if (this.dom.btnOpenSetup) {
      this.dom.btnOpenSetup.addEventListener('click', () => {
        this.openSetupWizard();
      });
    }
    if (this.dom.btnCloseSetupModal) {
      this.dom.btnCloseSetupModal.addEventListener('click', () => {
        this.toggleModal(this.dom.setupModal, false);
      });
    }
    if (this.dom.btnSetupCancel) {
      this.dom.btnSetupCancel.addEventListener('click', () => {
        this.toggleModal(this.dom.setupModal, false);
      });
    }
    if (this.dom.btnSetupScan) {
      this.dom.btnSetupScan.addEventListener('click', () => {
        this.scanHardware();
      });
    }
    if (this.dom.btnSetupAllSim) {
      this.dom.btnSetupAllSim.addEventListener('click', () => {
        this.setAllSetupToSim();
      });
    }
    if (this.dom.btnSetupDisableAll) {
      this.dom.btnSetupDisableAll.addEventListener('click', () => {
        this.setAllSetupToDisabled();
      });
    }
    if (this.dom.btnSetupLaunch) {
      this.dom.btnSetupLaunch.addEventListener('click', () => {
        this.initializeHardwareSetup();
      });
    }

    // Close modals on overlay backdrop click
    window.addEventListener('click', (e) => {
      if (e.target.classList && e.target.classList.contains('modal-overlay')) {
        this.toggleModal(e.target, false);
      }
    });

    // Keyboard ESC to close any open modal
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(modal => {
          this.toggleModal(modal, false);
        });
      }
    });
  }

  toggleModal(modalEl, show) {
    if (!modalEl) return;
    if (show) {
      modalEl.classList.remove('hidden');
    } else {
      modalEl.classList.add('hidden');
    }
  }

  // =========================================================================
  // Hardware Schema Fetching & Rendering
  // =========================================================================
  async fetchDevices() {
    try {
      this.setConnectionState('connecting', 'Connecting...');
      const response = await fetch('/api/devices');
      if (!response.ok) throw new Error(`HTTP error ${response.status}`);
      this.devices = await response.json();

      this.setConnectionState('online', 'Online');
      this.renderSidebar();
      this.renderDeviceCards();
      this.renderHeaderSummary();
      this.updateActiveDeviceCounts();
    } catch (err) {
      console.error('[WebDashboard] fetchDevices error:', err);
      this.setConnectionState('offline', 'Disconnected');
      this.showToast(`Failed to load device schemas: ${err.message}`, 'error');
    }
  }

  setConnectionState(status, text) {
    if (this.dom.connectionBeacon) {
      this.dom.connectionBeacon.className = `system-status-indicator ${status}`;
    }
    if (this.dom.connectionStateText) {
      this.dom.connectionStateText.innerText = text;
      if (status === 'online') {
        this.dom.connectionStateText.style.color = 'var(--status-active)';
      } else if (status === 'offline') {
        this.dom.connectionStateText.style.color = 'var(--status-error)';
      } else {
        this.dom.connectionStateText.style.color = 'var(--status-busy)';
      }
    }
    if (this.dom.syncStatusIndicator) {
      this.dom.syncStatusIndicator.innerText = status.toUpperCase();
      this.dom.syncStatusIndicator.className = status === 'online' ? 'telemetry-value text-success' : 'telemetry-value text-error';
    }
  }

  updateActiveDeviceCounts() {
    const devNames = Object.keys(this.devices);
    const count = devNames.length;
    if (this.dom.tabAllCount) this.dom.tabAllCount.innerText = count;
    if (this.dom.viewDeviceCount) this.dom.viewDeviceCount.innerText = `${count} devices active`;
  }

  renderSidebar() {
    if (!this.dom.sidebarNav) return;
    this.dom.sidebarNav.innerHTML = '';

    const activeDevs = [];
    const disabledDevs = [];
    Object.keys(this.devices).forEach(name => {
      if (this.devices[name]._disabled) disabledDevs.push(name);
      else activeDevs.push(name);
    });
    
    const sortedDevNames = [...activeDevs, ...disabledDevs];
    
    for (const devName of sortedDevNames) {
      const isDisabled = this.devices[devName]._disabled;
      const li = document.createElement('li');
      li.className = 'nav-item' + (isDisabled ? ' disabled-tab' : '');

      const button = document.createElement('button');
      const isSelected = this.activeFilter === devName;
      button.className = `nav-tab ${isSelected ? 'active' : ''}`;
      button.dataset.deviceTab = devName;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      button.addEventListener('click', () => this.setDeviceFilter(devName));

      // Color coding dot based on device type
      let dotColorClass = 'dot-all';
      const lower = devName.toLowerCase();
      if (lower.includes('stepper')) dotColorClass = 'dot-stepper';
      else if (lower.includes('dc')) dotColorClass = 'dot-dc';
      else if (lower.includes('chuck')) dotColorClass = 'dot-chuck';
      else if (lower.includes('temp')) dotColorClass = 'dot-temp';
      else if (lower.includes('rotator')) dotColorClass = 'dot-rotator';
      else if (lower.includes('red')) dotColorClass = 'dot-red';

      button.innerHTML = `
        
        <span class="tab-label" title="${devName}">${devName}</span>
        <span class="tab-status-pill" data-status-for="${devName}">--</span>
      `;

      li.appendChild(button);
      this.dom.sidebarNav.appendChild(li);
    }
  }

  setDeviceFilter(filterName) {
    this.activeFilter = filterName;

    // Update active tab styles
    document.querySelectorAll('.nav-tab').forEach(tab => {
      const match = tab.dataset.deviceTab === filterName;
      if (match) {
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
      } else {
        tab.classList.remove('active');
        tab.setAttribute('aria-selected', 'false');
      }
    });

    if (this.dom.currentViewTitle) {
      this.dom.currentViewTitle.innerText = filterName === 'all' ? 'Active Devices Dashboard' : `${filterName} Controls`;
    }

    // Filter cards
    document.querySelectorAll('.device-card').forEach(card => {
      const cardDev = card.dataset.device;
      if (filterName === 'all' || cardDev === filterName) {
        card.style.display = 'flex';
      } else {
        card.style.display = 'none';
      }
    });
  }

  renderHeaderSummary() {
    if (!this.dom.summaryChips) return;
    this.dom.summaryChips.innerHTML = '';

    const devNames = Object.keys(this.devices);
    if (devNames.length === 0) {
      this.dom.summaryChips.innerHTML = '<span class="summary-chip summary-chip-placeholder">No active devices found</span>';
      return;
    }

    for (const devName of devNames) {
      const chip = document.createElement('span');
      chip.className = 'summary-chip';
      chip.id = `chip-${this.sanitizeId(devName)}`;
      chip.innerHTML = `
        <span class="chip-dot"></span>
        <span>${devName}</span>
      `;
      this.dom.summaryChips.appendChild(chip);
    }
  }

  renderDeviceCards() {
    if (!this.dom.deviceGrid) return;
    this.dom.deviceGrid.innerHTML = '';

    const devEntries = Object.entries(this.devices);
    if (devEntries.length === 0) {
      this.dom.deviceGrid.innerHTML = `
        <div class="loading-state-card">
          <p class="loading-text">No active devices reported by the Unified Model Manager.</p>
        </div>`;
      return;
    }

    for (const [devName, schema] of devEntries) {
      const card = document.createElement('div');
      card.className = 'device-card';
      card.dataset.device = devName;
      card.id = `card-${this.sanitizeId(devName)}`;

      const sections = schema.sections || [];
      let sectionsHtml = '';

      for (let sIdx = 0; sIdx < sections.length; sIdx++) {
        const sec = sections[sIdx];
        const elements = sec.elements || [];
        let elementsHtml = '';

        for (let eIdx = 0; eIdx < elements.length; eIdx++) {
          const el = elements[eIdx];
          elementsHtml += this.buildElementHtml(devName, el);
        }

        sectionsHtml += `
          <div class="card-section">
            <div class="section-title">${sec.title || 'Control Parameters'}</div>
            ${elementsHtml}
          </div>
        `;
      }

      let focusPanelHtml = '';

      card.innerHTML = `
        <div class="card-header">
          <div class="card-title-group">
            <span class="card-title">${devName}</span>
          </div>
          <span class="badge badge-subtle" id="badge-${this.sanitizeId(devName)}">INITIALIZING</span>
        </div>
        <div class="card-body">
          ${sectionsHtml}
          ${focusPanelHtml}
        </div>
      `;

      this.dom.deviceGrid.appendChild(card);
    }

    this.bindCardInteractiveEvents();
  }

  buildElementHtml(devName, el) {
    const sanitizedDev = this.sanitizeId(devName);
    const label = el.text || el.model_attr || '';
    const cleanAttr = el.model_attr ? this.sanitizeId(el.model_attr) : '';

    if (el.type === 'readonly') {
      const isPos = el.model_attr && (el.model_attr.includes('pos') || el.model_attr.includes('temp'));
      const readoutClass = isPos ? 'schema-readonly numeric-readout' : 'schema-readonly';
      return `
        <div class="schema-row">
          <span class="schema-label">${label}</span>
          <span class="${readoutClass}" id="val-${sanitizedDev}-${cleanAttr}">--</span>
        </div>
      `;
    }

    if (el.type === 'entry') {
      return `
        <div class="schema-row">
          <span class="schema-label">${label}</span>
          <div class="schema-input-group">
            <input type="text" class="schema-input" id="input-${sanitizedDev}-${cleanAttr}" placeholder="Value" />
            <button type="button" class="btn btn-sm btn-secondary btn-set-attr" 
                    data-device="${devName}" data-attr="${el.model_attr}">Set</button>
          </div>
        </div>
      `;
    }

    if (el.type === 'button') {
      const isHalt = el.command && (el.command.toLowerCase().includes('stop') || el.command.toLowerCase().includes('halt'));
      const btnClass = isHalt ? 'btn btn-danger btn-full' : 'btn btn-secondary btn-full';
      return `
        <div class="schema-row">
          <button type="button" class="${btnClass} btn-dispatch-cmd" 
                  data-device="${devName}" data-command="${el.command}">${label}</button>
        </div>
      `;
    }

    if (el.type === 'toggle') {
      return `
        <div class="schema-row">
          <span class="schema-label">${label}</span>
          <button type="button" class="btn schema-toggle btn-dispatch-toggle" 
                  id="toggle-${sanitizedDev}-${cleanAttr}"
                  data-device="${devName}" 
                  data-command="${el.command}" 
                  data-attr="${el.model_attr}"
                  data-true-text="${el.true_text || 'ON'}" 
                  data-false-text="${el.false_text || 'OFF'}">
            ${el.false_text || 'OFF'}
          </button>
        </div>
      `;
    }

    if (el.type === 'dropdown') {
      const options = Array.isArray(el.options) ? el.options : [];
      const optionsHtml = options.map(opt => `<option value="${this.escapeHtml(String(opt))}">${this.escapeHtml(String(opt))}</option>`).join('');
      const optsCmdAttr = el.options_command ? ` data-options-command="${el.options_command}"` : '';
      return `
        <div class="schema-row">
          <span class="schema-label">${this.escapeHtml(label)}</span>
          <select class="schema-select btn-dispatch-dropdown" 
                  id="select-${sanitizedDev}-${cleanAttr}"
                  data-device="${devName}" 
                  data-attr="${el.model_attr || ''}"
                  data-command="${el.command || ''}"${optsCmdAttr}>
            <option value="">Select option...</option>
            ${optionsHtml}
          </select>
        </div>
      `;
    }

    return '';
  }

  bindCardInteractiveEvents() {
    // Dropdown selects options_command fetching
    document.querySelectorAll('select[data-options-command]').forEach(select => {
      if (select.dataset.populated) return;
      const dev = select.dataset.device;
      const cmd = select.dataset.optionsCommand;
      if (dev && cmd && select.options.length <= 1) { // Only placeholder exists
        fetch('/api/options?device=' + encodeURIComponent(dev) + '&command=' + encodeURIComponent(cmd))
          .then(r => r.json())
          .then(data => {
            if (data.status === 'ok' && Array.isArray(data.options)) {
              const optionsHtml = data.options.map(opt => `<option value="${this.escapeHtml(String(opt))}">${this.escapeHtml(String(opt))}</option>`).join('');
              select.innerHTML = '<option value="">Select option...</option>' + optionsHtml;
            }
            select.dataset.populated = "true";
          })
          .catch(e => console.warn('Failed to load dropdown options:', e));
      }
    });

    // Set Attr Buttons
    document.querySelectorAll('.btn-set-attr').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const dev = btn.dataset.device;
        const attr = btn.dataset.attr;
        const inputEl = document.getElementById(`input-${this.sanitizeId(dev)}-${this.sanitizeId(attr)}`);
        if (inputEl) {
          this.setDeviceAttribute(dev, attr, inputEl.value, btn);
        }
      });
    });

    // Inputs Enter Key
    document.querySelectorAll('.schema-input').forEach(input => {
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const row = input.closest('.schema-row');
          if (row) {
            const btn = row.querySelector('.btn-set-attr');
            if (btn) btn.click();
          }
        }
      });
    });

    // Command Buttons
    document.querySelectorAll('.btn-dispatch-cmd').forEach(btn => {
      btn.addEventListener('click', () => {
        const dev = btn.dataset.device;
        const cmd = btn.dataset.command;
        this.dispatchCommand(dev, cmd, [], btn);
      });
    });

    // Toggle Buttons
    document.querySelectorAll('.btn-dispatch-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const dev = btn.dataset.device;
        const cmd = btn.dataset.command;
        this.dispatchCommand(dev, cmd, [], btn);
      });
    });

    // Dropdown selects
    document.querySelectorAll('.btn-dispatch-dropdown').forEach(select => {
      select.addEventListener('change', (e) => {
        const dev = select.dataset.device;
        const cmd = select.dataset.command;
        const attr = select.dataset.attr;
        const val = e.target.value;
        if (cmd) {
          this.dispatchCommand(dev, cmd, [val]);
        } else if (attr) {
          this.setDeviceAttribute(dev, attr, val);
        }
      });
    });

    // Camera Focus ROI / Red Percent panel binding
    const focusCanvas = document.getElementById('focus-roi-canvas');
    if (focusCanvas) {
      this.dom.focusRoiCanvas = focusCanvas;
      this.dom.roiStatusBadge = document.getElementById('roi-status-badge');
      this.initFocusRoiEvents();
      
    }

    const btnCloseRoiModal = document.getElementById('btn-close-roi-modal');
    if (btnCloseRoiModal && !btnCloseRoiModal.dataset.bound) {
      btnCloseRoiModal.dataset.bound = 'true';
      btnCloseRoiModal.addEventListener('click', () => {
        this.stopFocusStream();
      });
    }

    const btnConfirmRoi = document.getElementById('btn-confirm-roi');
    if (btnConfirmRoi && !btnConfirmRoi.dataset.bound) {
      btnConfirmRoi.dataset.bound = 'true';
      btnConfirmRoi.addEventListener('click', () => {
        this.stopFocusStream();
      });
    }

    const btnPlotterStart = document.getElementById('btn-plotter-start');
    if (btnPlotterStart && !btnPlotterStart.dataset.bound) {
      btnPlotterStart.dataset.bound = 'true';
      this.dom.btnPlotterStart = btnPlotterStart;
      btnPlotterStart.addEventListener('click', () => {
        this.dispatchCommand('Red Percent Window', 'start_monitoring');
      });
    }

    const btnPlotterReset = document.getElementById('btn-plotter-reset');
    if (btnPlotterReset && !btnPlotterReset.dataset.bound) {
      btnPlotterReset.dataset.bound = 'true';
      this.dom.btnPlotterReset = btnPlotterReset;
      btnPlotterReset.addEventListener('click', () => {
        const curr = this.plotterData.currentRed.slice(-1)[0] || 0;
        this.baselineRed = curr;
        this.plotterData.deltaRed = [];
        if (this.dom.plotterBaselineRed) {
          this.dom.plotterBaselineRed.innerText = curr.toFixed(2) + '%';
        }
        this.showToast(`Baseline optical red reset to ${curr.toFixed(2)}%`, 'info');
      });
    }

    const btnPlotterStop = document.getElementById('btn-plotter-stop');
    if (btnPlotterStop && !btnPlotterStop.dataset.bound) {
      btnPlotterStop.dataset.bound = 'true';
      this.dom.btnPlotterStop = btnPlotterStop;
      btnPlotterStop.addEventListener('click', () => {
        this.dispatchCommand('Red Percent Window', 'stop_monitoring');
      });
    }
  }

  // =========================================================================
  // Polling Engine: Telemetry, State & Rolling Logs
  // =========================================================================
  setupPolling(intervalMs) {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }

    this.pollIntervalMs = intervalMs;
    if (this.dom.pollingFrequencyLabel) {
      this.dom.pollingFrequencyLabel.innerText = intervalMs > 0 ? `${intervalMs} ms` : 'PAUSED';
    }

    if (intervalMs > 0) {
      this.pollTimer = setInterval(() => this.runPollCycle(), intervalMs);
    }
  }

  async runPollCycle() {
    if (this.isPolling) return;
    this.isPolling = true;

    try {
      await Promise.all([
        this.pollState(),
        this.pollLogs(),
        this.pollErrors()
      ]);
    } catch (err) {
      console.warn('[WebDashboard] Polling cycle error:', err);
    } finally {
      this.isPolling = false;
    }
  }

  async pollState() {
    try {
      const res = await fetch('/api/state');
      if (!res.ok) {
        this.setConnectionState('offline', res.status >= 500 ? 'Server Error' : `HTTP ${res.status}`);
        return;
      }
      const data = await res.json();
      this.deviceState = data;
      this.setConnectionState('online', 'Online');

      for (const [devName, attrs] of Object.entries(data)) {
        const sanitizedDev = this.sanitizeId(devName);

        for (const [attr, val] of Object.entries(attrs)) {
          const cleanAttr = this.sanitizeId(attr);

          // Update readonly text display
          const valEl = document.getElementById(`val-${sanitizedDev}-${cleanAttr}`);
          if (valEl) {
            valEl.innerText = (val !== null && val !== undefined) ? String(val) : '--';
          }

          // Update entry input if not currently focused by the user
          const inputEl = document.getElementById(`input-${sanitizedDev}-${cleanAttr}`);
          if (inputEl && document.activeElement !== inputEl) {
            inputEl.placeholder = (val !== null && val !== undefined) ? String(val) : '';
          }

          // Update toggle button states
          const toggleEl = document.getElementById(`toggle-${sanitizedDev}-${cleanAttr}`);
          if (toggleEl) {
            const isTrue = val === true || val === 'True' || val === 1 || val === '1';
            const trueText = toggleEl.dataset.trueText || 'ON';
            const falseText = toggleEl.dataset.falseText || 'OFF';
            if (isTrue) {
              toggleEl.classList.add('active');
              toggleEl.innerText = trueText;
            } else {
              toggleEl.classList.remove('active');
              toggleEl.innerText = falseText;
            }
          }

          // Update dropdown select if not focused
          const selectEl = document.getElementById(`select-${sanitizedDev}-${cleanAttr}`);
          if (selectEl && document.activeElement !== selectEl && val !== null && val !== undefined) {
            const strVal = String(val);
            if ([...selectEl.options].some(o => o.value === strVal)) {
              selectEl.value = strVal;
            }
          }

          // Update red percent metrics if optical data is present
          if (attr.toLowerCase().includes('red') && typeof val === 'number') {
            this.pushPlotterSample(val);
          }
        }

        // =====================================================================
        // Dynamic Badging: Connection Status and Busy State
        // =====================================================================
        
        // Enforce autonomous/manual/enabled interlock
        const autonOn = attrs.auton_flag === true || attrs.auton_flag === 'True';
        const manualOn = attrs.manual_flag === true || attrs.manual_flag === 'True';
        const sysEnabled = attrs.system_enabled === true || attrs.system_enabled === 'True';
        
        const cardBody = document.querySelector(`#card-${sanitizedDev} .card-body`);
        if (cardBody) {
          const controls = cardBody.querySelectorAll('button, input, select');
          controls.forEach(ctrl => {
            // 'Serial Reconnect'/'reconnect_serial' dropped: runtime serial
            // reconnect is purged (D-11), so no such control can be rendered.
            const isEnableBtn = ctrl.innerText.includes('Enable') || ctrl.dataset.command === 'toggle_enable';
            const isPowerDown = ctrl.innerText.includes('Power Down') || ctrl.dataset.command === 'power_down';
            const isStop = ctrl.innerText.includes('Full Stop') || ctrl.dataset.command === 'full_stop';
            const isAutonToggle = ctrl.dataset.attr === 'auton_flag' || ctrl.dataset.command === 'toggle_auton';
            const isManualToggle = ctrl.dataset.attr === 'manual_flag' || ctrl.dataset.command === 'toggle_manual';
            
            if (!sysEnabled && attrs.system_enabled !== undefined) {
              if (!isEnableBtn && !isPowerDown) {
                ctrl.disabled = true;
              } else {
                ctrl.disabled = false;
              }
            } else {
              // System is enabled or doesn't have the flag
              if (autonOn) {
                if (isStop || isPowerDown || isAutonToggle || isManualToggle) ctrl.disabled = false;
                else ctrl.disabled = true;
              } else if (manualOn) {
                if (isStop || isPowerDown || isManualToggle || isAutonToggle) ctrl.disabled = false;
                else ctrl.disabled = true;
              } else {
                // Both off, normal operation
                ctrl.disabled = false;
              }
            }
          });
        }
        
        this.updateDeviceStatusBadges(devName, attrs);
      }
    } catch (err) {
      this.setConnectionState('offline', 'Connection Dropped');
    }
  }

  updateDeviceStatusBadges(devName, attrs) {
    const rawStatus = (attrs.connection_status || '').toLowerCase();
    const isBusy = Boolean(attrs.is_busy || attrs.moving || attrs.is_moving || attrs.monitoring);

    let badgeText = 'OFFLINE';
    let badgeClass = 'badge-offline';
    let pillText = 'Offline';
    let pillClass = 'offline';
    let chipDotClass = 'chip-offline';

    if (isBusy) {
      badgeText = 'BUSY';
      badgeClass = 'badge-busy';
      pillText = 'Busy';
      pillClass = 'busy';
      chipDotClass = '';
    } else if (rawStatus === 'hardware' || rawStatus === 'online' || rawStatus === 'connected') {
      badgeText = 'HARDWARE';
      badgeClass = 'badge-hardware';
      pillText = 'Hardware';
      pillClass = 'hardware';
      chipDotClass = '';
    } else if (rawStatus === 'simulated' || rawStatus === 'sim' || rawStatus === 'mock') {
      badgeText = 'SIMULATED';
      badgeClass = 'badge-simulated';
      pillText = 'Simulated';
      pillClass = 'simulated';
      chipDotClass = '';
    } else if (rawStatus === 'disconnected' || rawStatus === 'offline') {
      badgeText = 'OFFLINE';
      badgeClass = 'badge-offline';
      pillText = 'Offline';
      pillClass = 'offline';
      chipDotClass = 'chip-offline';
    } else {
      // If active telemetry is being received and attributes are present
      badgeText = 'ONLINE';
      badgeClass = 'badge-online';
      pillText = 'Online';
      pillClass = 'online';
      chipDotClass = '';
    }

    const sanitized = this.sanitizeId(devName);

    // 1. Update Card Badge
    const cardBadge = document.getElementById(`badge-${sanitized}`);
    if (cardBadge) {
      cardBadge.innerText = badgeText;
      cardBadge.className = `badge ${badgeClass}`;
    }

    // 2. Update Sidebar Tab Status Pill
    const pill = document.querySelector(`.tab-status-pill[data-status-for="${devName}"]`);
    if (pill) {
      pill.innerText = pillText;
      pill.className = `tab-status-pill ${pillClass}`;
    }

    // 3. Update Header Summary Chip
    const chip = document.getElementById(`chip-${sanitized}`);
    if (chip) {
      chip.className = `summary-chip ${chipDotClass}`;
    }
  }

  async pollLogs() {
    try {
      const res = await fetch('/api/logs');
      if (!res.ok) return;
      const data = await res.json();
      const rawLogs = data.logs || [];

      if (rawLogs.length !== this.logs.length) {
        this.logs = rawLogs;
        if (this.dom.logCounterBadge) {
          this.dom.logCounterBadge.innerText = String(this.logs.length);
        }
        this.renderLogs();
      }
    } catch (err) {
      // Quiet poll failure
    }
  }

  async pollErrors() {
    try {
      const res = await fetch('/api/errors');
      if (!res.ok) return;
      const data = await res.json();
      const errors = data.errors || [];

      for (const err of errors) {
        this.showToast(`${err.title || 'Error'}: ${err.message}`, err.type || 'error');
      }
    } catch (err) {
      // Quiet poll failure
    }
  }

  renderLogs() {
    if (!this.dom.logConsole) return;
    this.dom.logConsole.innerHTML = '';

    const fragment = document.createDocumentFragment();
    for (const entry of this.logs) {
      const div = document.createElement('div');
      div.className = 'log-entry';
      if (entry.includes('[ERROR]') || entry.toLowerCase().includes('fail')) {
        div.classList.add('log-error');
      } else if (entry.includes('[WARN]')) {
        div.classList.add('log-warn');
      } else if (entry.startsWith('[')) {
        div.classList.add('log-device');
      }
      div.textContent = entry;
      fragment.appendChild(div);
    }

    this.dom.logConsole.appendChild(fragment);

    if (this.autoScrollLogs) {
      this.dom.logConsole.scrollTop = this.dom.logConsole.scrollHeight;
    }
  }

  exportLogsToFile() {
    const text = this.logs.join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transfer-stage-log-${new Date().toISOString().replace(/[:.]/g, '-')}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    this.showToast('Controller log exported successfully.', 'success');
  }

  // =========================================================================
  // Command & Attribute Dispatch API
  // =========================================================================
  async dispatchCommand(deviceName, commandName, args = [], triggerBtn = null) {
    if (commandName === 'plot_data_ui') {
      const modal = document.getElementById('plot-dialog-modal');
      this.toggleModal(modal, true);
      
      const btnClose = document.getElementById('btn-close-plot-dialog');
      if (btnClose) btnClose.onclick = () => this.toggleModal(modal, false);

      const btnGen = document.getElementById('btn-generate-plot');
      if (btnGen) {
        btnGen.onclick = () => {
          const fileInput = document.getElementById('plot-csv-upload');
          const typeSelect = document.getElementById('plot-type-select');
          if (!fileInput.files.length) {
            this.showToast('Please upload a CSV file', 'warning');
            return;
          }
          
          const file = fileInput.files[0];
          const reader = new FileReader();
          reader.onload = async (e) => {
            const text = e.target.result;
            try {
              const res = await fetch('/api/plot', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  csv_data: text,
                  plot_type: typeSelect.value
                })
              });
              const data = await res.json();
              if (data.image_base64) {
                document.getElementById('plot-output-img').src = 'data:image/png;base64,' + data.image_base64;
              } else {
                this.showToast('Failed to generate plot', 'error');
              }
            } catch (err) {
              this.showToast('Error generating plot', 'error');
            }
          };
          reader.readAsText(file);
        };
      }
      return;
    }

    if (commandName === 'set_focus_area_ui') {
      const modal = document.getElementById('focus-roi-modal');
      this.toggleModal(modal, true);
      this.startFocusStream();
      return;
    }

    if (triggerBtn) {
      triggerBtn.classList.add('pending');
      triggerBtn.disabled = true;
    }

    try {
      const response = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device: deviceName,
          command: commandName,
          args: args
        })
      });

      let resData = null;
      try {
        resData = await response.json();
      } catch (e) {
        resData = { status: 'error', message: `Server returned HTTP ${response.status}` };
      }
      if (!response.ok || (resData && resData.status === 'error')) {
        const errorMsg = (resData && resData.message) || `Command ${commandName} failed (HTTP ${response.status})`;
        this.showToast(`[${deviceName}] ${errorMsg}`, 'error');
        console.error(`Command error on ${deviceName}.${commandName}:`, resData);
      } else {
        this.showToast(`[${deviceName}] ${commandName} executed`, 'success');
      }
    } catch (err) {
      this.showToast(`Network error executing ${commandName}: ${err.message}`, 'error');
    } finally {
      if (triggerBtn) {
        triggerBtn.classList.remove('pending');
        triggerBtn.disabled = false;
      }
    }
  }

  async setDeviceAttribute(deviceName, attributeName, value, triggerBtn = null) {
    if (triggerBtn) {
      triggerBtn.classList.add('pending');
      triggerBtn.disabled = true;
    }

    try {
      const response = await fetch('/api/set_attr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device: deviceName,
          attr: attributeName,
          value: value
        })
      });

      let resData = null;
      try {
        resData = await response.json();
      } catch (e) {
        resData = { status: 'error', message: `Server returned HTTP ${response.status}` };
      }
      if (!response.ok || (resData && resData.status === 'error')) {
        const errorMsg = (resData && resData.message) || `HTTP ${response.status}`;
        this.showToast(`[${deviceName}] Set ${attributeName} failed: ${errorMsg}`, 'error');
      } else {
        this.showToast(`[${deviceName}] ${attributeName} set to ${resData.value}`, 'success');
      }
    } catch (err) {
      this.showToast(`Network error setting ${attributeName}: ${err.message}`, 'error');
    } finally {
      if (triggerBtn) {
        triggerBtn.classList.remove('pending');
        triggerBtn.disabled = false;
      }
    }
  }

  async triggerGlobalEmergencyStop() {
    this.showToast('GLOBAL EMERGENCY STOP BROADCASTED', 'error');

    try {
      const response = await fetch('/api/system/full_stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });

      if (!response.ok) {
        const resData = await response.json().catch(() => null);
        const errorMsg = (resData && resData.message) || `HTTP ${response.status}`;
        this.showToast(`Global stop failed: ${errorMsg}`, 'error');
        console.error('Global stop error:', resData);
      }
    } catch (err) {
      this.showToast(`Network error executing global stop: ${err.message}`, 'error');
    }
  }

  handleLogConsoleCommand() {
    if (!this.dom.inputLogCmd) return;
    const cmdText = this.dom.inputLogCmd.value.trim();
    if (!cmdText) return;

    this.logs.push(`> ${cmdText}`);
    this.renderLogs();
    this.dom.inputLogCmd.value = '';

    // Route diagnostic command to available motion systems or SMC
    const targetDev = Object.keys(this.devices)[0] || 'SMC100 Rotator';
    this.dispatchCommand(targetDev, 'send_raw_command', [cmdText]).catch(() => {});
  }

  executeScriptFile(filePath) {
    this.showToast(`Executing script file: ${filePath}`, 'info');
    // Dispatch to a controller or stage if present
    const stage = Object.keys(this.devices).find(d => d.toLowerCase().includes('stepper') || d.toLowerCase().includes('probe'));
    if (stage) {
      this.dispatchCommand(stage, 'execute_script', [filePath]);
    }
  }

  // =========================================================================
  // Real-Time Optical Red Ratio Plotter Canvas
  // =========================================================================
  pushPlotterSample(currentRedVal) {
    const now = new Date().toLocaleTimeString();
    this.plotterData.timestamps.push(now);
    this.plotterData.currentRed.push(currentRedVal);

    if (this.baselineRed === 0.0) {
      this.baselineRed = currentRedVal;
    }

    const delta = currentRedVal - this.baselineRed;
    this.plotterData.deltaRed.push(delta);

    if (currentRedVal > this.peakRed) {
      this.peakRed = currentRedVal;
    }

    if (this.plotterData.currentRed.length > this.maxPlotPoints) {
      this.plotterData.timestamps.shift();
      this.plotterData.currentRed.shift();
      this.plotterData.deltaRed.shift();
    }

    // Update readout labels
    if (this.dom.plotterCurrentRed) {
      this.dom.plotterCurrentRed.innerText = currentRedVal.toFixed(2) + '%';
    }
    if (this.dom.plotterDeltaRed) {
      this.dom.plotterDeltaRed.innerText = (delta >= 0 ? '+' : '') + delta.toFixed(2) + '%';
    }
    if (this.dom.plotterPeakRed) {
      this.dom.plotterPeakRed.innerText = this.peakRed.toFixed(2) + '%';
    }

    if (this.dom.plotterModal && !this.dom.plotterModal.classList.contains('hidden')) {
      this.renderPlotterCanvas();
    }
  }

  startFocusStream() {
    if (this.focusStreamInterval) return;
    this.fetchFocusFrame();
    this.focusStreamInterval = setInterval(() => this.fetchFocusFrame(), 500);
  }

  stopFocusStream() {
    if (this.focusStreamInterval) {
      clearInterval(this.focusStreamInterval);
      this.focusStreamInterval = null;
    }
  }

  async fetchFocusFrame() {
    try {
      const res = await fetch('/api/screenshot');
      if (!res.ok) return;
      const data = await res.json();
      if (data.status === 'success' && data.image) {
        this.hostOriginalWidth = data.original_width || 1920;
        this.hostOriginalHeight = data.original_height || 1080;
        this.hostMonitorLeft = data.monitor_left || 0;
        this.hostMonitorTop = data.monitor_top || 0;
        
        const img = new Image();
        img.onload = () => {
          this.lastFocusImage = img;
          this.renderFocusRoiCanvas();
        };
        img.src = data.image;
      }
    } catch (e) {
      console.warn("Focus stream error:", e);
    }
  }

  initFocusRoiEvents() {
    const canvas = this.dom.focusRoiCanvas || document.getElementById('focus-roi-canvas');
    if (!canvas || canvas.dataset.bound) return;
    canvas.dataset.bound = 'true';
    this.dom.focusRoiCanvas = canvas;
    this.dom.roiStatusBadge = document.getElementById('roi-status-badge');

    let isSelecting = false;
    let startX = 0;
    let startY = 0;

    canvas.addEventListener('mousedown', (e) => {
      const rect = canvas.getBoundingClientRect();
      const scaleCanvasX = canvas.width / rect.width;
      const scaleCanvasY = canvas.height / rect.height;
      startX = (e.clientX - rect.left) * scaleCanvasX;
      startY = (e.clientY - rect.top) * scaleCanvasY;
      isSelecting = true;
      this.focusRoi.active = true;
      this.focusRoi.x = startX;
      this.focusRoi.y = startY;
      this.focusRoi.w = 0;
      this.focusRoi.h = 0;
      this.renderFocusRoiCanvas();
    });

    window.addEventListener('mousemove', (e) => {
      if (!isSelecting || !this.dom.focusRoiCanvas) return;
      const rect = this.dom.focusRoiCanvas.getBoundingClientRect();
      const scaleCanvasX = this.dom.focusRoiCanvas.width / rect.width;
      const scaleCanvasY = this.dom.focusRoiCanvas.height / rect.height;
      const curX = Math.max(0, Math.min(this.dom.focusRoiCanvas.width, (e.clientX - rect.left) * scaleCanvasX));
      const curY = Math.max(0, Math.min(this.dom.focusRoiCanvas.height, (e.clientY - rect.top) * scaleCanvasY));

      this.focusRoi.x = Math.min(startX, curX);
      this.focusRoi.y = Math.min(startY, curY);
      this.focusRoi.w = Math.abs(curX - startX);
      this.focusRoi.h = Math.abs(curY - startY);
      this.renderFocusRoiCanvas();
    });

    window.addEventListener('mouseup', () => {
      if (!isSelecting) return;
      isSelecting = false;
      if (this.focusRoi.w > 5 && this.focusRoi.h > 5) {
        this.commitFocusRoi();
      } else {
        this.focusRoi.active = false;
        this.renderFocusRoiCanvas();
      }
    });
  }

  renderFocusRoiCanvas() {
    const canvas = this.dom.focusRoiCanvas || document.getElementById('focus-roi-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    // Fill background black if no image
    if (!this.lastFocusImage) {
        ctx.fillStyle = '#09090b';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#71717a';
        ctx.font = '12px var(--font-mono)';
        ctx.textAlign = 'center';
        ctx.fillText('Waiting for Optical Feed...', canvas.width/2, canvas.height/2);
        return;
    }

    // Draw the scaled snapshot
    ctx.drawImage(this.lastFocusImage, 0, 0, canvas.width, canvas.height);

    // Draw the ROI box if active
    if (this.focusRoi && this.focusRoi.active) {
        ctx.strokeStyle = '#ef4444'; // Red-500
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.strokeRect(this.focusRoi.x, this.focusRoi.y, this.focusRoi.w, this.focusRoi.h);
        
        // Darken outside ROI
        ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
        ctx.fillRect(0, 0, canvas.width, this.focusRoi.y);
        ctx.fillRect(0, this.focusRoi.y + this.focusRoi.h, canvas.width, canvas.height - (this.focusRoi.y + this.focusRoi.h));
        ctx.fillRect(0, this.focusRoi.y, this.focusRoi.x, this.focusRoi.h);
        ctx.fillRect(this.focusRoi.x + this.focusRoi.w, this.focusRoi.y, canvas.width - (this.focusRoi.x + this.focusRoi.w), this.focusRoi.h);
        ctx.setLineDash([]);
    }
  }

  commitFocusRoi() {
    const canvas = this.dom.focusRoiCanvas || document.getElementById('focus-roi-canvas');
    if (!canvas) return;
    
    // Calculate scaling factors
    const scaleX = (this.hostOriginalWidth || 1920) / canvas.width;
    const scaleY = (this.hostOriginalHeight || 1080) / canvas.height;
    
    const hostX = Math.round(this.focusRoi.x * scaleX) + (this.hostMonitorLeft || 0);
    const hostY = Math.round(this.focusRoi.y * scaleY) + (this.hostMonitorTop || 0);
    const hostW = Math.round(this.focusRoi.w * scaleX);
    const hostH = Math.round(this.focusRoi.h * scaleY);
    
    if (this.dom.roiStatusBadge) {
        this.dom.roiStatusBadge.innerText = `ROI: ${hostW}x${hostH} px`;
        this.dom.roiStatusBadge.classList.add('badge-instrument');
        this.dom.roiStatusBadge.classList.remove('badge-subtle');
    }
    
    this.showToast(`Boundaries locked at [${hostX}, ${hostY}] (${hostW}x${hostH})`, 'success');
    
    // Dispatch to Python backend!
    this.dispatchCommand('Red Percent Window', 'set_focus_area', [hostX, hostY, hostW, hostH]);
  }

  renderPlotterCanvas() {
    const canvas = this.dom.redCanvas;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    ctx.clearRect(0, 0, width, height);

    // Grid lines
    ctx.strokeStyle = '#27272a';
    ctx.lineWidth = 1;
    for (let y = 20; y < height; y += 40) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    for (let x = 40; x < width; x += 60) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }

    const data = this.plotterData.currentRed;
    if (data.length < 2) return;

    // Draw Current Red Curve (Red)
    const minVal = Math.min(...data, 0);
    const maxVal = Math.max(...data, 100);
    const range = maxVal - minVal || 1;

    ctx.beginPath();
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 2;

    for (let i = 0; i < data.length; i++) {
      const x = (i / (this.maxPlotPoints - 1)) * width;
      const y = height - ((data[i] - minVal) / range) * (height - 30) - 15;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Draw Delta Curve (Blue)
    const deltaData = this.plotterData.deltaRed;
    if (deltaData.length >= 2) {
      ctx.beginPath();
      ctx.strokeStyle = '#3b82f6';
      ctx.lineWidth = 1.5;
      const deltaMin = Math.min(...deltaData, -10);
      const deltaMax = Math.max(...deltaData, 10);
      const deltaRange = deltaMax - deltaMin || 1;

      for (let i = 0; i < deltaData.length; i++) {
        const x = (i / (this.maxPlotPoints - 1)) * width;
        const y = height - ((deltaData[i] - deltaMin) / deltaRange) * (height - 30) - 15;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }

  // =========================================================================
  // Toast System
  // =========================================================================
  showToast(message, type = 'info') {
    if (!this.dom.toastContainer) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'alert');

    toast.innerHTML = `
      <div class="toast-content">
        <div class="toast-message">${message}</div>
      </div>
      <button class="toast-close" aria-label="Dismiss">&times;</button>
    `;

    const closeBtn = toast.querySelector('.toast-close');
    closeBtn.addEventListener('click', () => {
      toast.classList.add('toast-exit');
      setTimeout(() => toast.remove(), 200);
    });

    this.dom.toastContainer.appendChild(toast);

    setTimeout(() => {
      if (toast.parentElement) {
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 200);
      }
    }, 4500);
  }

  // =========================================================================
  // Hardware Setup Wizard Controller Logic
  // =========================================================================
  openSetupWizard() {
    this.toggleModal(this.dom.setupModal, true);
    this.renderSetupDeviceRows();
  }

  async scanHardware() {
    if (this.dom.setupScanSpinner) this.dom.setupScanSpinner.classList.remove('hidden');
    if (this.dom.setupScanBtnText) this.dom.setupScanBtnText.innerText = 'Scanning...';
    if (this.dom.btnSetupScan) this.dom.btnSetupScan.disabled = true;
    if (this.dom.setupScanIndicator) {
      this.dom.setupScanIndicator.innerHTML = '<div style="width: 100%; background: #333; height: 10px; border-radius: 5px; margin-top: 5px; overflow: hidden;"><div id="scan-progress-bar" style="width: 0%; height: 100%; background: var(--primary); transition: width 0.2s;"></div></div><div style="font-size: 11px; margin-top: 4px;">Scanning Interfaces...</div>';
      this.dom.setupScanIndicator.className = 'scan-indicator-badge scanning';
    }
    
    let progress = 0;
    const progressBarInterval = setInterval(() => {
        progress += Math.random() * 15;
        if (progress > 90) progress = 90;
        const bar = document.getElementById('scan-progress-bar');
        if (bar) bar.style.width = `${progress}%`;
    }, 100);

    let progressDots = 0;
    const progressInterval = setInterval(() => {
      progressDots = (progressDots + 1) % 4;
      const dots = '.'.repeat(progressDots);
      if (this.dom.setupScanBtnText) this.dom.setupScanBtnText.innerText = `Scanning${dots}`;
    }, 500);

    try {
      const res = await fetch('/api/setup/scan');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.scannedPorts = Array.isArray(data.ports) ? data.ports : ['SIM'];
      this.scannedControllers = Array.isArray(data.controllers) ? data.controllers : ['None'];

      // Ensure 'SIM' is always available as the first option
      if (!this.scannedPorts.includes('SIM')) {
        this.scannedPorts.unshift('SIM');
      }
      if (!this.scannedControllers.includes('None')) {
        this.scannedControllers.unshift('None');
      }

      const portCount = this.scannedPorts.filter(p => p !== 'SIM').length;
      const ctrlCount = this.scannedControllers.filter(c => c !== 'None' && c !== 'Virtual Controller').length;

      if (this.dom.setupScanIndicator) {
        this.dom.setupScanIndicator.innerText = 'Scan Complete';
        this.dom.setupScanIndicator.className = 'scan-indicator-badge scanned';
      }
      if (this.dom.setupScanMeta) {
        this.dom.setupScanMeta.innerText = `Found ${portCount} COM port options & ${ctrlCount} joystick controller(s).`;
      }

      this.renderSetupDeviceRows();
      this.showToast('Hardware interfaces scanned successfully.', 'info');
    } catch (err) {
      console.error('[SetupWizard] scanHardware error:', err);
      if (this.dom.setupScanIndicator) {
        this.dom.setupScanIndicator.innerText = 'Scan Error';
        this.dom.setupScanIndicator.className = 'scan-indicator-badge';
      }
      if (this.dom.setupScanMeta) {
        this.dom.setupScanMeta.innerText = `Scan failed: ${err.message}. Using default mock/simulation interfaces.`;
      }
      this.showToast(`Hardware scan error: ${err.message}`, 'error');
    } finally {
      clearInterval(progressInterval); clearInterval(progressBarInterval);
      if (this.dom.setupScanSpinner) this.dom.setupScanSpinner.classList.add('hidden');
      if (this.dom.setupScanBtnText) this.dom.setupScanBtnText.innerText = 'Rescan Hardware';
      if (this.dom.btnSetupScan) this.dom.btnSetupScan.disabled = false;
    }
  }

  renderSetupDeviceRows() {
    if (!this.dom.setupDeviceRows) return;
    this.dom.setupDeviceRows.innerHTML = '';

    for (let i = 0; i < this.setupDeviceConfigs.length; i++) {
      const dev = this.setupDeviceConfigs[i];
      const sanitized = this.sanitizeId(dev.id);

      const tr = document.createElement('tr');
      tr.id = `setup-row-${sanitized}`;
      if (!dev.enabled) tr.classList.add('row-disabled');

      // 1. Enabled Checkbox
      const tdCheck = document.createElement('td');
      tdCheck.style.textAlign = 'center';
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.id = `setup-chk-${sanitized}`;
      chk.checked = dev.enabled;
      chk.addEventListener('change', (e) => {
        dev.enabled = e.target.checked;
        tr.classList.toggle('row-disabled', !dev.enabled);
        const selPort = tr.querySelector('.setup-port-select');
        const selCtrl = tr.querySelector('.setup-ctrl-select');
        if (selPort) selPort.disabled = !dev.enabled;
        if (selCtrl) selCtrl.disabled = !dev.enabled;
        this.updateSetupRowPill(tr, dev);
      });
      tdCheck.appendChild(chk);
      tr.appendChild(tdCheck);

      // 2. Device Name & Description
      const tdDev = document.createElement('td');
      tdDev.innerHTML = `
        <div class="setup-dev-name-col">
          <div>
            <div class="setup-dev-title">${dev.name}</div>
            <div class="setup-dev-desc">${dev.desc}</div>
          </div>
        </div>
      `;
      tr.appendChild(tdDev);

      // 3. COM Port Select Dropdown
      const tdPort = document.createElement('td');
      if (dev.id === 'Red Percent Window' || dev.defaultPort === 'Headless') {
          tdPort.innerHTML = '<span style="color:var(--text-dim);font-size:11px;font-style:italic;">No Interface Required</span>';
          dev.defaultPort = 'Headless';
      } else {
          const portSelect = document.createElement('select');
          portSelect.id = `setup-port-${sanitized}`;
          portSelect.className = 'setup-select setup-port-select';
          portSelect.disabled = !dev.enabled;

          // Populate port options
          const currentPort = dev.defaultPort || 'SIM';
          for (const p of this.scannedPorts) {
            const opt = document.createElement('option');
            opt.value = p;
            opt.text = p === 'SIM' ? 'SIM (Simulation / Mock)' : p;
            if (p === currentPort) opt.selected = true;
            portSelect.appendChild(opt);
          }
          portSelect.addEventListener('change', (e) => {
            dev.defaultPort = e.target.value;
            this.updateSetupRowPill(tr, dev);
          });
          tdPort.appendChild(portSelect);
      }
      tr.appendChild(tdPort);

      // 4. Controller / Joystick Dropdown (or N/A)
      const tdCtrl = document.createElement('td');
      if (dev.hasController) {
        const ctrlSelect = document.createElement('select');
        ctrlSelect.id = `setup-ctrl-${sanitized}`;
        ctrlSelect.className = 'setup-select setup-ctrl-select';
        ctrlSelect.disabled = !dev.enabled;

        const currentCtrl = dev.defaultCtrl || 'None';
        for (const c of this.scannedControllers) {
          const opt = document.createElement('option');
          opt.value = c;
          opt.text = c;
          if (c === currentCtrl) opt.selected = true;
          ctrlSelect.appendChild(opt);
        }
        ctrlSelect.addEventListener('change', (e) => {
          dev.defaultCtrl = e.target.value;
        });
        tdCtrl.appendChild(ctrlSelect);
      } else {
        tdCtrl.innerHTML = `<span style="color: var(--text-dim); font-size: 11px;">N/A (Serial/Optical only)</span>`;
      }
      tr.appendChild(tdCtrl);

      // 5. Dynamic Mode Status Pill
      const tdStatus = document.createElement('td');
      tdStatus.className = 'setup-status-cell';
      tr.appendChild(tdStatus);
      this.updateSetupRowPill(tr, dev);

      this.dom.setupDeviceRows.appendChild(tr);
    }
  }

  updateSetupRowPill(tr, dev) {
    const statusCell = tr.querySelector('.setup-status-cell');
    if (!statusCell) return;

    if (!dev.enabled) {
      statusCell.innerHTML = `<span class="setup-pill-off">Disabled</span>`;
    } else if (dev.defaultPort === 'SIM') {
      statusCell.innerHTML = `<span class="setup-pill-sim">Simulated</span>`;
    } else {
      statusCell.innerHTML = `<span class="setup-pill-hw">Hardware</span>`;
    }
  }

  setAllSetupToSim() {
    for (const dev of this.setupDeviceConfigs) {
      dev.defaultPort = 'SIM';
    }
    this.renderSetupDeviceRows();
    this.showToast('All devices set to Simulation / Mock mode.', 'info');
  }

  setAllSetupToDisabled() {
    const btn = this.dom.btnSetupDisableAll;
    const currentlyDisabling = btn.innerText.trim() === 'Disable All';
    
    for (const dev of this.setupDeviceConfigs) {
      dev.enabled = !currentlyDisabling;
    }
    
    if (currentlyDisabling) {
      btn.innerText = 'Enable All';
      this.showToast('All devices disabled.', 'info');
    } else {
      btn.innerText = 'Disable All';
      this.showToast('All devices enabled.', 'info');
    }
    
    this.renderSetupDeviceRows();
  }

  async initializeHardwareSetup() {
    // Collect active device configurations
    const configs = [];
    const usedControllers = new Set();

    for (const dev of this.setupDeviceConfigs) {
      const port = dev.defaultPort || 'SIM';
      const ctrl = dev.hasController ? (dev.defaultCtrl || 'None') : 'None';

      // Check controller collision (excluding 'None')
      if (dev.enabled && ctrl !== 'None' && usedControllers.has(ctrl)) {
        this.showSetupAlert(`Controller Conflict: '${ctrl}' is assigned to multiple devices. Each axis joystick must be unique.`, 'error');
        return;
      }
      if (dev.enabled && ctrl !== 'None') {
        usedControllers.add(ctrl);
      }

      configs.push({
        device: dev.id,
        port: port,
        controller: ctrl,
        enabled: dev.enabled
      });
    }

    if (configs.filter(c => c.enabled).length === 0) {
      this.showSetupAlert('Please enable at least one device before launching the stage.', 'error');
      return;
    }

    this.showSetupAlert('Connecting and initializing hardware models...', 'success');
    if (this.dom.btnSetupLaunch) this.dom.btnSetupLaunch.disabled = true;

    try {
      const res = await fetch('/api/setup/initialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ configs: configs })
      });

      const result = await res.json();
      if (!res.ok || result.status !== 'ok') {
        throw new Error(result.message || `Initialization failed (HTTP ${res.status})`);
      }

      this.systemStatus = 'running';
      this.toggleModal(this.dom.setupModal, false);
      this.showToast(`Stage launched with ${result.initialized_devices ? result.initialized_devices.length : configs.length} active device(s)!`, 'success');

      // Reload active devices and schemas into dashboard
      await this.fetchDevices();
      await this.pollState();
    } catch (err) {
      console.error('[SetupWizard] initializeHardwareSetup error:', err);
      this.showSetupAlert(`Initialization Error: ${err.message}`, 'error');
      this.showToast(`Launch failed: ${err.message}`, 'error');
    } finally {
      if (this.dom.btnSetupLaunch) this.dom.btnSetupLaunch.disabled = false;
    }
  }

  showSetupAlert(msg, type = 'error') {
    if (!this.dom.setupAlertBox) return;
    this.dom.setupAlertBox.className = `setup-alert ${type}`;
    this.dom.setupAlertBox.innerText = msg;
    this.dom.setupAlertBox.classList.remove('hidden');
  }

  sanitizeId(str) {
    return String(str).replace(/[^a-zA-Z0-9_-]/g, '_');
  }

  escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}

// Global initialization
window.addEventListener('DOMContentLoaded', () => {
  window.app = new TransferStageApp();
  window.app.init();
});
