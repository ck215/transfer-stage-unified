#include <MAX6675.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// Use standard LiquidCrystal_I2C constructor (Address, Columns, Rows)
LiquidCrystal_I2C lcd(0x27, 20, 4);

int zcross = 2;
int pwmPin = 3;
int ktcSO = 8;
int ktcCS = 9;
int ktcCLK = 10;

MAX6675 ktc(ktcCS, ktcSO, ktcCLK);

float endpoint;                              // final temperature setpoint in deg C
float setpoint;                              // current temperature setpoint used to control ramp rate
float temp;                                  // current temperature
float error;                                 // difference between current temperature and setpoint temperature
float olderror;                              // the previous cycle error
float sum;                                   // sum of all previous error values
float kp;                                     // prop coef
float ki;                                     // int coef
float kd;                                       // diff coef
float diff;                                     // difference between current error and old error
float offset;                                   // constant offset for temperature
float propterm;                                 // p-term
float intterm;                                  // i term
float diffterm;                                 // d term
float newdelay;                                 // control value (sum of terms) determines modulation of next cycle
float spdelay;                                  // setpoint delay (ramp rate)
float timer;                                  // timer
float starttime;                              // start time of code

int counter;
int counter2;
int state;

// PROPOSED 2026-10-07 - NOT FLASHED, NEEDS BENCH REVIEW AND FLASHING.
// Host-gone watchdog. Until now whatever the last frame commanded persisted
// for as long as the board had power: a host that died, was killed, lost
// its USB cable or simply closed the port left the heater at its setpoint.
// The station's software close path cannot cover SIGKILL, a crash of the
// interpreter, a PC power loss or a pulled cable; the board must.
//
// "Host present" is the USB CDC DTR line: pyserial asserts DTR when it
// opens the port and Linux/Windows/macOS drop it when the port is closed or
// the process holding it dies (Teensy core: `Serial` is true only while
// DTR is set). So this needs NO new bytes on the wire and the protocol is
// unchanged: the station still sends the same frames, and nothing has to
// be sent periodically. After HOST_GONE_MS without a host the board does
// exactly what the heater-off frame does (endpoint 0, gains 0) and the
// LCD shows SP=0.
const unsigned long HOST_GONE_MS = 5000;     // owner's number to tune at the bench
unsigned long hostSeenAt = 0;

// PROPOSED 2026-11-24 - NOT FLASHED, NEEDS BENCH VALIDATION.
// (1) Integral anti-windup. 2026-11-24 16:01 the setpoint dropped 110 -> 80
// with the block at 113 C. While it cooled, `sum` piled up negative error
// (I term about -56, full scale is 8), so the output stayed below zero until
// the block was 10 C under the setpoint - and there the `problem` cut-off
// (error >= 10 -> heater off) took over, so it never heated again: 77 -> 48.5 C
// over 15 min at sp 80, until a new frame re-based the setpoint at 16:26.
// Replaying this loop's math on the logged readings reproduces it. Clamping
// the I term to the output's full scale makes the heater come back on at
// about 4 C under the setpoint, long before the cut-off.
const float I_TERM_MAX = 8.0;                  // newdelay's full scale (>= 8 = full heat)
// (2) Sensor faults. MAX6675::read() returns a status the sketch ignored; on
// a failed SPI read (0xFFFF) the library keeps the LAST temperature, so a
// dead sensor link read as a frozen temperature the PID kept acting on. Now
// SENSOR_FAULT_READS bad reads in a row cut the heater (as `problem`) and
// print one "ERR: ..." line per reading; the station's parser skips lines
// with fewer than three fields, so the reading frame itself is unchanged.
const int SENSOR_FAULT_READS = 3;              // ~1.7 s of bad reads
int badReads = 0;
boolean sensorFault = false;

// PROPOSED 2026-11-27 - NOT FLASHED, NEEDS BENCH VALIDATION.
// Owner, 2026-11-27: "the heater was left operating once the firmware
// crashed". Four independent cut-offs follow; each prints its own "ERR: ..."
// line (never a reading - the reading frame and the host-to-board bytes are
// unchanged).
//
// (3) Hardware watchdog (Kinetis WDOG, 1 kHz LPO clock). Serviced ONLY at the
// end of the temperature-read/PID/print block, so it proves the control loop
// itself ran - not merely that loop() is spinning. If that block does not
// complete for WDOG_TIMEOUT_MS the chip resets. Every pin returns to its
// reset state (disabled, high impedance), and startup_early_hook() below
// drives the heater pin LOW within the first microseconds of boot, before
// the core's ~300 ms USB start-up delay. WDOG_TIMEOUT_MS is 2000, not 1000:
// it is a crash catcher (the other cut-offs act within one cycle), and the
// block's worst case under faults that are not crashes must never reach it.
// Kick to kick (block start to block start, plus one block): normal ~577 ms;
// zero-cross lost 577 ms (millis() timer); lost-then-restored and flapping at
// most READ_FALLBACK_MS = 680 ms, because the block is due on EITHER 60
// pulses or the timer, so a pulse count frozen by a glitch never stretches
// it; plus at most one 8.3 ms half-cycle of PWM delay. One block is ~90 ms
// normally (the LCD: ~59 bytes x 6 I2C transactions); its worst cases are
// the LCD (see (6): ~0.13 s once its bus fails) and USB serial. Teensy's
// usb_serial waits up to 70 ms only when it needs a new 64-byte packet, and
// after one timeout returns at once until a packet frees: a host that holds
// the port and stops reading costs one 70 ms wait per block. The pathological
// host that frees one packet every ~70 ms could make about half of the
// block's ~24 write calls wait (every ERR line active): ~0.85 s. Worst
// interval: 680 + 8 + 130 + 850 = ~1.7 s - over 1000 ms, under 2000 ms.
// After the reset the board comes up
// as from power-on: endpoint 0, heater off, until the station sends a frame.
// "BOOT: reset=..." says why the board last reset; after a watchdog reset it
// is repeated every BOOT_REPEAT_READS readings, so a station that connects
// later still sees it.
const uint16_t WDOG_TIMEOUT_MS = 2000;         // see the interval budget above
const int BOOT_REPEAT_READS = 10;              // ~5.8 s
boolean resetByWatchdog = false;
const char *resetCause = "other";
int bootRepeat = 0;
// (4) Absolute over-temperature cut-off. Until now the only cut-offs were
// relative to the setpoint (error >= 10, temp - endpoint > 10), and endpoint
// was any float a frame asked for. At or above TEMP_MAX_C the heater is cut
// (as `problem`) until the block is OVER_TEMP_HYST_C below it. A frame whose
// endpoint is above TEMP_MAX_C is CLAMPED to TEMP_MAX_C (not refused: the
// rest of the frame - ramp, gains, offset - still applies, and the board's
// reported setpoint shows the clamped value); "ERR: endpoint clamped" repeats
// with each reading until a frame within the limit arrives.
const float TEMP_MAX_C = 260.0;                // owner, 2026-11-27
const float OVER_TEMP_HYST_C = 10.0;
boolean overTemp = false;
boolean endpointClamped = false;
// (5) Zero-cross loss. The temperature read, PID, LCD and serial output all
// ran every 60 zero-cross pulses, so with no pulses the board went silent and
// its LCD froze. Now a RISING edge on the zero-cross pin (interrupt, not the
// polled level - a pin stuck high has no edges) must arrive every ZC_LOST_MS.
// Without one the heater is held off, the read/print block runs on a millis()
// timer every READ_PERIOD_MS instead, and "ERR: zero-cross lost" goes out
// with each reading. Edges resuming restores normal control.
const unsigned long ZC_LOST_MS = 100;          // 60 Hz mains: an edge every 8.3 or 16.7 ms
const unsigned long READ_PERIOD_MS = 577;      // the measured zero-cross cadence (0.5766 s)
const unsigned long READ_FALLBACK_MS = 680;    // with pulses: a cap that never pre-empts the count
volatile unsigned long zcEdgeAt = 0;
boolean zcLost = false;
unsigned long lastReadAt = 0;
// (6) A faulty LCD bus must not stall the control loop. LiquidCrystal_I2C
// ignores Wire's errors, and Teensy Wire waits up to ~17 ms per transaction
// on a stuck bus (wait_idle: 16 ms, then a 9-clock recovery); the ~354
// transactions of one full update would then take ~6 s, a watchdog reset
// every block - the heater off for an LCD fault. Now each block first probes
// the LCD's address (one transaction: <= ~17 ms when stuck, microseconds
// when the backpack is unplugged and NACKs). A failed probe skips the LCD
// for that block; a probe that succeeds after a failure re-initialises the
// LCD (a replugged backpack comes up blank or garbled). During an update a
// character that takes over LCD_STEP_MAX_MS (normal ~1.5 ms; stuck ~6 x 17 =
// ~100 ms) abandons the rest of it, so a bus that fails mid-update costs at
// most ~0.13 s. LCD faults are not reported on the serial line.
const uint8_t LCD_ADDR = 0x27;
const unsigned long LCD_STEP_MAX_MS = 10;
boolean lcdUp = true;                          // set up in setup()
boolean lcdOk = true;                          // this block's update still going

void lcdText(uint8_t col, uint8_t row, const char *text) {
  if (!lcdOk) return;
  unsigned long t0 = millis();
  lcd.setCursor(col, row);
  if (millis() - t0 > LCD_STEP_MAX_MS) { lcdOk = false; lcdUp = false; return; }
  for (const char *c = text; *c; c++) {
    t0 = millis();
    lcd.write((uint8_t)*c);
    if (millis() - t0 > LCD_STEP_MAX_MS) { lcdOk = false; lcdUp = false; return; }
  }
}

void lcdValue(uint8_t col, uint8_t row, const char *label, float value, const char *unit) {
  char buf[32];
  char num[16];
  dtostrf(value, 0, 2, num);                   // as lcd.print(float): 2 decimals
  snprintf(buf, sizeof(buf), "%s%s%s", label, num, unit);
  lcdText(col, row, buf);
}

void lcdUpdate() {
  Wire.beginTransmission(LCD_ADDR);
  if (Wire.endTransmission() != 0) {           // NACK (unplugged) or bus timeout (stuck)
    lcdUp = false;
    return;
  }
  if (!lcdUp) {                                // back after a fault: start it afresh
    lcd.init();
    lcd.backlight();
    lcdUp = true;
  }
  lcdOk = true;
  lcdValue(0, 0, "SP=", endpoint, "C");
  lcdValue(11, 0, "CT=", temp, "C");
  lcdValue(4, 1, "RR =", spdelay, "s/C");
  lcdValue(1, 2, "Kp=", kp, "");
  lcdValue(10, 2, "Ki=", ki, "");
  lcdValue(6, 3, "Kd=", kd, "");
}

void zcEdge() {
  zcEdgeAt = millis();
}

// Runs from ResetHandler before .data/.bss are set up and before any clock but
// the core's: only constants and registers here. It replaces the core's weak
// default, so it must also do what that does (leave the WDOG disabled but
// updatable). Pin 3 is PTA12 on the Teensy 3.5 (core_pins.h, GPIOA_PDOR).
extern "C" void startup_early_hook(void) {
  WDOG_STCTRLH = WDOG_STCTRLH_ALLOWUPDATE;     // as the core's default hook
  SIM_SCGC5 |= SIM_SCGC5_PORTA;                // the port's clock, for its PCR
  pinMode(3, OUTPUT);                          // PDOR resets to 0: drives LOW
  digitalWriteFast(3, LOW);
}

void watchdogStart() {
  noInterrupts();
  WDOG_UNLOCK = WDOG_UNLOCK_SEQ1;
  WDOG_UNLOCK = WDOG_UNLOCK_SEQ2;
  __asm__ volatile ("nop");
  __asm__ volatile ("nop");
  WDOG_TOVALH = 0;
  WDOG_TOVALL = WDOG_TIMEOUT_MS;               // LPO 1 kHz, prescaler 1: 1 count = 1 ms
  WDOG_PRESC = 0;
  WDOG_STCTRLH = WDOG_STCTRLH_WDOGEN;          // LPO clock; ALLOWUPDATE cleared: locked until reset
  interrupts();
}

void watchdogKick() {
  noInterrupts();
  WDOG_REFRESH = 0xA602;
  WDOG_REFRESH = 0xB480;
  interrupts();
}

const byte numChars = 32;
char receivedChars[numChars];
boolean newData = false;
boolean problem = false;

//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

void setup() {
  pinMode(pwmPin, OUTPUT);                        // PROPOSED (3): heater off first (already low from startup_early_hook)
  digitalWrite(pwmPin, 0);
  uint8_t srs0 = RCM_SRS0;                        // PROPOSED (3): why the chip last reset
  resetByWatchdog = (srs0 & RCM_SRS0_WDOG) != 0;
  if (resetByWatchdog) resetCause = "watchdog";
  else if (srs0 & RCM_SRS0_POR) resetCause = "power-on";
  else if (srs0 & RCM_SRS0_PIN) resetCause = "pin";
  else if (srs0 & RCM_SRS0_LVD) resetCause = "low-voltage";
  Serial.begin(115200);                           // put your setup code here, to run once:
                                                // initialize serial communication at 115200 bits per second:
  delay(500);                                   // give the MAX a little time to settle

  ktc.begin();

  lcd.init();                                   // initialize lcd
  lcd.backlight();                              // turn on backlight

  pinMode(zcross, INPUT);                       // set pin modes
  pinMode(pwmPin, OUTPUT);

  sum = 0;
  olderror = 0;                                     // initialize values
  counter = 0;
  counter2 = 0;
  offset = 0;                                     // Temperature offset default = 0
  ktc.read();
  setpoint = ktc.getCelsius() + offset;          // initial setpoint
  endpoint = 0;                                   // final desired temperature, set to zero 
  spdelay = 5;                                      // set setpoint delay in seconds (amount of time it takes to increase setpoint)
  kp = 2.0;                                           // Proportion constant
  ki = 0.5;                                           // Diff constant
  kd = 0.1;                                           // Int constant
  starttime = millis();
  hostSeenAt = millis();                          // boot grace for the host-gone watchdog
  attachInterrupt(digitalPinToInterrupt(zcross), zcEdge, RISING);   // PROPOSED (5)
  zcEdgeAt = millis();                            // boot grace for zero-cross loss
  lastReadAt = millis();
  Serial.print("BOOT: reset=");                   // PROPOSED (3); usually unheard (no host yet)
  Serial.println(resetCause);
  watchdogStart();                                // PROPOSED (3): last, after the slow set-up
}

// PROPOSED (see HOST_GONE_MS): no host holding the port for HOST_GONE_MS ->
// the heater-off frame's effect, done by the board itself.
void hostGoneWatchdog() {
  if (Serial) {                                   // DTR set: a host has the port open
    hostSeenAt = millis();
    return;
  }
  if (endpoint != 0 && millis() - hostSeenAt > HOST_GONE_MS) {
    endpoint = 0;                                 // as <0,...,0,0,0,...>
    kp = 0;
    ki = 0;
    kd = 0;
    sum = 0;
    olderror = 0;
    setpoint = 0;
  }
}


//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

void loop(){
  int zcrossstate = digitalRead(zcross);
  
  recvWithStartEndMarkers();
  showNewData();
  hostGoneWatchdog();                             // PROPOSED, see HOST_GONE_MS

  unsigned long now = millis();                   // PROPOSED (5): see ZC_LOST_MS
  noInterrupts();
  unsigned long lastEdge = zcEdgeAt;
  interrupts();
  zcLost = (now - lastEdge > ZC_LOST_MS);
  if (zcLost) {
    digitalWrite(pwmPin, 0);                      // no mains timing: no heat
  }

  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%//PWM Modulator//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  if(zcrossstate == 1 && !zcLost){                                      // when ac current reaches zero, modulate the next half-wavelength. half-wavelength duration is 8300us 
    if(problem){
      digitalWrite(pwmPin, 0);                                               // PWM pin off: modulate for entire period
      delayMicroseconds(8300);
      state = -1;
    }
    else{
      if(newdelay >= 8){                                        // if newdelay (see Temperature Read section) is large don't modulate wave and allow maximum heating
        digitalWrite(pwmPin, 1);                                // turn on pwm pin (don't cut signal)
        delayMicroseconds(4150);                                // here you adjust value inside delay to determine amount of time PWM is on
        digitalWrite(pwmPin, 0);
        delayMicroseconds(4150);
        state = 1;
      }
      else if(newdelay <= 0 || temp-endpoint > 10){             // if newdelay is negative or temperature is way above setpoint cut the ac current and allow no heating 
        digitalWrite(pwmPin, 0);                                               // PWM pin off: modulate for entire period
        delayMicroseconds(8300);
        state = -1;
      }
      else{
        digitalWrite(pwmPin,1);                                 // if new delay is between 0 and 8
        delayMicroseconds(1000*(newdelay/2));                       // modulate signal by new delay
        digitalWrite(pwmPin, 0);
        delayMicroseconds(8300 - 1000*(newdelay/2));
        state = 0;
      }
    }
  counter = counter + 1;
  }

  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%//  Temperature Read  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  
  boolean readDue = counter >= 60                                   // PROPOSED (5): pulses OR the timer
      || now - lastReadAt >= (zcLost ? READ_PERIOD_MS : READ_FALLBACK_MS);
  if(readDue){                                            // every half-second (roughly - it is actually more like 0.5766s)
    lastReadAt = now;
    timer = (millis() - starttime)*(0.001);               // Timer (measure time)
    uint8_t ktcStatus = ktc.read();
    temp = ktc.getCelsius() + offset;                    // Read Temperature
    if (ktcStatus == STATUS_OK) {                        // PROPOSED (2): see SENSOR_FAULT_READS
      badReads = 0;
      sensorFault = false;
    } else if (++badReads >= SENSOR_FAULT_READS) {
      badReads = SENSOR_FAULT_READS;
      sensorFault = true;
    }
    if (temp >= TEMP_MAX_C) {                            // PROPOSED (4): see TEMP_MAX_C
      overTemp = true;
    } else if (temp < TEMP_MAX_C - OVER_TEMP_HYST_C) {
      overTemp = false;
    }

    counter2 = counter2 + 1;                                            // count every half-second
      if(counter2 >= 1.73425*spdelay && setpoint < endpoint){           // after the setpoint delay (ramp rate) and if setpoint is less than endpoint. (1.73425 is number of counts per second - almost 2)
        setpoint = setpoint + 1;                                        // increase setpoint (this allows you to control ramping rate)
        counter2 = 0;                                                   // reset half-second counter
      }
      else if(setpoint >= endpoint){                            // if setpoint is greater than endpoint
        setpoint = endpoint;                                    // dont increase setpoint past endpoint
        counter2 = 0;                                           // steady-state scenario
      }
      
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%// PID Calculation //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  
    error = setpoint - temp;                                    // error is difference between current setpoint and current temperature
    propterm = kp*error;                                        // p term - directly proportional to error
    
    sum = sum + error;                                          // add error over time
    if (ki > 0) {                                               // PROPOSED (1): anti-windup, see I_TERM_MAX
      float sumMax = I_TERM_MAX / (ki * 0.008333);
      if (sum > sumMax) sum = sumMax;
      if (sum < -sumMax) sum = -sumMax;
    }
    intterm = ki*sum*0.008333;                                  // i term - proportional to the sum * waveform period
    
    diff = (error - olderror)/0.008333;                         // difference between the current error and the previous error
    olderror = error;                                           // grab current error to be used as old error in next cycle
    diffterm = kd*diff;                                         // d term - proportional to this difference (this term tends to be large so we dont use it)
    
    newdelay = intterm + propterm + diffterm;                   // value that controls the heater behavior of the next cycle

    if(error >=10 || sensorFault || overTemp || zcLost){        // PROPOSED (2), (4), (5): each also cuts the heater
      problem = true;
    }
    else{
      problem = false;
    }
    
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%// LCD and Serial Print //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    lcdUpdate();                                        // PROPOSED (6): same text, fault-bounded

//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% PLOT THIS %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%//
  
    Serial.print(timer);                                // default print values: timer, temp, and setpoint
    Serial.print(" , ");
    Serial.print(temp);
    Serial.print(" , ");
    Serial.println(setpoint);
    if (sensorFault) {                                  // PROPOSED (2): its own line, never a reading
      Serial.print("ERR: thermocouple status 0x");
      Serial.println(ktcStatus, HEX);
    }
    if (overTemp) {                                     // PROPOSED (4)
      Serial.print("ERR: over-temperature ");
      Serial.print(temp);
      Serial.print(" C, limit ");
      Serial.print(TEMP_MAX_C);
      Serial.println(" C");
    }
    if (endpointClamped) {                              // PROPOSED (4)
      Serial.print("ERR: endpoint clamped to ");
      Serial.print(TEMP_MAX_C);
      Serial.println(" C");
    }
    if (zcLost) {                                       // PROPOSED (5)
      Serial.println("ERR: zero-cross lost");
    }
    if (resetByWatchdog && ++bootRepeat >= BOOT_REPEAT_READS) {   // PROPOSED (3)
      bootRepeat = 0;
      Serial.println("BOOT: reset=watchdog");
    }
    counter = 0;                                                                   // reset counter //
    watchdogKick();                                     // PROPOSED (3): the control loop completed
  }
}

//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%// Serial Read Stuff //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

void recvWithStartEndMarkers() {
    static boolean recvInProgress = false;
    static byte ndx = 0;
    char startMarker = '<';
    char endMarker = '>';
    char rc;
 
    while (Serial.available() > 0 && newData == false) {
        char peekChar = Serial.peek();
        if (peekChar == 0x73) {
          Serial.println("DEV: t");
          Serial.read();
          continue;
        }
        rc = Serial.read();
        if (recvInProgress == true) {
            if (rc != endMarker) {
                receivedChars[ndx] = rc;
                ndx++;
                if (ndx >= numChars) {
                    ndx = numChars - 1;
                }
            }
            else {
                receivedChars[ndx] = '\0'; // terminate the string
                recvInProgress = false;
                ndx = 0;
                newData = true;
                parseData();
            }
        }

        else if (rc == startMarker) {
            recvInProgress = true;
        }
    }
}

void showNewData() {
    if (newData == true) {
        ktc.read();
        setpoint = ktc.getCelsius() + offset;
        counter2 = 0;
        starttime = millis();
        newData = false;
        sum = 0;
        olderror = 0;
    }
}

void parseData() {                                                                                            // split the data into its parts
  char * strtokIndx; // this is used by strtok() as an index
  
  strtokIndx = strtok(receivedChars, ",");      // get the first part - the string
  endpoint = atof(strtokIndx); //         // convert this part to a float
  endpointClamped = !(endpoint <= TEMP_MAX_C);  // PROPOSED (4): also catches NaN
  if (endpointClamped) {
    endpoint = TEMP_MAX_C;
  }

  strtokIndx = strtok(NULL, ","); // this continues where the previous call left off
  spdelay = atof(strtokIndx);     // convert this part to a float
  
  strtokIndx = strtok(NULL, ","); 
  kp = atof(strtokIndx);     // convert this part to a float

  strtokIndx = strtok(NULL, ",");
  ki = atof(strtokIndx);

  strtokIndx = strtok(NULL, ",");
  kd = atof(strtokIndx);

  strtokIndx = strtok(NULL, ",");
  offset = atof(strtokIndx);
}
