#include <AccelStepper.h>
#include <TMCStepper.h>
#include <avr/wdt.h>

// define pins
#define zStep 22
#define zDir 23
#define zEN 24

#define yStep 32
#define yDir 33
#define yEN 34

#define xStep 42
#define xDir 43
#define xEN 44

// define UART parameters
#define DRIVER_ADDRESS   0b00  
#define R_SENSE          0.11f // Standard for most TMC2209 modules

// declare axes for driving
AccelStepper x_axis(1, xStep, xDir);
AccelStepper y_axis(1, yStep, yDir);
AccelStepper z_axis(1, zStep, zDir);

// declare axes for comms

TMC2209Stepper xUART(&Serial2, R_SENSE, DRIVER_ADDRESS);

TMC2209Stepper yUART(&Serial3, R_SENSE, DRIVER_ADDRESS);

TMC2209Stepper zUART(&Serial1, R_SENSE, DRIVER_ADDRESS);

// DEAD ZONE for manual control
#define MANUAL_DEAD_ZONE 0.05

#define MANUAL_SERIAL_BUFFER_SIZE 128
char manualSerialBuffer[MANUAL_SERIAL_BUFFER_SIZE];
int manualBufferIndex = 0;

bool system_enabled = false;

long XAXIS_DIST = 0; 
long YAXIS_DIST = 0; 
long ZAXIS_DIST = 0; 

long XAXIS_SIZE = 16; // defualts
long YAXIS_SIZE = 16; 
long ZAXIS_SIZE = 16; 

int FULL_SPEED = 400;
int SLOW_SPEED = 10;
long BRAKE_DISTANCE = 0;
bool AUTONOMOUS_ON = true;
bool MANUAL_ON = false;
bool DPAD_STEP = false;

// Holds the current manual command as a float (-1.0 to 1.0)
float manual_x_value = 0.0;
float manual_y_value = 0.0;
float manual_z_value = 0.0;
int dpad_LR = 0;
int dpad_UD = 0;
int bumpers = 0;
// long, not int (2026-10-10, review R-2 / X-19): an int is 16 bits on the Mega, and a jog packet may carry a step
// size up to 100000 (fieldIsStep), so a size past 32767 wrapped and the D-pad step ran the other way.
long x_step_size = 16;
long y_step_size = 16;
long z_step_size = 16;

// Possible states of the motors in autonomous mode
bool ALL_AXES_DONE = true; 
enum AxisState { 
  IDLE,
  MOVE_X,
  MOVE_Y,
  MOVE_Z,
  ALL_DONE
};

// Always start in idling state on initialization
AxisState current_state = IDLE;

// timer for Serial print
unsigned long previous_print_millis = 0;
#define PRINT_INTERVAL 100

// ---- Runaway guards, ported from stepper_firmware (bench incidents
// 2026-10-04 17:40 and 17:59 on the Stepper Probe: the board kept stepping on
// its last jog command while ignoring every byte the host sent, and only a
// hard reset stopped it). The chuck runs the same loop, so it has the same
// exposure.
// 1. Jog dead-man: in manual (and during a D-pad step) the host streams a
//    packet every 20 ms, neutral included (ChuckPositioner inherits Probe's
//    50 Hz gamepad pump). No packet for JOG_TIMEOUT_MS stops the motion;
//    coils keep holding (the host stops streaming when its window loses
//    focus, and that must not drop the Z axis).
// 2. Hardware watchdog: petted only while millis() advances (interrupts
//    alive) and the host UART is still enabled; otherwise the board resets
//    within ~1 s and setup() boots with the driver outputs off.
// 3. Every stop is unconditional and cancels a D-pad step; 'd' also pulls the
//    EN pins high, a disable that needs no UART to the drivers.
// 4. 'e' no longer blocks for 30 ms (that stall overflowed the 64-byte RX
//    buffer on every manual entry); motion is held for ENABLE_SETTLE_MS.
// EN is active-low here as on the stepper (setup() always drove the EN pins
// LOW to enable); the pin numbers are this sketch's own (zEN 24, yEN 34,
// xEN 44), not the stepper's.
#define JOG_TIMEOUT_MS 250
#define ENABLE_SETTLE_MS 30
unsigned long last_packet_ms = 0;
unsigned long enable_settle_until = 0;
bool settling = false;
unsigned long wdt_last_millis = 0;

void driversOutputsOff() {
  digitalWrite(xEN, HIGH);
  digitalWrite(yEN, HIGH);
  digitalWrite(zEN, HIGH);
}

void driversOutputsOn() {
  digitalWrite(xEN, LOW);
  digitalWrite(yEN, LOW);
  digitalWrite(zEN, LOW);
}

void holdPosition() {
  x_axis.setSpeed(0);
  y_axis.setSpeed(0);
  z_axis.setSpeed(0);
  x_axis.moveTo(x_axis.currentPosition());
  y_axis.moveTo(y_axis.currentPosition());
  z_axis.moveTo(z_axis.currentPosition());
}

void haltMotion() {
  MANUAL_ON = false;
  AUTONOMOUS_ON = false;
  DPAD_STEP = false;
  current_state = IDLE;
  manual_x_value = 0.0; manual_y_value = 0.0; manual_z_value = 0.0;
  dpad_LR = 0; dpad_UD = 0; bumpers = 0;
  holdPosition();
}

// The 'd' path (also #RES's failed revert, R-6). TOFF=0 is what actually disables a TMC2209 driver output stage (see
// setup()'s toff(0) at boot for the same convention) -- toff(2) here previously left the driver enabled with a
// different chopper off-time, never cutting coil current at all. Also run this unconditionally rather than gating on
// our own system_enabled belief: a stale/desynced flag must never be able to block the one command that actually kills
// power. EN high needs no UART, so it holds even for a driver that ignores the TOFF write.
void disableDrivers() {
  system_enabled = false;
  haltMotion();
  driversOutputsOff();
  xUART.toff(0);
  yUART.toff(0);
  zUART.toff(0);
}

// ---- Step resolution as a run-time lever (CR-1, 2026-10-23: Andrew's request via the owner). Was a local in setup().
// Boot default 2 (half steps: fast, coarse), today's value; `#RES <n>` changes it at standstill (PROTOCOL.md "#RES").
// The presets are the host's choice (Fast = 2, Fine = 8, PROVISIONAL on the station side): this board takes any power
// of two 1..256, so changing a preset never needs a reflash.
uint16_t microstepMode = 2;
// R2-1: set by `driver-revert-failed` (the drivers may disagree), cleared at boot or by an 'e' that finds all three
// drivers reading back microstepMode after rewriting it. Every #OK RES reply carries it as suspect=0|1.
bool res_suspect = false;

// TMCStepper 0.7.3 spells a full step 0 both ways: microsteps(1) writes nothing and the getter answers 0 for MRES 8.
// Map 1 <-> 0, as firmware/xyz_stage_axis does.
void setDriverMicrosteps(TMC2209Stepper &d, uint16_t ms) {
  d.microsteps(ms == 1 ? 0 : ms);
}

// The driver-UART truth: CHOPCONF read back from the driver over its own UART, not the library's shadow copy. A read
// that fails (no reply, a CRC error) confirms nothing: the library returns 0 then, which would decode as 256.
bool driverHasMicrosteps(TMC2209Stepper &d, uint16_t ms) {
  uint16_t got = d.microsteps();
  if (d.CRCerror) return false;
  if (got == 0) got = 1;
  return got == ms;
}

// Busy means MOVING (contract, amended 2026-10-23): an axis with a speed, a D-pad step, or a frame not yet done.
// distanceToGo() is not part of it: a jog runs on runSpeed(), which leaves the target behind, so it stayed non-zero
// after every jog with the sticks centred and the drivers at rest. setCurrentPosition() below resets the target anyway.
bool resBusy() {
  return x_axis.speed() != 0 || y_axis.speed() != 0 || z_axis.speed() != 0
      || DPAD_STEP || (AUTONOMOUS_ON && !ALL_AXES_DONE);
}

void replyRes() {
  Serial.print("#OK RES microsteps=");
  Serial.print((long)microstepMode);
  Serial.print(" pos=");
  Serial.print(x_axis.currentPosition());
  Serial.print(",");
  Serial.print(y_axis.currentPosition());
  Serial.print(",");
  Serial.print(z_axis.currentPosition());
  Serial.println(res_suspect ? " suspect=1" : " suspect=0");
}

// One '#' line (cap `res`: the lane carries #RES and nothing else). Exactly one reply line per command; nothing is
// ever sent unasked. No interrupt is involved: this runs from loop() like every other command here.
#define EXT_CMD_MAX 16
void parseExtLine() {
  String line = Serial.readStringUntil('\n');
  line.trim();
  const char *p = line.c_str() + 1;          // past the '#'
  char cmd[EXT_CMD_MAX + 1];
  uint8_t k = 0;
  // The first word, upper-cased as received. A byte that is not printable ASCII (a '#' that was really part of a
  // desynced jog packet) is sent back as '?', and a word is cut at EXT_CMD_MAX, so the reply stays one short line.
  for (; *p && *p != ' '; p++) {
    if (k >= EXT_CMD_MAX) continue;
    char c = *p;
    if (c >= 'a' && c <= 'z') c = c - 'a' + 'A';
    else if (c <= ' ' || c > '~') c = '?';
    cmd[k++] = c;
  }
  cmd[k] = '\0';
  if (strcmp(cmd, "RES") != 0) {
    Serial.print("#ERR ");
    Serial.print(cmd);
    Serial.println(" unknown");
    return;
  }
  while (*p == ' ') p++;
  if (*p == '\0') { replyRes(); return; }    // the query: changes nothing

  // The argument: decimal digits only, a power of two 1..256, nothing after it.
  long n = 0;
  uint8_t digits = 0;
  while (*p >= '0' && *p <= '9' && digits < 6) { n = n * 10 + (*p - '0'); p++; digits++; }
  while (*p == ' ') p++;
  if (digits == 0 || *p != '\0' || n < 1 || n > 256 || (n & (n - 1)) != 0) {
    Serial.println("#ERR RES bad-arg-power-of-2-1..256");
    return;
  }
  if (resBusy()) { Serial.println("#ERR RES busy"); return; }
  uint16_t ms = (uint16_t)n;
  uint16_t old = microstepMode;
  if (ms == old) { replyRes(); return; }     // a no-op that still answers

  // Standstill: the TMC2209 keeps its microstep counter across an MRES write, so the rotor does not move. Write all
  // three, read each back; on any mismatch put the old value back on all three, and touch nothing else.
  setDriverMicrosteps(xUART, ms);
  setDriverMicrosteps(yUART, ms);
  setDriverMicrosteps(zUART, ms);
  if (!driverHasMicrosteps(xUART, ms) || !driverHasMicrosteps(yUART, ms) || !driverHasMicrosteps(zUART, ms)) {
    setDriverMicrosteps(xUART, old);
    setDriverMicrosteps(yUART, old);
    setDriverMicrosteps(zUART, old);
    // R-6: read the revert back too. A driver that is not at the old value now leaves the axes mixed, which this board
    // cannot trust: disable everything (the 'd' path), keep microstepMode at the old value, and say so. The host
    // latches a stop; a person looks before it moves again.
    if (!driverHasMicrosteps(xUART, old) || !driverHasMicrosteps(yUART, old) || !driverHasMicrosteps(zUART, old)) {
      disableDrivers();
      res_suspect = true;   // R2-1: 'e' must re-establish agreement before anything is energized again
      Serial.println("#ERR RES driver-revert-failed");
      return;
    }
    Serial.println("#ERR RES driver-readback-mismatch");
    return;
  }
  microstepMode = ms;
  // The same physical place in the new counts (long arithmetic, C truncation toward zero, as the Teensy sketch's
  // spRescale), and setup()'s max-speed formula. Step sizes and speeds from the host stay in counts: not rescaled.
  x_axis.setCurrentPosition((x_axis.currentPosition() * (long)ms) / (long)old);
  y_axis.setCurrentPosition((y_axis.currentPosition() * (long)ms) / (long)old);
  z_axis.setCurrentPosition((z_axis.currentPosition() * (long)ms) / (long)old);
  x_axis.setMaxSpeed(1600L * ms / 2);
  y_axis.setMaxSpeed(1600L * ms / 2);
  z_axis.setMaxSpeed(1600L * ms / 2);
  replyRes();
}

// A jog packet is applied only if every field is plausible. A byte dropped
// from the RX buffer shifts the next packet into the one after it, and the
// shifted bytes decode as arbitrary floats (a huge speed, a NaN, a D-pad
// press); such a packet is ignored, and does not feed the jog dead-man.
// MAX_JOG_SPEED is the stepper's bound, kept as is: the host caps the chuck
// at 600 and setMaxSpeed() clamps it to 1600, so a real packet never nears it.
#define MAX_JOG_SPEED 6400.0
bool fieldIsStep(float v) {
  return isfinite(v) && v >= 0.0 && v <= 100000.0 && v == (float)(long)v;
}
bool fieldIsUnit(float v) {
  return isfinite(v) && v >= -1.0001 && v <= 1.0001;
}
bool fieldIsTri(float v) {
  return v == -1.0 || v == 0.0 || v == 1.0;
}

// Struct for packet format
struct __attribute__((packed)) ManualControlPacket {
    uint8_t start_marker;       // 0xAA
    uint8_t mode;               // Mode integer for the packet
    float x_axisStatus;         // X axis status
    float y_axisStatus;         // Y axis status
    float z_axisStatus;         // Z axis status
    float x_stepSize;           // X step size
    float y_stepSize;           // Y step size
    float z_stepSize;           // Z step size
    float dpad_LR;              // D-pad left-right value
    float dpad_UD;              // D-pad up-down value
    float bumpers;              // Bumper combined value
    float manual_jog_speed;     // Manual jog speed
};

const size_t BINARY_PACKET_SIZE = sizeof(ManualControlPacket);
ManualControlPacket incomingPacket;
unsigned long rejected_packets = 0;

// Checks the global incomingPacket (a struct parameter would break the
// Arduino prototype generator, which places prototypes above the struct).
bool packetIsValid() {
  const ManualControlPacket &p = incomingPacket;
  return fieldIsUnit(p.x_axisStatus) && fieldIsUnit(p.y_axisStatus)
      && fieldIsUnit(p.z_axisStatus)
      && fieldIsStep(p.x_stepSize) && fieldIsStep(p.y_stepSize)
      && fieldIsStep(p.z_stepSize)
      && p.x_stepSize >= 1.0 && p.y_stepSize >= 1.0 && p.z_stepSize >= 1.0
      && fieldIsTri(p.dpad_LR) && fieldIsTri(p.dpad_UD) && fieldIsTri(p.bumpers)
      && isfinite(p.manual_jog_speed) && p.manual_jog_speed >= 0.0
      && p.manual_jog_speed <= MAX_JOG_SPEED;
}

String getValue(String data, char separator, int index)
{
  int found = 0;
  int strIndex[] = {0, -1};
  int maxIndex = data.length() - 1;

  for (int i = 0; i <= maxIndex && found <= index; i++) {
    if (data[i] == separator || i == maxIndex) {
      found++;
      strIndex[0] = strIndex[1] + 1;
      strIndex[1] = (i == maxIndex) ? i + 1 : i;
    }
  }
  return found > index ? data.substring(strIndex[0], strIndex[1]) : "";
}

char* getValue(char* data, char separator, int index)
{
  static char buffer[32];

  int found = 0;
  int strIndex[] = {0, -1};
  int maxIndex = strlen(data) - 1;

  for (int i = 0; i <= maxIndex && found <= index; i++) {
    if (data[i] == separator || i == maxIndex) {
      found++;
      strIndex[0] = strIndex[1] + 1;
      strIndex[1] = (i == maxIndex) ? i + 1 : i;
    }
  }

  if (found > index) {
    int len = strIndex[1] - strIndex[0];
    if (len > 31) len = 31;
    strncpy(buffer, data + strIndex[0], len);
    buffer[len] = '\0';
    return buffer;
  }

  buffer[0] = '\0';
  return buffer;
}

void parseSerialAuto() // only run if there is new information in the buffer
{
    if (Serial.available() > 0) {
        
        String incomingData = Serial.readStringUntil('\n');
        incomingData.trim();

        if (incomingData.length() > 0)
        {
            // Serial2.print("Received Command: ");
            // Serial2.println(incomingData);

            bool new_manual_on = (getValue(incomingData, ',', 10).toInt() == 1);
            bool new_auto_on = (getValue(incomingData, ',', 11).toInt() == 1);

            // CASE 1: MANUAL MODE ENGAGED
            if (new_manual_on) {
                if (!MANUAL_ON) {
                   // Serial2.println("MANUAL MODE ENGAGED - Halting Autonomous.");
                }
                MANUAL_ON = true;
                AUTONOMOUS_ON = false;
                current_state = IDLE;

                // // Reset timers whenever a mode is engaged
                // resetTimers();

                // Stop all motors immediately
                x_axis.setSpeed(0);
                y_axis.setSpeed(0);
                z_axis.setSpeed(0);
                
                // Parse FULL_SPEED (Field 4) as the MAX manual speed
                FULL_SPEED = getValue(incomingData, ',', 4).toInt(); 

                // --- PARSE AS FLOATS ---
                // Parse fields 0, 1, 2 as MANUAL DIRECTION/SPEED COMMANDS
                manual_x_value = getValue(incomingData, ',', 0).toFloat();
                manual_y_value = getValue(incomingData, ',', 1).toFloat();
                manual_z_value = getValue(incomingData, ',', 2).toFloat();

                // Serial2.print("Manual CMDs");
                // Serial2.print(" | MaxSpeed:"); Serial2.println(FULL_SPEED);
            } 
            
            // CASE 2: AUTONOMOUS MODE ENGAGED
            else if (new_auto_on) {
                if (!AUTONOMOUS_ON) {
                    // Serial2.println("AUTONOMOUS MODE ENGAGED");
                }
                AUTONOMOUS_ON = true;
                MANUAL_ON = false;
                
                // Reset manual commands to 0
                manual_x_value = 0.0;
                manual_y_value = 0.0;
                manual_z_value = 0.0;

                // // Reset timers whenever a mode is engaged
                // resetTimers();

                XAXIS_SIZE = getValue(incomingData, ',', 0).toInt();
                YAXIS_SIZE = getValue(incomingData, ',', 1).toInt();
                ZAXIS_SIZE = getValue(incomingData, ',', 2).toInt();
                FULL_SPEED = getValue(incomingData, ',', 4).toInt(); 
                SLOW_SPEED = getValue(incomingData, ',', 5).toInt(); 
                BRAKE_DISTANCE = getValue(incomingData, ',', 6).toInt(); // what is this one for?
                XAXIS_DIST = -1*getValue(incomingData, ',', 7).toInt(); 
                YAXIS_DIST = getValue(incomingData, ',', 8).toInt(); 
                ZAXIS_DIST = -1*getValue(incomingData, ',', 9).toInt(); 

                // calculate these once, as they are used twice. long, not int (2026-10-10, review R-2 / X-19): size x
                // distance past 32767 counts wrapped in the Mega's 16-bit int and the axis ran the other way.
                long x_steps = XAXIS_SIZE*XAXIS_DIST;
                long y_steps = YAXIS_SIZE*YAXIS_DIST;
                long z_steps = ZAXIS_SIZE*ZAXIS_DIST;

                // Serial2.print("New Targets -> X: "); Serial2.print(x_steps);
                // Serial2.print(" | Y: "); Serial2.print(y_steps);
                // Serial2.print(" | Z: "); Serial2.println(z_steps);

                // calculate speed independent of step size
                double direction_size = sqrt(pow(XAXIS_DIST, 2) + pow(YAXIS_DIST, 2) + pow(ZAXIS_DIST,2));

                long x_speed;
                long y_speed;
                long z_speed;

                if (direction_size > 0)
                {
                    x_speed = (FULL_SPEED * XAXIS_DIST) / direction_size;
                    y_speed = (FULL_SPEED * YAXIS_DIST) / direction_size;
                    z_speed = (FULL_SPEED * ZAXIS_DIST) / direction_size;
                } else
                {
                    x_speed = 0;
                    y_speed = 0;
                    z_speed = 0;
                }
                
                // set target speed and position
                x_axis.move(x_steps);
                y_axis.move(y_steps);
                z_axis.move(z_steps);
                x_axis.setSpeed(x_speed);
                y_axis.setSpeed(y_speed);
                z_axis.setSpeed(z_speed);
                
                ALL_AXES_DONE = false;
            }
            
            // CASE 3: A "STOP" command (neither mode is 1)
            else
            {
                // Unconditional (2026-10-04): a stop must also land during a
                // D-pad step, when MANUAL_ON and AUTONOMOUS_ON are both false.
                haltMotion();
            }
        }
    }
}

void parseHybridSerial() {
    while (Serial.available() > 0) {
        uint8_t peekChar = (uint8_t)Serial.peek();

        // --- OPTION A: MANUAL BINARY PACKET ---
        if (peekChar == 0xAA) {
            if (Serial.available() >= (int)BINARY_PACKET_SIZE) {   // (int): available() is an int; -Wsign-compare
                Serial.readBytes((char*)&incomingPacket, BINARY_PACKET_SIZE);
                if (incomingPacket.mode == 1 && !packetIsValid()) {
                    incomingPacket.mode = 255;     // ignored below
                    rejected_packets++;
                }
                if (incomingPacket.mode == 0 || incomingPacket.mode == 1) {
                    last_packet_ms = millis();
                }

                // Binary mode 1: Engage Manual
                if (incomingPacket.mode == 1) {
                    if (!MANUAL_ON && !DPAD_STEP) {
                        Serial2.println("MANUAL MODE ENGAGED - Halting Autonomous.");
                        AUTONOMOUS_ON = false;
                        MANUAL_ON = true;
                        current_state = IDLE;
                    }
                    FULL_SPEED = incomingPacket.manual_jog_speed;
                    manual_x_value = -1*incomingPacket.x_axisStatus;
                    manual_y_value = -1*incomingPacket.y_axisStatus;
                    manual_z_value = incomingPacket.z_axisStatus;
                    dpad_LR = incomingPacket.dpad_LR;
                    dpad_UD = incomingPacket.dpad_UD;
                    bumpers = incomingPacket.bumpers;
                    x_step_size = incomingPacket.x_stepSize;
                    y_step_size = incomingPacket.y_stepSize;
                    z_step_size = incomingPacket.z_stepSize;
                }

                // Binary Mode 0: Explicit Stop
                else if (incomingPacket.mode == 0) {
                    handleAllStop();
                }
            } else {
                break;
            }
        }

        // ---OPTION B: EXPLICIT DISABLE (idempotent — safe even if already disabled)
        else if (peekChar == 0x64) { // 'd'
            Serial.read();
            disableDrivers();
        }

        // ---OPTION B2: EXPLICIT ENABLE (idempotent — safe even if already enabled)
        else if (peekChar == 0x65) { // 'e'
            Serial.read();

            // R2-1: after a failed #RES revert the drivers may disagree. Rewrite microstepMode to all three and read
            // each back first; only when all three agree does the flag clear and the enable go ahead. Otherwise stay
            // disabled (EN high, TOFF 0, system_enabled false): no reply, the host reads suspect=1 from #RES.
            if (!system_enabled && res_suspect) {
                setDriverMicrosteps(xUART, microstepMode);
                setDriverMicrosteps(yUART, microstepMode);
                setDriverMicrosteps(zUART, microstepMode);
                if (driverHasMicrosteps(xUART, microstepMode) && driverHasMicrosteps(yUART, microstepMode)
                    && driverHasMicrosteps(zUART, microstepMode)) {
                    res_suspect = false;
                } else {
                    disableDrivers();
                }
            }

            if (!system_enabled && !res_suspect) {
                system_enabled = true;

                // Safety: Force speeds to 0 before power-up so they don't jump instantly
                x_axis.setSpeed(0);
                y_axis.setSpeed(0);
                z_axis.setSpeed(0);

                xUART.toff(4);
                yUART.toff(4);
                zUART.toff(4);
                driversOutputsOn();
                // Was delay(30): the blocking stall dropped incoming bytes.
                // Motion is held for the same window, non-blocking.
                enable_settle_until = millis() + ENABLE_SETTLE_MS;
                settling = true;
            }
        }

        else if (peekChar == 0x73) { // --- OPTION C: IDENTITY QUERY ---
            Serial.read();
            Serial.println("DEV: c caps=res");   // caps=res (2026-10-23): this board answers #RES
        }

        // --- OPTION C2: A '#' LINE (cap res: #RES, the step resolution) ---
        else if (peekChar == '#') {
            parseExtLine();
        }
        
        // --- OPTION D: AUTONOMOUS TEXT STRING ---
        else if (peekChar == '-' || (peekChar >= '0' && peekChar <= '9')) {
            parseSerialAuto(); // Uses original text string logic
        }

        // clear buffer
        else {
            Serial.read();
        }
    }
}

void handleAllStop() {
    // Unconditional, and cancels a D-pad step (2026-10-04).
    haltMotion();
}

// IMPLEMENT DEBUG INFO HERE

// Autonomous control handler
void runAutoMode() 
{
    x_axis.runSpeedToPosition();
    y_axis.runSpeedToPosition();
    z_axis.runSpeedToPosition();

    if (x_axis.distanceToGo() == 0 && y_axis.distanceToGo() == 0 && z_axis.distanceToGo() == 0)
    {
        if (!ALL_AXES_DONE) {
            // Serial2.println("------------------------------------------");
            // Serial2.println("SEQUENCE COMPLETE. All Axes Halted.");
            // Serial2.println("------------------------------------------");

            x_axis.setSpeed(0);
            y_axis.setSpeed(0);
            z_axis.setSpeed(0);

            current_state = IDLE;
            ALL_AXES_DONE = true;
        }
    }
}

// DPad step handler
void runDpadStep()
{
    x_axis.runSpeedToPosition();
    y_axis.runSpeedToPosition();
    z_axis.runSpeedToPosition();
    if (x_axis.distanceToGo() == 0 && y_axis.distanceToGo() == 0 && z_axis.distanceToGo() == 0)
    {
        // Serial2.println("------------------------------------------");
        // Serial2.println("SEQUENCE COMPLETE. All Axes Halted.");
        // Serial2.println("------------------------------------------");

        x_axis.setSpeed(0);
        y_axis.setSpeed(0);
        z_axis.setSpeed(0);

        MANUAL_ON = true;
        DPAD_STEP = false;
    }
}


// Manual control handler
void runManualMode() 
{   
    // Apply controller deadzone
    if ((sqrt(pow(manual_x_value,2)+pow(manual_y_value,2)+pow(manual_z_value,2))) > MANUAL_DEAD_ZONE)
    {
      // run x
      x_axis.setSpeed(manual_x_value*FULL_SPEED);
      x_axis.runSpeed();

      // run y
      y_axis.setSpeed(manual_y_value*FULL_SPEED);
      y_axis.runSpeed();
      
      // run z
      z_axis.setSpeed(manual_z_value*FULL_SPEED);
      z_axis.runSpeed();
    }
    else // move to nearest step size increment before stopping -- may create unintended movement (direction) for user
    {
        if (x_axis.currentPosition() % x_step_size == 0)
        {
            x_axis.setSpeed(0);
        }
        if (y_axis.currentPosition() % y_step_size == 0)
        {
            y_axis.setSpeed(0);
        }
        if (z_axis.currentPosition() % z_step_size == 0)
        {
            z_axis.setSpeed(0);
        }
        x_axis.runSpeed();
        y_axis.runSpeed();
        z_axis.runSpeed();
    }
    if (bumpers || dpad_LR || dpad_UD) {
        DPAD_STEP = true;
        MANUAL_ON = false;
        x_axis.move(dpad_LR*x_step_size);
        y_axis.move(dpad_UD*y_step_size);
        z_axis.move(bumpers*z_step_size);
        x_axis.setSpeed(FULL_SPEED);
        y_axis.setSpeed(FULL_SPEED);
        z_axis.setSpeed(FULL_SPEED);
        
    }
}

void setup() {
  // A watchdog reset leaves the watchdog running: clear it before anything
  // slow, or setup() itself would be reset over and over.
  MCUSR = 0;
  wdt_disable();
  // configure serial
  Serial.begin(500000);

  Serial.setTimeout(2);
  // Serial2.begin(230400);
  // Serial2.println("\nStarting 3-Axis Controller. Waiting for Python command...");

  // configure pins
  pinMode(xEN, OUTPUT);
  pinMode(yEN, OUTPUT);
  pinMode(zEN, OUTPUT);

  // Boot with the driver outputs off (2026-10-04); 'e' turns them on.
  driversOutputsOff();

  Serial1.begin(115200);
  Serial2.begin(115200);
  Serial3.begin(115200);

  // microstepMode (file scope, above) boots at 2: default for step size considerations is 2 for 1/2 microsteps, which
  // should be smallest unit of reliable distance. #RES changes it at run time.

  // configure drivers

  xUART.begin();    
  xUART.I_scale_analog(false);     
  xUART.toff(0);         
  xUART.rms_current(600);    // can increase this to 700 if we want even more torque for, say, more accurate microsteps
  xUART.microsteps(microstepMode);      
  xUART.intpol(true);     
  xUART.pwm_autoscale(true);
  xUART.en_spreadCycle(false);

  yUART.begin();        
  yUART.I_scale_analog(false);    
  yUART.toff(0);         
  yUART.rms_current(600);    
  yUART.microsteps(microstepMode);      
  yUART.intpol(true);     
  yUART.pwm_autoscale(true);
  yUART.en_spreadCycle(false);

  zUART.begin();       
  zUART.I_scale_analog(false);    
  zUART.toff(0);        
  zUART.rms_current(400);    
  zUART.microsteps(microstepMode);      
  zUART.intpol(true);     
  zUART.pwm_autoscale(true);
  zUART.en_spreadCycle(false);

  x_axis.setCurrentPosition(xUART.test_connection());
  y_axis.setCurrentPosition(yUART.test_connection());
  z_axis.setCurrentPosition(zUART.test_connection());

  if (microstepMode != 0)
  {
    x_axis.setMaxSpeed(1600*microstepMode/2);
    y_axis.setMaxSpeed(1600*microstepMode/2);
    z_axis.setMaxSpeed(1600*microstepMode/2);
  } else
  {
    x_axis.setMaxSpeed(800);
    y_axis.setMaxSpeed(800);
    z_axis.setMaxSpeed(800);
  }

  delay(5); // TODO: don't know why this is here but might be important so I'm keeping it

  wdt_last_millis = millis();
  wdt_enable(WDTO_1S);
}

// Pet the watchdog only while the board is demonstrably healthy: millis()
// advancing (timer interrupts alive) and the host UART receiver, transmitter
// and RX interrupt still enabled. If either fails, the host can no longer be
// heard: cut the motors here and let the watchdog reset the board.
void watchdogCheck() {
  const uint8_t uart_ok = _BV(RXEN0) | _BV(TXEN0) | _BV(RXCIE0);
  if ((UCSR0B & uart_ok) != uart_ok) {
    haltMotion();
    driversOutputsOff();
    return;   // no pet: reset within ~1 s
  }
  unsigned long m = millis();
  if (m != wdt_last_millis) {
    wdt_last_millis = m;
    wdt_reset();
  }
}

// Jog dead-man: manual motion (and a D-pad step) needs a packet at least every
// JOG_TIMEOUT_MS. Stops the motion, keeps the coils holding, stays in manual
// so the next packet resumes from neutral.
void jogDeadman() {
  if (!(MANUAL_ON || DPAD_STEP)) return;
  if (millis() - last_packet_ms <= JOG_TIMEOUT_MS) return;
  manual_x_value = 0.0; manual_y_value = 0.0; manual_z_value = 0.0;
  dpad_LR = 0; dpad_UD = 0; bumpers = 0;
  DPAD_STEP = false;
  MANUAL_ON = true;
  holdPosition();
}

void status_update_print_serial() 
{
    unsigned long current_millis = millis();
    if (current_millis - previous_print_millis >= PRINT_INTERVAL) 
    {
        if (Serial.availableForWrite() < 35) {
            return;
        }
        long current_x_count, current_y_count, current_z_count;
        
        current_x_count = x_axis.currentPosition(); 
        current_y_count = y_axis.currentPosition();
        current_z_count = z_axis.currentPosition();
        
        // Compute total absolute position = folded absolute + current encoder delta

        // Send POS: to Python (always, for GUI position display)
        Serial.print("POS:");
        Serial.print(current_x_count);
        Serial.print(",");
        Serial.print(current_y_count);
        Serial.print(",");
        Serial.println(current_z_count);

        // // Debug output to Serial2 (only during active modes)
        // if (AUTONOMOUS_ON || ON)
        // {
        //     if (AUTONOMOUS_ON)
        //     {
        //         Serial2.print("State: ");
        //         Serial2.print(current_state);
        //         Serial2.print(" | EncX:"); Serial2.print(current_x_count);
        //         Serial2.print(" | EncY:"); Serial2.print(current_y_count);
        //         Serial2.print(" | EncZ:"); Serial2.print(current_z_count);
        //     }
        //     Serial2.print(" | AbsX:"); Serial2.print(total_x);
        //     Serial2.print(" | AbsY:"); Serial2.print(total_y);
        //     Serial2.print(" | AbsZ:"); Serial2.println(total_z);
        // }

        previous_print_millis = current_millis;
    }
}

void loop() {
  // put your main code here, to run repeatedly:
  // Change direction once the motor reaches target position
  // Move the motor one step
  watchdogCheck();
  parseHybridSerial();
  jogDeadman();

  if (settling && (long)(millis() - enable_settle_until) >= 0) settling = false;

  if (!settling) {
    if (MANUAL_ON) runManualMode();

    if (AUTONOMOUS_ON) runAutoMode();

    if (DPAD_STEP) runDpadStep();
  }

  status_update_print_serial();
}