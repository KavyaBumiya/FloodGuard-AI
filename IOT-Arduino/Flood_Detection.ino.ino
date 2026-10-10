#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Preferences.h>
#include "time.h"

// ============================================================
// OPTIONAL SECRETS
// ============================================================

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef SECRET_REGISTRATION_TOKEN
#define SECRET_REGISTRATION_TOKEN ""
#endif

#ifndef SECRET_DEVICE_TOKEN
#define SECRET_DEVICE_TOKEN ""
#endif


// ============================================================
// WIFI
// ============================================================

const char* WIFI_SSID     = "OnePlus5G";
const char* WIFI_PASSWORD = "12345678";


// ============================================================
// FLOODGUARD API
// ============================================================

String API_URL_REGISTER =
  "https://floodguard-backend.ashyplant-04472250.centralindia.azurecontainerapps.io/api/iot/devices/register";

String API_URL_TELEMETRY =
  "https://floodguard-backend.ashyplant-04472250.centralindia.azurecontainerapps.io/api/iot/telemetry";


// ============================================================
// NODE DETAILS
// ============================================================

String NODE_CODE = "FLOOD-ESP32-02";

String REGISTRATION_TOKEN = String(SECRET_REGISTRATION_TOKEN);
String DEVICE_TOKEN       = String(SECRET_DEVICE_TOKEN);


// ============================================================
// STATIC LOCATION
// ============================================================

const float NODE_LAT = 19.0713;
const float NODE_LNG = 72.8830;


// ============================================================
// SENSOR PINS
// ============================================================

const int SENSOR_AO_PIN = 34;       // Water-level sensor AO
const int SENSOR_DO_PIN = 27;       // Water-level sensor DO (backup)
const int RAINDROP_AO_PIN = 35;     // Raindrop module AO -> GPIO35
const int BUZZER_PIN    = 23;

// ============================================================
// RAINDROP MODULE CALIBRATION (DEMO ONLY)
// ============================================================
// Most FC-37/YL-83-style modules give a LOWER AO reading when wetter.
// First check Serial Monitor with the plate DRY and with a few drops.
// Adjust RAIN_DRY_RAW to your actual dry reading and tune the thresholds
// below if your module behaves differently. This is wetness-based demo
// logic, NOT a calibrated measurement of rainfall in mm/hr.
const int RAIN_DRY_RAW = 3500;
const int RAIN_WET_RAW = 1200;

// Two rainfall demo levels, plus dry/no-rain.
// A small change from dry = LOW RAIN; a much wetter plate = HIGH RAIN.
const float RAIN_DETECTED_PERCENT = 8.0;
const float HIGH_RAIN_PERCENT = 45.0;


// ============================================================
// SENSOR CALIBRATION
// ============================================================

const int DRY_RAW = 3150;
const int WET_RAW = 1550;

const float MAX_WATER_LEVEL_CM = 30.0;


// ============================================================
// WATER LEVEL THRESHOLDS
// ============================================================

// 0 - <20 cm  = NORMAL
// 20 - <25 cm = CRITICAL
// >=25 cm     = FLOOD ALERT

const float NORMAL_MAX_CM   = 20.0;
const float CRITICAL_MAX_CM = 25.0;


// ============================================================
// POND AREA
// ============================================================

const float POND_AREA_M2 = 200.0;


// ============================================================
// API INTERVAL
// ============================================================

const unsigned long POST_INTERVAL_MS = 1000;


// ============================================================
// TIMING
// ============================================================

unsigned long lastPostTime = 0;


// ============================================================
// WATER RISE CALCULATION
// ============================================================

unsigned long previousLevelTime = 0;

float previousWaterLevelCm = 0.0;

float estimatedRiseRate = 0.0;


// ============================================================
// EVENT ID
// ============================================================

unsigned long readingSeq = 0;


// ============================================================
// PREFERENCES
// ============================================================

Preferences prefs;


// ============================================================
// CURRENT STATUS
// ============================================================

String currentStatus = "NORMAL";


// ============================================================
// LOAD READING SEQUENCE
// ============================================================

void loadReadingSeq() {

  prefs.begin("floodguard", true);

  readingSeq = prefs.getULong(
    "reading_seq",
    0
  );

  prefs.end();
}


// ============================================================
// SAVE READING SEQUENCE
// ============================================================

void saveReadingSeq() {

  prefs.begin("floodguard", false);

  prefs.putULong(
    "reading_seq",
    readingSeq
  );

  prefs.end();
}


// ============================================================
// WIFI CONNECTION
// ============================================================

void connectWiFi() {

  Serial.println();
  Serial.println("Connecting to WiFi...");

  WiFi.begin(
    WIFI_SSID,
    WIFI_PASSWORD
  );

  unsigned long startTime = millis();

  while (
    WiFi.status() != WL_CONNECTED &&
    millis() - startTime < 20000
  ) {

    delay(500);

    Serial.print(".");
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {

    Serial.println("WiFi connected");

    Serial.print("IP Address: ");

    Serial.println(
      WiFi.localIP()
    );

  } else {

    Serial.println(
      "WiFi connection failed"
    );
  }
}


// ============================================================
// TIME SYNCHRONIZATION
// ============================================================

void syncTime() {

  configTime(
    0,
    0,
    "pool.ntp.org",
    "time.nist.gov"
  );

  Serial.println(
    "Synchronizing time..."
  );

  struct tm timeinfo;

  for (int i = 0; i < 20; i++) {

    if (getLocalTime(&timeinfo)) {

      Serial.println(
        "Time synchronized"
      );

      return;
    }

    delay(500);
  }

  Serial.println(
    "Time synchronization failed"
  );
}


// ============================================================
// GET UTC TIMESTAMP
// ============================================================

String getTimestamp() {

  struct tm timeinfo;

  if (!getLocalTime(&timeinfo)) {

    return "2026-01-01T00:00:00Z";
  }

  char buffer[30];

  strftime(
    buffer,
    sizeof(buffer),
    "%Y-%m-%dT%H:%M:%SZ",
    &timeinfo
  );

  return String(buffer);
}


// ============================================================
// READ FILTERED SENSOR
// ============================================================

int readFilteredRaw() {

  const int SAMPLE_COUNT = 20;

  int samples[SAMPLE_COUNT];


  for (
    int i = 0;
    i < SAMPLE_COUNT;
    i++
  ) {

    samples[i] =
      analogRead(SENSOR_AO_PIN);

    delay(5);
  }


  // Sort samples

  for (
    int i = 0;
    i < SAMPLE_COUNT - 1;
    i++
  ) {

    for (
      int j = i + 1;
      j < SAMPLE_COUNT;
      j++
    ) {

      if (samples[j] < samples[i]) {

        int temp = samples[i];

        samples[i] = samples[j];

        samples[j] = temp;
      }
    }
  }


  // Median

  return samples[SAMPLE_COUNT / 2];
}


// ============================================================
// READ RAINDROP MODULE AND ESTIMATE SURFACE WETNESS
// ============================================================

int readRainRaw() {
  const int SAMPLE_COUNT = 10;
  long total = 0;

  for (int i = 0; i < SAMPLE_COUNT; i++) {
    total += analogRead(RAINDROP_AO_PIN);
    delay(3);
  }

  return (int)(total / SAMPLE_COUNT);
}

float rainRawToWetnessPercent(int raw) {
  // Assumes AO decreases as the plate gets wetter.
  // Change RAIN_DRY_RAW / RAIN_WET_RAW after checking your readings.
  if (RAIN_DRY_RAW == RAIN_WET_RAW) return 0.0;

  float wetness = (float)(RAIN_DRY_RAW - raw) * 100.0 /
                  (float)(RAIN_DRY_RAW - RAIN_WET_RAW);

  if (wetness < 0.0) wetness = 0.0;
  if (wetness > 100.0) wetness = 100.0;
  return wetness;
}

String getRainStatus(float wetnessPercent, float waterLevelCm) {
  // The raindrop plate being dry always means NO RAIN, even if the
  // separate water-level sensor reports a high level.
  if (wetnessPercent < RAIN_DETECTED_PERCENT) {
    return "NO RAIN (DRY SENSOR)";
  }

  // If drops are detected, use the water-level thresholds to make the
  // demo labels progress with the flood status. These are not real
  // rainfall measurements.
  if (waterLevelCm >= CRITICAL_MAX_CM) {
    return "HIGH RAIN (SIMULATED)";
  }

  // A wet plate means drops have been detected. Below flood-alert level,
  // show moderate rain for the demo, including when only a few drops fall.
  return "MODERATE RAIN (SIMULATED)";
}

float estimateDemoRainfallMmHr(float wetnessPercent, float waterLevelCm) {
  // Required backend field only. These are demo codes, NOT actual mm/hr:
  // dry plate = 0, detected drops = 5, wet plate + flood alert = 10.
  if (wetnessPercent < RAIN_DETECTED_PERCENT) return 0.0;
  if (waterLevelCm >= CRITICAL_MAX_CM) return 10.0;
  return 5.0;
}


// ============================================================
// RAW VALUE → WATER LEVEL
// ============================================================

float rawToWaterLevel(int raw) {

  // IMPORTANT:
  // Dry = high RAW
  // Wet = low RAW
  // Therefore:
  // More water = higher water level

  float waterLevel =
    (float)(DRY_RAW - raw)
    * MAX_WATER_LEVEL_CM
    / (float)(DRY_RAW - WET_RAW);


  // Clamp

  if (waterLevel < 0.0) {

    waterLevel = 0.0;
  }

  if (
    waterLevel >
    MAX_WATER_LEVEL_CM
  ) {

    waterLevel =
      MAX_WATER_LEVEL_CM;
  }


  return waterLevel;
}


// ============================================================
// GET WATER STATUS
// ============================================================

String getWaterStatus(
  float waterLevelCm
) {

  // 0 - <20 cm
  if (waterLevelCm < NORMAL_MAX_CM) {

    return "NORMAL";
  }


  // 20 - <25 cm
  else if (
    waterLevelCm <
    CRITICAL_MAX_CM
  ) {

    return "CRITICAL";
  }


  // >=25 cm
  else {

    return "FLOOD ALERT";
  }
}


// ============================================================
// CALCULATE WATER RISE / INFLOW RATE
// ============================================================

float calculateRiseRate(
  float currentLevelCm
) {

  unsigned long currentTime =
    millis();


  if (previousLevelTime == 0) {

    previousLevelTime =
      currentTime;

    previousWaterLevelCm =
      currentLevelCm;

    return 0.0;
  }


  unsigned long elapsedMs =
    currentTime -
    previousLevelTime;


  if (elapsedMs < 1000) {

    return estimatedRiseRate;
  }


  float elapsedSeconds =
    elapsedMs / 1000.0;


  float levelRiseCm =
    currentLevelCm -
    previousWaterLevelCm;


  // Ignore tiny sensor fluctuations

  if (abs(levelRiseCm) < 0.05) {

    levelRiseCm = 0.0;
  }


  // Water level decreased

  if (levelRiseCm < 0.0) {

    levelRiseCm = 0.0;
  }


  float levelRiseM =
    levelRiseCm / 100.0;


  float volumeChangeM3 =
    levelRiseM *
    POND_AREA_M2;


  float rate =
    volumeChangeM3 /
    elapsedSeconds;


  previousWaterLevelCm =
    currentLevelCm;

  previousLevelTime =
    currentTime;

  estimatedRiseRate =
    rate;


  return rate;
}


// ============================================================
// BUZZER CONTROL
// ============================================================

void updateBuzzer(
  String status
) {

  unsigned long now =
    millis();


  // ==========================================================
  // NORMAL
  // ==========================================================

  if (status == "NORMAL") {

    digitalWrite(
      BUZZER_PIN,
      LOW
    );

    return;
  }


  // ==========================================================
  // CRITICAL
  //
  // ON 300 ms
  // OFF 300 ms
  // ON 300 ms
  // OFF 1400 ms
  // ==========================================================

  if (status == "CRITICAL") {

    unsigned long cycleTime =
      now % 2300;


    if (
      (cycleTime < 300) ||
      (
        cycleTime >= 600 &&
        cycleTime < 900
      )
    ) {

      digitalWrite(
        BUZZER_PIN,
        HIGH
      );

    } else {

      digitalWrite(
        BUZZER_PIN,
        LOW
      );
    }

    return;
  }


  // ==========================================================
  // FLOOD ALERT
  // Continuous buzzer
  // ==========================================================

  if (status == "FLOOD ALERT") {

    digitalWrite(
      BUZZER_PIN,
      HIGH
    );

    return;
  }
}


// ============================================================
// REGISTER DEVICE
// ============================================================

bool registerDevice() {

  if (
    WiFi.status() !=
    WL_CONNECTED
  ) {

    Serial.println(
      "WiFi not connected"
    );

    return false;
  }


  if (
    REGISTRATION_TOKEN.length() == 0
  ) {

    Serial.println(
      "Registration token missing"
    );

    return false;
  }


  WiFiClientSecure net;

  net.setInsecure();


  HTTPClient http;


  if (
    !http.begin(
      net,
      API_URL_REGISTER
    )
  ) {

    Serial.println(
      "HTTP registration begin failed"
    );

    return false;
  }


  http.addHeader(
    "Content-Type",
    "application/json"
  );


  http.addHeader(
    "Authorization",
    "Bearer " +
    REGISTRATION_TOKEN
  );


  String body =
    "{"
    "\"node_code\":\"" +
      NODE_CODE +
    "\","
    "\"name\":\"Flood Sensor " +
      NODE_CODE +
    "\","
    "\"node_type\":\"physical\","
    "\"role\":\"midstream\","
    "\"warning_threshold_cm\":20.0,"
    "\"lat\":" +
      String(NODE_LAT, 6) +
    ","
    "\"lng\":" +
      String(NODE_LNG, 6) +
    "}";


  Serial.println();
  Serial.println(
    "Registering device..."
  );

  Serial.println(body);


  int httpCode =
    http.POST(body);


  String response =
    http.getString();


  Serial.print(
    "Registration HTTP Code: "
  );

  Serial.println(httpCode);


  Serial.println(
    "Registration Response:"
  );

  Serial.println(response);


  if (
    httpCode < 200 ||
    httpCode >= 300
  ) {

    http.end();

    return false;
  }


  // ==========================================================
  // FIND TOKEN
  // ==========================================================

  int tokenStart =
    response.indexOf(
      "\"token\":\""
    );


  if (tokenStart >= 0) {

    tokenStart += 9;


    int tokenEnd =
      response.indexOf(
        "\"",
        tokenStart
      );


    if (
      tokenEnd >
      tokenStart
    ) {

      DEVICE_TOKEN =
        response.substring(
          tokenStart,
          tokenEnd
        );


      prefs.begin(
        "floodguard",
        false
      );


      prefs.putString(
        "dev_token",
        DEVICE_TOKEN
      );


      prefs.end();


      Serial.println(
        "Device token saved successfully."
      );


      http.end();

      return true;
    }
  }


  Serial.println(
    "Device token not found."
  );


  http.end();

  return false;
}


// ============================================================
// LOAD DEVICE TOKEN
// ============================================================

void loadDeviceToken() {

  if (
    DEVICE_TOKEN.length() > 0
  ) {

    return;
  }


  prefs.begin(
    "floodguard",
    true
  );


  DEVICE_TOKEN =
    prefs.getString(
      "dev_token",
      ""
    );


  prefs.end();


  if (
    DEVICE_TOKEN.length() > 0
  ) {

    Serial.println(
      "Saved device token loaded."
    );
  }
}


// ============================================================
// SEND TELEMETRY
// ============================================================

bool sendTelemetry(
  float waterLevelCm,
  float rainfallEstimateMmHr
) {

  if (
    WiFi.status() !=
    WL_CONNECTED
  ) {

    Serial.println(
      "Telemetry skipped: WiFi disconnected"
    );

    return false;
  }


  if (
    DEVICE_TOKEN.length() == 0
  ) {

    Serial.println(
      "Telemetry skipped: Device token missing"
    );

    return false;
  }


  WiFiClientSecure net;

  net.setInsecure();


  HTTPClient http;


  if (
    !http.begin(
      net,
      API_URL_TELEMETRY
    )
  ) {

    Serial.println(
      "Telemetry HTTP begin failed"
    );

    return false;
  }


  const char* headerKeys[] = {
    "Retry-After"
  };


  http.collectHeaders(
    headerKeys,
    1
  );


  http.addHeader(
    "Content-Type",
    "application/json"
  );


  http.addHeader(
    "Authorization",
    "Bearer " +
    DEVICE_TOKEN
  );


  // ==========================================================
  // EVENT ID
  // ==========================================================

  readingSeq++;

  saveReadingSeq();


  String eventId =
    NODE_CODE +
    "-" +
    String(readingSeq);


  String timestamp =
    getTimestamp();


  // ==========================================================
  // TELEMETRY BODY
  // ==========================================================

  String body =
    "{"
    "\"node_code\":\"" +
      NODE_CODE +
    "\","
    "\"water_level_cm\":" +
      String(
        waterLevelCm,
        2
      ) +
    ","
    "\"rainfall_mm_hr\":" +
      String(rainfallEstimateMmHr, 2) +
    ","
    "\"timestamp\":\"" +
      timestamp +
    "\","
    "\"event_id\":\"" +
      eventId +
    "\""
    "}";


  Serial.println();
  Serial.println(
    "Sending telemetry:"
  );

  Serial.println(body);


  int httpCode =
    http.POST(body);


  String response =
    http.getString();


  Serial.print(
    "Telemetry HTTP Code: "
  );

  Serial.println(httpCode);


  if (
    httpCode >= 200 &&
    httpCode < 300
  ) {

    Serial.println(
      "Telemetry sent successfully."
    );

    http.end();

    return true;
  }


  // ==========================================================
  // RATE LIMIT
  // ==========================================================

  if (httpCode == 429) {

    String retryAfter =
      http.header(
        "Retry-After"
      );


    long retrySeconds =
      retryAfter.toInt();


    if (retrySeconds < 1) {

      retrySeconds = 1;
    }


    Serial.print(
      "Rate limited. Retry after "
    );

    Serial.print(
      retrySeconds
    );

    Serial.println(
      " seconds."
    );

  } else {

    Serial.println(
      "Telemetry failed."
    );

    Serial.println(
      response
    );
  }


  http.end();

  return false;
}


// ============================================================
// SETUP
// ============================================================

void setup() {

  Serial.begin(115200);

  delay(1000);


  Serial.println();

  Serial.println(
    "===================================="
  );

  Serial.println(
    "     FLOODGUARD AI - ESP32 NODE"
  );

  Serial.println(
    "===================================="
  );


  // ==========================================================
  // PINS
  // ==========================================================

  pinMode(
    SENSOR_DO_PIN,
    INPUT
  );

  pinMode(RAINDROP_AO_PIN, INPUT);

  pinMode(
    BUZZER_PIN,
    OUTPUT
  );


  digitalWrite(
    BUZZER_PIN,
    LOW
  );


  // ==========================================================
  // ADC
  // ==========================================================

  analogReadResolution(12);


  // ==========================================================
  // PREFERENCES
  // ==========================================================

  loadReadingSeq();

  loadDeviceToken();


  // ==========================================================
  // WIFI
  // ==========================================================

  connectWiFi();


  // ==========================================================
  // TIME
  // ==========================================================

  if (
    WiFi.status() ==
    WL_CONNECTED
  ) {

    syncTime();
  }


  // ==========================================================
  // REGISTER DEVICE
  // ==========================================================

  if (
    DEVICE_TOKEN.length() == 0
  ) {

    Serial.println(
      "No saved device token."
    );

    registerDevice();
  }


  // ==========================================================
  // READY
  // ==========================================================

  Serial.println();

  Serial.println(
    "System ready."
  );


  Serial.println(
    "Thresholds:"
  );

  Serial.println(
    "0 - <20 cm  = NORMAL"
  );

  Serial.println(
    "20 - <25 cm = CRITICAL"
  );

  Serial.println(
    ">=25 cm     = FLOOD ALERT"
  );
  Serial.println("Rain status: dry plate = NO RAIN; drops = MODERATE; wet plate + flood alert = HIGH (demo).");
  Serial.println("API rainfall values are placeholders: 0=dry, 5=moderate, 10=high; NOT real mm/hr.");
  Serial.println("Calibrate RAIN_DRY_RAW / RAIN_WET_RAW from your module readings.");
}


// ============================================================
// LOOP
// ============================================================

void loop() {

  // ==========================================================
  // WIFI RECONNECT
  // ==========================================================

  if (
    WiFi.status() !=
    WL_CONNECTED
  ) {

    connectWiFi();
  }


  // ==========================================================
  // READ SENSOR
  // ==========================================================

  int rawValue =
    readFilteredRaw();


  // ==========================================================
  // WATER LEVEL
  // ==========================================================

  float waterLevelCm =
    rawToWaterLevel(
      rawValue
    );

  // Read raindrop module. This indicates plate wetness, not measured rainfall.
  int rainRawValue = readRainRaw();
  float rainWetnessPercent = rainRawToWetnessPercent(rainRawValue);
  String rainStatus = getRainStatus(rainWetnessPercent, waterLevelCm);
  float rainfallEstimateMmHr = estimateDemoRainfallMmHr(rainWetnessPercent, waterLevelCm);


  // ==========================================================
  // STATUS
  // ==========================================================

  currentStatus =
    getWaterStatus(
      waterLevelCm
    );


  // ==========================================================
  // RISE RATE
  // ==========================================================

  float riseRate =
    calculateRiseRate(
      waterLevelCm
    );


  // ==========================================================
  // BUZZER
  // ==========================================================

  updateBuzzer(
    currentStatus
  );


  // ==========================================================
  // SERIAL MONITOR
  // ==========================================================

  Serial.println();

  Serial.println(
    "------------------------------------"
  );


  Serial.print(
    "RAW = "
  );

  Serial.println(
    rawValue
  );


  Serial.print(
    "Water Level = "
  );

  Serial.print(
    waterLevelCm,
    2
  );

  Serial.println(
    " cm"
  );


  Serial.print(
    "Status = "
  );

  Serial.println(
    currentStatus
  );

  Serial.print("Raindrop AO RAW = ");
  Serial.println(rainRawValue);
  Serial.print("Raindrop Surface Wetness = ");
  Serial.print(rainWetnessPercent, 1);
  Serial.println(" % (surface wetness estimate)");
  Serial.print("Rain Status = ");
  Serial.println(rainStatus);
  Serial.print("Rainfall Field = ");
  Serial.print(rainfallEstimateMmHr, 2);
  Serial.println(" demo code (0=dry, 5=moderate, 10=high; NOT measured mm/hr)");


  Serial.print(
    "Estimated Rise/Inflow Rate = "
  );

  Serial.print(
    riseRate,
    6
  );

  Serial.println(
    " m3/s"
  );


  Serial.print(
    "WiFi = "
  );

  Serial.println(
    WiFi.status() ==
    WL_CONNECTED
      ? "CONNECTED"
      : "DISCONNECTED"
  );


  // ==========================================================
  // API EVERY 1 SECOND
  // ==========================================================

  if (
    WiFi.status() ==
      WL_CONNECTED &&
    millis() - lastPostTime >=
      POST_INTERVAL_MS
  ) {

    lastPostTime =
      millis();


    sendTelemetry(
      waterLevelCm,
      rainfallEstimateMmHr
    );
  }


  delay(100);
}