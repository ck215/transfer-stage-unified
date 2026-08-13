import tkinter as tk
from tkinter import ttk, filedialog
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

        self.save_btn = ttk.Button(color_frame, text="Save Log", command=self.save_log_to_file)
        self.save_btn.grid(row=3, column=0, columnspan=2, pady=5)

        info_frame = ttk.LabelFrame(self, text="Serial Output")
        info_frame.pack(pady=5, padx=10, fill=tk.X)
        ttk.Label(info_frame, text="Red % values are printed to console", font=('Arial', 8)).pack()
        
        self.poll_display()

    def select_focus_area(self):
        # Create a borderless, transparent, fullscreen overlay window
        selection_window = tk.Toplevel(self.winfo_toplevel())
        selection_window.attributes('-fullscreen', True)
        selection_window.attributes('-alpha', 0.3)
        selection_window.configure(bg='gray10')
        selection_window.attributes('-topmost', True)

        screen_width = selection_window.winfo_screenwidth()
        screen_height = selection_window.winfo_screenheight()

        self.start_x = None
        self.start_y = None
        self.rect_id = None
        self.dragging = False

        canvas = tk.Canvas(selection_window, highlightthickness=0,
                           width=screen_width, height=screen_height)
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
                    self.system.focus_area = {
                        'left': left,
                        'top': top,
                        'width': width,
                        'height': height
                    }

                    selection_window.destroy()

                    self.area_label.config(text=f"{width}x{height} at ({left},{top})")
                    self.start_btn.config(state=tk.NORMAL)

        def cancel_selection(event):
            selection_window.destroy()

        canvas.bind('<Button-1>', start_selection)
        canvas.bind('<B1-Motion>', update_selection)
        canvas.bind('<ButtonRelease-1>', end_selection)
        canvas.bind('<Escape>', cancel_selection)
        selection_window.bind('<Escape>', cancel_selection)

        instruction = tk.Label(selection_window,
                               text="Click and drag to select focus area",
                               fg='red', bg='gray10', font=('Arial', 30))
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

    def reset_baseline(self):
        self.system.reset_baseline()

    def save_log_to_file(self):
        log_data = self.system.get_log_data()
        if not log_data:
            print("[color_test] No data to save.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt")],
            title="Save Red Detection Log"
        )

        if file_path:
            try:
                with open(file_path, "w") as f:
                    for line in log_data:
                        f.write(line + "\n")
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

    def destroy(self):
        print("[color_test] Cleaning up and closing RedPercentView...")
        self.system.stop_monitoring()
        super().destroy()
        print("[color_test] Cleanup complete.")
