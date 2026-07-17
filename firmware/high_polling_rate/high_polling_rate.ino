// Pinout Definition.     // Interrupt pins for encoders
// X AXIS
#define X_MOTOR_PWM 5 
#define X_MOTOR_IN1 10 
#define X_MOTOR_IN2 7  
#define ENCODER_CLK_X 2   // D2: INT0
#define ENCODER_DT_X 27

// Y AXIS
#define Y_MOTOR_PWM 6
#define Y_MOTOR_IN1 8 
#define Y_MOTOR_IN2 14
#define ENCODER_CLK_Y 20  // D20: INT5
#define ENCODER_DT_Y 31

// Z AXIS
#define Z_MOTOR_PWM 9
#define Z_MOTOR_IN1 35
#define Z_MOTOR_IN2 36
#define ENCODER_CLK_Z 3   // D3: INT1
#define ENCODER_DT_Z 4

// How frequently the status is printed
#define PRINT_INTERVAL 50

// DEAD ZONE for manual control
#define MANUAL_DEAD_ZONE 0.05

// MINIMUM PWN to overcome motor stall
#define MIN_STALL_PWM 25

// Serial buffer of manual commands
#define MANUAL_SERIAL_BUFFER_SIZE 128
char manualSerialBuffer[MANUAL_SERIAL_BUFFER_SIZE];
int manualBufferIndex = 0;

// Tuning variables for step size and speed controls, passed by python control GUI
long XAXIS_DIST = 0; 
long YAXIS_DIST = 0; 
long ZAXIS_DIST = 0; 

long XAXIS_SIZE = 1;
long YAXIS_SIZE = 1;
long ZAXIS_SIZE = 1;

int FULL_SPEED = 255;
int SLOW_SPEED = 10;
long BRAKE_DISTANCE = 0;
bool AUTONOMOUS_ON = true;
bool MANUAL_ON = false;

// State variables — relative encoder counts, reset to 0 at the start of each move
volatile long X_ENCODER_COUNT = 0;
volatile long Y_ENCODER_COUNT = 0;
volatile long Z_ENCODER_COUNT = 0;

// Absolute position from boot-time zero. Accumulated across all moves, never reset.
volatile long X_ABS_POS = 0;
volatile long Y_ABS_POS = 0;
volatile long Z_ABS_POS = 0;

// Holds the current manual command as a float (-1.0 to 1.0)
float manual_x_value = 0.0;
float manual_y_value = 0.0;
float manual_z_value = 0.0;

long lastInterruptTimeX = 0;
long lastInterruptTimeY = 0;
long lastInterruptTimeZ = 0;

// Possible states of the motors in autonomous mode
bool ALL_AXES_DONE = false; 
enum AxisState { 
  IDLE,
  MOVE_X,
  MOVE_Y,
  MOVE_Z,
  ALL_DONE
};

// Always start in idling state on initialization
AxisState current_state = IDLE;

// --- PID Structures, Instances, and Timers ---
struct AxisPID {
    double Kp = 1.5;         // Proportional gain multiplier
    double Ki = 0.5;         // Integral gain multiplier
    double Kd = 0.5;         // Derivative gain multiplier
    double integralActiveError = 0.0;
    double lastError = 0.0;
    double iLimit = 100.0;   // Prevents integral windup
};

// We can tune individually for each dimension!
AxisPID pidX;
AxisPID pidY;
AxisPID pidZ;

const double DT = 0.005;                       // 10ms loop time in seconds
const unsigned long PID_INTERVAL_MS = 5;       // 5ms threshold for millis() timer
unsigned long previous_pid_millis = 0;         // Tracks last PID execution

// Struct for packet format
struct __attribute__((packed)) ManualControlPacket {
    uint8_t start_marker;       // 0xAA
    uint8_t mode;               // Mode integer for the packet
    float x_axisStatus;         // X axis status
    float y_axisStatus;         // Y axis status
    float z_axisStatus;         // Z axis status
    float manual_jog_speed;     // Manual jog speed
    float dpad_left;            // D-pad left value
    float dpad_right;           // D-pad right value
    float dpad_up;              // D-pad up value
    float dpad_down;            // D-pad down value
};

const size_t BINARY_PACKET_SIZE = sizeof(ManualControlPacket);
ManualControlPacket incomingPacket;

// --- Active Braking Tuning ---
// How many milliseconds to apply full reverse power when the joystick centers
#define COUNTER_PULSE_MS 2
#define COUNTER_PULSE_PWM 80 // The power of the counter-kick

struct ManualAxisState {
    int last_dir = 0;              // 1 = moving positive, -1 = moving negative, 0 = stopped
    bool is_braking = false;       // Flag to indicate active counter-pulse is firing
    unsigned long brake_start = 0; // Timestamp for the pulse duration
};

ManualAxisState manX;
ManualAxisState manY;
ManualAxisState manZ;

#define EXPO_FACTOR 0.75

// --------------------------------------------------

// Folds current encoder counts into absolute position, then resets encoders to zero.
// Must be called at every mode transition, stop, or before starting a new relative move.
void fold_encoders_to_absolute()
{
    noInterrupts();
    X_ABS_POS += X_ENCODER_COUNT;
    Y_ABS_POS += Y_ENCODER_COUNT;
    Z_ABS_POS += Z_ENCODER_COUNT;
    X_ENCODER_COUNT = 0;
    Y_ENCODER_COUNT = 0;
    Z_ENCODER_COUNT = 0;
    interrupts();
}

// Helper functions to parse serial data

// Universal string parser
String getValue(String data, char separator, int index)
{
  int found = 0;
  int strIndex[] = {0, -1};
  int maxIndex = data.length() - 1;

  for (int i = 0; i <= maxIndex && found <= index; i++) {
    if (data.charAt(i) == separator || i == maxIndex) {
      found++;
      strIndex[0] = strIndex[1] + 1;
      strIndex[1] = (i == maxIndex) ? i + 1 : i;
    }
  }
  return found > index ? data.substring(strIndex[0], strIndex[1]) : "";
}

// Auton serial parser
void parseSerialAuto() 
{
    if (Serial.available() > 0) {
        
        String incomingData = Serial.readStringUntil('\n');
        incomingData.trim();

        if (incomingData.length() > 0) {

            // Skip echoed POS: lines that loop back through the pty
            if (incomingData.startsWith("POS:")) {
                return;
            }
            
            Serial2.print("Received Command: ");
            Serial2.println(incomingData);

            bool new_manual_on = (getValue(incomingData, ',', 10).toInt() == 1);
            bool new_auto_on = (getValue(incomingData, ',', 11).toInt() == 1);

            // CASE 1: MANUAL MODE ENGAGED
            if (new_manual_on) {
                if (!MANUAL_ON) {
                   Serial2.println("MANUAL MODE ENGAGED - Halting Autonomous.");
                }

                // Fold encoder counts before switching modes
                fold_encoders_to_absolute();

                MANUAL_ON = true;
                AUTONOMOUS_ON = false;
                current_state = IDLE;

                // Stop all motors immediately
                analogWrite(X_MOTOR_PWM, 0);
                analogWrite(Y_MOTOR_PWM, 0);
                analogWrite(Z_MOTOR_PWM, 0);
                
                // Parse FULL_SPEED (Field 4) as the MAX manual speed
                FULL_SPEED = getValue(incomingData, ',', 4).toInt(); 
                
                // --- PARSE AS FLOATS ---
                // Parse fields 0, 1, 2 as MANUAL DIRECTION/SPEED COMMANDS
                manual_x_value = getValue(incomingData, ',', 0).toFloat();
                manual_y_value = getValue(incomingData, ',', 1).toFloat();
                manual_z_value = getValue(incomingData, ',', 2).toFloat();

                Serial2.print("Manual CMDs");
                Serial2.print(" | MaxSpeed:"); Serial2.println(FULL_SPEED);
            } 
            
            // CASE 2: AUTONOMOUS MODE ENGAGED
            else if (new_auto_on) {
                if (!AUTONOMOUS_ON) {
                    Serial2.println("AUTONOMOUS MODE ENGAGED");
                }

                // Fold encoder counts before starting new autonomous move
                fold_encoders_to_absolute();

                AUTONOMOUS_ON = true;
                MANUAL_ON = false;
                
                // Reset manual commands to 0
                manual_x_value = 0.0;
                manual_y_value = 0.0;
                manual_z_value = 0.0;

                XAXIS_SIZE = getValue(incomingData, ',', 0).toInt();
                YAXIS_SIZE = getValue(incomingData, ',', 1).toInt();
                ZAXIS_SIZE = getValue(incomingData, ',', 2).toInt();
                FULL_SPEED = getValue(incomingData, ',', 4).toInt(); 
                SLOW_SPEED = getValue(incomingData, ',', 5).toInt(); 
                BRAKE_DISTANCE = getValue(incomingData, ',', 6).toInt(); 
                XAXIS_DIST = getValue(incomingData, ',', 7).toInt(); 
                YAXIS_DIST = getValue(incomingData, ',', 8).toInt(); 
                ZAXIS_DIST = getValue(incomingData, ',', 9).toInt(); 

                Serial2.print("New Targets -> X: "); Serial2.print(XAXIS_DIST * XAXIS_SIZE);
                Serial2.print(" | Y: "); Serial2.print(YAXIS_DIST * YAXIS_SIZE);
                Serial2.print(" | Z: "); Serial2.println(ZAXIS_DIST * ZAXIS_SIZE);

                current_state = MOVE_X;
                ALL_AXES_DONE = false;
            }
            
            // CASE 3: A "STOP" command (neither mode is 1)
            else 
            {
                 if (MANUAL_ON || AUTONOMOUS_ON) 
                 {
                    Serial2.println("ALL MODES DISENGAGED. HALTING.");

                    // Fold encoder counts before stopping
                    fold_encoders_to_absolute();
                 
                    MANUAL_ON = false;
                    AUTONOMOUS_ON = false;
                    current_state = IDLE;
                    manual_x_value = 0.0;
                    manual_y_value = 0.0;
                    manual_z_value = 0.0;
                    analogWrite(X_MOTOR_PWM, 0);
                    analogWrite(Y_MOTOR_PWM, 0);
                    analogWrite(Z_MOTOR_PWM, 0);
                }  
            }
        }
    }
}

// Manual serial parser
void parseHybridSerial() {
    // FIXED: Changed from 'if' to 'while' to instantly drain the RX buffer and prevent overflows
    while (Serial.available() > 0) {
        char peekChar = Serial.peek();

        // --- OPTION A: MANUAL BINARY PACKET ---
        if ((uint8_t)peekChar == 0xAA) {
            
            // FIXED: Only process if the entire binary packet has arrived in the buffer
            if (Serial.available() >= BINARY_PACKET_SIZE) {
                Serial.readBytes((char*)&incomingPacket, BINARY_PACKET_SIZE);

                // Binary Mode 1: Engage Manual
                if (incomingPacket.mode == 1) {
                    if (!MANUAL_ON) {
                        Serial2.println("MANUAL MODE ENGAGED - Halting Autonomous.");
                        fold_encoders_to_absolute();
                        AUTONOMOUS_ON = false;
                        MANUAL_ON = true;
                        current_state = IDLE;
                    }
                    FULL_SPEED = (int)incomingPacket.manual_jog_speed;
                    manual_x_value = incomingPacket.x_axisStatus;
                    manual_y_value = incomingPacket.y_axisStatus;
                    manual_z_value = -1*incomingPacket.z_axisStatus;
                }
                
                // Binary Mode 0: Explicit Stop
                else if (incomingPacket.mode == 0) {
                    handleAllStop();
                }
            } else {
                // FIXED: Partial binary packet. Break the loop so we don't stall the motors waiting for bytes.
                break; 
            }
        }
        
        // --- OPTION B: AUTONOMOUS TEXT STRING ---
        else {
            parseSerialAuto(); 
        }
    }
}

void handleAllStop() {
  if (MANUAL_ON || AUTONOMOUS_ON) {
    Serial2.println("ALL MODES DISENGAGED. HALTING.");
    fold_encoders_to_absolute();
    MANUAL_ON = false;
    AUTONOMOUS_ON = false;
    current_state = IDLE;
    manual_x_value = 0.0; manual_y_value = 0.0; manual_z_value = 0.0;
    
    // Hard brake to kill micron drift
    analogWrite(X_MOTOR_PWM, 255); digitalWrite(X_MOTOR_IN1, HIGH); digitalWrite(X_MOTOR_IN2, HIGH);
    analogWrite(Y_MOTOR_PWM, 255); digitalWrite(Y_MOTOR_IN1, HIGH); digitalWrite(Y_MOTOR_IN2, HIGH);
    analogWrite(Z_MOTOR_PWM, 255); digitalWrite(Z_MOTOR_IN1, HIGH); digitalWrite(Z_MOTOR_IN2, HIGH);

    // Clear counter braking state flags
    manX.last_dir = 0; manX.is_braking = false;
    manY.last_dir = 0; manY.is_braking = false;
    manZ.last_dir = 0; manZ.is_braking = false;
  }
}

// Rotary Encoder updaters
void updateEncoderX() { if (digitalRead(ENCODER_DT_X) == HIGH) { X_ENCODER_COUNT++; } else { X_ENCODER_COUNT--; } }

void updateEncoderY() { if (digitalRead(ENCODER_DT_Y) == HIGH) { Y_ENCODER_COUNT++; } else { Y_ENCODER_COUNT--; } }

void updateEncoderZ() { if (digitalRead(ENCODER_DT_Z) == HIGH) { Z_ENCODER_COUNT++; } else { Z_ENCODER_COUNT--; } }

// Global var allows for print timing every 100ms
unsigned long previous_print_millis = 0;

// Sends absolute position to Python over Serial AND debug info over Serial2.
// Called from main loop() so it runs in ALL modes (manual, autonomous, idle)
void status_update_print_serial() 
{
    unsigned long current_millis = millis();
    if (current_millis - previous_print_millis >= PRINT_INTERVAL) 
    {
        // FIXED: TX Buffer protection. Skip printing if the buffer is nearly full to prevent blocking!
        if (Serial.availableForWrite() < 35) {
            return; // Exit function without updating previous_print_millis, it will try again next loop.
        }

        long current_x_count, current_y_count, current_z_count;
        long abs_x, abs_y, abs_z;
        
        noInterrupts();
        current_x_count = X_ENCODER_COUNT; 
        current_y_count = Y_ENCODER_COUNT;
        current_z_count = Z_ENCODER_COUNT;
        abs_x = X_ABS_POS;
        abs_y = Y_ABS_POS;
        abs_z = Z_ABS_POS;
        interrupts();
        
        // Compute total absolute position = folded absolute + current encoder delta
        long total_x = abs_x + current_x_count;
        long total_y = abs_y + current_y_count;
        long total_z = abs_z + current_z_count;

        // Send POS: to Python (always, for GUI position display)
        Serial.print("POS:");
        Serial.print(total_x);
        Serial.print(",");
        Serial.print(total_y);
        Serial.print(",");
        Serial.println(total_z);

        // Debug output to Serial2 (only during active modes)
        if (AUTONOMOUS_ON || MANUAL_ON)
        {
            if (AUTONOMOUS_ON)
            {
                Serial2.print("State: ");
                Serial2.print(current_state);
                Serial2.print(" | EncX:"); Serial2.print(current_x_count);
                Serial2.print(" | EncY:"); Serial2.print(current_y_count);
                Serial2.print(" | EncZ:"); Serial2.print(current_z_count);
            }
            Serial2.print(" | AbsX:"); Serial2.print(total_x);
            Serial2.print(" | AbsY:"); Serial2.print(total_y);
            Serial2.print(" | AbsZ:"); Serial2.println(total_z);
        }

        previous_print_millis = current_millis;
    }
}

// Autonomous movement function
void driveAxisAuto(volatile long& current_count, 
              long target, 
              int pwm_pin, int in1_pin, int in2_pin,
              AxisPID& axisPID,
              AxisState next_state)
{
    long error = target - current_count;
    
    // Exact arrival check (Wipe PID history for clean future moves)
    if (abs(error)==0) 
    {
        analogWrite(pwm_pin, 0); 
        digitalWrite(in1_pin, LOW);
        digitalWrite(in2_pin, LOW);
        
        axisPID.integralActiveError = 0;
        axisPID.lastError = 0;
        
        current_state = next_state;
        return; 
    }

    // PID Math calculation
    double P = axisPID.Kp * error;
    
    axisPID.integralActiveError += error * DT;
    axisPID.integralActiveError = constrain(axisPID.integralActiveError, -axisPID.iLimit, axisPID.iLimit);
    double I = axisPID.Ki * axisPID.integralActiveError;
    
    double derivative = (error - axisPID.lastError) / DT;
    double D = axisPID.Kd * derivative;
    
    double rawPIDOutput = P + I + D;
    axisPID.lastError = error; 
    
    // Direction & Deadband Scaling (Minimum stall PWM = 25)
    double finalPWM = 0;
    
    if (rawPIDOutput > 0) 
    {
        digitalWrite(in1_pin, HIGH); 
        digitalWrite(in2_pin, LOW);
        finalPWM = MIN_STALL_PWM + (rawPIDOutput * (255.0 - MIN_STALL_PWM) / 255.0);
    } 
    else 
    {
        digitalWrite(in1_pin, LOW);
        digitalWrite(in2_pin, HIGH);
        finalPWM = -MIN_STALL_PWM + (rawPIDOutput * (255.0 - MIN_STALL_PWM) / 255.0);
    }
    
    // Bounds clamping
    int outputPWM = (int)abs(finalPWM);
    outputPWM = constrain(outputPWM, MIN_STALL_PWM, 255);
    
    analogWrite(pwm_pin, outputPWM);
}

// Autonomous control handler 
void runAutoMode() {
    switch (current_state) 
            {
                case MOVE_X:
                    if (XAXIS_DIST != 0) {driveAxisAuto(X_ENCODER_COUNT, (XAXIS_DIST * XAXIS_SIZE), X_MOTOR_PWM, X_MOTOR_IN1, X_MOTOR_IN2, pidX, MOVE_Y);}
                    else current_state = MOVE_Y;
                    break;
                case MOVE_Y:
                    if (YAXIS_DIST != 0) {driveAxisAuto(Y_ENCODER_COUNT, (YAXIS_DIST * YAXIS_SIZE), Y_MOTOR_PWM, Y_MOTOR_IN1, Y_MOTOR_IN2, pidY, MOVE_Z);}
                    else current_state = MOVE_Z;
                    break;
                case MOVE_Z:
                    if (ZAXIS_DIST != 0) {driveAxisAuto(Z_ENCODER_COUNT, (ZAXIS_DIST * ZAXIS_SIZE), Z_MOTOR_PWM, Z_MOTOR_IN1, Z_MOTOR_IN2, pidZ, ALL_DONE);}
                    else current_state = ALL_DONE;
                    break;
                case ALL_DONE:
                    if (!ALL_AXES_DONE) {
                        ALL_AXES_DONE = true;
                        
                        // Fold final encoder counts into absolute position on completion
                        fold_encoders_to_absolute();
                        
                        // Just in case, force a final update of the GUI position
                        previous_print_millis = millis() - PRINT_INTERVAL; 
                        status_update_print_serial();
                        
                        Serial2.println("------------------------------------------");
                        Serial2.println("SEQUENCE COMPLETE. All Axes Halted.");
                        Serial2.println("------------------------------------------");
                        current_state = IDLE;
                    }
                    break;
                case IDLE:
                    break;
            }
}

// Manual axis control function
void driveAxisManual(float axis_value, int pwm_pin, int in1_pin, int in2_pin, int max_speed, ManualAxisState& state)
{
    unsigned long current_time = millis();

    // --- DEAD ZONE & BRAKING HANDLING ---
    if (abs(axis_value) < MANUAL_DEAD_ZONE)
    {
        // 1. Did we JUST enter the dead zone from an active move?
        if (state.last_dir != 0 && !state.is_braking) {
            state.is_braking = true;
            state.brake_start = current_time;
        }

        // 2. Are we currently in the middle of a counter-pulse?
        if (state.is_braking) {
            if (current_time - state.brake_start < COUNTER_PULSE_MS) {
                // Fire the counter pulse in the OPPOSITE direction of travel
                if (state.last_dir == 1) { // Was moving forward
                    digitalWrite(in1_pin, LOW);
                    digitalWrite(in2_pin, HIGH);
                } else {                   // Was moving backward
                    digitalWrite(in1_pin, HIGH);
                    digitalWrite(in2_pin, LOW);
                }
                analogWrite(pwm_pin, COUNTER_PULSE_PWM);
                return; // Exit early to maintain the pulse
            } else {
                // Timer expired. Pulse complete.
                state.is_braking = false;
                state.last_dir = 0;
            }
        }

        // 3. Standard Hard EMF Brake (holds position once pulse is done)
        analogWrite(pwm_pin, 255);
        digitalWrite(in1_pin, HIGH);
        digitalWrite(in2_pin, HIGH);
        return;
    }

    // Cancel any active braking if the user pushes the joystick again mid-pulse
    state.is_braking = false; 
    
    // Blend the linear and cubic values based on your EXPO_FACTOR
    float cubic_value = axis_value * axis_value * axis_value;
    float scaled_axis = (EXPO_FACTOR * cubic_value) + ((1.0 - EXPO_FACTOR) * axis_value);
    int motor_speed = constrain((int)(max_speed * abs(scaled_axis)), 20, 255);

    if (axis_value > 0) {
        state.last_dir = 1;
        digitalWrite(in1_pin, HIGH);
        digitalWrite(in2_pin, LOW);
        analogWrite(pwm_pin, motor_speed);
    } 
    else if (axis_value < 0) {
        state.last_dir = -1;
        digitalWrite(in1_pin, LOW);
        digitalWrite(in2_pin, HIGH);
        analogWrite(pwm_pin, motor_speed);
    } 
}

// Manual control handler
void runManualMode() 
{
    int max_manual_speed = FULL_SPEED; 
    
    driveAxisManual(manual_x_value, X_MOTOR_PWM, X_MOTOR_IN1, X_MOTOR_IN2, max_manual_speed, manX);
    driveAxisManual(manual_y_value * -1, Y_MOTOR_PWM, Y_MOTOR_IN1, Y_MOTOR_IN2, max_manual_speed, manY);
    driveAxisManual(manual_z_value, Z_MOTOR_PWM, Z_MOTOR_IN1, Z_MOTOR_IN2, max_manual_speed, manZ);
}

// One time device setup
void setup() 
{
    Serial.begin(500000);
    Serial2.begin(500000);
    
    // FIXED: Prevent readStringUntil() and readBytes() from stalling the main loop!
    Serial.setTimeout(2); 
    Serial2.setTimeout(2); 
    
    Serial2.println("\nStarting 3-Axis Controller. Waiting for Python command...");

    pinMode(X_MOTOR_PWM, OUTPUT); pinMode(X_MOTOR_IN1, OUTPUT); pinMode(X_MOTOR_IN2, OUTPUT);
    pinMode(Y_MOTOR_PWM, OUTPUT); pinMode(Y_MOTOR_IN1, OUTPUT); pinMode(Y_MOTOR_IN2, OUTPUT);
    pinMode(Z_MOTOR_PWM, OUTPUT); pinMode(Z_MOTOR_IN1, OUTPUT); pinMode(Z_MOTOR_IN2, OUTPUT);

    pinMode(ENCODER_CLK_X, INPUT); pinMode(ENCODER_DT_X, INPUT);
    pinMode(ENCODER_CLK_Y, INPUT); pinMode(ENCODER_DT_Y, INPUT);
    pinMode(ENCODER_CLK_Z, INPUT); pinMode(ENCODER_DT_Z, INPUT); 

    digitalWrite(X_MOTOR_IN1, LOW); digitalWrite(X_MOTOR_IN2, LOW); analogWrite(X_MOTOR_PWM, 0);
    digitalWrite(Y_MOTOR_IN1, LOW); digitalWrite(Y_MOTOR_IN2, LOW); analogWrite(Y_MOTOR_PWM, 0);
    digitalWrite(Z_MOTOR_IN1, LOW); digitalWrite(Z_MOTOR_IN2, LOW); analogWrite(Z_MOTOR_PWM, 0);
    
    attachInterrupt(digitalPinToInterrupt(ENCODER_CLK_X), updateEncoderX, RISING);
    attachInterrupt(digitalPinToInterrupt(ENCODER_CLK_Y), updateEncoderY, RISING);
    attachInterrupt(digitalPinToInterrupt(ENCODER_CLK_Z), updateEncoderZ, RISING); 

    delay(5); 
}

// Main loop 
void loop() 
{
    parseHybridSerial();

    // Always parse inputs unthrottled
    if (MANUAL_ON)
    {
        runManualMode();
    }

    // Force PID loop to execute on exact intervals for accurate calculus
    unsigned long current_time = millis();
    if (current_time - previous_pid_millis >= PID_INTERVAL_MS) 
    {
        // FIXED: Strict addition to prevent PID integral calculation drift over time
        previous_pid_millis += PID_INTERVAL_MS; 
        
        if (AUTONOMOUS_ON)
        {
            runAutoMode();
        }
    }

    // Always report position regardless of mode (self-throttled to PRINT_INTERVAL)
    status_update_print_serial();
}