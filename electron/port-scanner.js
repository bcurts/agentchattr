const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

const DEFAULT_INTERVAL_MS = 5000;
const NETSTAT_ARGS = ['-ano'];
const AGENTS_API_URL = 'http://127.0.0.1:8300/api/agents';
const KNOWN_AGENTCHATTR_PORTS = new Set([8200, 8201, 8300]);
const MAX_HISTORY_ENTRIES = 100;
const NETSTAT_BUFFER_BYTES = 10 * 1024 * 1024;
const TASKLIST_BUFFER_BYTES = 1024 * 1024;

let scanTimer = null;
let scanInFlight = false;
let targetWindow = null;
let previousPortsByKey = new Map();
let processNameCache = new Map();
const history = [];

function parseEndpoint(endpoint) {
  if (!endpoint) {
    return null;
  }

  if (endpoint.startsWith('[')) {
    const separatorIndex = endpoint.lastIndexOf(']:');

    if (separatorIndex === -1) {
      return null;
    }

    const address = endpoint.slice(1, separatorIndex);
    const port = parseInt(endpoint.slice(separatorIndex + 2), 10);

    if (Number.isNaN(port)) {
      return null;
    }

    return { address, port };
  }

  const separatorIndex = endpoint.lastIndexOf(':');

  if (separatorIndex === -1) {
    return null;
  }

  const address = endpoint.slice(0, separatorIndex);
  const port = parseInt(endpoint.slice(separatorIndex + 1), 10);

  if (Number.isNaN(port)) {
    return null;
  }

  return { address, port };
}

function parseNetstatLine(line) {
  const parts = line.trim().split(/\s+/);

  if (parts.length < 5) {
    return null;
  }

  const [protocol, localEndpoint, , state, rawPid] = parts;

  if (state !== 'LISTENING') {
    return null;
  }

  const endpoint = parseEndpoint(localEndpoint);

  if (!endpoint) {
    return null;
  }

  const pid = parseInt(rawPid, 10);

  if (Number.isNaN(pid)) {
    return null;
  }

  return {
    protocol,
    address: endpoint.address,
    port: endpoint.port,
    pid,
  };
}

function parseTasklistProcessName(stdout) {
  const firstLine = stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstLine || firstLine.startsWith('INFO:')) {
    return null;
  }

  const match = firstLine.match(/^"((?:[^"]|"")*)"/);

  if (!match) {
    return null;
  }

  return match[1].replace(/""/g, '"');
}

function normaliseToken(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/\.exe$/i, '')
    .replace(/[^a-z0-9]+/g, '');
}

function portKey(entry) {
  return `${entry.protocol}|${entry.address}|${entry.port}|${entry.pid}`;
}

function pushHistoryEntry(entry) {
  history.push(entry);

  while (history.length > MAX_HISTORY_ENTRIES) {
    history.shift();
  }
}

function isWindowAvailable(mainWindow) {
  if (!mainWindow) {
    return false;
  }

  if (typeof mainWindow.isDestroyed === 'function' && mainWindow.isDestroyed()) {
    return false;
  }

  if (!mainWindow.webContents) {
    return false;
  }

  if (
    typeof mainWindow.webContents.isDestroyed === 'function' &&
    mainWindow.webContents.isDestroyed()
  ) {
    return false;
  }

  return true;
}

async function scanPorts() {
  const { stdout } = await execFileAsync('netstat', NETSTAT_ARGS, {
    maxBuffer: NETSTAT_BUFFER_BYTES,
    windowsHide: true,
  });

  return stdout
    .split(/\r?\n/)
    .filter((line) => line.includes('LISTENING'))
    .map(parseNetstatLine)
    .filter(Boolean);
}

async function lookupProcessName(pid) {
  const safePid = parseInt(pid, 10);

  if (Number.isNaN(safePid)) {
    return 'Unknown';
  }

  try {
    const { stdout } = await execFileAsync(
      'tasklist',
      ['/FI', `PID eq ${safePid}`, '/FO', 'CSV', '/NH'],
      {
        maxBuffer: TASKLIST_BUFFER_BYTES,
        windowsHide: true,
      }
    );

    return parseTasklistProcessName(stdout) ?? 'Unknown';
  } catch (error) {
    console.warn(`Failed to resolve process name for PID ${safePid}:`, error);
    return 'Unknown';
  }
}

async function resolveProcessNames(ports) {
  const activePids = new Set();

  for (const entry of ports) {
    if (Number.isInteger(entry.pid)) {
      activePids.add(entry.pid);
    }
  }

  // Drop vanished PIDs so reused Windows PIDs do not keep stale names.
  for (const cachedPid of Array.from(processNameCache.keys())) {
    if (!activePids.has(cachedPid)) {
      processNameCache.delete(cachedPid);
    }
  }

  const uncachedPids = Array.from(activePids).filter((pid) => !processNameCache.has(pid));

  await Promise.all(
    uncachedPids.map(async (pid) => {
      processNameCache.set(pid, await lookupProcessName(pid));
    })
  );

  return ports.map((entry) => ({
    ...entry,
    processName: processNameCache.get(entry.pid) ?? 'Unknown',
  }));
}

function findAgentMatch(processName, agents) {
  const processToken = normaliseToken(processName);

  if (!processToken) {
    return null;
  }

  for (const [agentName, details] of Object.entries(agents)) {
    const nameToken = normaliseToken(agentName);
    const labelToken = normaliseToken(details?.label);

    if (
      (nameToken && (processToken.includes(nameToken) || nameToken.includes(processToken))) ||
      (labelToken && (processToken.includes(labelToken) || labelToken.includes(processToken)))
    ) {
      return {
        agent: agentName,
        agentColour: details?.color ?? details?.colour ?? null,
      };
    }
  }

  return null;
}

async function fetchRegisteredAgents() {
  try {
    const response = await fetch(AGENTS_API_URL);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();

    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return null;
    }

    return payload;
  } catch (error) {
    console.warn('Failed to fetch registered agents:', error);
    return null;
  }
}

async function tagAgentPorts(ports) {
  const registeredAgents = await fetchRegisteredAgents();
  const agentchattrColour =
    registeredAgents?.agentchattr?.color ?? registeredAgents?.agentchattr?.colour ?? null;

  return ports.map((entry) => {
    if (KNOWN_AGENTCHATTR_PORTS.has(entry.port)) {
      return {
        ...entry,
        agent: 'agentchattr',
        agentColour: agentchattrColour,
      };
    }

    const matchedAgent = registeredAgents
      ? findAgentMatch(entry.processName, registeredAgents)
      : null;

    if (matchedAgent) {
      return {
        ...entry,
        ...matchedAgent,
      };
    }

    return {
      ...entry,
      agent: 'System',
      agentColour: null,
    };
  });
}

function updateHistory(currentPorts) {
  const currentPortsByKey = new Map(currentPorts.map((entry) => [portKey(entry), entry]));
  const timestamp = Date.now();

  for (const [key, entry] of currentPortsByKey.entries()) {
    if (!previousPortsByKey.has(key)) {
      pushHistoryEntry({
        type: 'open',
        port: entry.port,
        pid: entry.pid,
        processName: entry.processName ?? 'Unknown',
        agent: entry.agent ?? null,
        timestamp,
      });
    }
  }

  for (const [key, entry] of previousPortsByKey.entries()) {
    if (!currentPortsByKey.has(key)) {
      pushHistoryEntry({
        type: 'close',
        port: entry.port,
        pid: entry.pid,
        processName: entry.processName ?? 'Unknown',
        agent: entry.agent ?? null,
        timestamp,
      });
    }
  }

  previousPortsByKey = currentPortsByKey;
}

async function performScanCycle() {
  if (scanInFlight) {
    return;
  }

  scanInFlight = true;

  try {
    let scannedPorts;

    try {
      scannedPorts = await scanPorts();
    } catch (error) {
      console.error('Failed to scan listening ports:', error);
      return;
    }

    const portsWithNames = await resolveProcessNames(scannedPorts);
    const taggedPorts = await tagAgentPorts(portsWithNames);

    updateHistory(taggedPorts);

    if (isWindowAvailable(targetWindow)) {
      targetWindow.webContents.send('port-data', {
        ports: taggedPorts,
        history: getHistory(),
      });
    }
  } catch (error) {
    console.error('Port scanning cycle failed:', error);
  } finally {
    scanInFlight = false;
  }
}

function startScanning(mainWindow, intervalMs = DEFAULT_INTERVAL_MS) {
  stopScanning();

  targetWindow = mainWindow ?? null;

  const safeIntervalMs =
    Number.isFinite(intervalMs) && intervalMs > 0 ? intervalMs : DEFAULT_INTERVAL_MS;

  void performScanCycle();

  scanTimer = setInterval(() => {
    void performScanCycle();
  }, safeIntervalMs);
}

function stopScanning() {
  if (scanTimer) {
    clearInterval(scanTimer);
    scanTimer = null;
  }

  targetWindow = null;
}

function getHistory() {
  return history.slice();
}

module.exports = {
  startScanning,
  stopScanning,
  getHistory,
};
