// TMC2209 UART diagnostic, Teensy 3.5: each cycle tries single-wire (half-duplex on pin 1) then two-wire (TX1 pin 1, RX1 pin 0).
// The line idles 50 ms before the first request so the driver can sync. Never enables the driver.
const int EN_PIN = 4, STEP_PIN = 2, DIR_PIN = 3;

uint8_t crc8(const uint8_t *d, uint8_t n) {                 // TMC UART CRC (polynomial 0x07, LSB first)
  uint8_t crc = 0;
  for (uint8_t i = 0; i < n; i++) {
    uint8_t b = d[i];
    for (uint8_t j = 0; j < 8; j++) { if ((crc >> 7) ^ (b & 1)) crc = (crc << 1) ^ 0x07; else crc <<= 1; b >>= 1; }
  }
  return crc;
}

void probe(uint8_t addr) {                                  // read IOIN (0x06): its top byte is VERSION, 0x21 on a TMC2209
  uint8_t req[4] = {0x05, addr, 0x06, 0};
  req[3] = crc8(req, 3);
  while (Serial1.available()) Serial1.read();
  Serial1.write(req, 4);
  Serial1.flush();
  uint8_t buf[32]; uint8_t n = 0; uint32_t t0 = millis();
  while (millis() - t0 < 15 && n < sizeof buf) if (Serial1.available()) buf[n++] = Serial1.read();
  Serial.printf("addr %u: %u bytes:", addr, n);
  for (uint8_t i = 0; i < n; i++) Serial.printf(" %02X", buf[i]);
  for (uint8_t i = 0; i + 7 < n; i++)
    if (buf[i] == 0x05 && buf[i + 1] == 0xFF && buf[i + 2] == 0x06)
      Serial.printf("  -> REPLY version=0x%02X crc=%s", buf[i + 3], crc8(buf + i, 7) == buf[i + 7] ? "ok" : "BAD");
  Serial.println();
}

void setup() {
  pinMode(EN_PIN, OUTPUT); digitalWrite(EN_PIN, HIGH);      // outputs off
  pinMode(STEP_PIN, OUTPUT); digitalWrite(STEP_PIN, LOW);
  pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW);
  Serial.begin(115200);
}

void run(const char *name, uint16_t format) {
  Serial1.begin(115200, format);
  delay(50);                                                // idle HIGH long enough for the driver's UART to sync
  Serial.printf("=== %s\n", name);
  probe(0); probe(0); probe(1); probe(2); probe(3);
  Serial1.end();
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < 2500) return;
  last = millis();
  run("SINGLE-WIRE (half-duplex, pin 1)", SERIAL_8N1_HALF_DUPLEX);
  run("TWO-WIRE (TX pin 1, RX pin 0)", SERIAL_8N1);
  Serial.println("---");
}
