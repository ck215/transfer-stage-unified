import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
from lib import toupcam  # Your provided toupcam.py library

class CameraPresetManager:
    def __init__(self, root):
        self.root = root
        self.root.title("AmLite Preset Manager")
        self.root.geometry("450x250")
        
        # Attempt to open the camera
        self.cam = toupcam.Toupcam.Open(None)
        if self.cam is None:
            messagebox.showwarning(
                "Camera Not Found", 
                "Could not connect to the camera. If AmLite is currently running, it may be holding an exclusive USB lock on the device."
            )
            
        # Define Tkinter variables with defaults
        self.settings = {
            "exposure_us": tk.IntVar(value=10000), 
            "gain": tk.IntVar(value=100),         
            "brightness": tk.IntVar(value=0),     
            "contrast": tk.IntVar(value=0)        
        }
        
        self.build_ui()
        
    def build_ui(self):
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Create UI Sliders based on library boundaries
        self.create_slider(frame, "Exposure (µs)", self.settings["exposure_us"], 100, 100000, 0)
        self.create_slider(frame, "Gain", self.settings["gain"], 100, 1000, 1) 
        self.create_slider(frame, "Brightness", self.settings["brightness"], -255, 255, 2) 
        self.create_slider(frame, "Contrast", self.settings["contrast"], -255, 255, 3) 
        
        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=20)
        
        ttk.Button(btn_frame, text="Apply to Camera", command=self.apply_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Save Preset", command=self.save_preset).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Load Preset", command=self.load_preset).pack(side=tk.LEFT, padx=5)
        
    def create_slider(self, parent, label, var, vmin, vmax, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=5)
        slider = ttk.Scale(parent, from_=vmin, to=vmax, variable=var, orient=tk.HORIZONTAL, length=200)
        slider.grid(row=row, column=1, padx=10, pady=5)
        ttk.Label(parent, textvariable=var).grid(row=row, column=2, sticky=tk.E)

    def apply_settings(self):
        if not self.cam:
            # Try to reconnect in case it was freed up
            self.cam = toupcam.Toupcam.Open(None)
            if not self.cam:
                messagebox.showerror("Error", "Camera not connected. Please close AmLite and try again.")
                return
                
        try:
            # Push settings to the Toupcam hardware
            self.cam.put_ExpoTime(self.settings["exposure_us"].get())
            self.cam.put_ExpoAGain(self.settings["gain"].get())
            self.cam.put_Brightness(self.settings["brightness"].get())
            self.cam.put_Contrast(self.settings["contrast"].get())
            messagebox.showinfo("Success", "Settings applied successfully!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply settings: {e}")

    def save_preset(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json", 
            filetypes=[("JSON Presets", "*.json")],
            title="Save Camera Preset"
        )
        if filepath:
            data = {k: v.get() for k, v in self.settings.items()}
            try:
                with open(filepath, 'w') as f:
                    json.dump(data, f, indent=4)
            except Exception as e:
                messagebox.showerror("Error", f"Could not save file: {e}")
                
    def load_preset(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON Presets", "*.json")],
            title="Load Camera Preset"
        )
        if filepath:
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    # Update GUI sliders
                    for k, v in data.items():
                        if k in self.settings:
                            self.settings[k].set(v)
                # Automatically apply once loaded
                self.apply_settings()
            except Exception as e:
                messagebox.showerror("Error", f"Could not load preset: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = CameraPresetManager(root)
    root.mainloop()