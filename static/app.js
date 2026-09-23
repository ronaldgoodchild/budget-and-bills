const state = {
  calYear: new Date().getFullYear(),
  calMonth: new Date().getMonth() + 1, // 1-12
};

function fmtMoney(n) {
  const sign = n < 0 ? "-" : "";
  return sign + "$" + Math.abs(n).toFixed(2);
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Locally-hosted logos for companies whose favicon doesn't come through
// cleanly from the icon service - drop a PNG in static/logos/ and add an
// entry here to override the auto-fetched one for that domain. Keys are
// matched loosely (case-insensitive, "www."/".com" optional) since manually
// typed domains in the bill edit form won't always be perfectly formatted.
const LOCAL_LOGO_OVERRIDES = {
  // "example.com": "/static/logos/example.png",
};

function billLogoHtml(domain) {
  if (!domain) return "";
  const key = String(domain).trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "");
  const src = LOCAL_LOGO_OVERRIDES[key] || `https://icons.duckduckgo.com/ip3/${encodeURIComponent(domain)}.ico`;
  return `<img class="bill-logo" src="${src}" alt="" onerror="this.style.display='none'">`;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed: ${res.status}`);
  }
  return res.json();
}

// --- Tabs -------------------------------------------------------------------

function initTabs() {
  const settingsTrigger = document.getElementById("settings-trigger");
  const settingsDropdown = document.getElementById("settings-dropdown-menu");
  const settingsSubTabs = ["import", "notifications", "settings"];

  document.querySelectorAll(".tab-btn:not(.nav-group-trigger)").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
      if (settingsSubTabs.includes(btn.dataset.tab)) {
        settingsTrigger.classList.add("active");
      }
      settingsDropdown.classList.remove("open");
      if (btn.dataset.tab === "dashboard") loadDashboard();
      if (btn.dataset.tab === "reports") loadReports();
      if (btn.dataset.tab === "billtrends") loadBillTrends();
      if (btn.dataset.tab === "calendar") loadCalendar();
      if (btn.dataset.tab === "bills") { loadBills(); loadDetected(); }
      if (btn.dataset.tab === "transactions") loadTransactions();
      if (btn.dataset.tab === "budgets") loadBudgets();
      if (btn.dataset.tab === "whatif") loadWhatIf();
      if (btn.dataset.tab === "debts") loadDebts();
      if (btn.dataset.tab === "notifications") loadNotificationSettings();
      if (btn.dataset.tab === "settings") loadSettings();
    });
  });

  settingsTrigger.addEventListener("click", (e) => {
    e.stopPropagation();
    settingsDropdown.classList.toggle("open");
  });
  document.addEventListener("click", (e) => {
    if (!document.getElementById("settings-nav-group").contains(e.target)) {
      settingsDropdown.classList.remove("open");
    }
  });
}

// --- Dashboard ---------------------------------------------------------------

async function loadDashboard() {
  const d = await api("/api/dashboard");
  document.getElementById("stat-balance").textContent = fmtMoney(d.current_balance);
  document.getElementById("stat-balance-date").textContent = "as of " + (d.as_of_date || "--");

  if (d.lowest_projected_balance !== null) {
    document.getElementById("stat-lowest").textContent = fmtMoney(d.lowest_projected_balance);
    document.getElementById("stat-lowest").style.color = d.lowest_projected_balance < 0 ? "var(--expense)" : "";
    document.getElementById("stat-lowest-date").textContent = "on " + fmtDate(d.lowest_projected_date);
  } else {
    document.getElementById("stat-lowest").textContent = "--";
    document.getElementById("stat-lowest-date").textContent = "";
  }

  const net = d.monthly_income_estimate - d.monthly_expense_estimate;
  document.getElementById("stat-net").textContent = fmtMoney(net);
  document.getElementById("stat-net").style.color = net < 0 ? "var(--expense)" : "var(--income)";
  document.getElementById("stat-net-sub").textContent =
    `Income ~${fmtMoney(d.monthly_income_estimate)} / Bills ~${fmtMoney(d.monthly_expense_estimate)}`;

  if (d.safe_to_spend !== null) {
    document.getElementById("stat-safe").textContent = fmtMoney(d.safe_to_spend);
    document.getElementById("stat-safe").style.color = d.safe_to_spend < 0 ? "var(--expense)" : "var(--income)";
    document.getElementById("stat-safe-sub").textContent = d.next_income_date
      ? `until paycheck on ${fmtDate(d.next_income_date)}`
      : "over the next 60 days";
  } else {
    document.getElementById("stat-safe").textContent = "--";
    document.getElementById("stat-safe-sub").textContent = "";
  }

  loadForecastChart();
  loadMoneyLeaks();

  const warnEl = document.getElementById("low-balance-warning");
  if (d.lowest_projected_balance !== null && d.lowest_projected_balance < 0) {
    warnEl.classList.remove("hidden");
    warnEl.textContent = `⚠ Your projected balance goes negative (${fmtMoney(d.lowest_projected_balance)}) around ${fmtDate(d.lowest_projected_date)} based on scheduled bills. Consider moving money or adjusting a bill date.`;
  } else {
    warnEl.classList.add("hidden");
  }

  const upcoming7 = document.getElementById("upcoming-7");
  upcoming7.innerHTML = "";
  if (d.upcoming_7_days.length === 0) {
    upcoming7.innerHTML = '<div class="muted">Nothing due in the next 7 days.</div>';
  }
  d.upcoming_7_days.forEach(e => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div class="name-with-logo">
        ${billLogoHtml(e.logo_domain)}
        <div>
          <div class="name">${e.name} ${e.autopay ? '<span class="pill">autopay</span>' : ""}</div>
          <div class="sub">${fmtDate(e.date)} &middot; ${e.category || ""}</div>
          ${e.pay_url ? `<a href="${escapeAttr(e.pay_url)}" target="_blank" rel="noopener" class="btn btn-small" style="margin-top:4px;">Pay Online</a>` : ""}
        </div>
      </div>
      <div class="${e.type === "income" ? "amt-income" : "amt-expense"}">
        ${e.type === "income" ? "+" : "-"}${fmtMoney(Math.abs(e.amount))}
      </div>`;
    upcoming7.appendChild(row);
  });

  const catEl = document.getElementById("cat-spending");
  catEl.innerHTML = "";
  if (d.spending_by_category_90d.length === 0) {
    catEl.innerHTML = '<div class="muted">Import transactions to see category spending.</div>';
  }
  d.spending_by_category_90d.slice(0, 10).forEach(c => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div><div class="name">${c.category}</div><div class="sub">${c.cnt} transactions</div></div>
      <div class="amt-expense">${fmtMoney(c.total)}</div>`;
    catEl.appendChild(row);
  });

  // Recurring suggestion banner
  const candidates = await api("/api/detect-recurring");
  const banner = document.getElementById("recurring-banner");
  if (candidates.length > 0) {
    banner.classList.remove("hidden");
    banner.innerHTML = `🔎 Found ${candidates.length} recurring charge${candidates.length > 1 ? "s" : ""} in your transactions that ${candidates.length > 1 ? "aren't" : "isn't"} tracked as a bill yet. <a href="#" id="goto-bills-link">Review them in Bills &rarr;</a>`;
    document.getElementById("goto-bills-link").addEventListener("click", (ev) => {
      ev.preventDefault();
      document.querySelector('.tab-btn[data-tab="bills"]').click();
    });
  } else {
    banner.classList.add("hidden");
  }

  // Rising bills banner
  const trends = await api("/api/bill-trends?months=6");
  const trendsBanner = document.getElementById("bill-trends-banner");
  if (trends.rising_count > 0) {
    trendsBanner.classList.remove("hidden");
    const names = trends.rising_bills.slice(0, 3).map(b => `${b.name} (+${b.pct_change}%)`).join(", ");
    trendsBanner.innerHTML = `📈 ${trends.rising_count} bill${trends.rising_count > 1 ? "s" : ""} trending up: ${names}. <a href="#" id="goto-billtrends-link">See Bill Trends &rarr;</a>`;
    document.getElementById("goto-billtrends-link").addEventListener("click", (ev) => {
      ev.preventDefault();
      document.querySelector('.tab-btn[data-tab="billtrends"]').click();
    });
  } else {
    trendsBanner.classList.add("hidden");
  }
}

async function loadForecastChart() {
  const data = await api("/api/forecast?days=60");
  const container = document.getElementById("forecast-chart");
  const series = data.series.filter(p => p.balance !== null);
  if (series.length === 0) {
    container.innerHTML = '<div class="muted">Set your current balance in Settings to see a forecast.</div>';
    return;
  }

  const width = 900, height = 220, padding = 30;
  const balances = series.map(p => p.balance);
  const minBal = Math.min(0, ...balances);
  const maxBal = Math.max(...balances);
  const range = (maxBal - minBal) || 1;

  const xStep = (width - padding * 2) / Math.max(series.length - 1, 1);
  const yFor = (bal) => height - padding - ((bal - minBal) / range) * (height - padding * 2);
  const xFor = (i) => padding + i * xStep;

  const points = series.map((p, i) => `${xFor(i).toFixed(1)},${yFor(p.balance).toFixed(1)}`).join(" ");
  const zeroY = yFor(0);

  const isDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const lineColor = "#2563eb";
  const fillColor = isDark ? "rgba(59,130,246,0.15)" : "rgba(37,99,235,0.12)";
  const zeroColor = "#dc2626";

  const areaPoints = `${xFor(0).toFixed(1)},${(height - padding).toFixed(1)} ${points} ${xFor(series.length - 1).toFixed(1)},${(height - padding).toFixed(1)}`;

  const lowestIdx = balances.indexOf(Math.min(...balances));
  const lowestPoint = series[lowestIdx];

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
      ${minBal < 0 ? `<line x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}" stroke="${zeroColor}" stroke-dasharray="4,4" stroke-width="1"/>` : ""}
      <polygon points="${areaPoints}" fill="${fillColor}" />
      <polyline points="${points}" fill="none" stroke="${lineColor}" stroke-width="2"/>
      <circle cx="${xFor(lowestIdx).toFixed(1)}" cy="${yFor(lowestPoint.balance).toFixed(1)}" r="4" fill="${lowestPoint.balance < 0 ? zeroColor : lineColor}" />
      <text x="${xFor(lowestIdx).toFixed(1)}" y="${(yFor(lowestPoint.balance) - 8).toFixed(1)}" class="forecast-tooltip" text-anchor="middle">${fmtMoney(lowestPoint.balance)}</text>
      <text x="${padding}" y="${height - 6}" class="forecast-tooltip">${fmtDate(series[0].date)}</text>
      <text x="${width - padding}" y="${height - 6}" class="forecast-tooltip" text-anchor="end">${fmtDate(series[series.length - 1].date)}</text>
    </svg>`;
}

async function loadMoneyLeaks() {
  const insights = await api("/api/insights?days=90");
  const summary = document.getElementById("money-leaks-summary");
  summary.innerHTML = `
    <div class="card stat" style="margin:0;">
      <div class="stat-label">Discretionary Spending</div>
      <div class="stat-value" style="color:var(--expense);">${fmtMoney(insights.discretionary_monthly_avg)}/mo</div>
      <div class="stat-sub">${insights.discretionary_pct_of_income !== null ? insights.discretionary_pct_of_income + "% of income" : ""}</div>
    </div>
    <div class="card stat" style="margin:0;">
      <div class="stat-label">Essential Spending</div>
      <div class="stat-value">${fmtMoney(insights.essential_monthly_avg)}/mo</div>
      <div class="stat-sub">bills, utilities, insurance, etc.</div>
    </div>`;

  const list = document.getElementById("money-leaks-list");
  list.innerHTML = "";
  if (insights.top_discretionary.length === 0) {
    list.innerHTML = '<div class="muted">No discretionary spending found in the last 90 days, or everything is classified as essential - check the Budgets tab to adjust classifications.</div>';
    return;
  }
  insights.top_discretionary.forEach(c => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div>
        <div class="name">${c.category}</div>
        <div class="sub">${c.cnt} transactions &middot; ${c.pct_of_income !== null ? c.pct_of_income + "% of income" : ""}</div>
      </div>
      <div style="text-align:right;">
        <div class="amt-expense">${fmtMoney(c.monthly_avg)}/mo</div>
        <div class="sub">cut in half: save ${fmtMoney(c.half_savings_monthly)}/mo</div>
      </div>`;
    list.appendChild(row);
  });
}

// --- Reports ---------------------------------------------------------------------

const PIE_PALETTE = [
  "#2563eb", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899",
  "#14b8a6", "#f97316", "#6366f1", "#84cc16", "#06b6d4", "#d946ef", "#64748b",
];

function renderPieChart(categories) {
  const container = document.getElementById("reports-pie-chart");
  const total = categories.reduce((sum, c) => sum + c.total, 0);
  if (total === 0) {
    container.innerHTML = '<div class="muted">No spending in this period.</div>';
    return;
  }
  const size = 200, cx = size / 2, cy = size / 2, r = 80, strokeWidth = 38;
  const circumference = 2 * Math.PI * r;

  let offset = 0;
  const circles = categories.map((c, i) => {
    const fraction = c.total / total;
    const dash = fraction * circumference;
    const circle = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
      stroke="${PIE_PALETTE[i % PIE_PALETTE.length]}" stroke-width="${strokeWidth}"
      stroke-dasharray="${dash.toFixed(2)} ${(circumference - dash).toFixed(2)}"
      stroke-dashoffset="${(-offset).toFixed(2)}"
      transform="rotate(-90 ${cx} ${cy})" />`;
    offset += dash;
    return circle;
  }).join("");

  container.innerHTML = `
    <svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
      ${circles}
      <text x="${cx}" y="${cy - 4}" text-anchor="middle" font-size="15" font-weight="700" fill="var(--text)">${fmtMoney(total)}</text>
      <text x="${cx}" y="${cy + 14}" text-anchor="middle" font-size="10" fill="var(--muted)">total spent</text>
    </svg>`;
}

function renderPieLegend(categories) {
  const el = document.getElementById("reports-pie-legend");
  el.innerHTML = "";
  categories.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div style="display:flex; align-items:center;">
        <span class="legend-swatch" style="background:${PIE_PALETTE[i % PIE_PALETTE.length]}"></span>
        <span class="name">${c.category}</span>
      </div>
      <div style="text-align:right;">
        <div>${fmtMoney(c.total)}</div>
        <div class="sub">${c.pct}% &middot; ${c.cnt} txns</div>
      </div>`;
    el.appendChild(row);
  });
}

function renderTrendChart(monthlyTrend) {
  const container = document.getElementById("reports-trend-chart");
  if (!monthlyTrend || monthlyTrend.length === 0) {
    container.innerHTML = '<div class="muted">Not enough history yet.</div>';
    return;
  }
  const width = 420, height = 200, padding = 32;
  const max = Math.max(...monthlyTrend.map(m => m.total));
  const barGap = 14;
  const barWidth = (width - padding * 2 - barGap * (monthlyTrend.length - 1)) / monthlyTrend.length;

  const bars = monthlyTrend.map((m, i) => {
    const barHeight = max > 0 ? (m.total / max) * (height - padding * 2) : 0;
    const x = padding + i * (barWidth + barGap);
    const y = height - padding - barHeight;
    const [year, month] = m.month.split("-");
    const label = new Date(parseInt(year, 10), parseInt(month, 10) - 1, 1)
      .toLocaleDateString(undefined, { month: "short" });
    return `
      <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="4" fill="var(--primary)" />
      <text x="${(x + barWidth / 2).toFixed(1)}" y="${(y - 6).toFixed(1)}" text-anchor="middle" font-size="11" fill="var(--text)">${fmtMoney(m.total)}</text>
      <text x="${(x + barWidth / 2).toFixed(1)}" y="${height - padding + 16}" text-anchor="middle" font-size="11" fill="var(--muted)">${label}</text>`;
  }).join("");

  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">${bars}</svg>`;
}

async function loadReports() {
  const days = document.getElementById("reports-period").value;
  const data = await api(`/api/reports/spending?days=${days}`);

  document.getElementById("reports-total-income").textContent = fmtMoney(data.total_income);
  document.getElementById("reports-total-expense").textContent = fmtMoney(data.total_expense);
  const netEl = document.getElementById("reports-net");
  netEl.textContent = fmtMoney(data.net);
  netEl.style.color = data.net < 0 ? "var(--expense)" : "var(--income)";

  renderPieChart(data.categories);
  renderPieLegend(data.categories);
  renderTrendChart(data.monthly_trend);

  const merchantsEl = document.getElementById("reports-top-merchants");
  merchantsEl.innerHTML = "";
  if (data.top_merchants.length === 0) {
    merchantsEl.innerHTML = '<div class="muted">No transactions in this period.</div>';
  }
  data.top_merchants.forEach(m => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div><div class="name">${m.name}</div><div class="sub">${m.cnt} transactions</div></div>
      <div class="amt-expense">${fmtMoney(m.total)}</div>`;
    merchantsEl.appendChild(row);
  });
}

function initReports() {
  document.getElementById("reports-period").addEventListener("change", loadReports);
}

// --- Bill Trends ---------------------------------------------------------------------

function renderBillTrendChart(bill) {
  const width = 420, height = 150, padding = 28;
  const months = bill.months;
  const amounts = months.map(m => m.total);
  const max = Math.max(...amounts, 0.01);
  const min = Math.min(...amounts, 0);
  const range = (max - min) || 1;
  const barGap = 10;
  const barWidth = (width - padding * 2 - barGap * (months.length - 1)) / months.length;

  const color = bill.direction === "up" ? "var(--expense)" : bill.direction === "down" ? "var(--income)" : "var(--primary)";

  const bars = months.map((m, i) => {
    const barHeight = ((m.total - min) / range) * (height - padding * 2) + 4;
    const x = padding + i * (barWidth + barGap);
    const y = height - padding - barHeight;
    const isLatest = i === months.length - 1;
    const [year, mo] = m.month.split("-");
    const label = new Date(parseInt(year, 10), parseInt(mo, 10) - 1, 1).toLocaleDateString(undefined, { month: "short" });
    return `
      <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="3" fill="${isLatest ? color : "var(--border)"}" />
      <text x="${(x + barWidth / 2).toFixed(1)}" y="${(y - 5).toFixed(1)}" text-anchor="middle" font-size="10" fill="var(--text)">${fmtMoney(m.total)}</text>
      <text x="${(x + barWidth / 2).toFixed(1)}" y="${height - padding + 15}" text-anchor="middle" font-size="10" fill="var(--muted)">${label}</text>`;
  }).join("");

  return `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">${bars}</svg>`;
}

async function loadBillTrends() {
  const months = document.getElementById("billtrends-months").value;
  const data = await api(`/api/bill-trends?months=${months}`);

  const alertEl = document.getElementById("billtrends-alert");
  if (data.rising_count > 0) {
    alertEl.classList.remove("hidden");
    alertEl.textContent = `⚠ ${data.rising_count} bill${data.rising_count > 1 ? "s have" : " has"} gone up compared to its recent average - check the details below.`;
  } else {
    alertEl.classList.add("hidden");
  }

  const risingEl = document.getElementById("billtrends-rising");
  risingEl.innerHTML = "";
  const rising = data.bills.filter(b => b.is_alert);
  if (rising.length === 0) {
    risingEl.innerHTML = '<div class="muted">Nothing trending up right now.</div>';
  }
  rising.forEach(b => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div><div class="name">${b.name}</div><div class="sub">${b.category}</div></div>
      <div style="text-align:right;">
        <div class="amt-expense">${fmtMoney(b.baseline_amount)} &rarr; ${fmtMoney(b.latest_amount)}</div>
        <div class="sub" style="color:var(--expense)">+${b.pct_change}%</div>
      </div>`;
    risingEl.appendChild(row);
  });

  const chartsEl = document.getElementById("billtrends-charts");
  chartsEl.innerHTML = "";
  const variable = data.bills.filter(b => b.is_variable);
  if (variable.length === 0) {
    chartsEl.innerHTML = '<div class="muted">No bills with enough month-to-month variation to chart yet - check back after a few more billing cycles.</div>';
  }
  variable.forEach(b => {
    const wrap = document.createElement("div");
    wrap.style.marginBottom = "18px";
    const arrow = b.direction === "up" ? "▲" : b.direction === "down" ? "▼" : "→";
    const arrowColor = b.direction === "up" ? "var(--expense)" : b.direction === "down" ? "var(--income)" : "var(--muted)";
    wrap.innerHTML = `
      <div class="row-between" style="margin-bottom:4px;">
        <span class="name">${b.name} <span class="pill">${b.category}</span></span>
        <span style="color:${arrowColor}; font-weight:600;">${arrow} ${b.pct_change > 0 ? "+" : ""}${b.pct_change}%</span>
      </div>
      ${renderBillTrendChart(b)}`;
    chartsEl.appendChild(wrap);
  });

  const steadyEl = document.getElementById("billtrends-steady");
  steadyEl.innerHTML = "";
  const steady = data.bills.filter(b => !b.is_variable);
  if (steady.length === 0) {
    steadyEl.innerHTML = '<div class="muted">None - everything has some variation.</div>';
  }
  steady.forEach(b => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div><div class="name">${b.name}</div><div class="sub">${b.category}</div></div>
      <div class="amt-expense">${fmtMoney(b.latest_amount)}</div>`;
    steadyEl.appendChild(row);
  });
}

function initBillTrends() {
  document.getElementById("billtrends-months").addEventListener("change", loadBillTrends);
}

// --- Calendar ------------------------------------------------------------------

async function loadCalendar() {
  const monthStr = `${state.calYear}-${String(state.calMonth).padStart(2, "0")}`;
  const data = await api(`/api/calendar?month=${monthStr}`);
  const first = new Date(state.calYear, state.calMonth - 1, 1);
  document.getElementById("cal-title").textContent =
    first.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  const overdueBanner = document.getElementById("overdue-banner");
  const overdueList = document.getElementById("overdue-list");
  if (data.overdue && data.overdue.length > 0) {
    overdueBanner.classList.remove("hidden");
    overdueList.innerHTML = "";
    data.overdue.forEach(e => {
      const row = document.createElement("div");
      row.className = "list-row";
      row.innerHTML = `
        <div>
          <div class="name">${e.name}</div>
          <div class="sub">was due ${fmtDate(e.due_date)} &middot; ${e.category || ""}</div>
        </div>
        <div style="display:flex; align-items:center; gap:8px">
          <div class="${e.type === "income" ? "amt-income" : "amt-expense"}">${fmtMoney(Math.abs(e.amount))}</div>
          <button class="btn btn-small btn-primary" data-id="${e.bill_id}">Mark Paid</button>
        </div>`;
      row.querySelector("button").addEventListener("click", async () => {
        await api(`/api/bills/${e.bill_id}/pay`, { method: "POST" });
        loadCalendar();
      });
      overdueList.appendChild(row);
    });
  } else {
    overdueBanner.classList.add("hidden");
  }

  const grid = document.getElementById("calendar-grid");
  grid.innerHTML = "";
  ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach(d => {
    const el = document.createElement("div");
    el.className = "cal-dow";
    el.textContent = d;
    grid.appendChild(el);
  });

  const startWeekday = first.getDay();
  for (let i = 0; i < startWeekday; i++) {
    const el = document.createElement("div");
    el.className = "cal-day empty";
    grid.appendChild(el);
  }

  const todayIso = new Date().toISOString().slice(0, 10);
  const daysInMonth = new Date(state.calYear, state.calMonth, 0).getDate();

  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${state.calYear}-${String(state.calMonth).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const info = data.days[iso] || { balance: null, events: [] };
    const el = document.createElement("div");
    el.className = "cal-day" + (iso === todayIso ? " today" : "");
    let html = `<div class="daynum">${day}</div>`;
    if (info.balance !== null) {
      html += `<div class="balance ${info.balance < 0 ? "negative" : ""}">${fmtMoney(info.balance)}</div>`;
    }
    info.events.slice(0, 3).forEach(e => {
      html += `<div class="evt ${e.type}">${e.type === "income" ? "+" : "-"}${fmtMoney(Math.abs(e.amount))} ${e.name}</div>`;
    });
    if (info.events.length > 3) {
      html += `<div class="muted" style="font-size:0.68rem">+${info.events.length - 3} more</div>`;
    }
    el.innerHTML = html;
    el.addEventListener("click", () => openDayModal(iso, info));
    grid.appendChild(el);
  }
}

function openDayModal(iso, info) {
  document.getElementById("day-modal-title").textContent = fmtDate(iso);
  const body = document.getElementById("day-modal-body");
  if (info.events.length === 0) {
    body.innerHTML = '<p class="muted">Nothing scheduled this day.</p>';
  } else {
    body.innerHTML = info.events.map(e => `
      <div class="list-row">
        <div class="name-with-logo">
          ${billLogoHtml(e.logo_domain)}
          <div>
            <div class="name">${e.name} ${e.autopay ? '<span class="pill">autopay</span>' : ""}</div>
            <div class="sub">${e.category || ""}${e.payment_method ? " &middot; " + e.payment_method : ""}</div>
            ${e.pay_url ? `<a href="${escapeAttr(e.pay_url)}" target="_blank" rel="noopener" class="btn btn-small" style="margin-top:4px;">Pay Online</a>` : ""}
          </div>
        </div>
        <div class="${e.type === "income" ? "amt-income" : "amt-expense"}">
          ${e.type === "income" ? "+" : "-"}${fmtMoney(Math.abs(e.amount))}
        </div>
      </div>`).join("");
  }
  if (info.balance !== null) {
    body.innerHTML += `<p class="muted" style="margin-top:10px">Projected balance after this day: <strong>${fmtMoney(info.balance)}</strong></p>`;
  }
  document.getElementById("day-modal").classList.remove("hidden");
}

function initCalendarNav() {
  document.getElementById("cal-prev").addEventListener("click", () => {
    state.calMonth--;
    if (state.calMonth < 1) { state.calMonth = 12; state.calYear--; }
    loadCalendar();
  });
  document.getElementById("cal-next").addEventListener("click", () => {
    state.calMonth++;
    if (state.calMonth > 12) { state.calMonth = 1; state.calYear++; }
    loadCalendar();
  });
  document.getElementById("cal-today").addEventListener("click", () => {
    const now = new Date();
    state.calYear = now.getFullYear();
    state.calMonth = now.getMonth() + 1;
    loadCalendar();
  });
  document.getElementById("day-close").addEventListener("click", () => {
    document.getElementById("day-modal").classList.add("hidden");
  });
}

// --- Bills ---------------------------------------------------------------------

async function loadBills() {
  const bills = await api("/api/bills");
  const tbody = document.querySelector("#bills-table tbody");
  tbody.innerHTML = "";
  bills.forEach(b => {
    const tr = document.createElement("tr");
    const payLink = b.pay_url
      ? `<a class="btn btn-small" href="${escapeAttr(b.pay_url)}" target="_blank" rel="noopener">Pay Online</a>`
      : "";
    tr.innerHTML = `
      <td><div class="name-with-logo">${billLogoHtml(b.logo_domain)}<div>${b.name}${b.payment_method ? `<div class="sub">${b.payment_method}</div>` : ""}</div></div></td>
      <td class="${b.type === "income" ? "amt-income" : "amt-expense"}">${fmtMoney(b.amount)}</td>
      <td>${b.type}</td>
      <td>${b.category || ""}</td>
      <td>${b.frequency}</td>
      <td>${b.next_due_date}</td>
      <td>${b.autopay ? "Yes" : "No"}</td>
      <td>
        ${payLink}
        <button class="btn btn-small" data-action="pay" data-id="${b.id}">Mark Paid</button>
        <button class="btn btn-small" data-action="edit" data-id="${b.id}">Edit</button>
        <button class="btn btn-small" data-action="delete" data-id="${b.id}">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      if (action === "pay") {
        await api(`/api/bills/${id}/pay`, { method: "POST" });
        loadBills();
      } else if (action === "delete") {
        if (confirm("Remove this bill?")) {
          await api(`/api/bills/${id}`, { method: "DELETE" });
          loadBills();
        }
      } else if (action === "edit") {
        const bill = bills.find(b => String(b.id) === id);
        openBillModal(bill);
      }
    });
  });
}

async function loadDetected() {
  const candidates = await api("/api/detect-recurring");
  const el = document.getElementById("detected-list");
  el.innerHTML = "";
  if (candidates.length === 0) {
    el.innerHTML = '<div class="muted">No new recurring charges detected. Import more transaction history to find more.</div>';
    return;
  }
  candidates.forEach(c => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div>
        <div class="name">${c.display_name}</div>
        <div class="sub">${c.frequency} &middot; seen ${c.occurrences}x &middot; last ${fmtDate(c.last_date)}</div>
      </div>
      <div style="display:flex; align-items:center; gap:8px">
        <div class="${c.is_income ? "amt-income" : "amt-expense"}">${fmtMoney(c.avg_amount)}</div>
        <button class="btn btn-small btn-primary" data-key="${c.merchant_key}" data-action="add-detected">Add as Bill</button>
        <button class="btn btn-small" data-key="${c.merchant_key}" data-action="dismiss-detected">Dismiss</button>
      </div>`;
    el.appendChild(row);

    row.querySelector('[data-action="add-detected"]').addEventListener("click", () => {
      openBillModal({
        name: c.display_name,
        amount: c.avg_amount,
        type: c.is_income ? "income" : "expense",
        category: c.is_income ? "Income" : "Bill",
        frequency: c.frequency,
        due_day: c.suggested_due_day,
        next_due_date: c.suggested_next_due,
        autopay: 1,
        merchant_key: c.merchant_key,
        source: "detected",
      });
    });
    row.querySelector('[data-action="dismiss-detected"]').addEventListener("click", async () => {
      await api("/api/detect-recurring/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ merchant_key: c.merchant_key }),
      });
      loadDetected();
    });
  });
}

function openBillModal(bill) {
  document.getElementById("bill-modal-title").textContent = bill && bill.id ? "Edit Bill" : "Add Bill";
  document.getElementById("bill-id").value = (bill && bill.id) || "";
  document.getElementById("bill-name").value = (bill && bill.name) || "";
  document.getElementById("bill-amount").value = (bill && bill.amount) || "";
  document.getElementById("bill-type").value = (bill && bill.type) || "expense";
  document.getElementById("bill-category").value = (bill && bill.category) || "";
  document.getElementById("bill-frequency").value = (bill && bill.frequency) || "monthly";
  document.getElementById("bill-next-due").value = (bill && bill.next_due_date) || "";
  document.getElementById("bill-autopay").checked = !!(bill && bill.autopay);
  document.getElementById("bill-pay-url").value = (bill && bill.pay_url) || "";
  document.getElementById("bill-payment-method").value = (bill && bill.payment_method) || "";
  document.getElementById("bill-logo-domain").value = (bill && bill.logo_domain) || "";
  document.getElementById("bill-notes").value = (bill && bill.notes) || "";
  document.getElementById("bill-modal").dataset.merchantKey = (bill && bill.merchant_key) || "";
  document.getElementById("bill-modal").dataset.source = (bill && bill.source) || "manual";
  document.getElementById("bill-modal").classList.remove("hidden");
}

function initBillModal() {
  document.getElementById("btn-add-bill").addEventListener("click", () => openBillModal(null));
  document.getElementById("bill-cancel").addEventListener("click", () => {
    document.getElementById("bill-modal").classList.add("hidden");
  });
  document.getElementById("bill-save").addEventListener("click", async () => {
    const id = document.getElementById("bill-id").value;
    const dueDateVal = document.getElementById("bill-next-due").value;
    const freq = document.getElementById("bill-frequency").value;
    const dueDate = new Date(dueDateVal + "T00:00:00");
    const payload = {
      name: document.getElementById("bill-name").value.trim(),
      amount: parseFloat(document.getElementById("bill-amount").value),
      type: document.getElementById("bill-type").value,
      category: document.getElementById("bill-category").value.trim() || "Bill",
      frequency: freq,
      due_day: (freq === "monthly" || freq === "annual") ? dueDate.getDate() : null,
      next_due_date: dueDateVal,
      autopay: document.getElementById("bill-autopay").checked,
      pay_url: document.getElementById("bill-pay-url").value.trim(),
      payment_method: document.getElementById("bill-payment-method").value.trim(),
      logo_domain: document.getElementById("bill-logo-domain").value.trim(),
      notes: document.getElementById("bill-notes").value.trim(),
      merchant_key: document.getElementById("bill-modal").dataset.merchantKey || null,
      source: document.getElementById("bill-modal").dataset.source || "manual",
    };
    if (!payload.name || isNaN(payload.amount) || !dueDateVal) {
      alert("Please fill in name, amount, and next due date.");
      return;
    }
    if (payload.pay_url && !/^https?:\/\//i.test(payload.pay_url)) {
      alert("Pay Online URL must start with http:// or https://");
      return;
    }
    try {
      if (id) {
        await api(`/api/bills/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        await api("/api/bills", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
    } catch (e) {
      alert("Failed to save bill: " + e.message);
      return;
    }
    document.getElementById("bill-modal").classList.add("hidden");
    loadBills();
    loadDetected();
  });
}

// --- Transactions ----------------------------------------------------------------

async function loadTransactions() {
  const cats = await api("/api/categories");
  const sel = document.getElementById("txn-filter-category");
  const current = sel.value;
  sel.innerHTML = '<option value="">All Categories</option>' +
    cats.map(c => `<option value="${c}">${c}</option>`).join("");
  sel.value = current || "";

  const searchBox = document.getElementById("txn-search");
  const params = new URLSearchParams();
  if (sel.value) params.set("category", sel.value);
  if (searchBox.value.trim()) params.set("q", searchBox.value.trim());
  const url = "/api/transactions" + (params.toString() ? "?" + params.toString() : "");
  const txns = await api(url);
  const tbody = document.querySelector("#txn-table tbody");
  tbody.innerHTML = "";
  txns.forEach(t => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${t.date}</td>
      <td>${t.description}</td>
      <td class="${t.amount < 0 ? "amt-expense" : "amt-income"}">${fmtMoney(t.amount)}</td>
      <td>${t.status}</td>
      <td><input type="text" class="cat-edit" data-id="${t.id}" value="${t.category}" style="width:150px; padding:4px 6px; margin:0;"></td>`;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll(".cat-edit").forEach(input => {
    input.addEventListener("change", async () => {
      const applyAll = confirm("Apply this category to all similar transactions from this merchant in the future too?");
      await api(`/api/transactions/${input.dataset.id}/category`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: input.value, apply_to_all: applyAll }),
      });
      loadTransactions();
    });
  });

  sel.onchange = loadTransactions;
  let searchDebounce;
  searchBox.oninput = () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(loadTransactions, 300);
  };
}

// --- Import ------------------------------------------------------------------------

function renderReconcileResults(reconciled) {
  const el = document.getElementById("reconcile-result");
  el.innerHTML = "";
  if (!reconciled || reconciled.length === 0) {
    el.innerHTML = '<div class="muted">Nothing new to reconcile right now.</div>';
    return;
  }
  reconciled.forEach(r => {
    const row = document.createElement("div");
    row.className = "list-row";
    const amountNote = r.amount_changed
      ? ` <span class="pill" style="color:var(--expense);">was ${fmtMoney(r.old_amount)}, now ${fmtMoney(r.new_amount)}</span>`
      : "";
    row.innerHTML = `
      <div>
        <div class="name">${r.bill_name} marked paid${amountNote}</div>
        <div class="sub">matched to transaction on ${fmtDate(r.matched_date)}</div>
      </div>
      <button class="btn btn-small" data-payment-id="${r.payment_id}">Undo</button>`;
    row.querySelector("button").addEventListener("click", async (ev) => {
      await api(`/api/bill-payments/${r.payment_id}`, { method: "DELETE" });
      ev.target.closest(".list-row").remove();
      loadDashboard();
    });
    el.appendChild(row);
  });
}

function renderSuggestions(suggestions) {
  const el = document.getElementById("suggestions-list");
  el.innerHTML = "";
  if (!suggestions || suggestions.length === 0) {
    el.innerHTML = '<div class="muted">No ambiguous matches right now.</div>';
    return;
  }
  suggestions.forEach(s => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div>
        <div class="name">${s.bill_name} <span class="pill">expected ${fmtMoney(s.bill_amount)} on ${fmtDate(s.bill_due_date)}</span></div>
        <div class="sub">possible match: "${s.transaction_description.trim().slice(0, 60)}" &middot; ${fmtMoney(Math.abs(s.transaction_amount))} on ${fmtDate(s.transaction_date)}</div>
      </div>
      <div style="display:flex; gap:8px;">
        <button class="btn btn-small btn-primary" data-action="confirm">Confirm Match</button>
        <button class="btn btn-small" data-action="dismiss">Not a Match</button>
      </div>`;
    row.querySelector('[data-action="confirm"]').addEventListener("click", async () => {
      await api("/api/reconcile/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bill_id: s.bill_id, transaction_id: s.transaction_id }),
      });
      row.remove();
      loadDashboard();
    });
    row.querySelector('[data-action="dismiss"]').addEventListener("click", async () => {
      await api("/api/reconcile/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bill_id: s.bill_id, transaction_id: s.transaction_id }),
      });
      row.remove();
    });
    el.appendChild(row);
  });
}

function initImport() {
  document.getElementById("btn-import").addEventListener("click", async () => {
    const fileInput = document.getElementById("csv-file");
    const resultEl = document.getElementById("import-result");
    if (!fileInput.files.length) {
      alert("Choose a CSV file first.");
      return;
    }
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    resultEl.classList.remove("hidden", "banner-warn", "banner-good");
    resultEl.textContent = "Importing...";
    try {
      const res = await api("/api/import", { method: "POST", body: fd });
      resultEl.classList.add("banner-good");
      const reconciledCount = res.reconciled ? res.reconciled.length : 0;
      resultEl.textContent = `Done. Added ${res.added} new transactions, updated ${res.updated} (e.g. pending→posted), skipped ${res.skipped} already-imported duplicates out of ${res.total_rows} rows in the file. Auto-reconciled ${reconciledCount} bill${reconciledCount === 1 ? "" : "s"}.`;
      renderReconcileResults(res.reconciled);
      renderSuggestions(res.suggestions);
      loadDashboard();
    } catch (e) {
      resultEl.classList.add("banner-warn");
      resultEl.textContent = "Import failed: " + e.message;
    }
  });

  document.getElementById("btn-reconcile-now").addEventListener("click", async (ev) => {
    ev.target.textContent = "Checking...";
    ev.target.disabled = true;
    try {
      const res = await api("/api/reconcile", { method: "POST" });
      renderReconcileResults(res.reconciled);
      renderSuggestions(res.suggestions);
      loadDashboard();
    } finally {
      ev.target.textContent = "Check Now";
      ev.target.disabled = false;
    }
  });

  renderSuggestions([]);
}

// --- Budgets ------------------------------------------------------------------------

async function loadBudgets() {
  const [budgets, cats, kinds] = await Promise.all([
    api("/api/budgets"), api("/api/categories"), api("/api/category-types"),
  ]);
  const sel = document.getElementById("budget-category");
  sel.innerHTML = cats.map(c => `<option value="${c}">${c}</option>`).join("");
  const kindByCategory = {};
  kinds.forEach(k => { kindByCategory[k.category] = k.kind; });

  const list = document.getElementById("budgets-list");
  list.innerHTML = "";
  if (budgets.length === 0) {
    list.innerHTML = '<div class="muted">No spending yet to show. Import transactions first.</div>';
    return;
  }
  budgets.forEach(b => {
    const row = document.createElement("div");
    row.className = "budget-row";
    const kind = kindByCategory[b.category] || "discretionary";
    const kindSelect = `
      <select class="kind-select" data-cat="${b.category}" style="width:auto; display:inline-block; margin:0 0 0 8px; padding:2px 6px; font-size:0.78rem;">
        <option value="essential" ${kind === "essential" ? "selected" : ""}>Essential</option>
        <option value="discretionary" ${kind === "discretionary" ? "selected" : ""}>Discretionary</option>
        <option value="neutral" ${kind === "neutral" ? "selected" : ""}>Neutral (not spending)</option>
      </select>`;
    if (b.monthly_limit) {
      const pct = Math.min(100, (b.spent_last_30d / b.monthly_limit) * 100);
      const cls = pct >= 100 ? "over-limit" : pct >= 80 ? "over-warn" : "";
      row.innerHTML = `
        <div class="budget-top">
          <span class="name">${b.category}${kindSelect}</span>
          <span>${fmtMoney(b.spent_last_30d)} / ${fmtMoney(b.monthly_limit)}
            <button class="budget-remove" data-cat="${b.category}">remove limit</button>
          </span>
        </div>
        <div class="budget-bar-track"><div class="budget-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
    } else {
      row.innerHTML = `
        <div class="budget-top">
          <span class="name">${b.category}${kindSelect}</span>
          <span class="muted">${fmtMoney(b.spent_last_30d)} spent (last 30 days) &middot; no limit set</span>
        </div>`;
    }
    list.appendChild(row);
  });

  list.querySelectorAll(".budget-remove").forEach(btn => {
    btn.addEventListener("click", async () => {
      await api(`/api/budgets/${encodeURIComponent(btn.dataset.cat)}`, { method: "DELETE" });
      loadBudgets();
    });
  });

  list.querySelectorAll(".kind-select").forEach(select => {
    select.addEventListener("change", async () => {
      await api("/api/category-types", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: select.dataset.cat, kind: select.value }),
      });
    });
  });
}

function initBudgets() {
  document.getElementById("btn-add-budget").addEventListener("click", async () => {
    const category = document.getElementById("budget-category").value;
    const limit = parseFloat(document.getElementById("budget-limit").value);
    if (!category || isNaN(limit)) {
      alert("Choose a category and enter a limit.");
      return;
    }
    await api("/api/budgets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category, monthly_limit: limit }),
    });
    document.getElementById("budget-limit").value = "";
    loadBudgets();
  });
}

// --- What-If ------------------------------------------------------------------------

let whatIfBillsCache = [];

function whatIfRowHtml(idx) {
  const options = whatIfBillsCache
    .map(b => `<option value="${b.id}">${b.name} (currently ${b.next_due_date})</option>`)
    .join("");
  return `
    <div class="row-between whatif-row" data-idx="${idx}" style="gap:10px; margin-bottom:8px;">
      <select class="whatif-bill" style="flex:2; margin:0;"><option value="">Choose a bill...</option>${options}</select>
      <input type="date" class="whatif-date" style="flex:1; margin:0;">
      <button class="btn btn-small whatif-remove" type="button">Remove</button>
    </div>`;
}

function addWhatIfRow() {
  const container = document.getElementById("whatif-rows");
  const idx = container.children.length;
  const div = document.createElement("div");
  div.innerHTML = whatIfRowHtml(idx);
  const row = div.firstElementChild;
  row.querySelector(".whatif-remove").addEventListener("click", () => row.remove());
  container.appendChild(row);
}

async function loadWhatIf() {
  whatIfBillsCache = await api("/api/bills");
  const container = document.getElementById("whatif-rows");
  container.innerHTML = "";
  document.getElementById("whatif-results-card").classList.add("hidden");
  addWhatIfRow();
}

function renderWhatIfChart(baselineSeries, modifiedSeries) {
  const container = document.getElementById("whatif-chart");
  const width = 900, height = 240, padding = 30;
  const allBalances = [...baselineSeries, ...modifiedSeries].map(p => p.balance).filter(b => b !== null);
  const minBal = Math.min(0, ...allBalances);
  const maxBal = Math.max(...allBalances);
  const range = (maxBal - minBal) || 1;
  const n = baselineSeries.length;
  const xStep = (width - padding * 2) / Math.max(n - 1, 1);
  const yFor = (bal) => height - padding - ((bal - minBal) / range) * (height - padding * 2);
  const xFor = (i) => padding + i * xStep;

  const toPoints = (series) => series.map((p, i) => `${xFor(i).toFixed(1)},${yFor(p.balance).toFixed(1)}`).join(" ");
  const zeroY = yFor(0);

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
      ${minBal < 0 ? `<line x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}" stroke="#dc2626" stroke-dasharray="4,4" stroke-width="1"/>` : ""}
      <polyline points="${toPoints(baselineSeries)}" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="5,3"/>
      <polyline points="${toPoints(modifiedSeries)}" fill="none" stroke="#2563eb" stroke-width="2.5"/>
      <text x="${padding}" y="${height - 6}" class="forecast-tooltip">${fmtDate(baselineSeries[0].date)}</text>
      <text x="${width - padding}" y="${height - 6}" class="forecast-tooltip" text-anchor="end">${fmtDate(baselineSeries[baselineSeries.length - 1].date)}</text>
    </svg>`;
}

function initWhatIf() {
  document.getElementById("btn-whatif-add-row").addEventListener("click", addWhatIfRow);

  document.getElementById("btn-whatif-run").addEventListener("click", async () => {
    const rows = document.querySelectorAll("#whatif-rows .whatif-row");
    const changes = [];
    rows.forEach(row => {
      const billId = row.querySelector(".whatif-bill").value;
      const newDate = row.querySelector(".whatif-date").value;
      if (billId && newDate) {
        changes.push({ bill_id: parseInt(billId, 10), next_due_date: newDate });
      }
    });
    if (changes.length === 0) {
      alert("Pick at least one bill and a new due date.");
      return;
    }
    const res = await api("/api/whatif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes, days: 60 }),
    });

    document.getElementById("whatif-results-card").classList.remove("hidden");
    document.getElementById("whatif-baseline-lowest").textContent = fmtMoney(res.baseline.lowest_balance);
    document.getElementById("whatif-baseline-lowest-date").textContent = res.baseline.lowest_date ? "on " + fmtDate(res.baseline.lowest_date) : "";
    document.getElementById("whatif-modified-lowest").textContent = fmtMoney(res.modified.lowest_balance);
    document.getElementById("whatif-modified-lowest-date").textContent = res.modified.lowest_date ? "on " + fmtDate(res.modified.lowest_date) : "";
    const delta = res.modified.lowest_balance - res.baseline.lowest_balance;
    document.getElementById("whatif-modified-lowest").style.color = delta >= 0 ? "var(--income)" : "var(--expense)";

    renderWhatIfChart(res.baseline.series, res.modified.series);
  });
}

// --- Debts --------------------------------------------------------------------------

async function loadDebts() {
  const debts = await api("/api/debts");
  const list = document.getElementById("debts-list");
  list.innerHTML = "";
  if (debts.length === 0) {
    list.innerHTML = '<div class="muted">No debts tracked yet. Add one to see its payoff timeline.</div>';
    return;
  }
  debts.forEach(d => {
    const row = document.createElement("div");
    row.className = "card";
    row.style.margin = "0 0 12px 0";
    const payoff = d.payoff;
    row.innerHTML = `
      <div class="row-between">
        <div>
          <div class="name" style="font-size:1.05rem;">${d.name}</div>
          <div class="sub">${fmtMoney(d.balance)} balance &middot; ${d.apr}% APR &middot; ${fmtMoney(d.minimum_payment)}/mo</div>
        </div>
        <div>
          <button class="btn btn-small" data-action="edit">Edit</button>
          <button class="btn btn-small" data-action="delete">Delete</button>
        </div>
      </div>
      <div class="grid grid-2" style="margin-top:10px;">
        <div class="card stat" style="margin:0;">
          <div class="stat-label">Payoff at current payment</div>
          <div class="stat-value">${payoff.months !== null ? payoff.months + " months" : "Never (payment too low)"}</div>
          <div class="stat-sub">${payoff.payoff_date ? "around " + fmtDate(payoff.payoff_date) : ""} ${payoff.total_interest !== null ? "&middot; " + fmtMoney(payoff.total_interest) + " total interest" : ""}</div>
        </div>
        <div class="card stat" style="margin:0;">
          <div class="stat-label">With +$100/mo extra</div>
          <div class="stat-value">${payoff.months_with_extra !== null ? payoff.months_with_extra + " months" : "--"}</div>
          <div class="stat-sub">${payoff.months !== null && payoff.months_with_extra !== null ? "saves " + (payoff.months - payoff.months_with_extra) + " months, " + fmtMoney(payoff.interest_saved) + " interest" : ""}</div>
        </div>
      </div>`;
    row.querySelector('[data-action="edit"]').addEventListener("click", () => openDebtModal(d));
    row.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      if (confirm("Remove this debt?")) {
        await api(`/api/debts/${d.id}`, { method: "DELETE" });
        loadDebts();
      }
    });
    list.appendChild(row);
  });
}

function openDebtModal(debt) {
  document.getElementById("debt-modal-title").textContent = debt && debt.id ? "Edit Debt" : "Add Debt";
  document.getElementById("debt-id").value = (debt && debt.id) || "";
  document.getElementById("debt-name").value = (debt && debt.name) || "";
  document.getElementById("debt-balance").value = (debt && debt.balance) || "";
  document.getElementById("debt-apr").value = (debt && debt.apr) || "";
  document.getElementById("debt-payment").value = (debt && debt.minimum_payment) || "";
  document.getElementById("debt-notes").value = (debt && debt.notes) || "";
  document.getElementById("debt-modal").classList.remove("hidden");
}

function initDebts() {
  document.getElementById("btn-add-debt").addEventListener("click", () => openDebtModal(null));
  document.getElementById("debt-cancel").addEventListener("click", () => {
    document.getElementById("debt-modal").classList.add("hidden");
  });
  document.getElementById("debt-save").addEventListener("click", async () => {
    const id = document.getElementById("debt-id").value;
    const payload = {
      name: document.getElementById("debt-name").value.trim(),
      balance: parseFloat(document.getElementById("debt-balance").value),
      apr: parseFloat(document.getElementById("debt-apr").value) || 0,
      minimum_payment: parseFloat(document.getElementById("debt-payment").value),
      notes: document.getElementById("debt-notes").value.trim(),
    };
    if (!payload.name || isNaN(payload.balance) || isNaN(payload.minimum_payment)) {
      alert("Please fill in name, balance, and monthly payment.");
      return;
    }
    if (id) {
      await api(`/api/debts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      await api("/api/debts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    document.getElementById("debt-modal").classList.add("hidden");
    loadDebts();
  });
}

// --- Notifications ------------------------------------------------------------------

function isTruthy(v) {
  return ["1", "true", "yes", "on"].includes(String(v).trim().toLowerCase());
}

async function loadNotificationSettings() {
  const s = await api("/api/notifications/settings");
  document.getElementById("notify-days-ahead").value = s.notify_days_ahead || 3;
  document.getElementById("notify-low-balance").value = s.notify_low_balance_threshold || 200;
  document.getElementById("notify-time").value = s.notify_time || "08:00";

  document.getElementById("ntfy-enabled").checked = isTruthy(s.ntfy_enabled);
  document.getElementById("ntfy-server").value = s.ntfy_server || "https://ntfy.sh";
  document.getElementById("ntfy-topic").value = s.ntfy_topic || "";

  document.getElementById("sms-gateway-enabled").checked = isTruthy(s.sms_gateway_enabled);
  document.getElementById("sms-gateway-phone").value = s.sms_gateway_phone || "";
  if (s.sms_gateway_carrier) document.getElementById("sms-gateway-carrier").value = s.sms_gateway_carrier;

  document.getElementById("smtp-host").value = s.smtp_host || "";
  document.getElementById("smtp-port").value = s.smtp_port || "587";
  document.getElementById("smtp-user").value = s.smtp_user || "";
  document.getElementById("smtp-pass").value = s.smtp_pass || "";

  document.getElementById("email-enabled").checked = isTruthy(s.email_enabled);
  document.getElementById("email-to").value = s.email_to || "";

  document.getElementById("twilio-enabled").checked = isTruthy(s.twilio_enabled);
  document.getElementById("twilio-sid").value = s.twilio_account_sid || "";
  document.getElementById("twilio-token").value = s.twilio_auth_token || "";
  document.getElementById("twilio-from").value = s.twilio_from_number || "";
  document.getElementById("twilio-to").value = s.twilio_to_number || "";

  loadNotificationHistory();
}

async function loadNotificationHistory() {
  const rows = await api("/api/notifications/history");
  const el = document.getElementById("notify-history");
  el.innerHTML = "";
  if (rows.length === 0) {
    el.innerHTML = '<div class="muted">No notifications sent yet.</div>';
    return;
  }
  rows.forEach(r => {
    const row = document.createElement("div");
    row.className = "list-row";
    row.innerHTML = `
      <div><div class="name">${r.channel}</div><div class="sub">${new Date(r.sent_at).toLocaleString()}</div></div>
      <div class="sub" style="max-width:400px; white-space:pre-wrap;">${r.message ? r.message.split("\n")[0] : ""}</div>`;
    el.appendChild(row);
  });
}

async function saveNotificationSettings(payload, savedBannerId) {
  await api("/api/notifications/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (savedBannerId) {
    const el = document.getElementById(savedBannerId);
    if (el) {
      el.classList.remove("hidden");
      setTimeout(() => el.classList.add("hidden"), 2000);
    }
  }
}

async function testChannel(channel, btn) {
  const original = btn.textContent;
  btn.textContent = "Sending...";
  btn.disabled = true;
  try {
    await api("/api/notifications/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel }),
    });
    alert(`Test ${channel} notification sent - check your phone/inbox.`);
  } catch (e) {
    alert(`Failed to send: ${e.message}`);
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

function initNotifications() {
  document.getElementById("btn-save-notify-general").addEventListener("click", () => {
    saveNotificationSettings({
      notify_days_ahead: document.getElementById("notify-days-ahead").value,
      notify_low_balance_threshold: document.getElementById("notify-low-balance").value,
      notify_time: document.getElementById("notify-time").value,
    }, "notify-general-saved");
  });

  document.getElementById("btn-run-check-now").addEventListener("click", async () => {
    const resultEl = document.getElementById("notify-run-result");
    resultEl.classList.remove("hidden", "banner-warn", "banner-good");
    resultEl.textContent = "Checking...";
    const res = await api("/api/notifications/run-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: true }),
    });
    resultEl.classList.add("banner-good");
    resultEl.textContent = res.sent
      ? `Sent: ${res.channels.map(c => `${c.channel} (${c.status})`).join(", ")}`
      : `Nothing to report: ${res.reason}`;
    loadNotificationHistory();
  });

  document.getElementById("btn-save-ntfy").addEventListener("click", () => {
    saveNotificationSettings({
      ntfy_enabled: document.getElementById("ntfy-enabled").checked,
      ntfy_server: document.getElementById("ntfy-server").value,
      ntfy_topic: document.getElementById("ntfy-topic").value,
    });
  });
  document.getElementById("btn-test-ntfy").addEventListener("click", (ev) => testChannel("ntfy", ev.target));
  document.getElementById("btn-gen-topic").addEventListener("click", () => {
    const rand = Array.from(crypto.getRandomValues(new Uint8Array(5)))
      .map(b => b.toString(36)).join("").slice(0, 8);
    document.getElementById("ntfy-topic").value = `budget-${rand}`;
  });

  document.getElementById("btn-save-sms-gateway").addEventListener("click", () => {
    saveNotificationSettings({
      sms_gateway_enabled: document.getElementById("sms-gateway-enabled").checked,
      sms_gateway_phone: document.getElementById("sms-gateway-phone").value,
      sms_gateway_carrier: document.getElementById("sms-gateway-carrier").value,
    });
  });
  document.getElementById("btn-test-sms-gateway").addEventListener("click", (ev) => testChannel("sms_gateway", ev.target));

  document.getElementById("btn-save-smtp").addEventListener("click", () => {
    saveNotificationSettings({
      smtp_host: document.getElementById("smtp-host").value,
      smtp_port: document.getElementById("smtp-port").value,
      smtp_user: document.getElementById("smtp-user").value,
      smtp_pass: document.getElementById("smtp-pass").value,
    });
  });

  document.getElementById("btn-save-email").addEventListener("click", () => {
    saveNotificationSettings({
      email_enabled: document.getElementById("email-enabled").checked,
      email_to: document.getElementById("email-to").value,
    });
  });
  document.getElementById("btn-test-email").addEventListener("click", (ev) => testChannel("email", ev.target));

  document.getElementById("btn-save-twilio").addEventListener("click", () => {
    saveNotificationSettings({
      twilio_enabled: document.getElementById("twilio-enabled").checked,
      twilio_account_sid: document.getElementById("twilio-sid").value,
      twilio_auth_token: document.getElementById("twilio-token").value,
      twilio_from_number: document.getElementById("twilio-from").value,
      twilio_to_number: document.getElementById("twilio-to").value,
    });
  });
  document.getElementById("btn-test-twilio").addEventListener("click", (ev) => testChannel("twilio", ev.target));
}

// --- Settings ------------------------------------------------------------------------

async function loadSettings() {
  const s = await api("/api/settings");
  document.getElementById("setting-balance").value = s.current_balance || "";
  document.getElementById("setting-balance-date").value = s.current_balance_date || new Date().toISOString().slice(0, 10);
}

function initSettings() {
  const feedUrlInput = document.getElementById("calendar-feed-url");
  if (feedUrlInput) {
    feedUrlInput.value = `${window.location.origin}/api/calendar.ics`;
  }
  const copyBtn = document.getElementById("btn-copy-calendar-url");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(feedUrlInput.value);
        copyBtn.textContent = "Copied!";
      } catch (e) {
        feedUrlInput.select();
        document.execCommand("copy");
        copyBtn.textContent = "Copied!";
      }
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
    });
  }

  document.getElementById("btn-save-settings").addEventListener("click", async () => {
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_balance: document.getElementById("setting-balance").value,
        current_balance_date: document.getElementById("setting-balance-date").value,
      }),
    });
    const saved = document.getElementById("settings-saved");
    saved.classList.remove("hidden");
    setTimeout(() => saved.classList.add("hidden"), 2000);
  });

  document.getElementById("btn-clear-data").addEventListener("click", async () => {
    const typed = prompt(
      "This permanently deletes every transaction, bill, budget, debt, and setting. " +
      "This cannot be undone.\n\nType RESET (all caps) to confirm:"
    );
    if (typed !== "RESET") {
      return;
    }
    const resultEl = document.getElementById("clear-data-result");
    resultEl.classList.remove("hidden", "banner-warn", "banner-good");
    resultEl.textContent = "Clearing...";
    try {
      await api("/api/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: "RESET" }),
      });
      resultEl.classList.add("banner-good");
      resultEl.textContent = "All data cleared. Reloading...";
      setTimeout(() => window.location.reload(), 1200);
    } catch (e) {
      resultEl.classList.add("banner-warn");
      resultEl.textContent = "Failed to clear data: " + e.message;
    }
  });
}

// --- Init ------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initCalendarNav();
  initBillModal();
  initImport();
  initBudgets();
  initReports();
  initBillTrends();
  initWhatIf();
  initDebts();
  initNotifications();
  initSettings();
  loadDashboard();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/static/sw.js").catch(() => {});
    });
  }
});
