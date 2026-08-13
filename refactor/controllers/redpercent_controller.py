import tkinter as tk
from tkinter import filedialog
import threading
import time
from PIL import Image
import mss
import numpy as np

class RedPercentController:
    def __init__(self, model, view):
        self.model = model
        self.view = view
        
        self.bind_commands()
        self.view.master.protocol("WM_DELETE_WINDOW", self.cleanup)

    def bind_commands(self):
        self.view.select_btn.config(command=self.select_focus_area)
        self.view.start_btn.config(command=self.start_monitoring)
        self.view.stop_btn.config(command=self.stop_monitoring)
        self.view.reset_btn.config(command=self.reset_baseline)
        self.view.save_btn.config(command=self.save_log_to_file)

    def select_focus_area(self):
        self.view.master.withdraw()

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
                    self.model.focus_area = {
                        'left': left,
                        'top': top,
                        'width': width,
                        'height': height
                    }

                    selection_window.destroy()
                    self.view.master.deiconify()

                    self.view.area_label.config(text=f"{width}x{height} at ({left},{top})")
                    self.view.start_btn.config(state=tk.NORMAL)

        def cancel_selection(event):
            selection_window.destroy()
            self.view.master.deiconify()

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
        if not self.model.focus_area:
            return None

        try:
            screenshot = sct.grab(self.model.focus_area)
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
            while self.model.monitoring:
                image = self.capture_focus_area(sct)

                if image is not None:
                    red_pct = self.detect_red(image)

                    if first_reading:
                        self.model.baseline_red = red_pct
                        first_reading = False

                        log_entry = f"BASELINE SET - Red: {red_pct:.1f}%"
                        print(log_entry)
                        self.model.add_log(log_entry)

                    self.model.current_red = red_pct

                    rounded_red = round(red_pct, 1)
                    if abs(rounded_red - self.model.last_printed_red) >= 0.1:
                        log_entry = f"RED: {rounded_red:.1f}%"
                        print(log_entry)
                        self.model.add_log(log_entry)

                        self.model.last_printed_red = rounded_red

                    red_change = ((red_pct - self.model.baseline_red) / max(self.model.baseline_red, 0.1)) * 100

                    try:
                        self.view.master.after(0, self.update_display, red_pct, red_change)
                    except tk.TclError:
                        break

                time.sleep(0.1)

    def save_log_to_file(self):
        if not self.model.log_data:
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
                    for line in self.model.log_data:
                        f.write(line + "\n")

                print(f"[color_test] Log saved to: {file_path}")
            except Exception as e:
                print(f"[color_test] Error saving file: {e}")
        else:
            print("[color_test] Save cancelled.")

    def update_display(self, red_pct, red_change):
        try:
            if not self.view.master.winfo_exists():
                return

            self.view.red_label.config(text=f"{red_pct:.1f}%")

            color = "green" if red_change > 0 else "red" if red_change < 0 else "black"
            self.view.red_change_label.config(text=f"{red_change:+.1f}%", fg=color)

        except tk.TclError:
            pass

    def start_monitoring(self):
        self.model.monitoring = True
        self.view.start_btn.config(state=tk.DISABLED)
        self.view.stop_btn.config(state=tk.NORMAL)
        self.view.reset_btn.config(state=tk.NORMAL)

        print("=== MONITORING STARTED ===")

        self.monitor_thread = threading.Thread(target=self.monitor_colors)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

    def stop_monitoring(self):
        self.model.monitoring = False
        self.view.start_btn.config(state=tk.NORMAL)
        self.view.stop_btn.config(state=tk.DISABLED)

        print("=== MONITORING STOPPED ===")

    def reset_baseline(self):
        self.model.baseline_red = self.model.current_red

        log_entry = f"BASELINE RESET - Red: {self.model.baseline_red:.1f}%"
        print(log_entry)
        self.model.add_log(log_entry)

    def cleanup(self):
        print("[color_test] Cleaning up and closing window...")
        self.model.monitoring = False

        try:
            if self.view.master.winfo_exists():
                self.view.master.destroy()
        except:
            pass

        print("[color_test] Cleanup complete.")
