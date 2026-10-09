// limits_diag: raw level check of the stage sensor inputs, for the validator bench (Teensy 3.5).
// Motion is impossible: EN (pin 4) is held HIGH (TMC2209 outputs off), STEP/DIR held LOW, the UART is never started.
// Every 100 ms it reads Teensy pins 5 (LS1), 6 (LS2), 7 (Vo) and the neighbours 8, 9 three ways:
//   F = no pull (a floating pin reads at random), U = internal pull-up, D = internal pull-down.
// and names what the pin is attached to:
//   OPEN = U1 D0: nothing drives it (switch open, wire open, or phototransistor dark/blocked)
//   GND  = U0 D0: tied to ground (switch closed to GND, or phototransistor lit with the slot clear)
//   HIGH = U1 D1: something drives it high (5 V/Vcc on that wire, or the stage GND back-fed through the LED)
//   ODD  = U0 D1: should not happen; re-seat the wire
// '*' marks a line where any pin's state changed. Open the Serial Monitor (any baud). Re-flash stepper_validator after.

const uint8_t EN_PIN = 4, STEP_PIN = 2, DIR_PIN = 3;
const uint8_t PINS[] = {5, 6, 7, 8, 9};
const char *const NAMES[] = {"p5 LS1", "p6 LS2", "p7 Vo ", "p8 nbr", "p9 nbr"};
const uint8_t N = sizeof PINS;
uint8_t lastState[N];
uint32_t lines = 0;

static void setAll(uint8_t mode) { for (uint8_t i = 0; i < N; i++) pinMode(PINS[i], mode); }
static void readAll(uint8_t *out) {
  delayMicroseconds(500);                       // settle: ~33 kOhm into a couple of metres of cable is a few tens of us
  for (uint8_t i = 0; i < N; i++) out[i] = digitalRead(PINS[i]);
}
static uint8_t classify(uint8_t u, uint8_t d) { return u ? (d ? 2 : 0) : (d ? 3 : 1); }   // 0 OPEN 1 GND 2 HIGH 3 ODD
static const char *const STATE[] = {"OPEN", "GND ", "HIGH", "ODD "};

void setup() {
  pinMode(EN_PIN, OUTPUT); digitalWrite(EN_PIN, HIGH);   // driver outputs off, first
  pinMode(STEP_PIN, OUTPUT); digitalWrite(STEP_PIN, LOW);
  pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW);
  setAll(INPUT_PULLUP);
  for (uint8_t i = 0; i < N; i++) lastState[i] = 255;
  Serial.begin(115200);
  while (!Serial && millis() < 4000) {}
  Serial.println("limits_diag: EN held HIGH, no motion possible. OPEN=U1D0 GND=U0D0 HIGH=U1D1. '*' = a state changed.");
}

void loop() {
  static uint32_t next = 0;
  if ((int32_t)(millis() - next) < 0) return;
  next = millis() + 100;
  uint8_t f[N], u[N], d[N];
  setAll(INPUT);          readAll(f);
  setAll(INPUT_PULLDOWN); readAll(d);
  setAll(INPUT_PULLUP);   readAll(u);           // end on pull-up, as the validator firmware runs them
  bool changed = false;
  uint8_t st[N];
  for (uint8_t i = 0; i < N; i++) { st[i] = classify(u[i], d[i]); if (st[i] != lastState[i]) changed = true; lastState[i] = st[i]; }
  if (lines++ % 20 == 0) Serial.println("     t_ms   pin    F U D state | ...");
  Serial.print(changed ? '*' : ' ');
  char t[12]; snprintf(t, sizeof t, "%9lu", (unsigned long)millis()); Serial.print(t);
  for (uint8_t i = 0; i < N; i++) {
    Serial.print(i == 3 ? "  || " : "  | ");
    Serial.print(NAMES[i]); Serial.print(' ');
    Serial.print(f[i]); Serial.print(' '); Serial.print(u[i]); Serial.print(' '); Serial.print(d[i]); Serial.print(' ');
    Serial.print(STATE[st[i]]);
  }
  Serial.println();
}
