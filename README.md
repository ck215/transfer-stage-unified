# Transfer Stage Unified Control System User Guide

This document acts as a manual for the operation of transfer stage devices. It is currently OUT OF DATE and being migrated from previous, separate controllers which are now integrated into a single control system. For clarification or updated instructions, contact Carter or Ian in the lab Slack.

## Turning on and navigating the Desktop
The PC is designed to be compatible with two operating systems, Windows and Linux (Mint). When turning on the PC, a GRUB menu will appear with options on which operating system to enter into. If no key inputs are recieved, Linux Mint will boot by default. To select an alternative at boot, use the up and down arrow keys to navigate to either the Windows Boot Loader or the UEFI/BIOS of the PC for advanced troubleshooting. Unless a feature directly on Windows is desired (for example, saved color profiles, although this will be added to Linux soon), the system should be allowed to turn on as normal.

To switch between operating systems at any time, simply restart the PC using the power button in the bottom left corner of either of the operating system's start menus.

The password to both operating systems is on the monitor attached to the transfer stage. Once on the desktop itself, icons for the Transfer Stage controller, Google Drive, and AmScope software are easily accessible. Further instruction on each is provided in later sections of this tutorial.

## Connecting the Xbox Controller

The Xbox Controller should be powered on and connected to the transfer station PC before opening the software. It may be plugged in with a cable, in which case its Xbox logo should be lit up. If it is not wired, it should be connected wirelessly. You can verify this by checking the PC's Bluetooth settings, located at the bottom right of the desktop. If it is not connected, reconnect it using the following process:

- Open the Bluetooth pairing menu on the PC, under Bluetooth Manager
- Power on the controller by holding the Xbox logo button until it lights up. Then, hold the pairing button, which is on the front (further from the user) of the controller, until the light flashes rapidly
- Select the controller on the pairing menu on the PC
- Ensure it is now listed under Bluetooth Manager devices as "connected"

### Opening the Software

Double click the Transfer Stage Launcher icon on the desktop. It should automatically populate ports and open a configuration GUI.

- Select a controller for each device using the drop-down on the right side, if desired.
- If your controller does not show up, ensure it is connected. If connecting a new controller after this check, use the Refresh Devices button to allow the program to identify the new devices and present them as options.
- Press Launch Controllers to launch the control windows. One window will launch for each control system selected at program startup.

## Using the Software

### Motion Controllers (Stepper, Chuck, DC)

- The transfer stage code is currently designed to be used in two ways; autonomous and manual mode. Each mode has a button on the GUI, alongside a series of fields for control paramaters, as well as an emergency FULL STOP button which applies to **both modes**.

- **Absolute Position** represents the probe's current location relative to where the probe was when the program was initialized. It can be used to keep track of realative coordinates between modes during a session, or to keep track of DC motor drift.

#### Autonomous

Autonomous mode is designed for moving in fixed step sizes. 

- The smallest step size available to the stepper probe is 1, which represents 1/16 of a step (~300 nanometers). This level of movement is not verified to be reliable. Step sizes of 4 (1.25 um) and above are verified. The **Step Sizes** field can be used to apply a fixed multiplier to each stepping input. For example, at step size 1, a command sent to step 100 in the x direction will move that many counts forward; at step size 5, it will instead move 500 counts forward.

> A full step (16 microsteps) is measured to be almost exactly 5 microns in any given direction.

- **Relative Step Counts** is used to provide the desired steps for the probe to take in x, y, and z directions respectively when sent autonomously. Think of the input field as the components of a vector, and the system will move at the given speed in the direction of and to the terminal point of that vector. However, diagonal movement is not verified as of yet, and it is recommended to only move in one axis at a time.

> Also note commands can be interrupted, and if I send a pulse to move 100 steps twice, the probe may move anywhere between 100 to 200 steps, depending on when the second command was sent. For back to back commands, ensure the probe has come to a stop completely before proceding with a successive command.

- **Full Speed** is the field used to determine the speed the probe will use while stepping during an autonomous command. There is negligible acceleration time on the stepper variant.
- 
- Once the parameters in all the aforementioned fields are set as desired, use **Start Stepping** to send the autonomous command to the probe.

> Once a command is sent, its parameters are fixed for that iteration of autonomous command. During motion, any command can be stopped at any time by using the Full Stop button. Commands can NOT be updated by sending another command; this will simply begin a NEW command with the new parameters, and may result in unexpected behavior. If a command is sent on accident, it is best practice to Full Stop the existing command, then re-enter autonomous mode and send a NEW command with the remedied paramaters.

#### Manual

TBA

## Temperature Controller

TBA

## Troubleshooting Steps

First, ensure that the device(s) are connected properly such that...

- The USB-B to USB-A cable should travel from the Arduino Mega 2560 within the box to the back of the transfer station PC
- All jumper cables should be connected and terminate at either the Arduino, the serial connector, or the power supply.

> If any jumper cables are loose, **immediately and carefully** disconnect the PSU from the power strip and the USB-B to USB-A cable from the PC to the Arduino, and contact an author for investigation.

- If multiple controllers are detected with the PC, the terminal will display all available options and request that the user select one controller. The window will launch before this, leaving the controller selection prompt in the terminal. You can still select after the window has opened. **ONLY THE XBOX CONTROLLER IS CURRENTLY SUPPORTED**. Chose the index displayed corresponding to the Xbox controller and press enter. 

## Common Error Messages

## Advanced Troubleshooting

## FAQ

## Appendix

Images and definitions of commonly referenced terminology
