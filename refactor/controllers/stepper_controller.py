import tkinter as tk
from tkinter import filedialog
from pathlib import Path
import time
from gcodeparser import parse_gcode_lines

import src.color_test_new as color_test
import src.serialDrive as serialDrive

class StepperController:
    def __init__(self, root, model, controller_driver, serial_conn, active_claims, process_name):
        self.root = root
        self.model = model
        self.controller = controller_driver
        self.serial = serial_conn
        self.active_claims = active_claims
        self.process_name = process_name
        self.view = None
        self._running = True
        self.disable_timer_id = None
        self._is_polling_pos = False
        self.open_script = ''

    def set_view(self, view):
        self.view = view
        
        # Protocal for window closing
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # Start the global position polling loop (runs in all modes)
        self._poll_position()
        self._update_controller_dropdown_loop()
        
    def _update_controller_dropdown_loop(self):
        if not self._running:
            return

        hardware_controllers = self.controller.get_physical_controllers()

        other_claims = {
            id_str for proc, id_str in self.active_claims.items()
            if proc != self.process_name
        }

        available_options = ["None"]
        for ctrl in hardware_controllers:
            if ctrl not in other_claims:
                available_options.append(ctrl)

        if self.view:
            self.view.controller_dropdown['values'] = available_options

        current_selection = self.active_claims.get(self.process_name, "None")
        self.model.controller_var.set(current_selection)

        self.root.after(500, self._update_controller_dropdown_loop)

    def on_controller_dropdown_selected(self, event):
        selected = self.model.controller_var.get()

        success = self.controller.change_controller(selected)

        if not success or "None" in selected:
            self.active_claims[self.process_name] = "None"
            if self.model.manualFlag:
                self.full_stop_button()
        else:
            self.active_claims[self.process_name] = selected
            print(f"[{self.process_name}] Successfully mapped to {selected}")

    def _poll_position(self):
        if not self._running:
            self._is_polling_pos = False
            return

        if self.serial.ser is None:
            print("[AppLogic] Serial connection not established. Cannot poll position.")
            self._is_polling_pos = False
            return
        else:
            pos = self.serial.read_position()
            if pos is not None:
                self.model.pos_x.set(str(pos[0]))
                self.model.pos_y.set(str(pos[1]))
                self.model.pos_z.set(str(pos[2]))

            self.root.after(50, self._poll_position)

    def open_controller_log_window(self):
        if not self.view:
            return
        is_connected = self.controller.joystick is not None
        self.view.open_controller_log_window(is_controller_connected=is_connected)
        if is_connected:
            try:
                self.view.controller_log_print(f"[AppLogic] Controller connected: {self.controller.joystick.get_name()}")
            except Exception as e:
                print(f"[AppLogic] Controller connected, but error printing controller name in log window: {e}")

    def enter_autonomous_mode_button(self):
        if not (self.model.system_enabled):
            print("\n[AppLogic] System is not enabled. Command not sent.")
            return
        print("\n[AppLogic] ENTER AUTONOMOUS MODE button clicked.")
        print("AUTONOMOUS MODE ENGAGED")
        print("Send stepping requests with START STEPPING button.")
        print("Press FULL STOP to stop stepping.")
        print("Enter manual mode using the MANUAL MODE button to control the stage manually with the Xbox Controller.")
        self.model.autonFlag = True
        self.model.manualFlag = False

        self.controller.stop_polling()

        if self.view and self.view.controller_log_window:
            self.view.close_controller_log_window()

        try:
            params = self.get_gui_params(manual_flag=False, auton_flag=False)
            self.serial.send_autonomous_command(params)
        except Exception as e:
            print(f"[AppLogic] Error sending stop on mode switch: {e}")

    def select_script_button(self):
        filepath = filedialog.askopenfilename(
            title="Select a File",
            filetypes=[("All Files", "*.*"), ("Text Files", "*.txt")]
        )
        if filepath:
            self.model.selected_script.set(Path(filepath).name)
            self.open_script = filepath

    def enable_button(self):
        print("\n[AppLogic] ENABLE/DISABLE button clicked.")
        if (self.model.system_enabled):
            self.model.system_enabled = False
            self.full_stop_button()
            self.serial.disable()
            if self.view:
                self.view.enable_button.config(text="Enable System",
                bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))

            if self.disable_timer_id:
                self.root.after_cancel(self.disable_timer_id)
                self.disable_timer_id = None

        else:
            try:
                self.serial.enable()
                self.model.system_enabled = True
                if self.view:
                    self.view.enable_button.config(text="Disable System",
                    bg='darkred', fg='black', font=('Arial', 10, 'bold'))
                
                self.disable_timer_id = self.root.after(300000, self.auto_disable)
            except ValueError as e:
                print(e)
                
    def reset_disable_timer(self):
        if self.model.system_enabled and self.disable_timer_id:
            self.root.after_cancel(self.disable_timer_id)
            self.disable_timer_id = self.root.after(300000, self.auto_disable)
            
    def auto_disable(self):
        print("\n[AppLogic] 5-minute timeout reached due to inactivity. Auto-disabling system.")
        if self.model.system_enabled:
            self.enable_button()

    def get_gui_params(self, manual_flag: bool, auton_flag: bool):
        return {
            "x_step_size": self.model.x_step.get(),             
            "y_step_size": self.model.y_step.get(),              
            "z_step_size": self.model.z_step.get(),              
            "full_speed": self.model.full_speed.get(),           
            "slow_speed": 0,           
            "brake_distance": 0,   
            "x_dist": self.model.x_dist.get(),                   
            "y_dist": self.model.y_dist.get(),                   
            "z_dist": self.model.z_dist.get(),  
            "command_code_manual": int(manual_flag),               
            "command_code_auton": int(auton_flag)                      
        }

    def start_stepping_button(self):
        if not (self.model.system_enabled):
            print("\n[AppLogic] System is not enabled. Command not sent.")
            return
            
        self.reset_disable_timer()
        if self.model.autonFlag and not self.model.manualFlag:
            print("\n[AppLogic] START STEPPING button clicked.")
            try:
                params = self.get_gui_params(self.model.manualFlag, self.model.autonFlag)
                self.serial.send_autonomous_command(params)
            except Exception as e:
                print(f"[AppLogic] Error in calling send_autonomous_command: {e}")
        else:
            print("\n[AppLogic] Cannot step while not in AUTONOMOUS MODE.")

    def full_stop_button(self):
        print("\n[AppLogic] FULL STOP button clicked.")
        print("FULL STOP engaged. Select a mode to continue.")
        try:
            self.model.manualFlag = False
            self.model.autonFlag = False
            self.controller.stop_polling()
            params = self.get_gui_params(self.model.manualFlag, self.model.autonFlag)
            if self.view:
                self.view.close_controller_log_window()
            self.serial.send_autonomous_command(params)
            
        except Exception as e:
            print(f"[AppLogic] Error in stopping stepping: {e}")

    def run_script_button(self):
        print("[AppLogic] RUN SCRIPT button clicked.")
        try:
            with open(self.open_script, 'r') as f:
                print("1!")
                for line in parse_gcode_lines(f, include_comments=False):
                    print("2!")
                    print(line)
        except Exception as e:
            print("[AppLogic] Script parse failed. Perhaps selected file is not gcode.")

    def _manual_mode_loop(self):
        if self.controller.joystick is None:
            print("[AppLogic] No controller connected. Please connect a controller before entering MANUAL MODE.")
            self.model.manualFlag = False
        
            try:
                self.full_stop_button()
                if (self.model.system_enabled):
                    self.enable_button()
            except Exception as e:
                print(f"[AppLogic] Error sending stop command on controller disconnect: {e}")
            return
        
        if self.model.manualFlag == False:
            print("[AppLogic] Exiting manual mode loop.")
            return
        
        try:
            params = self.get_controller_params()
            self.serial.send_manual_mode_command(params)

            self.root.after(5, self._manual_mode_loop)  
            
        except Exception as e:
            print(f"[AppLogic] Error in manual mode loop: {e}")

    def enter_manual_mode_button(self):
        if not (self.model.system_enabled):
            print("\n[AppLogic] System is not enabled. Command not sent.")
            return
        if self.controller.joystick is None:
            print("\nNo controller connected. Please connect a controller before entering MANUAL MODE.")
            self.model.manualFlag = False
            return
        if self.model.manualFlag:
            print("\n[AppLogic] Already in MANUAL MODE.")
            return

        print("\n[AppLogic] ENTER MANUAL MODE button clicked.")
        print("MANUAL MODE ENGAGED")
        print("Reading controller input... press FULL STOP to stop controller input and switch modes.")
        self.model.manualFlag = True
        self.model.autonFlag = False

        self.controller.start_polling(
            gui=self.root, 
            log_updater=self.view.controller_log_print if self.view else print, 
            activity_callback=self.reset_disable_timer
        )

        self.open_controller_log_window()
        
        self._manual_mode_loop()

    def get_controller_params(self):
        return {
            "x_axisStatus": self.controller.prev_axis_states.get(self.controller.controller_binds[0], 0.0),
            "y_axisStatus": self.controller.prev_axis_states.get(self.controller.controller_binds[1], 0.0),
            "z_axisStatusR": self.controller.prev_axis_states.get(self.controller.controller_binds[2], -1.0),
            "z_axisStatusL": self.controller.prev_axis_states.get(self.controller.controller_binds[3], -1.0),
            "x_stepSize": self.model.x_step.get(),
            "y_stepSize": self.model.y_step.get(),
            "z_stepSize": self.model.z_step.get(),
            "dpad_LR": self.controller.prev_hat_states.get(0, (0, 0))[0],
            "dpad_UD": self.controller.prev_hat_states.get(0, (0, 0))[1],
            "LBumper": self.controller.prev_button_states.get(self.controller.controller_binds[4], 0),
            "RBumper": self.controller.prev_button_states.get(self.controller.controller_binds[5], 0),
            "manual_jog_speed": self.model.man_full_speed.get()                             
        }
    
    def serial_reconnect_button(self):
        print("\n[AppLogic] SERIAL RECONNECT button clicked.")
        self.serial.close()
        time.sleep(1)
        self.serial = serialDrive.SerialArduino(port=self.model.serial_port.get())
        if self.serial.ser and self.serial.ser.is_open:
            print("[AppLogic] Serial reconnected successfully.")
            if getattr(self, '_is_polling_pos', False) is False:
                self._poll_position()
        else:
            print("[AppLogic] Failed to reconnect serial.")
            
    def color_test_window(self):
        color_test.run_color_test()
        print("\n[AppLogic] COLOR TEST button clicked.")
        return
    
    def _on_closing(self):
        print("[AppLogic] Closing application...")
        if (self.model.system_enabled):
            self.serial.disable()
        self._running = False
        self.controller.stop_polling()
        self.controller.close()
        self.serial.close()
        
        print("\n[ROOT_GUI] GUI closing...")
        if self.view:
            self.view.close_controller_log_window()
        self.root.destroy()
