const express = require("express");
const fs = require("fs");
const crypto = require("crypto");
const { Pool } = require("pg");

const app = express();
app.use(express.json({ limit: "16kb" }));

const PORT = Number(process.env.PORT || 8080);

function readSecret(path, fallback = "") {
  try {
    return fs.readFileSync(path, "utf8").trim();
  } catch {
    return fallback;
  }
}

const API_KEY = readSecret("/run/secrets/api_key", process.env.API_KEY || "");

const pool = new Pool({
  host: process.env.DB_HOST || "postgres",
  port: Number(process.env.DB_PORT || 5432),
  database: process.env.DB_NAME || "sensor",
  user: process.env.DB_USER || "iot_write_user",
  password: readSecret(
    "/run/secrets/db_write_password",
    process.env.DB_PASSWORD || "",
  ),
});

function authenticateController(req, res, next) {
  const providedKey = req.header("x-api-key") || "";

  // timing-safe compare to prevent against timing attacks
  if (!API_KEY || providedKey.length !== API_KEY.length || !crypto.timingSafeEqual(Buffer.from(providedKey), Buffer.from(API_KEY))) {
    return res.status(401).json({ error: "unauthorized" });
  }

  next();
}

function validateSensorPayload(body) {
  if (!body || typeof body !== "object") {
    throw new Error("payload must be a JSON object");
  }

  const { sensor_id, temperature, humidity, timestamp } = body;

  if (!sensor_id || typeof sensor_id !== "string" || sensor_id.length > 64) {
    throw new Error("sensor_id must be a non-empty string up to 64 characters");
  }

  if (typeof temperature !== "number" || !Number.isFinite(temperature)) {
    throw new Error("temperature must be a number");
  }

  if (typeof humidity !== "number" || !Number.isFinite(humidity)) {
    throw new Error("humidity must be a number");
  }

  // Range corresponds the actual sensor range of the DHT22.
  if (temperature < -40 || temperature > 80) {
    throw new Error("temperature out of allowed range -40..80");
  }

  if (humidity < 0 || humidity > 100) {
    throw new Error("humidity out of allowed range 0..100");
  }

  const parsedTimestamp = new Date(timestamp);

  if (!timestamp || Number.isNaN(parsedTimestamp.getTime())) {
    throw new Error("timestamp must be a valid date");
  }

  const now = Date.now();
  const ageMs = now - parsedTimestamp.getTime();
  const futureMs = parsedTimestamp.getTime() - now;

  if (futureMs > 5 * 60 * 1000) {
    throw new Error("timestamp is too far in the future");
  }

  if (ageMs > 7 * 24 * 60 * 60 * 1000) {
    throw new Error("timestamp is older than 7 days");
  }

  return {
    sensor_id,
    temperature,
    humidity,
    timestamp: parsedTimestamp,
  };
}

async function insertSensorMeasurements({ sensor_id, temperature, humidity, timestamp }) {
  const query = `
    INSERT INTO sensor_data (sensor_id, value, unit, recorded_at)
    VALUES
      ($1, $2, $3, $4),
      ($5, $6, $7, $8)
    RETURNING id, sensor_id, value, unit, recorded_at
  `;

  const values = [
    `${sensor_id}_temperature`, temperature, "C", timestamp,
    `${sensor_id}_humidity`, humidity, "%", timestamp
  ];

  const result = await pool.query(query, values);
  return result.rows;
}

app.get("/health", (req, res) => {
  res.json({ status: "ok", service: "server.js" });
});

app.post(
  "/api/internal/sensordata",
  authenticateController,
  async (req, res) => {
    try {
      const sensorData = validateSensorPayload(req.body);
      const inserted = await insertSensorMeasurements(sensorData);
      res.status(201).json({
        status: "ok",
        inserted,
      });
    } catch (err) {
      //DB-Error as 500, validation and auth errors as 400
      if (err.message.includes("DB") || err.code){
        return res.status(500).json({ error: "internal server error" });
      }
      res.status(400).json({ error: err.message || "invalid payload" });
    }
  },
);

app.listen(PORT, "0.0.0.0", () => {
  console.log(`server.js listening on 0.0.0.0:${PORT}`);
});
