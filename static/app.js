// IC-9700 CI-V Web Controller - Tree UI
const WS_URL = `ws://${location.host}/ws`;
let ws = null, reconnectTimer = null;
let currentFreq = 0, currentMode = "", currentFilter = "", pendingFilter = 1;

function sendCmd(action, payload = {}) {
  if (!ws || ws.readyState !== WebSocket.OPEN) { log("未连接", "err"); return; }
  const msg = JSON.stringify({ action, ...payload });
  ws.send(msg);
  log(`>> ${action}`, "tx");
}

function connectWS() {
  if (ws) return;
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      log("WebSocket 已连接", "tx");
      document.getElementById("conn-pill").textContent = "WS就绪";
      document.getElementById("conn-pill").className = "status-pill ok";
      clearTimeout(reconnectTimer);
    };
    ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
    ws.onclose = () => { ws = null; setConnPill(false); reconnectTimer = setTimeout(connectWS, 2000); };
    ws.onerror = () => log("WebSocket 错误", "err");
  } catch (e) { reconnectTimer = setTimeout(connectWS, 2000); }
}

let connMode = "serial";

function setConnPill(connected, mode) {
  const el = document.getElementById("conn-pill");
  if (connected) {
    el.textContent = mode === "lan" ? "LAN已连接" : "串口已连接";
    el.className = "status-pill ok";
  } else {
    el.textContent = "未连接";
    el.className = "status-pill err";
  }
}

function loadLanSettings() {
  try {
    const saved = localStorage.getItem("lan_settings");
    if (saved) {
      const cfg = JSON.parse(saved);
      if (cfg.host) document.getElementById("lan-host").value = cfg.host;
      if (cfg.username !== undefined) document.getElementById("lan-user").value = cfg.username;
      if (cfg.password !== undefined) document.getElementById("lan-pass").value = cfg.password;
      if (cfg.mode) {
        document.getElementById("conn-type").value = cfg.mode;
        updateConnTypeUI();
      }
    }
  } catch (e) { /* ignore */ }
}

function saveLanSettings() {
  try {
    const cfg = {
      host: document.getElementById("lan-host").value,
      username: document.getElementById("lan-user").value,
      password: document.getElementById("lan-pass").value,
      mode: document.getElementById("conn-type").value
    };
    localStorage.setItem("lan_settings", JSON.stringify(cfg));
  } catch (e) { /* ignore */ }
}

function updateConnTypeUI() {
  const type = document.getElementById("conn-type").value;
  connMode = type;
  document.getElementById("serial-ctrls").style.display = type === "serial" ? "" : "none";
  document.getElementById("lan-ctrls").style.display = type === "lan" ? "" : "none";
}

function handleMessage(msg) {
  if (msg.type === "civ_response") handleCIV(msg);
  else if (msg.type === "connection") {
    setConnPill(msg.connected, msg.mode || connMode);
    if (msg.connected && (msg.mode === "lan" || connMode === "lan")) {
      saveLanSettings();
    }
    if (msg.connected) {
      setTimeout(() => refreshActivePanel("home"), 500);
    }
  }
  else if (msg.type === "error") log(`错误: ${msg.message}`, "err");
}

function handleCIV(msg) {
  const cmd = msg.cmd, hex = msg.payload_hex || "";
  const payload = hexToBytes(hex);
  if (msg.event === "frequency") {
    currentFreq = msg.frequency || 0;
    document.getElementById("disp-freq").textContent = formatFreq(currentFreq);
    if (typeof updateSatDisplay === "function") updateSatDisplay();
  } else if (msg.event === "mode") {
    currentMode = msg.mode || ""; currentFilter = msg.filter || "";
    document.getElementById("disp-mode").textContent = currentMode;
    document.getElementById("disp-filter").textContent = currentFilter;
  } else if (msg.event === "level") {
    updateSlider(msg.subcmd, msg.value);
  } else if (msg.event === "meter") {
    updateMeter(msg.subcmd, msg.value);
  } else if (msg.event === "function") {
    updateSwitch(msg.subcmd, msg.value);
  } else if (msg.event === "tx_status") {
    const isTx = payload[0] === 1;
    const el = document.getElementById("disp-tx-status");
    el.textContent = isTx ? "TX" : "RX";
    el.className = "value " + (isTx ? "tx" : "rx");
  } else if (msg.event === "split_duplex") {
    updateSplitButtons(msg.value);
  } else if (msg.event === "tuning_step") {
    const sel = document.getElementById("sel-tuning-step");
    if (sel) sel.value = String(msg.value);
  } else if (msg.event === "attenuator") {
    updateSwitch(0x11, msg.value);
  } else if (msg.event === "xfc") {
    updateXFCButton(msg.value);
  } else if (msg.event === "tx_power_setting") {
    updateTXPowerButton(msg.value);
  } else if (msg.event === "rit") {
    updateRITButton(msg.value === 1);
  } else if (msg.event === "extended" && msg.item !== undefined) {
    updateExtControl(msg.item, msg.value);
  }
  log(`<< [${cmd.toString(16).padStart(2,'0').toUpperCase()}] ${hex}`, "rx");
}

function updateSplitButtons(val) {
  const onBtn = document.getElementById("btn-split-on");
  const offBtn = document.getElementById("btn-split-off");
  const isOn = val === 1;
  if (onBtn) { onBtn.className = "btn-toggle " + (isOn ? "on" : "off"); onBtn.style.opacity = isOn ? "1" : "0.5"; }
  if (offBtn) { offBtn.className = "btn-toggle " + (isOn ? "off" : "on"); offBtn.style.opacity = isOn ? "0.5" : "1"; }
}

function updateXFCButton(val) {
  const onBtn = document.getElementById("btn-xfc-on");
  const offBtn = document.getElementById("btn-xfc-off");
  const isOn = val === 1;
  if (onBtn) onBtn.className = "btn-toggle " + (isOn ? "on" : "off");
  if (offBtn) offBtn.className = "btn-toggle " + (isOn ? "off" : "on");
}

function updateTXPowerButton(val) {
  const btn = document.getElementById("btn-tx-pwr-set-on");
  const isOn = val === 1;
  if (btn) {
    btn.textContent = isOn ? "TX输出 ON" : "TX输出 OFF";
    btn.className = "btn-toggle " + (isOn ? "on" : "off");
  }
}

function updateRITButton(on) {
  const onBtn = document.getElementById("btn-rit-on");
  const offBtn = document.getElementById("btn-rit-off");
  if (onBtn) { onBtn.textContent = "RIT ON"; onBtn.className = "btn-toggle " + (on ? "on" : "off"); }
  if (offBtn) { offBtn.textContent = "RIT OFF"; offBtn.className = "btn-toggle " + (on ? "off" : "on"); }
}

function updateExtControl(item, value) {
  const itemHex = item.toString(16).padStart(4, '0').toLowerCase();
  const el = document.getElementById("ext-" + itemHex);
  if (el) {
    // value from backend: either integer (BCD decoded) or byte list
    const v = Array.isArray(value) ? (value.length >= 2 ? (value[0] << 8) | value[1] : (value.length > 0 ? value[0] : 0)) : value;
    if (el.tagName === "BUTTON") {
      el.textContent = v ? "ON" : "OFF";
      el.className = "btn-toggle " + (v ? "on" : "off");
      return;
    }
    if (el.tagName === "SELECT") {
      el.value = String(v);
      return;
    }
  }
  // Try slider (1A)
  const sliderId = "exts-" + itemHex;
  const s = document.getElementById(sliderId);
  if (s && value.length > 0) {
    const v = Array.isArray(value) ? (value.length >= 2 ? (value[0] << 8) | value[1] : value[0]) : value;
    s.value = v;
    const n = document.getElementById(sliderId + "-num");
    if (n) {
      const max = parseInt(s.max) || 255;
      n.textContent = Math.round(v * 100 / max);
    }
    return;
  }
}

function updateSlider(sub, val) {
  const id = `slider-${sub.toString(16).padStart(2,'0')}`;
  const s = document.getElementById(id);
  if (s) {
    s.value = val;
    const n = document.getElementById(id + "-num");
    if (n) {
      const max = parseInt(s.max) || 255;
      n.textContent = Math.round(val * 100 / max);
    }
  }
}

function updateSwitch(sub, val) {
  const id = `sw-${sub.toString(16).padStart(2,'0')}`;
  const btn = document.getElementById(id);
  if (btn) {
    const max = parseInt(btn.dataset.max);
    if (max === 1) { btn.textContent = val ? "ON" : "OFF"; btn.className = "btn-toggle " + (val ? "on" : "off"); }
    else { btn.textContent = String(val); }
  }
  const sel = document.getElementById(id + "-sel");
  if (sel) sel.value = String(val);
}

function updateMeter(sub, value) {
  const map = {
    0x02: { id:"meter-smeter", max:241, fmt:(v)=>{
      const s = Math.min(9, Math.floor(v / 13.33));
      return 'S' + s + (v > 120 ? '+' : '');
    }},
    0x11: { id:"meter-po", max:213, fmt:(v)=>`${Math.round(v*100/213)}%` },
    0x12: { id:"meter-swr", max:120, fmt:(v)=>{
      if(v<=0)return"1.0"; if(v<=48)return(1.0+v/96).toFixed(1);
      if(v<=80)return(1.5+(v-48)/64).toFixed(1); return(2.0+(v-80)/40).toFixed(1);
    }},
    0x13: { id:"meter-alc", max:120, fmt:(v)=>String(v) },
    0x14: { id:"meter-comp", max:210, fmt:(v)=>`${(v*15/130).toFixed(1)}dB` },
    0x15: { id:"meter-vd", max:241, fmt:(v)=>{
      if(v<=13)return(v*10/13).toFixed(1)+'V';
      return (10+(v-13)*6/(241-13)).toFixed(1)+'V';
    }},
    0x16: { id:"meter-id", max:241, fmt:(v)=>{
      if(v<=121)return(v*10/121).toFixed(1)+'A';
      return (10+(v-121)*10/(241-121)).toFixed(1)+'A';
    }},
  };
  const c = map[sub]; if (!c) return;
  const bar = document.getElementById(c.id)?.querySelector(".fill");
  const txt = document.getElementById("val-" + c.id.split("-")[1]);
  if (bar) bar.style.width = Math.min(100, (value/c.max)*100) + "%";
  if (txt) txt.textContent = c.fmt(value);
}

function formatFreq(hz) { return (hz/1e6).toFixed(6).replace(/\B(?=(\d{3})+(?!\d))/g, ",") + " MHz"; }
function hexToBytes(hex) { const b=[]; for(let i=0;i<hex.length;i+=2) b.push(parseInt(hex.substr(i,2),16)); return b; }

function log(text, type="info") {
  const box = document.getElementById("log-box"); if(!box) return;
  const line = document.createElement("div");
  line.className = type==="tx"?"log-tx":type==="rx"?"log-rx":type==="err"?"log-err":"";
  line.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
  box.appendChild(line); box.scrollTop = box.scrollHeight;
  while(box.children.length>300) box.removeChild(box.firstChild);
}

async function refreshPorts() {
  try {
    const data = await (await fetch("/api/ports")).json();
    const sel = document.getElementById("port-select");
    sel.innerHTML = "";
    data.ports.forEach(p=>{ const o=document.createElement("option"); o.value=p; o.textContent=p; sel.appendChild(o); });
  } catch(e){ log("无法获取端口","err"); }
}

// ========== Tree Navigation ==========
function initTree() {
  document.querySelectorAll(".tree-branch[data-expand]").forEach(branch => {
    branch.addEventListener("click", () => {
      const arrow = branch.querySelector(".arrow");
      const children = branch.nextElementSibling;
      const isOpen = children.classList.contains("open");
      children.classList.toggle("open", !isOpen);
      if (arrow) arrow.classList.toggle("open", !isOpen);
    });
  });
  document.querySelectorAll(".tree-branch[data-target], .tree-leaf[data-target]").forEach(leaf => {
    leaf.addEventListener("click", () => {
      const target = leaf.dataset.target;
      document.querySelectorAll(".tree-branch.active, .tree-leaf.active").forEach(el=>el.classList.remove("active"));
      leaf.classList.add("active");
      document.querySelectorAll(".panel-section.active").forEach(el=>el.classList.remove("active"));
      const panel = document.getElementById("panel-" + target);
      if (panel) { panel.classList.add("active"); refreshActivePanel(target); }
    });
  });
}

// ========== Panel-level CI-V refresh on page activation ==========
const PANEL_EXTRA_READS = {
  "home":       ["freq", "mode", "smeter", "tx_status"],
  "freq-mode":  ["mode", "tuning_step"],
  "split-duplex": ["split"],
  "rit-xfc":    ["xfc"],
  "tx-power":   ["tx_power_setting"],
  "agc-preamp": ["attenuator", "ext_agc"],
};

function sendPanelReads(reads) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ action: "poll_panel", reads: reads }));
}

function refreshActivePanel(panelId) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const panel = document.getElementById("panel-" + panelId);
  if (!panel) return;

  const reads = [];
  const seen = new Set();

  function add(r) {
    const key = r.type + ":" + (r.subcmd || r.item || "");
    if (seen.has(key)) return;
    seen.add(key);
    reads.push(r);
  }

  // Level reads from sliders
  panel.querySelectorAll('input[type="range"][data-sub]').forEach(el => {
    add({ type: "level", subcmd: parseInt(el.dataset.sub) });
  });
  // Function reads from buttons/selects (not sliders)
  panel.querySelectorAll('button[data-sub], select[data-sub]').forEach(el => {
    add({ type: "function", subcmd: parseInt(el.dataset.sub) });
  });
  // 1A_05 reads
  panel.querySelectorAll('[data-item]').forEach(el => {
    add({ type: "1a_05", item: parseInt(el.dataset.item) });
  });

  // Extra reads from the supplemental map
  const extras = PANEL_EXTRA_READS[panelId] || [];
  extras.forEach(t => {
    reads.push({ type: "special", target: t });
  });

  if (reads.length > 0) {
    sendPanelReads(reads);
  }
}

// ========== Panel Generators ==========
function makeSlider(container, subcmd, name, min=0, max=255) {
  const id = `slider-${subcmd.toString(16).padStart(2,'0')}`;
  const div = document.createElement("div"); div.className = "slider-box";
  const initPct = Math.round(128 * 100 / max);
  div.innerHTML = `<div class="top"><span class="name">${name}</span><span class="num" id="${id}-num">${initPct}</span></div>
    <input type="range" id="${id}" min="${min}" max="${max}" value="128" data-sub="${subcmd}">`;
  container.appendChild(div);
  const s = div.querySelector("input");
  s.addEventListener("input", ()=>{
    const pct = Math.round(parseInt(s.value) * 100 / max);
    document.getElementById(id+"-num").textContent = pct;
  });
  s.addEventListener("change", ()=>{ sendCmd("set_level", {subcmd: parseInt(s.dataset.sub), value: parseInt(s.value)}); });
}

function makeSwitch(container, subcmd, name, type="toggle", maxVal=1, options=null) {
  const id = `sw-${subcmd.toString(16).padStart(2,'0')}`;
  const div = document.createElement("div"); div.className = "switch-box";
  if (type === "toggle") {
    div.innerHTML = `<span class="name">${name}</span><button id="${id}" class="btn-toggle off" data-max="${maxVal}" data-sub="${subcmd}">OFF</button>`;
  } else if (type === "select") {
    const opts = (options || Array.from({length: maxVal+1}, (_,i)=>String(i)))
      .map((o,i)=>`<option value="${i}">${o}</option>`).join("");
    div.innerHTML = `<span class="name">${name}</span><select id="${id}-sel" data-sub="${subcmd}">${opts}</select>`;
  }
  container.appendChild(div);
  const ctrl = div.querySelector("button, select");
  if (ctrl.tagName === "BUTTON") {
    ctrl.addEventListener("click", ()=>{
      const isOn = ctrl.textContent === "ON";
      const next = isOn ? 0 : 1;
      ctrl.textContent = next ? "ON" : "OFF"; ctrl.className = "btn-toggle " + (next ? "on" : "off");
      sendCmd("set_function", {subcmd: parseInt(ctrl.dataset.sub), value: next});
    });
  } else {
    ctrl.addEventListener("change", ()=>{ sendCmd("set_function", {subcmd: parseInt(ctrl.dataset.sub), value: parseInt(ctrl.value)}); });
  }
}

function makeSelect(container, item, name, options) {
  const id = `ext-${item.toString(16).padStart(4,'0')}`;
  const div = document.createElement("div"); div.className = "select-box";
  const opts = options.map((o,i)=>`<option value="${i}">${o}</option>`).join("");
  div.innerHTML = `<div class="name">${name}</div><select id="${id}" data-item="${item}">${opts}</select>`;
  container.appendChild(div);
  div.querySelector("select").addEventListener("change", (e)=>{
    sendCmd("set_1a_05", {item: item, value: parseInt(e.target.value)});
  });
}

function makeToggle(container, item, name) {
  const id = `ext-${item.toString(16).padStart(4,'0')}`;
  const div = document.createElement("div"); div.className = "switch-box";
  div.innerHTML = `<span class="name">${name}</span><button id="${id}" class="btn-toggle off" data-item="${item}">OFF</button>`;
  container.appendChild(div);
  const btn = div.querySelector("button");
  btn.addEventListener("click", ()=>{
    const next = btn.textContent === "ON" ? 0 : 1;
    btn.textContent = next ? "ON" : "OFF"; btn.className = "btn-toggle " + (next ? "on" : "off");
    sendCmd("set_1a_05", {item: item, value: next});
  });
}

function makeSlider1A(container, item, name, min=0, max=255) {
  const id = `exts-${item.toString(16).padStart(4,'0')}`;
  const midVal = Math.floor((min+max)/2);
  const midPct = Math.round(midVal * 100 / max);
  const div = document.createElement("div"); div.className = "slider-box";
  div.innerHTML = `<div class="top"><span class="name">${name}</span><span class="num" id="${id}-num">${midPct}</span></div>
    <input type="range" id="${id}" min="${min}" max="${max}" value="${midVal}" data-item="${item}">`;
  container.appendChild(div);
  const s = div.querySelector("input");
  s.addEventListener("input", ()=>{
    const pct = Math.round(parseInt(s.value) * 100 / max);
    document.getElementById(id+"-num").textContent = pct;
  });
  s.addEventListener("change", ()=>{ sendCmd("set_1a_05", {item: item, value: parseInt(s.value)}); });
}

// ========== Init Panels ==========
function initPanels() {
  // TX Power slider
  makeSlider(document.getElementById("txpower-sliders"), 0x0A, "RF功率 (0-255)", 0, 255);

  // RX Levels
  const rxLevels = document.getElementById("rxlevels-sliders");
  makeSlider(rxLevels, 0x01, "AF音量", 0, 255);
  makeSlider(rxLevels, 0x02, "RF增益", 0, 255);
  makeSlider(rxLevels, 0x03, "静噪 SQL", 0, 255);

  // PBT
  const pbtSliders = document.getElementById("pbt-sliders");
  makeSlider(pbtSliders, 0x07, "PBT1 (CCW-CW)", 0, 255);
  makeSlider(pbtSliders, 0x08, "PBT2 (CCW-CW)", 0, 255);
  const pbtSw = document.getElementById("pbt-switches");
  makeSwitch(pbtSw, 0x4F, "双峰值滤波器");
  makeSwitch(pbtSw, 0x50, "拨号锁定");

  // NB
  const nbSliders = document.getElementById("nb-sliders");
  makeSlider(nbSliders, 0x12, "NB电平", 0, 255);
  const nbSw = document.getElementById("nb-switches");
  makeSwitch(nbSw, 0x22, "噪声消除 NB");

  // NR
  const nrSliders = document.getElementById("nr-sliders");
  makeSlider(nrSliders, 0x06, "NR电平 (0-100%)", 0, 255);
  const nrSw = document.getElementById("nr-switches");
  makeSwitch(nrSw, 0x40, "降噪 NR");
  makeSwitch(nrSw, 0x41, "自动陷波");

  // Notch
  const notchSliders = document.getElementById("notch-sliders");
  makeSlider(notchSliders, 0x0D, "陷波器位置", 0, 255);
  const notchSw = document.getElementById("notch-switches");
  makeSwitch(notchSw, 0x48, "手动陷波");
  makeSwitch(notchSw, 0x57, "陷波宽度", "select", 2, ["WIDE","MID","NAR"]);

  // AGC / Preamp
  const preampSw = document.getElementById("preamp-switches");
  makeSwitch(preampSw, 0x02, "前置放大器", "select", 3, ["OFF","P.AMP ON","EXT-P.AMP ON","BOTH ON"]);
  makeSwitch(preampSw, 0x11, "衰减器", "select", 1, ["OFF","10dB"]);
  // Extended AGC (1A 04): OFF + 14 fine steps
  const agcSw = document.getElementById("agc-switches");
  const agcNames = ["OFF (关闭)", "0.1s (快)", "0.2s", "0.3s", "0.5s", "0.8s", "1.2s", "1.6s", "2.0s", "2.5s", "3.0s", "4.0s", "5.0s", "6.0s"];
  const agcDiv = document.createElement("div"); agcDiv.className = "switch-box";
  agcDiv.innerHTML = `<span class="name">AGC时间</span><select id="ext-1a04">
    ${agcNames.map((n,i)=>`<option value="${i}">${n}</option>`).join("")}
  </select>`;
  agcSw.appendChild(agcDiv);
  agcDiv.querySelector("select").addEventListener("change", (e)=>{
    sendCmd("set_ext_agc", {value: parseInt(e.target.value)});
  });

  // MIC Audio
  const micSliders = document.getElementById("mic-sliders");
  makeSlider(micSliders, 0x0B, "MIC增益", 0, 255);
  makeSlider(micSliders, 0x0E, "COMP电平", 0, 255);
  makeSlider(micSliders, 0x0C, "键速 (6-48 WPM)", 0, 255);
  makeSlider(micSliders, 0x09, "CW音调 (300-900Hz)", 0, 255);
  const micSw = document.getElementById("mic-switches");
  makeSwitch(micSw, 0x44, "语音压缩器");
  makeSwitch(micSw, 0x42, "中继音");
  makeSwitch(micSw, 0x43, "音静噪");
  makeSwitch(micSw, 0x4B, "DTCS");
  makeSwitch(micSw, 0x5B, "DSQL/CSQL", "select", 2, ["OFF","DSQL","CSQL"]);
  makeSwitch(micSw, 0x58, "SSB发射带宽", "select", 2, ["WIDE","MID","NAR"]);
  makeSwitch(micSw, 0x56, "DSP IF滤波器", "select", 1, ["SHARP","SOFT"]);
  makeSwitch(micSw, 0x65, "IP Plus");

  // MONI / VOX
  const moniSliders = document.getElementById("moni-sliders");
  makeSlider(moniSliders, 0x15, "监听电平", 0, 255);
  makeSlider(moniSliders, 0x16, "VOX增益", 0, 255);
  makeSlider(moniSliders, 0x17, "Anti-VOX", 0, 255);
  makeSlider(moniSliders, 0x0F, "Break-In延迟", 0, 255);
  const moniSw = document.getElementById("moni-switches");
  makeSwitch(moniSw, 0x45, "监听 MONI");
  makeSwitch(moniSw, 0x46, "VOX");
  makeSwitch(moniSw, 0x47, "BK-IN", "select", 2, ["OFF","Semi","Full"]);

  // ========== Function Settings (1A 05) ==========
  // Beep
  const fb = document.getElementById("func-beep-container");
  makeSlider1A(fb, 0x0027, "Beep电平", 0, 255);
  makeToggle(fb, 0x0028, "Beep电平限制");
  makeToggle(fb, 0x0029, "Beep确认音");
  makeSelect(fb, 0x0030, "频段边缘Beep", ["OFF","ON (默认)","ON (用户)","ON (用户)&TX限制"]);
  makeSlider1A(fb, 0x0031, "Beep音调 主频段 (500-2000Hz)", 50, 200);
  makeSlider1A(fb, 0x0032, "Beep音调 副频段 (500-2000Hz)", 50, 200);

  // TX Delay / Timer
  const ftx = document.getElementById("func-tx-container");
  makeSelect(ftx, 0x0038, "TX延迟 144M", ["OFF","10ms","15ms","20ms","25ms","30ms"]);
  makeSelect(ftx, 0x0039, "TX延迟 430M", ["OFF","10ms","15ms","20ms","25ms","30ms"]);
  makeSelect(ftx, 0x0040, "TX延迟 1200M", ["OFF","10ms","15ms","20ms","25ms","30ms"]);
  makeSelect(ftx, 0x0041, "超时定时器", ["OFF","3分","5分","10分","20分","30分"]);

  // Split / Repeater
  const fsp = document.getElementById("func-split-container");
  makeToggle(fsp, 0x0042, "PTT锁定");
  makeToggle(fsp, 0x0043, "快速Split");
  makeToggle(fsp, 0x0045, "Split锁定");
  makeSelect(fsp, 0x0046, "自动中继器", ["OFF","ON (DUP,TONE)"]);

  // RTTY
  const frt = document.getElementById("func-rtty-container");
  makeSelect(frt, 0x0047, "RTTY Mark频率", ["1275Hz","1615Hz","2125Hz"]);
  makeSelect(frt, 0x0048, "RTTY频移", ["170Hz","200Hz","425Hz"]);
  makeSelect(frt, 0x0049, "RTTY键控极性", ["正常","反转"]);

  // Speech
  const fspch = document.getElementById("func-speech-container");
  makeSelect(fspch, 0x0050, "语音语言", ["英语","日语"]);
  makeSelect(fspch, 0x0051, "字母读音", ["正常","音标码"]);
  makeSelect(fspch, 0x0052, "语音速度", ["慢","快"]);
  makeSelect(fspch, 0x0053, "RX呼号语音", ["OFF","Kerchunk","All"]);
  makeToggle(fspch, 0x0054, "RX>CS语音");
  makeToggle(fspch, 0x0055, "S-Level语音");
  makeToggle(fspch, 0x0056, "模式语音");
  makeSlider1A(fspch, 0x0057, "语音电平", 0, 255);

  // Lock / Keyboard
  const flk = document.getElementById("func-lock-container");
  makeSelect(flk, 0x0058, "SPEECH/LOCK切换", ["SPEECH/LOCK","LOCK/SPEECH"]);
  makeSelect(flk, 0x0059, "锁定功能", ["主旋钮","面板"]);
  makeSelect(flk, 0x0060, "备忘录数量", ["5ch","10ch"]);
  makeSelect(flk, 0x0061, "主旋钮自动步进", ["OFF","Low","High"]);
  makeSelect(flk, 0x0062, "MIC上下速度", ["慢","快"]);
  makeToggle(flk, 0x0063, "AFC限制");
  makeSelect(flk, 0x0064, "陷波开关(SSB)", ["自动","手动","自动/手动"]);
  makeSelect(flk, 0x0065, "陷波开关(AM)", ["自动","手动","自动/手动"]);
  makeToggle(flk, 0x0066, "SSB/CW同步调谐");
  makeSelect(flk, 0x0067, "CW常规边带", ["LSB","USB"]);
  makeSelect(flk, 0x0068, "屏幕键盘类型", ["十键","全键盘"]);
  makeSelect(flk, 0x0069, "全键盘布局", ["英语","德语","法语"]);

  // Other Function
  const fot = document.getElementById("func-other-container");
  makeToggle(fot, 0x0070, "截屏电源键");
  makeSelect(fot, 0x0071, "截屏文件类型", ["PNG","BMP"]);
  makeSlider1A(fot, 0x0072, "REF调整", 0, 255);
  makeSlider1A(fot, 0x0073, "REF微调", 0, 255);

  // ========== DV/DD ==========
  const dvb = document.getElementById("dv-basic-container");
  makeSelect(dvb, 0x0074, "待机Beep", ["OFF","ON","To me(高音)","To me(警报/高音)"]);
  makeSelect(dvb, 0x0075, "自动回复", ["OFF","ON","Voice"]);
  makeSelect(dvb, 0x0076, "DV数据TX", ["PTT","自动"]);

  const dvd = document.getElementById("dv-data-container");
  makeToggle(dvd, 0x0077, "DV快速数据");
  makeSelect(dvd, 0x0078, "GPS数据速度", ["慢","快"]);
  makeSelect(dvd, 0x0079, "TX延迟(PTT)", ["OFF","1s","2s","3s","4s","5s","6s","7s","8s","9s","10s"]);

  const dvm = document.getElementById("dv-monitor-container");
  makeSelect(dvm, 0x0080, "数字监听", ["自动","数字","模拟"]);
  makeToggle(dvm, 0x0081, "数字中继器设置");
  makeToggle(dvm, 0x0082, "DV自动检测");
  makeSelect(dvm, 0x0083, "RX录音(RPT)", ["全部","仅最新"]);

  const dvdd = document.getElementById("dv-dd-container");
  makeToggle(dvdd, 0x0084, "BK");
  makeToggle(dvdd, 0x0085, "EMR");
  makeSlider1A(dvdd, 0x0086, "EMR AF电平", 0, 255);
  makeToggle(dvdd, 0x0087, "DD TX抑制(开机)");
  makeSelect(dvdd, 0x0088, "DD包输出", ["正常","全部"]);

  // ========== Connectors ==========
  const cp = document.getElementById("conn-preamp-container");
  makeToggle(cp, 0x0093, "外接前置放大 144M");
  makeToggle(cp, 0x0094, "外接前置放大 430M");
  makeToggle(cp, 0x0095, "外接前置放大 1200M");
  makeSelect(cp, 0x0096, "外接扬声器分离", ["分离","混合"]);
  makeSelect(cp, 0x0097, "耳机电平", Array.from({length:31},(_,i)=>`${i-15}dB`));
  makeSelect(cp, 0x0098, "耳机L/R混合", ["分离","混合","自动"]);

  const cacc = document.getElementById("conn-acc-container");
  makeSelect(cacc, 0x0099, "ACC AF/SQL输出", ["主","副"]);
  makeSelect(cacc, 0x0100, "ACC输出选择", ["AF","IF"]);
  makeSlider1A(cacc, 0x0101, "ACC AF输出电平", 0, 255);
  makeToggle(cacc, 0x0102, "ACC AF静噪");
  makeToggle(cacc, 0x0103, "ACC Beep/语音输出");
  makeSlider1A(cacc, 0x0104, "ACC IF输出电平", 0, 255);
  makeToggle(cacc, 0x0117, "ACC SEND 144M");
  makeToggle(cacc, 0x0118, "ACC SEND 430M");
  makeToggle(cacc, 0x0119, "ACC SEND 1200M");

  const cusb = document.getElementById("conn-usb-container");
  makeSelect(cusb, 0x0105, "USB输出选择", ["AF","IF"]);
  makeSlider1A(cusb, 0x0106, "USB AF输出电平", 0, 255);
  makeToggle(cusb, 0x0107, "USB AF静噪");
  makeToggle(cusb, 0x0108, "USB Beep/语音输出");
  makeSlider1A(cusb, 0x0109, "USB IF输出电平", 0, 255);
  makeSelect(cusb, 0x0120, "USB SEND", ["OFF","USB(A) DTR","USB(A) RTS","USB(B) DTR","USB(B) RTS"]);
  makeSelect(cusb, 0x0121, "USB键控(CW)", ["OFF","USB(A) DTR","USB(A) RTS","USB(B) DTR","USB(B) RTS"]);
  makeSelect(cusb, 0x0122, "USB键控(RTTY)", ["OFF","USB(A) DTR","USB(A) RTS","USB(B) DTR","USB(B) RTS"]);
  makeToggle(cusb, 0x0123, "USB连接禁止定时器");

  const clan = document.getElementById("conn-lan-container");
  makeSelect(clan, 0x0110, "LAN输出选择", ["AF","IF"]);
  makeToggle(clan, 0x0111, "LAN AF静噪");
  // MOD level presets: use select with CI-V values as option values
  function makeModSelect(name, item) {
    const div = document.createElement("div"); div.className = "switch-box";
    const id = "ext-" + item.toString(16).padStart(4,'0');
    div.innerHTML = `<span class="name">${name}</span><select id="${id}" data-item="${item}">
      <option value="0">0%</option>
      <option value="64">25%</option>
      <option value="128">50%</option>
      <option value="191">75%</option>
      <option value="255">100%</option>
    </select>`;
    div.querySelector("select").addEventListener("change", (e) => {
      sendCmd("set_1a_05", {item: item, value: parseInt(e.target.value)});
    });
    return div;
  }
  clan.appendChild(makeModSelect("ACC MOD", 0x0112));
  clan.appendChild(makeModSelect("USB MOD", 0x0113));
  clan.appendChild(makeModSelect("LAN MOD", 0x0114));
  makeSelect(clan, 0x0115, "DATA OFF MOD", ["MIC","ACC","MIC,ACC","USB","MIC,USB","LAN"]);
  makeSelect(clan, 0x0116, "DATA MOD", ["MIC","ACC","MIC,ACC","USB","MIC,USB","LAN"]);

  const cciv = document.getElementById("conn-civ-container");
  makeToggle(cciv, 0x0124, "外接键盘语音");
  makeToggle(cciv, 0x0125, "外接键盘键控器");
  makeToggle(cciv, 0x0126, "外接键盘RTTY");
  makeToggle(cciv, 0x0127, "CI-V收发");
  makeSelect(cciv, 0x0129, "CI-V USB端口", ["Link to REMOTE","Unlink"]);
  makeToggle(cciv, 0x0130, "CI-V USB回显");
  makeToggle(cciv, 0x0131, "CI-V DATA回显");
  makeSelect(cciv, 0x0132, "USB(B)功能", ["OFF","RTTY解码","DV数据"]);
  makeSelect(cciv, 0x0133, "DATA功能", ["OFF","RTTY解码","DV数据","GPS/气象","CI-V"]);
  makeToggle(cciv, 0x0134, "GPS输出");
  makeSelect(cciv, 0x0135, "DV/GPS波特率", ["4800","9600"]);
  makeSelect(cciv, 0x0136, "RTTY解码波特率", ["4800","9600","19200","38400"]);

  // ========== Network ==========
  const nbasic = document.getElementById("net-basic-container");
  makeToggle(nbasic, 0x0137, "DHCP");
  makeSelect(nbasic, 0x0140, "子网掩码", Array.from({length:30},(_,i)=>`${i+1} bits`));
  makeSelect(nbasic, 0x0145, "网络控制", ["OFF","ON"]);

  const nport = document.getElementById("net-port-container");
  makeSelect(nport, 0x0146, "远程关机设置", ["仅关机","待机/关机"]);
  makeSelect(nport, 0x0150, "互联网接入线路", ["FTTH","ADSL/CATV"]);

  // ========== Display ==========
  const dlcd = document.getElementById("disp-lcd-container");
  makeSlider1A(dlcd, 0x0152, "LCD背光", 0, 255);
  makeSlider1A(dlcd, 0x0019, "背光亮度 (14命令)", 0, 255);
  makeSelect(dlcd, 0x0153, "显示类型", ["A","B"]);
  makeSelect(dlcd, 0x0154, "显示字体", ["基础","圆体"]);

  const dcs = document.getElementById("disp-callsign-container");
  makeToggle(dcs, 0x0155, "表头峰值保持");
  makeToggle(dcs, 0x0156, "记忆名称");
  makeToggle(dcs, 0x0157, "MN-Q弹出");
  makeToggle(dcs, 0x0158, "BW弹出(PBT)");
  makeToggle(dcs, 0x0159, "BW弹出(FIL)");
  makeSelect(dcs, 0x0160, "RX呼号显示", ["OFF","正常","RX保持","保持"]);
  makeToggle(dcs, 0x0161, "RX位置指示器");
  makeSelect(dcs, 0x0162, "RX位置显示", ["OFF","主/副","仅主"]);
  makeSelect(dcs, 0x0163, "RX位置显示定时器", ["5s","10s","15s","30s","保持"]);
  makeToggle(dcs, 0x0164, "回复位置显示");
  makeSelect(dcs, 0x0165, "TX呼号显示", ["OFF","你的呼号","我的呼号"]);
  makeSelect(dcs, 0x0166, "滚动速度", ["慢","快"]);
  makeSelect(dcs, 0x0167, "屏保", ["OFF","15分","30分","60分"]);
  makeToggle(dcs, 0x0168, "开机信息");
  makeToggle(dcs, 0x0169, "开机确认");

  const dunit = document.getElementById("disp-unit-container");
  makeSelect(dunit, 0x0170, "纬度/经度单位", ["ddd°mm.mm'","ddd°mm'ss\""]);
  makeSelect(dunit, 0x0171, "高度/距离单位", ["m","ft/mi"]);
  makeSelect(dunit, 0x0172, "速度单位", ["km/h","mph","knots"]);
  makeSelect(dunit, 0x0173, "温度单位", ["°C","°F"]);
  makeSelect(dunit, 0x0174, "气压单位", ["hPa","mb","mmHg","inHg"]);
  makeSelect(dunit, 0x0175, "降雨量单位", ["mm","inch"]);
  makeSelect(dunit, 0x0176, "显示语言", ["英语","日语"]);
  makeSelect(dunit, 0x0177, "系统语言", ["英语","日语"]);

  // ========== Time ==========
  const tbase = document.getElementById("time-basic-container");
  makeToggle(tbase, 0x0181, "NTP功能");
  makeToggle(tbase, 0x0183, "GPS时间校正");

  const tgps = document.getElementById("time-gps-container");
  // UTC offset is complex, skip for now or add raw input later

  // ========== Scope ==========
  const scd = document.getElementById("scope-display-container");
  makeToggle(scd, 0x0187, "发射时频谱");
  makeSelect(scd, 0x0188, "Max保持", ["OFF","10s保持","ON"]);
  makeSelect(scd, 0x0189, "CENTER类型显示", ["滤波器中心","载波点中心","载波点中心(绝对频率)"]);
  makeSelect(scd, 0x0190, "标记位置(固定类型)", ["滤波器中心","载波点"]);
  makeSelect(scd, 0x0192, "平均", ["OFF","2","3","4"]);
  makeSelect(scd, 0x0193, "波形类型", ["填充","填充+线条"]);
  makeToggle(scd, 0x0197, "瀑布图显示");
  makeSelect(scd, 0x0198, "瀑布图速度", ["慢","中","快"]);
  makeSelect(scd, 0x0199, "瀑布图尺寸", ["小","中","大"]);
  makeSelect(scd, 0x0200, "瀑布图峰值颜色", ["Grid1","Grid2","Grid3","Grid4","Grid5","Grid6","Grid7","Grid8"]);
  makeToggle(scd, 0x0201, "瀑布图标记自动隐藏");

  const sce = document.getElementById("scope-edge-container");
  // Fixed edges are complex frequency settings, left for raw command

  // ========== Audio Scope / Voice TX ==========
  const asc = document.getElementById("audio-scope-container");
  makeSelect(asc, 0x0211, "FFT波形类型", ["线条","填充"]);
  makeToggle(asc, 0x0213, "FFT瀑布图");

  const vts = document.getElementById("voice-tx-set-container");
  makeSlider1A(vts, 0x0215, "语音TX电平", 0, 255);
  makeToggle(vts, 0x0216, "自动监听");
  makeSelect(vts, 0x0217, "重复时间", Array.from({length:15},(_,i)=>`${i+1}s`));

  // ========== Keyer / CW / RTTY ==========
  const ky = document.getElementById("keyer-container");
  makeSelect(ky, 0x0218, "数字样式", ["正常","190→ANO","190→ANT","90→NO","90→NT"]);
  makeSelect(ky, 0x0219, "计数触发", Array.from({length:8},(_,i)=>`M${i+1}`));

  const cw = document.getElementById("cw-key-container");
  makeSlider1A(cw, 0x0221, "侧音电平", 0, 255);
  makeToggle(cw, 0x0222, "侧音电平限制");
  makeSelect(cw, 0x0223, "键控器重复时间", Array.from({length:60},(_,i)=>`${i+1}s`));
  makeSelect(cw, 0x0224, "点划比", Array.from({length:18},(_,i)=>`${(2.8+i*0.1).toFixed(1)}`));
  makeSelect(cw, 0x0225, "上升时间", ["2ms","4ms","6ms","8ms"]);
  makeSelect(cw, 0x0226, "桨极性", ["正常","反转"]);
  makeSelect(cw, 0x0227, "键类型", ["直键","Bug","桨"]);
  makeToggle(cw, 0x0228, "MIC上下键控器");

  const rtty = document.getElementById("rtty-decode-container");
  makeSelect(rtty, 0x0229, "FFT平均", ["OFF","2","3","4"]);
  makeToggle(rtty, 0x0231, "解码USOS");
  makeSelect(rtty, 0x0232, "换行码", ["CR,LF,CR+LF","CR+LF"]);
  makeToggle(rtty, 0x0233, "TX USOS");
  makeSelect(rtty, 0x0234, "卫星TX显示字符", ["RX显示","TX显示"]);
  makeToggle(rtty, 0x0237, "解码日志");
  makeSelect(rtty, 0x0238, "日志文件类型", ["文本","HTML"]);
  makeToggle(rtty, 0x0239, "日志时间戳");
  makeSelect(rtty, 0x0240, "日志时间戳(时间)", ["本地","UTC"]);
  makeToggle(rtty, 0x0241, "日志时间戳(频率)");

  // ========== Recorder / Scan / GPS / DTMF ==========
  const rec = document.getElementById("recorder-container");
  makeSelect(rec, 0x0242, "录音TX音频", ["直接","监听"]);
  makeSelect(rec, 0x0243, "录音RX条件", ["始终","静噪自动"]);
  makeToggle(rec, 0x0244, "文件分割");
  makeSelect(rec, 0x0245, "录音操作", ["主/副分离","主/副联动"]);
  makeToggle(rec, 0x0246, "PTT自动录音");
  makeSelect(rec, 0x0247, "PTT自动录音预录", ["OFF","5s","10s","15s"]);
  makeSelect(rec, 0x0248, "跳过时间", ["3s","5s","10s","30s"]);

  const ss = document.getElementById("scan-set-container");
  makeSelect(ss, 0x0249, "扫描速度", ["慢","快"]);
  makeToggle(ss, 0x0250, "扫描恢复");
  makeSelect(ss, 0x0251, "暂停定时器", ["2s","4s","6s","8s","10s","12s","14s","16s","18s","20s","保持"]);
  makeSelect(ss, 0x0252, "恢复定时器", ["0s","1s","2s","3s","4s","5s","保持"]);
  makeSelect(ss, 0x0253, "临时跳过定时器", ["5分","10分","15分","扫描中","开机期间"]);
  makeToggle(ss, 0x0254, "扫描主旋钮操作");

  const gps = document.getElementById("gps-container");
  makeSelect(gps, 0x0255, "GPS选择", ["OFF","外接GPS","手动"]);
  makeSelect(gps, 0x0256, "GPS接收器波特率", ["4800","9600"]);
  makeSelect(gps, 0x0258, "GPS TX模式", ["OFF","D-PRS","NMEA"]);
  makeSelect(gps, 0x0260, "D-PRS TX格式", ["位置","对象","项目","气象"]);
  makeToggle(gps, 0x0273, "D-PRS高度");

  const dtmf = document.getElementById("dtmf-container");
  makeSelect(dtmf, 0x0320, "DTMF速度", ["100ms","200ms","300ms","500ms"]);
  makeSlider1A(dtmf, 0x0321, "NB电平 144M", 0, 255);
  makeSelect(dtmf, 0x0322, "NB深度 144M", Array.from({length:10},(_,i)=>`${i+1}`));
  makeSlider1A(dtmf, 0x0323, "NB宽度 144M", 0, 255);
  makeSlider1A(dtmf, 0x0324, "NB电平 430M", 0, 255);
  makeSelect(dtmf, 0x0325, "NB深度 430M", Array.from({length:10},(_,i)=>`${i+1}`));
  makeSlider1A(dtmf, 0x0326, "NB宽度 430M", 0, 255);
  makeSlider1A(dtmf, 0x0327, "NB电平 1200M", 0, 255);
  makeSelect(dtmf, 0x0328, "NB深度 1200M", Array.from({length:10},(_,i)=>`${i+1}`));
  makeSlider1A(dtmf, 0x0329, "NB宽度 1200M", 0, 255);
  makeSelect(dtmf, 0x0330, "VOX延迟", Array.from({length:21},(_,i)=>`${(i*0.1).toFixed(1)}s`));
  makeSelect(dtmf, 0x0331, "VOX语音延迟", ["OFF","短","中","长"]);
  makeToggle(dtmf, 0x0332, "TX功率限制 144M");
  makeSlider1A(dtmf, 0x0333, "TX功率限制值 144M", 0, 255);
  makeToggle(dtmf, 0x0334, "TX功率限制 430M");
  makeSlider1A(dtmf, 0x0335, "TX功率限制值 430M", 0, 255);
  makeToggle(dtmf, 0x0336, "TX功率限制 1200M");
  makeSlider1A(dtmf, 0x0337, "TX功率限制值 1200M", 0, 255);
  makeSelect(dtmf, 0x0338, "接收呼号显示", ["呼号","名称"]);
  makeSelect(dtmf, 0x0339, "指南针方向", ["航向朝上","北朝上","南朝上"]);
}

// ========== Events ==========
document.addEventListener("DOMContentLoaded", () => {
  initTree();
  initPanels();
  connectWS();
  refreshPorts();
  loadLanSettings();

  document.getElementById("btn-refresh-ports").addEventListener("click", refreshPorts);
  document.getElementById("conn-type").addEventListener("change", updateConnTypeUI);
  document.getElementById("btn-connect").addEventListener("click", ()=>{
    const type = document.getElementById("conn-type").value;
    if (type === "lan") {
      sendCmd("connect_lan", {
        host: document.getElementById("lan-host").value,
        username: document.getElementById("lan-user").value,
        password: document.getElementById("lan-pass").value,
        control_port: 50001,
        civ_port: 50002
      });
    } else {
      sendCmd("connect", {port: document.getElementById("port-select").value, baudrate: parseInt(document.getElementById("baud-select").value)});
    }
  });
  document.getElementById("btn-disconnect").addEventListener("click", ()=>sendCmd("disconnect"));

  document.getElementById("btn-set-freq").addEventListener("click", ()=>{
    const v = parseInt(document.getElementById("input-freq").value);
    if(!isNaN(v)) sendCmd("set_frequency", {freq:v});
  });
  document.getElementById("btn-poll-freq").addEventListener("click", ()=>sendCmd("poll", {targets:["freq"]}));
  document.getElementById("btn-poll-mode").addEventListener("click", ()=>sendCmd("poll", {targets:["mode"]}));

  document.getElementById("btn-mem-select").addEventListener("click", ()=>sendCmd("memory", {channel: parseInt(document.getElementById("input-channel").value)}));
  document.getElementById("btn-mem-write").addEventListener("click", ()=>sendCmd("memory_write"));
  document.getElementById("btn-mem-copy").addEventListener("click", ()=>sendCmd("memory_copy_vfo"));
  document.getElementById("btn-mem-clear").addEventListener("click", ()=>sendCmd("memory_clear"));

  document.getElementById("btn-split-on").addEventListener("click", ()=>sendCmd("set_split", {on:true}));
  document.getElementById("btn-split-off").addEventListener("click", ()=>sendCmd("set_split", {on:false}));

  document.getElementById("btn-set-step").addEventListener("click", ()=>sendCmd("set_tuning_step", {step: parseInt(document.getElementById("sel-tuning-step").value)}));

  document.getElementById("btn-read-main").addEventListener("click", ()=>sendCmd("raw", {data:"FEFEA2E007D200FD"}));
  document.getElementById("btn-read-sub").addEventListener("click", ()=>sendCmd("raw", {data:"FEFEA2E007D201FD"}));

  document.getElementById("btn-rit-on").addEventListener("click", function(){ sendCmd("set_rit", {on:true}); this.textContent="RIT ON"; this.className="btn-toggle on"; });
  document.getElementById("btn-rit-off").addEventListener("click", function(){ sendCmd("set_rit", {on:false}); this.textContent="RIT OFF"; this.className="btn-toggle off"; });
  document.getElementById("btn-rit-set").addEventListener("click", ()=>{
    const f = parseInt(document.getElementById("input-rit").value);
    const dir = document.getElementById("sel-rit-dir").value;
    sendCmd("set_rit_freq", {freq: f, direction: dir});
  });

  document.getElementById("btn-xfc-on").addEventListener("click", function(){ sendCmd("set_xfc", {on:true}); this.textContent="XFC ON"; this.className="btn-toggle on"; });
  document.getElementById("btn-xfc-off").addEventListener("click", function(){ sendCmd("set_xfc", {on:false}); this.textContent="XFC OFF"; this.className="btn-toggle off"; });

  document.getElementById("btn-tx-pwr-set-on").addEventListener("click", function(){
    const on = this.textContent === "TX输出 OFF";
    sendCmd("set_tx_power_setting", {on: !on});
    this.textContent = on ? "TX输出 ON" : "TX输出 OFF";
    this.className = "btn-toggle " + (on ? "on" : "off");
  });
  document.getElementById("btn-read-tx-pwr").addEventListener("click", ()=>sendCmd("read_tx_power_setting"));

  document.getElementById("btn-voice-play").addEventListener("click", ()=>sendCmd("voice_tx", {channel: parseInt(document.getElementById("input-voice").value)}));
  document.getElementById("btn-voice-stop").addEventListener("click", ()=>sendCmd("voice_tx", {channel:0}));

  document.getElementById("btn-cw-send").addEventListener("click", ()=>{
    // CW message command 17
    const text = document.getElementById("input-cw-msg").value;
    if(!text) return;
    const bytes = Array.from(text).map(c=>c.charCodeAt(0).toString(16).padStart(2,'0')).join('');
    sendCmd("raw", {data: `FEFEA2E017${bytes}FD`});
  });

  document.getElementById("btn-set-scan-span").addEventListener("click", ()=>sendCmd("set_scan_span", {span: parseInt(document.getElementById("sel-scan-span").value)}));
  document.getElementById("btn-scan-resume-on").addEventListener("click", ()=>sendCmd("set_scan_resume", {on:true}));
  document.getElementById("btn-scan-resume-off").addEventListener("click", ()=>sendCmd("set_scan_resume", {on:false}));

  document.getElementById("btn-power-on").addEventListener("click", ()=>sendCmd("power", {on:true}));
  document.getElementById("btn-power-off").addEventListener("click", ()=>sendCmd("power", {on:false}));

  document.getElementById("btn-raw-send").addEventListener("click", ()=>{
    const raw = document.getElementById("input-raw").value.trim().replace(/\s/g,"");
    if(raw) sendCmd("raw", {data: raw});
  });
  document.getElementById("btn-clear-log").addEventListener("click", ()=>{ document.getElementById("log-box").innerHTML=""; });

  // Mode buttons
  document.querySelectorAll("[data-cmd='mode']").forEach(btn=>{
    btn.addEventListener("click", ()=>{
      const modeVal = parseInt(btn.dataset.val);
      sendCmd("set_mode", {mode: modeVal, filter: pendingFilter});
    });
  });
  document.querySelectorAll("[data-cmd='filter']").forEach(btn=>{
    btn.addEventListener("click", ()=>{
      pendingFilter = parseInt(btn.dataset.val);
      if(currentMode){
        const map={"LSB":0x00,"USB":0x01,"AM":0x02,"CW":0x03,"RTTY":0x04,"FM":0x05,"CW-R":0x07,"RTTY-R":0x08,"DV":0x17,"DD":0x22};
        const mv = map[currentMode]; if(mv!==undefined) sendCmd("set_mode", {mode:mv, filter:pendingFilter});
      }
    });
  });
  document.querySelectorAll("[data-cmd='vfo']").forEach(btn=>btn.addEventListener("click", ()=>sendCmd("vfo", {vfo:btn.dataset.val})));
  document.querySelectorAll("[data-cmd='scan']").forEach(btn=>btn.addEventListener("click", ()=>sendCmd("scan", {type:btn.dataset.val})));
  document.querySelectorAll("[data-cmd='duplex']").forEach(btn=>btn.addEventListener("click", ()=>sendCmd("set_duplex", {duplex:btn.dataset.val})));

  // ========== Satellite Panel ==========
  let _satMainHz = 0, _satMainMd = "---", _satSubHz = 0, _satSubMd = "---";

  function updateSatDisplay() {
    document.getElementById("sat-main-freq").textContent = (_satMainHz ? formatSatFreq(_satMainHz) + " MHz" : "---.---.---");
    document.getElementById("sat-main-mode").textContent = _satMainMd;
    document.getElementById("sat-sub-freq").textContent = (_satSubHz ? formatSatFreq(_satSubHz) + " MHz" : "---.---.---");
    document.getElementById("sat-sub-mode").textContent = _satSubMd;
  }

  function formatSatFreq(hz) {
    const parts = (hz/1e6).toFixed(3).split('.');
    return parts[0] + '.' + parts[1].padStart(3,'0');
  }

  document.getElementById("btn-sat-main").addEventListener("click", function() {
    sendCmd("vfo", {vfo: "main"});
    this.className = "btn-toggle on";
    document.getElementById("btn-sat-sub").className = "btn-toggle off";
  });
  document.getElementById("btn-sat-sub").addEventListener("click", function() {
    sendCmd("vfo", {vfo: "sub"});
    this.className = "btn-toggle off";
    document.getElementById("btn-sat-sub").className = "btn-toggle on";
  });

  document.getElementById("btn-sat-main-read").addEventListener("click", ()=>{
    // Select MAIN, then read freq+mode
    sendCmd("vfo", {vfo: "main"});
    setTimeout(()=>sendCmd("poll", {targets:["freq","mode"]}), 200);
  });

  document.getElementById("btn-sat-sub-read").addEventListener("click", ()=>{
    // Select SUB, read freq+mode, store as _satSub, then back to MAIN
    // First store current MAIN values
    const savedMain = _satMainHz, savedMode = _satMainMd;
    sendCmd("vfo", {vfo: "sub"});
    setTimeout(()=>{
      sendCmd("poll", {targets:["freq","mode"]});
      // After a delay, restore MAIN
      setTimeout(()=>{
        _satSubHz = currentFreq; _satSubMd = currentMode;
        sendCmd("vfo", {vfo: "main"});
        _satMainHz = savedMain; _satMainMd = savedMode;
        updateSatDisplay();
      }, 500);
    }, 200);
  });

  document.getElementById("btn-sat-vfo-eq").addEventListener("click", ()=>sendCmd("vfo", {vfo: "equal"}));
  document.getElementById("btn-sat-vfo-ex").addEventListener("click", ()=>{
    sendCmd("vfo", {vfo: "exchange"});
    // Swap our stored values too
    [_satMainHz, _satSubHz] = [_satSubHz, _satMainHz];
    [_satMainMd, _satSubMd] = [_satSubMd, _satMainMd];
    updateSatDisplay();
  });

  // Hook into handleCIV to capture MAIN freq/mode
  const _origHC = handleCIV;
  handleCIV = function(msg) {
    _origHC(msg);
    if (msg.event === "frequency") {
      _satMainHz = msg.frequency;
      updateSatDisplay();
    }
    if (msg.event === "mode") {
      _satMainMd = msg.mode || "---";
      updateSatDisplay();
    }
  };
});
