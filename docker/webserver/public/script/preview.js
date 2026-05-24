// Mockup preview script for development and design iteration without backend or auth setup.
document.getElementById("loading-state").style.display = "none";
document.getElementById("app-container").style.display = "block";
document.getElementById("username").textContent = "iotuser01";
document.getElementById("live-badge").style.display = "inline-flex";

const now = Date.now();
const mockData = Array.from({ length: 30 }, (_, i) => ({
  x: now - (30 - i) * 60_000,
  temp: 19 + Math.sin(i / 3) * 1.5 + Math.random() * 0.3,
  hum: 50 + Math.cos(i / 4) * 8 + Math.random() * 2,
}));

new Chart(document.getElementById("iotChart"), {
  type: "line",
  data: {
    datasets: [
      { label: "Temperatur (°C)", yAxisID: "yTemp",
        data: mockData.map(d => ({ x: d.x, y: d.temp })),
        borderColor: "#0d6efd", backgroundColor: "rgba(13,110,253,0.12)",
        borderWidth: 2, tension: 0.3, fill: true, pointRadius: 2 },
      { label: "Luftfeuchte (%)", yAxisID: "yHum",
        data: mockData.map(d => ({ x: d.x, y: d.hum })),
        borderColor: "#fd7e14", backgroundColor: "rgba(253,126,20,0.10)",
        borderWidth: 2, tension: 0.3, fill: true, pointRadius: 2 },
    ],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    scales: {
      x: { type: "time" },
      yTemp: { position: "left", title: { display: true, text: "Temperatur (°C)" } },
      yHum:  { position: "right", min: 0, max: 100, grid: { drawOnChartArea: false } },
    },
  },
});