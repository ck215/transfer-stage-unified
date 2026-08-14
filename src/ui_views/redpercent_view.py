import tkinter as tk
from tkinter import ttk, filedialog, messagebox
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
        
        self.sync_x_var = tk.BooleanVar(value=False)
        self.sync_x_chk = ttk.Checkbutton(color_frame, text="Sync Stepper X Location", variable=self.sync_x_var, command=self._toggle_sync)
        self.sync_x_chk.grid(row=3, column=0, columnspan=2, pady=5, sticky=tk.W)

    def _toggle_sync(self):
        self.system.sync_x_location = self.sync_x_var.get()
        
        self.poll_display()

    def select_focus_area(self):
        try:
            from PIL import ImageGrab, ImageTk
        except ImportError:
            print("Pillow is required for screenshot selection.")
            return
            
        # Add a 500ms delay before taking the screenshot so window movement/animations can settle
        self.after(500, lambda: self._perform_screenshot_selection(ImageGrab, ImageTk))

    def _perform_screenshot_selection(self, ImageGrab, ImageTk):

        selection_window = tk.Toplevel(self.winfo_toplevel())
        selection_window.attributes('-fullscreen', True)
        selection_window.attributes('-topmost', True)
        
        # Optional Mac compatibility to remove window headers
        try:
            selection_window.overrideredirect(True)
        except Exception:
            pass

        screen_width = selection_window.winfo_screenwidth()
        screen_height = selection_window.winfo_screenheight()
        
        # Take a screenshot of the current screen to freeze the display
        screenshot = ImageGrab.grab(all_screens=True)
        
        # Handle macOS Retina scaling by forcing the screenshot to match Tkinter's logical screen dimensions
        if screenshot.width != screen_width or screenshot.height != screen_height:
            screenshot = screenshot.resize((screen_width, screen_height))
            
        self._screenshot_img = ImageTk.PhotoImage(screenshot)

        self.start_x = None
        self.start_y = None
        self.rect_id = None
        self.dragging = False

        canvas = tk.Canvas(selection_window, highlightthickness=0,
                           width=screen_width, height=screen_height, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)
        
        # Draw the frozen screenshot
        canvas.create_image(0, 0, image=self._screenshot_img, anchor="nw")

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

    def destroy(self):
        print("[color_test] Cleaning up and closing RedPercentView...")
        self.system.stop_monitoring()
        super().destroy()
        print("[color_test] Cleanup complete.")
