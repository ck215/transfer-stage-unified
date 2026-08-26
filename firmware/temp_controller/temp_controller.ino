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

const byte numChars = 32;
char receivedChars[numChars];
boolean newData = false;
boolean problem = false;

//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

void setup() {
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
}


//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

void loop(){
  int pwmPinstate = digitalRead(pwmPin);
  int zcrossstate = digitalRead(zcross);
  
  recvWithStartEndMarkers();
  showNewData();
  
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%//PWM Modulator//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  if(zcrossstate == 1){                                       // when ac current reaches zero, modulate the next half-wavelength. half-wavelength duration is 8300us 
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
  
  if(counter == 60){                                      // every half-second (roughly - it is actually more like 0.5766s)
    timer = (millis() - starttime)*(0.001);               // Timer (measure time)
    ktc.read();
    temp = ktc.getCelsius() + offset;                    // Read Temperature
    
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
    intterm = ki*sum*0.008333;                                  // i term - proportional to the sum * waveform period
    
    diff = (error - olderror)/0.008333;                         // difference between the current error and the previous error
    olderror = error;                                           // grab current error to be used as old error in next cycle
    diffterm = kd*diff;                                         // d term - proportional to this difference (this term tends to be large so we dont use it)
    
    newdelay = intterm + propterm + diffterm;                   // value that controls the heater behavior of the next cycle

    if(error >=10){
      problem = true;
    }
    else{
      problem = false;
    }
    
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
  //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%// LCD and Serial Print //%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    lcd.setCursor(0,0);
    lcd.print("SP=");
    lcd.print(endpoint);
    lcd.print("C");
    lcd.setCursor(11,0);
    lcd.print("CT=");
    lcd.print(temp);
    lcd.print("C");
    lcd.setCursor(4,1);
    lcd.print("RR =");
    lcd.print(spdelay);
    lcd.print("s/C");
    lcd.setCursor(1,2);
    lcd.print("Kp=");
    lcd.print(kp);
    lcd.setCursor(10,2);
    lcd.print("Ki=");
    lcd.print(ki);
    lcd.setCursor(6,3);
    lcd.print("Kd=");
    lcd.print(kd);

//%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% PLOT THIS %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%//
  
    Serial.print(timer);                                // default print values: timer, temp, and setpoint
    Serial.print(" , ");
    Serial.print(temp);
    Serial.print(" , ");
    Serial.println(setpoint);
    counter = 0;                                                                   // reset counter //
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
