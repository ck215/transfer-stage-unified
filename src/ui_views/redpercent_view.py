import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from domain_models.redpercent_system import RedPercentSystem

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
                if len(dims_found) >= 2:
                    plot_type_var.set("2D")
                else:
                    plot_type_var.set("1D")
                    
                dim1_var = tk.StringVar(value=dims_found[0])
                dim2_var = tk.StringVar(value=dims_found[1] if len(dims_found) > 1 else dims_found[0])
                
                rb_frame = ttk.Frame(dialog)
                rb_frame.pack(anchor=tk.W, padx=20)
                
                ttk.Radiobutton(rb_frame, text="0D (Time/Index)", variable=plot_type_var, value="0D").pack(anchor=tk.W, pady=2)
                ttk.Radiobutton(rb_frame, text="1D (Single Dimension)", variable=plot_type_var, value="1D").pack(anchor=tk.W, pady=2)
                if len(dims_found) >= 2:
                    ttk.Radiobutton(rb_frame, text="2D (Two Dimensions)", variable=plot_type_var, value="2D").pack(anchor=tk.W, pady=2)
                    
                opt_frame = ttk.Frame(dialog)
                opt_frame.pack(pady=15)
                
                ttk.Label(opt_frame, text="Dim 1 (1D/2D):").grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
                ttk.OptionMenu(opt_frame, dim1_var, dim1_var.get(), *dims_found).grid(row=0, column=1, sticky=tk.W, pady=2)
                
                if len(dims_found) >= 2:
                    ttk.Label(opt_frame, text="Dim 2 (2D):").grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
                    ttk.OptionMenu(opt_frame, dim2_var, dim2_var.get(), *dims_found).grid(row=1, column=1, sticky=tk.W, pady=2)
                    
                result = {}
                def on_ok():
                    result['type'] = plot_type_var.get()
                    result['dim1'] = dim1_var.get()
                    result['dim2'] = dim2_var.get()
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
            else:
                selected_plot_type = "0D"
                dim1_sel = None
                dim2_sel = None

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
