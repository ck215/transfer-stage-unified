#include <AccelStepper.h>
#include <TMCStepper.h>

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
int x_step_size = 16;
int y_step_size = 16;
int z_step_size = 16;

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

                // calculate these once, as they are used twice
                int x_steps = XAXIS_SIZE*XAXIS_DIST;
                int y_steps = YAXIS_SIZE*YAXIS_DIST;
                int z_steps = ZAXIS_SIZE*ZAXIS_DIST;

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
                 if (MANUAL_ON || AUTONOMOUS_ON) 
                 {
                    // Serial2.println("ALL MODES DISENGAGED. HALTING.");

                    // // Reset timers whenever a mode is engaged
                    // resetTimers();

                    MANUAL_ON = false;
                    AUTONOMOUS_ON = false;
                    current_state = IDLE;
                    manual_x_value = 0.0;
                    manual_y_value = 0.0;
                    manual_z_value = 0.0;
                    x_axis.setSpeed(0);
                    y_axis.setSpeed(0);
                    z_axis.setSpeed(0);
                }  
            }
        }
    }
}

void parseHybridSerial() {
    while (Serial.available() > 0) {
        uint8_t peekChar = (uint8_t)Serial.peek();

        // --- OPTION A: MANUAL BINARY PACKET ---
        if (peekChar == 0xAA) {
            if (Serial.available() >= BINARY_PACKET_SIZE) {
                Serial.readBytes((char*)&incomingPacket, BINARY_PACKET_SIZE);

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

        // ---OPTION B: TOGGLE ENABLE
        else if (peekChar == 0x74) {
            Serial.read();

            if (system_enabled) {
                system_enabled = false;
                xUART.toff(0);
                yUART.toff(0);
                zUART.toff(0);
            }
            else {
                system_enabled = true;
                
                // Safety: Force speeds to 0 before power-up so they don't jump instantly
                x_axis.setSpeed(0);
                y_axis.setSpeed(0);
                z_axis.setSpeed(0);

                xUART.toff(4);
                yUART.toff(4);
                zUART.toff(4);
                delay(30); // Gives drivers a clean 30ms window to power up cleanly
            }
        }

        else if (peekChar == 0x73) { // --- OPTION C: IDENTITY QUERY ---
            Serial.read();
            Serial.println("DEV: c");
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
    if (MANUAL_ON || AUTONOMOUS_ON) {
        Serial2.println("ALL MODES DISENGAGED. HALTING.");
        MANUAL_ON = false;
        AUTONOMOUS_ON = false;
        current_state = IDLE;
        manual_x_value = 0.0; manual_y_value = 0.0; manual_z_value = 0.0;
        x_axis.setSpeed(0);
        y_axis.setSpeed(0);
        z_axis.setSpeed(0);
    }
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
  // configure serial
  Serial.begin(500000);

  Serial.setTimeout(2);
  // Serial2.begin(230400);
  // Serial2.println("\nStarting 3-Axis Controller. Waiting for Python command...");

  // configure pins
  pinMode(xEN, OUTPUT);
  pinMode(yEN, OUTPUT);
  pinMode(zEN, OUTPUT);

  digitalWrite(xEN, LOW);
  digitalWrite(yEN, LOW);
  digitalWrite(zEN, LOW);

  Serial1.begin(115200);
  Serial2.begin(115200);
  Serial3.begin(115200);

  int microstepMode = 2; // default for step size considerations is 2 for 1/2 microsteps, which should be smallest unit of reliable distance

  // configure drivers

  xUART.begin();    
  xUART.I_scale_analog(false);     
  xUART.toff(0);         
  xUART.rms_current(600);    // can increase this to 700 if we want even more torque for, say, more accurate microsteps
  xUART.microsteps(microstepMode);      
  xUART.intpol(true);     
  yUART.pwm_autoscale(true);
  yUART.en_spreadCycle(false);

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
        long abs_x, abs_y, abs_z;
        
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
  parseHybridSerial();

  if (MANUAL_ON) runManualMode();

  if (AUTONOMOUS_ON) runAutoMode();

  if (DPAD_STEP) runDpadStep();

  status_update_print_serial();
}