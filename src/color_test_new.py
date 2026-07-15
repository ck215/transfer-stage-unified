import tkinter as tk
from tkinter import ttk
from tkinter import filedialog  #ADDED
import threading
import time
from PIL import Image
import mss
import numpy as np

# Module-level variable to hold the app instance
app_instance = None

def run_color_test():
    """Create and run the ColorDetector application."""
    global app_instance
    print("\n[color_test] Starting external application for color detection...")
    app_instance = ColorDetector()
    app_instance.run()

def cleanup():
    """
    Public cleanup function to be called from the main app.
    This will find the active instance and tell it to close.
    """
    global app_instance
    if app_instance:
        print("[color_test] Main app requested cleanup.")
        app_instance.cleanup()
        app_instance = None

class ColorDetector:
    """sizing, variable delaration, setup for gui (edited for only red values)"""
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Screen Color Detector (Red Only)")
        self.root.geometry("350x250")

        # Variables
        self.monitoring = False
        self.focus_area = None
        self.baseline_red = 0
        self.current_red = 0
        self.last_printed_red = -1

        # ADDED: log storage
        self.log_data = []

        # GUI Setup
        self.setup_gui()

        # Hook close button
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)

    def setup_gui(self):
        """Setup the GUI elements for the application: buttons and interfrace"""
        control_frame = ttk.Frame(self.root)
        control_frame.pack(pady=10)

        self.select_btn = ttk.Button(control_frame, text="Select Focus Area",
                                     command=self.select_focus_area)
        self.select_btn.pack(side=tk.LEFT, padx=5)

        self.start_btn = ttk.Button(control_frame, text="Start Monitoring",
                                    command=self.start_monitoring, state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(control_frame, text="Stop Monitoring",
                                   command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        status_frame = ttk.Frame(self.root)
        status_frame.pack(pady=10)

        ttk.Label(status_frame, text="Focus Area:").grid(row=0, column=0, sticky=tk.W)
        self.area_label = ttk.Label(status_frame, text="Not selected")
        self.area_label.grid(row=0, column=1, sticky=tk.W)

        color_frame = ttk.LabelFrame(self.root, text="Red Detection")
        color_frame.pack(pady=10, padx=10, fill=tk.X)

        ttk.Label(color_frame, text="Red %:").grid(row=0, column=0, sticky=tk.W)
        self.red_label = ttk.Label(color_frame, text="0.0%")
        self.red_label.grid(row=0, column=1, sticky=tk.W)

        ttk.Label(color_frame, text="Red Change:").grid(row=1, column=0, sticky=tk.W)
        self.red_change_label = tk.Label(color_frame, text="0.0%", fg="black")
        self.red_change_label.grid(row=1, column=1, sticky=tk.W)

        self.reset_btn = ttk.Button(color_frame, text="Reset Baseline",
                                    command=self.reset_baseline, state=tk.DISABLED)
        self.reset_btn.grid(row=2, column=0, columnspan=2, pady=5)

        # ADDED: Save Log Button
        self.save_btn = ttk.Button(color_frame, text="Save Log",
                                  command=self.save_log_to_file)
        self.save_btn.grid(row=3, column=0, columnspan=2, pady=5)

        info_frame = ttk.LabelFrame(self.root, text="Serial Output")
        info_frame.pack(pady=5, padx=10, fill=tk.X)
        ttk.Label(info_frame, text="Red % values are printed to console",
                  font=('Arial', 8)).pack()

    def select_focus_area(self):
        """Allow user to select a rectangular area on screen"""
        self.root.withdraw() # Hide main window

        selection_window = tk.Toplevel()
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
                    self.focus_area = {
                        'left': left,
                        'top': top,
                        'width': width,
                        'height': height
                    }

                    selection_window.destroy()
                    self.root.deiconify()

                    self.area_label.config(text=f"{width}x{height} at ({left},{top})")
                    self.start_btn.config(state=tk.NORMAL)

        def cancel_selection(event):
            selection_window.destroy()
            self.root.deiconify()

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

    def capture_focus_area(self, sct):
        """Capture the selected focus area from screen"""
        if not self.focus_area:
            return None

        try:
            screenshot = sct.grab(self.focus_area)
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
            return np.array(img)
        except Exception as e:
            print(f"Error capturing screen: {e}")
            return None

    def detect_red(self, image):
        if image is None:
            return 0

        r = image[:, :, 0]
        g = image[:, :, 1]
        b = image[:, :, 2]

        red_mask = (r > 150) & (g < 100) & (b < 100)

        total_pixels = image.shape[0] * image.shape[1]
        red_pixels = np.sum(red_mask)

        return (red_pixels / total_pixels) * 100

    def monitor_colors(self):
        first_reading = True

        with mss.mss() as sct:
            while self.monitoring:
                image = self.capture_focus_area(sct)

                if image is not None:
                    red_pct = self.detect_red(image)

                    if first_reading:
                        self.baseline_red = red_pct
                        first_reading = False

                        # OLD:
                        # print(f"BASELINE SET - Red: {red_pct:.1f}%")

                        # NEW (log + print)
                        log_entry = f"BASELINE SET - Red: {red_pct:.1f}%"
                        print(log_entry)
                        self.log_data.append(log_entry)

                    self.current_red = red_pct

                    rounded_red = round(red_pct, 1)
                    if abs(rounded_red - self.last_printed_red) >= 0.1:

                        # OLD:
                        # print(f"RED: {rounded_red:.1f}%")

                        # NEW (log + print)
                        log_entry = f"RED: {rounded_red:.1f}%"
                        print(log_entry)
                        self.log_data.append(log_entry)

                        self.last_printed_red = rounded_red

                    red_change = ((red_pct - self.baseline_red) / max(self.baseline_red, 0.1)) * 100

                    try:
                        self.root.after(0, self.update_display, red_pct, red_change)
                    except tk.TclError:
                        break

                time.sleep(0.1)

    def save_log_to_file(self):
        """Save logged serial output to a text file (manual button trigger)"""
        if not self.log_data:
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
                    for line in self.log_data:
                        f.write(line + "\n")

                print(f"[color_test] Log saved to: {file_path}")
            except Exception as e:
                print(f"[color_test] Error saving file: {e}")
        else:
            print("[color_test] Save cancelled.")

    def update_display(self, red_pct, red_change):
        """Update the GUI display with current values"""
        try:
            if not self.root.winfo_exists():
                return

            self.red_label.config(text=f"{red_pct:.1f}%")

            color = "green" if red_change > 0 else "red" if red_change < 0 else "black"
            self.red_change_label.config(text=f"{red_change:+.1f}%", fg=color)

        except tk.TclError:
            pass

    def start_monitoring(self):
        """Start the color monitoring"""
        self.monitoring = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.reset_btn.config(state=tk.NORMAL)

        print("=== MONITORING STARTED ===")

        self.monitor_thread = threading.Thread(target=self.monitor_colors)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def stop_monitoring(self):
        """Stop the color monitoring"""
        self.monitoring = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

        print("=== MONITORING STOPPED ===")

    def reset_baseline(self):
        """Reset the baseline values to current readings"""
        self.baseline_red = self.current_red

        # OLD:console print only
        # print(f"BASELINE RESET - Red: {self.baseline_red:.1f}%")

        # NEW (log + print)
        log_entry = f"BASELINE RESET - Red: {self.baseline_red:.1f}%"
        print(log_entry)
        self.log_data.append(log_entry)

    def run(self):
        """Start the application"""
        self.root.mainloop()

    def cleanup(self):
        """
        Safely shuts down the color detector application.
        Stops monitoring and destroys the Tkinter window.
        """

        print("[color_test] Cleaning up and closing window...")
        self.monitoring = False

        # OLD AUTO SAVE 
        # self.save_log_to_file()

        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except:
            pass

        print("[color_test] Cleanup complete.")


if __name__ == "__main__":
    app = ColorDetector()
    app.run()