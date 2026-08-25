import threading
import time
try:
    from PIL import Image
    import mss
    import numpy as np
except ImportError:
    Image = None
    mss = None
    np = None
import typing
import csv

class RedPercentDataLog:
    def __init__(self, sync_dimensions=None):
        self.sync_dimensions = sync_dimensions or []
        self.red_values = []
        self.loc_values = {dim: [] for dim in self.sync_dimensions}
        self.lock = threading.Lock()
        
    def add_entry(self, red_pct, locs=None):
        with self.lock:
            self.red_values.append(red_pct)
            locs = locs or {}
            for dim in self.sync_dimensions:
                self.loc_values[dim].append(locs.get(dim, 0.0))
            
    def save_to_csv(self, filepath):
        with self.lock:
            with open(filepath, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                headers = ["Red Percent"]
                for dim in self.sync_dimensions:
                    headers.append(f"Stepper {dim} Location")
                writer.writerow(headers)
                
                for i in range(len(self.red_values)):
                    row = [self.red_values[i]]
                    for dim in self.sync_dimensions:
                        row.append(self.loc_values[dim][i])
                    writer.writerow(row)

class RedPercentSystem:
    def __init__(self):
        self.red_percent = 0.0
        self.is_monitoring = False
        self.stop_event = threading.Event()
        self.thread = None
        self.monitor_thread = None
        
        self.baseline = None
        self.data_log = []
        
        self.stepper_model = None
        
        self.custom_view_class = RedPercentView
        self.sync_dimensions = []
        self.data_log = None
        self.monitoring = False
        self.focus_area: typing.Optional[dict] = None
        self.baseline_red = 0.0
        self.current_red = 0.0
        self.red_change = 0.0
        self._monitor_thread = None

    def capture_focus_area(self, sct):
        if not self.focus_area:
            return None
        try:
            screenshot = sct.grab(self.focus_area)
            if Image is not None and np is not None:
                img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
                return np.array(img)
            return None
        except Exception as e:
            print(f"Error capturing screen: {e}")
            return None

    def detect_red(self, image):
        if image is None:
            return 0.0
        r = image[:, :, 0]
        g = image[:, :, 1]
        b = image[:, :, 2]
        red_mask = (r > 150) & (g < 100) & (b < 100)
        total_pixels = image.shape[0] * image.shape[1]
        if total_pixels == 0:
            return 0.0
        red_pixels = np.sum(red_mask)
        return (red_pixels / total_pixels) * 100

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Red Detection",
                    "elements": [
                        {"type": "readonly", "text": "Current Red %:", "model_attr": "current_red"},
                        {"type": "readonly", "text": "Red Change %:", "model_attr": "red_change"}
                    ]
                },
                {
                    "title": "System Control",
                    "elements": [
                        {"type": "button", "text": "Start Monitoring", "command": "start_monitoring", "bg": "darkgreen", "fg": "white"},
                        {"type": "button", "text": "Stop Monitoring", "command": "stop_monitoring", "bg": "darkred", "fg": "white"},
                        {"type": "button", "text": "Reset Baseline", "command": "reset_baseline", "bg": "gray", "fg": "white"},
                        {"type": "button", "text": "Save Log", "command": "save_log", "bg": "blue", "fg": "white"}
                    ]
                }
            ]
        }

    def save_log(self):
        if not self.data_log or not self.data_log.red_values:
            print("[color_test] No data to save.")
            return

        from tkinter import filedialog
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            title="Save Red Detection Log"
        )

        if file_path:
            try:
                self.data_log.save_to_csv(file_path)
                print(f"[color_test] Log saved to: {file_path}")
            except Exception as e:
                print(f"[color_test] Error saving file: {e}")
        else:
            print("[color_test] Save cancelled.")

    def start_monitoring(self):
        if self.monitoring:
            return
        self.monitoring = True
        print("=== MONITORING STARTED ===")
        if not self.data_log:
            self.data_log = RedPercentDataLog(self.sync_dimensions)
        self._monitor_thread = threading.Thread(target=self._monitor_colors)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def stop_monitoring(self):
        print("=== MONITORING STOPPED ===")
        self.monitoring = False

    def reset_baseline(self):
        self.baseline_red = self.current_red
        print(f"BASELINE RESET - Red: {self.baseline_red:.1f}%")

    def _monitor_colors(self):
        first_reading = True
        
        with mss.mss() as sct:
            while self.monitoring:
                image = self.capture_focus_area(sct)
                if image is not None:
                    red_pct = self.detect_red(image)
                    if first_reading:
                        self.baseline_red = red_pct
                        print(f"BASELINE SET - Red: {self.baseline_red:.1f}%")
                        first_reading = False
                    
                    self.current_red = red_pct
                    self.red_change = ((red_pct - self.baseline_red) / max(self.baseline_red, 0.1)) * 100
                    
                    if self.data_log:
                        rounded_red = round(red_pct, 1)
                        if not hasattr(self, 'last_logged_red'):
                            self.last_logged_red = -1000.0
                            
                        if abs(rounded_red - self.last_logged_red) >= 0.1:
                            print(f"RED: {rounded_red:.1f}%")
                            self.last_logged_red = rounded_red
                            locs = {}
                            if self.stepper_model:
                                if 'X' in self.sync_dimensions:
                                    try: locs['X'] = float(self.stepper_model.pos_x)
                                    except: locs['X'] = 0.0
                                if 'Y' in self.sync_dimensions:
                                    try: locs['Y'] = float(self.stepper_model.pos_y)
                                    except: locs['Y'] = 0.0
                                if 'Z' in self.sync_dimensions:
                                    try: locs['Z'] = float(self.stepper_model.pos_z)
                                    except: locs['Z'] = 0.0
                                    
                            self.data_log.add_entry(rounded_red, locs)
                        
                time.sleep(0.016) # ~60 FPS continuous logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import LinearSegmentedColormap

class RedPercentView(tk.Frame):
    def __init__(self, master=None, system=None):
        super().__init__(master)
        self.system = system or RedPercentSystem()


        # GUI Setup
        control_frame = ttk.Frame(self)
        control_frame.pack(pady=10)

        self.select_btn = ttk.Button(control_frame, text="Select Focus Area", command=self.select_focus_area)
        self.select_btn.pack(side=tk.LEFT, padx=5)

        self.start_btn = ttk.Button(control_frame, text="Start Monitoring", state=tk.DISABLED, command=self.start_monitoring)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(control_frame, text="Stop Monitoring", state=tk.DISABLED, command=self.stop_monitoring)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.plot_btn = ttk.Button(control_frame, text="Plot CSV", command=self.open_plot_window)
        self.plot_btn.pack(side=tk.LEFT, padx=5)

        status_frame = ttk.Frame(self)
        status_frame.pack(pady=10)

        ttk.Label(status_frame, text="Focus Area:").grid(row=0, column=0, sticky=tk.W)
        self.area_label = ttk.Label(status_frame, text="Not selected")
        self.area_label.grid(row=0, column=1, sticky=tk.W)

        color_frame = ttk.LabelFrame(self, text="Red Detection")
        color_frame.pack(pady=10, padx=10, fill=tk.X)

        ttk.Label(color_frame, text="Red %:").grid(row=0, column=0, sticky=tk.W)
        self.red_label = ttk.Label(color_frame, text="0.0%")
        self.red_label.grid(row=0, column=1, sticky=tk.W)

        ttk.Label(color_frame, text="Red Change:").grid(row=1, column=0, sticky=tk.W)
        self.red_change_label = tk.Label(color_frame, text="0.0%", fg="black")
        self.red_change_label.grid(row=1, column=1, sticky=tk.W)

        self.reset_btn = ttk.Button(color_frame, text="Reset Baseline", state=tk.DISABLED, command=self.reset_baseline)
        self.reset_btn.grid(row=2, column=0, columnspan=2, pady=5)
        
        self.sync_vars = {
            'X': tk.BooleanVar(value=False),
            'Y': tk.BooleanVar(value=False),
            'Z': tk.BooleanVar(value=False)
        }
        
        sync_frame = ttk.Frame(color_frame)
        sync_frame.grid(row=3, column=0, columnspan=2, pady=5, sticky=tk.W)
        ttk.Label(sync_frame, text="Sync Dimensions:").pack(side=tk.LEFT)
        for dim in ['X', 'Y', 'Z']:
            chk = ttk.Checkbutton(sync_frame, text=dim, variable=self.sync_vars[dim], command=self._update_sync_dimensions)
            chk.pack(side=tk.LEFT, padx=2)

        self.poll_display()

    def _update_sync_dimensions(self):
        self.system.sync_dimensions = [dim for dim in ['X', 'Y', 'Z'] if self.sync_vars[dim].get()]

    def select_focus_area(self):
        # Create a borderless, transparent, fullscreen overlay window
        selection_window = tk.Toplevel(self.winfo_toplevel())
        
        screen_width = selection_window.winfo_screenwidth()
        screen_height = selection_window.winfo_screenheight()
        
        selection_window.geometry(f"{screen_width}x{screen_height}+0+0")
        selection_window.attributes('-alpha', 0.3)
        selection_window.configure(bg='gray10')
        selection_window.attributes('-topmost', True)
        
        try:
            selection_window.overrideredirect(True)
        except Exception:
            pass

        self.start_x = None
        self.start_y = None
        self.rect_id = None
        self.dragging = False

        canvas = tk.Canvas(selection_window, highlightthickness=0,
                           width=screen_width, height=screen_height, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)

        def start_selection(event):
            self.start_x = event.x
            self.start_y = event.y
            self.dragging = True
            if self.rect_id:
                canvas.delete(self.rect_id)

        def update_selection(event):
            if self.dragging:
                if self.rect_id:
                    canvas.delete(self.rect_id)
                self.rect_id = canvas.create_rectangle(
                    self.start_x, self.start_y, event.x, event.y,
                    outline='red', width=3
                )

        def end_selection(event):
            if self.dragging:
                self.dragging = False
                end_x = event.x
                end_y = event.y

                left = min(self.start_x, end_x)
                top = min(self.start_y, end_y)
                width = abs(end_x - self.start_x)
                height = abs(end_y - self.start_y)

                if width > 10 and height > 10:
                    # Explicitly cast to int to prevent mss from failing with float coordinates
                    self.system.focus_area = {
                        'left': int(left),
                        'top': int(top),
                        'width': int(width),
                        'height': int(height)
                    }

                    selection_window.destroy()

                    self.area_label.config(text=f"{int(width)}x{int(height)} at ({int(left)},{int(top)})")
                    self.start_btn.config(state=tk.NORMAL)

        def cancel_selection(event):
            selection_window.destroy()

        canvas.bind('<Button-1>', start_selection)
        canvas.bind('<B1-Motion>', update_selection)
        canvas.bind('<ButtonRelease-1>', end_selection)
        canvas.bind('<Escape>', cancel_selection)
        selection_window.bind('<Escape>', cancel_selection)

        instruction = tk.Label(selection_window,
                               text="Click and drag to select focus area. Press ESC to cancel.",
                               fg='red', bg='black', font=('Arial', 24, 'bold'))
        instruction.place(relx=0.5, rely=0.05, anchor=tk.CENTER)

        canvas.focus_set()

    def start_monitoring(self):
        self.system.start_monitoring()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.reset_btn.config(state=tk.NORMAL)

    def stop_monitoring(self):
        self.system.stop_monitoring()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        if self.system.data_log and self.system.data_log.red_values:
            if messagebox.askyesno("Save Log", "Monitoring stopped. Would you like to save the data to a CSV?"):
                self.save_log_to_file()

    def reset_baseline(self):
        self.system.reset_baseline()

    def save_log_to_file(self):
        if not self.system.data_log or not self.system.data_log.red_values:
            print("[color_test] No data to save.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            title="Save Red Detection Log"
        )

        if file_path:
            try:
                self.system.data_log.save_to_csv(file_path)
                print(f"[color_test] Log saved to: {file_path}")
            except Exception as e:
                print(f"[color_test] Error saving file: {e}")
        else:
            print("[color_test] Save cancelled.")

    def poll_display(self):
        if not self.winfo_exists():
            return
        
        red_pct = self.system.current_red
        red_change = self.system.red_change
        
        self.red_label.config(text=f"{red_pct:.1f}%")
        color = "green" if red_change > 0 else "red" if red_change < 0 else "black"
        self.red_change_label.config(text=f"{red_change:+.1f}%", fg=color)
        
        self.after(100, self.poll_display)

    def open_plot_window(self):
        plot_win = tk.Toplevel(self)
        plot_win.title("Plot CSV Data")
        plot_win.geometry("800x600")

        top_frame = ttk.Frame(plot_win)
        top_frame.pack(side=tk.TOP, fill=tk.X, pady=10)

        plot_frame = ttk.Frame(plot_win)
        plot_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)

        def load_csv():
            filepath = filedialog.askopenfilename(
                title="Select CSV",
                filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
                parent=plot_win
            )
            if not filepath:
                return
            
            red_percents = []
            dim_data = {}
            
            try:
                with open(filepath, 'r', newline='') as f:
                    reader = csv.reader(f)
                    try:
                        headers = next(reader)
                    except StopIteration:
                        messagebox.showerror("Error", "CSV file is empty.", parent=plot_win)
                        return
                    
                    red_idx = 0
                    dim_indices = {}
                    
                    for i, h in enumerate(headers):
                        h_lower = h.strip().lower()
                        if "red" in h_lower:
                            red_idx = i
                        elif "x" in h_lower:
                            dim_indices['X'] = i
                        elif "y" in h_lower:
                            dim_indices['Y'] = i
                        elif "z" in h_lower:
                            dim_indices['Z'] = i
                        # Fallback for older formats where 'stepper x' or just 'stepper' was used
                        elif "stepper" in h_lower or "location" in h_lower:
                            if 'X' not in dim_indices:
                                dim_indices['X'] = i
                            
                    for row in reader:
                        if not row:
                            continue
                        try:
                            r_val = float(row[red_idx])
                            red_percents.append(r_val)
                            for dim, idx in dim_indices.items():
                                if len(row) > idx:
                                    if dim not in dim_data:
                                        dim_data[dim] = []
                                    dim_data[dim].append(float(row[idx]))
                        except ValueError:
                            continue
                            
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load CSV:\n{e}", parent=plot_win)
                return
                
            dims_found = list(dim_data.keys())
            
            if len(dims_found) > 0:
                dialog = tk.Toplevel(plot_win)
                dialog.title("Select Plot Type")
                dialog.transient(plot_win)
                dialog.grab_set()
                
                ttk.Label(dialog, text="Select the type of plot:").pack(pady=10)
                
                plot_type_var = tk.StringVar()
                if len(dims_found) >= 3:
                    plot_type_var.set("3D")
                elif len(dims_found) >= 2:
                    plot_type_var.set("2D")
                else:
                    plot_type_var.set("1D")
                    
                dim1_var = tk.StringVar(value=dims_found[0])
                dim2_var = tk.StringVar(value=dims_found[1] if len(dims_found) > 1 else dims_found[0])
                dim3_var = tk.StringVar(value=dims_found[2] if len(dims_found) > 2 else dims_found[0])
                
                rb_frame = ttk.Frame(dialog)
                rb_frame.pack(anchor=tk.W, padx=20)
                
                ttk.Radiobutton(rb_frame, text="0D (Time/Index)", variable=plot_type_var, value="0D").pack(anchor=tk.W, pady=2)
                ttk.Radiobutton(rb_frame, text="1D (Single Dimension)", variable=plot_type_var, value="1D").pack(anchor=tk.W, pady=2)
                if len(dims_found) >= 2:
                    ttk.Radiobutton(rb_frame, text="2D (Two Dimensions)", variable=plot_type_var, value="2D").pack(anchor=tk.W, pady=2)
                if len(dims_found) >= 3:
                    ttk.Radiobutton(rb_frame, text="3D (Three Dimensions)", variable=plot_type_var, value="3D").pack(anchor=tk.W, pady=2)
                    
                opt_frame = ttk.Frame(dialog)
                opt_frame.pack(pady=15)
                
                ttk.Label(opt_frame, text="Dim 1 (1D/2D/3D):").grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
                ttk.OptionMenu(opt_frame, dim1_var, dim1_var.get(), *dims_found).grid(row=0, column=1, sticky=tk.W, pady=2)
                
                if len(dims_found) >= 2:
                    ttk.Label(opt_frame, text="Dim 2 (2D/3D):").grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
                    ttk.OptionMenu(opt_frame, dim2_var, dim2_var.get(), *dims_found).grid(row=1, column=1, sticky=tk.W, pady=2)
                    
                if len(dims_found) >= 3:
                    ttk.Label(opt_frame, text="Dim 3 (3D):").grid(row=2, column=0, sticky=tk.E, padx=5, pady=2)
                    ttk.OptionMenu(opt_frame, dim3_var, dim3_var.get(), *dims_found).grid(row=2, column=1, sticky=tk.W, pady=2)
                    
                result = {}
                def on_ok():
                    result['type'] = plot_type_var.get()
                    result['dim1'] = dim1_var.get()
                    result['dim2'] = dim2_var.get()
                    result['dim3'] = dim3_var.get()
                    dialog.destroy()
                    
                ttk.Button(dialog, text="Plot Data", command=on_ok).pack(pady=10)
                
                # Center dialog
                plot_win.update_idletasks()
                x = plot_win.winfo_x() + (plot_win.winfo_width() // 2) - 150
                y = plot_win.winfo_y() + (plot_win.winfo_height() // 2) - 150
                dialog.geometry(f"+{x}+{y}")
                
                plot_win.wait_window(dialog)
                
                if 'type' not in result:
                    return # Dialog closed without plotting
                
                selected_plot_type = result['type']
                dim1_sel = result['dim1']
                dim2_sel = result['dim2']
                dim3_sel = result['dim3']
            else:
                selected_plot_type = "0D"
                dim1_sel = None
                dim2_sel = None
                dim3_sel = None

            for widget in plot_frame.winfo_children():
                widget.destroy()
                
            fig = Figure(figsize=(8, 6), dpi=100)
            
            if selected_plot_type == "0D":
                ax = fig.add_subplot(111)
                ax.plot(red_percents, marker='o', linestyle='-', color='b')
                ax.set_xlabel('Index (Time / Samples)')
                ax.set_ylabel('Red Percent')
                ax.set_title('Red Percent Data')
                ax.grid(True)
            elif selected_plot_type == "1D":
                ax = fig.add_subplot(111)
                if dim_data[dim1_sel] and len(dim_data[dim1_sel]) == len(red_percents):
                    paired = sorted(zip(dim_data[dim1_sel], red_percents))
                    sorted_xs = [p[0] for p in paired]
                    sorted_rs = [p[1] for p in paired]
                    ax.plot(sorted_xs, sorted_rs, marker='o', linestyle='-', color='b')
                    ax.set_xlabel(f'Stepper {dim1_sel} Location')
                else:
                    ax.plot(red_percents, marker='o', linestyle='-', color='b')
                    ax.set_xlabel('Index')
                ax.set_ylabel('Red Percent')
                ax.set_title(f'Red Percent vs {dim1_sel}')
                ax.grid(True)
            elif selected_plot_type == "2D":
                ax = fig.add_subplot(111, projection='3d')
                x = dim_data[dim1_sel]
                y = dim_data[dim2_sel]
                z = red_percents
                
                if len(x) == len(z) and len(y) == len(z):
                    scatter = ax.scatter(x, y, z, c=z, cmap='coolwarm', marker='o')
                    ax.set_xlabel(f'Stepper {dim1_sel} Location')
                    ax.set_ylabel(f'Stepper {dim2_sel} Location')
                    ax.set_zlabel('Red Percent')
                    ax.set_title(f'Red Percent vs {dim1_sel} and {dim2_sel}')
                    fig.colorbar(scatter, ax=ax, label='Red Percent')
                else:
                    ax.text2D(0.5, 0.5, "Data mismatch error", transform=ax.transAxes)
            elif selected_plot_type == "3D":
                ax = fig.add_subplot(111, projection='3d')
                x = dim_data[dim1_sel]
                y = dim_data[dim2_sel]
                z = dim_data[dim3_sel]
                c = red_percents
                
                if len(x) == len(c) and len(y) == len(c) and len(z) == len(c):
                    scatter = ax.scatter(x, y, z, c=c, cmap='coolwarm', marker='o')
                    ax.set_xlabel(f'Stepper {dim1_sel} Location')
                    ax.set_ylabel(f'Stepper {dim2_sel} Location')
                    ax.set_zlabel(f'Stepper {dim3_sel} Location')
                    ax.set_title(f'Red Percent over {dim1_sel}, {dim2_sel}, {dim3_sel}')
                    fig.colorbar(scatter, ax=ax, label='Red Percent')
                else:
                    ax.text2D(0.5, 0.5, "Data mismatch error", transform=ax.transAxes)
            
            canvas = FigureCanvasTkAgg(fig, master=plot_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        load_btn = ttk.Button(top_frame, text="Select & Load CSV File", command=load_csv)
        load_btn.pack(side=tk.LEFT, padx=10)

    def destroy(self):
        print("[color_test] Cleaning up and closing RedPercentView...")
        self.system.stop_monitoring()
        super().destroy()
        print("[color_test] Cleanup complete.")
