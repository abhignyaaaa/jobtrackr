(function () {
  var el = document.getElementById("chart-data");
  if (!el || typeof Chart === "undefined") return;
  var data = JSON.parse(el.textContent);
  var css = getComputedStyle(document.documentElement);
  var text = css.getPropertyValue("--muted").trim() || "#5d6879";
  var grid = css.getPropertyValue("--border").trim() || "#e2e6ec";
  var accent = css.getPropertyValue("--accent").trim() || "#3b4fd8";
  Chart.defaults.color = text;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

  // One fixed colour per status, matching the badges.
  var statusColors = {
    "Saved": "#94a3b8", "Applied": "#3b4fd8", "Assessment": "#d08a10", "Interview": "#8a5cf6",
    "Offer Received": "#16a34a", "Rejected": "#dc2f45", "Withdrawn": "#64748b"
  };
  var total = data.status.counts.reduce(function (a, b) { return a + b; }, 0);

  new Chart(document.getElementById("statusChart"), {
    type: "bar",
    data: {
      labels: data.status.labels,
      datasets: [{ data: data.status.counts, backgroundColor: data.status.labels.map(function (s) { return statusColors[s]; }), borderRadius: 4 }]
    },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (c) {
        var pct = total ? Math.round(100 * c.raw / total) : 0; return c.raw + " (" + pct + "%)"; } } } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: grid } }, y: { grid: { display: false } } }
    }
  });

  new Chart(document.getElementById("activityChart"), {
    type: "bar",
    data: {
      labels: data.activity.labels,
      datasets: [{ label: "Applications", data: data.activity.counts, backgroundColor: accent, borderRadius: 4,
        maxBarThickness: 28 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { title: function (i) { return "Week of " + i[0].label; } } } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: grid } }, x: { grid: { display: false } } }
    }
  });
})();
