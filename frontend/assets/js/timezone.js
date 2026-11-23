/**
 * NexOra global timezone service.
 *
 * The backend stores every timestamp in UTC and persists one administrator
 * timezone setting. This module is the single frontend place that:
 *
 *  - remembers the configured IANA timezone,
 *  - formats any API timestamp in that timezone,
 *  - converts a local <input type="datetime-local"> value to a UTC ISO string
 *    using that timezone (never the browser timezone), and
 *  - notifies the application after the setting changes.
 *
 * It deliberately has no imports so ui.js can depend on it without cycles.
 */

const STORAGE_KEY = "nexora.timezone";
const DEFAULT_TIMEZONE = "UTC";
const formatterCache = new Map();

let state = {
  timezone: readCachedTimezone(),
  info: null,
  loaded: false,
  pending: null,
};

const listeners = new Set();

function isUsableTimezone(name) {
  if (typeof name !== "string" || !name.trim()) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: name.trim() }).format(0);
    return true;
  } catch {
    return false;
  }
}

function readCachedTimezone() {
  if (typeof window === "undefined") return DEFAULT_TIMEZONE;
  try {
    const cached = window.localStorage.getItem(STORAGE_KEY);
    return isUsableTimezone(cached) ? cached : DEFAULT_TIMEZONE;
  } catch {
    return DEFAULT_TIMEZONE;
  }
}

function writeCachedTimezone(name) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, name);
  } catch {
    // Private mode/storage denial must not break formatting.
  }
}

function notify() {
  listeners.forEach((listener) => {
    try {
      listener(getTimezoneInfo());
    } catch {
      // A broken listener must not prevent the rest of the UI from updating.
    }
  });
}

function formatterFor(timeZone, options) {
  const key = `${timeZone}|${JSON.stringify(options)}`;
  let formatter = formatterCache.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(undefined, { timeZone, ...options });
    formatterCache.set(key, formatter);
  }
  return formatter;
}

function validDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function currentTimezone() {
  return state.timezone;
}

export function getTimezoneInfo() {
  return {
    default_timezone: DEFAULT_TIMEZONE,
    ...(state.info || {}),
    timezone: state.timezone,
    is_default: state.timezone === DEFAULT_TIMEZONE,
    loaded: state.loaded,
  };
}

export function subscribeTimezone(listener) {
  if (typeof listener !== "function") return () => {};
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function applyTimezoneConfig(payload) {
  const candidate =
    typeof payload === "string"
      ? payload
      : payload && typeof payload.timezone === "string"
        ? payload.timezone
        : payload && typeof payload.display_timezone === "string"
          ? payload.display_timezone
          : "";
  if (!isUsableTimezone(candidate)) {
    return getTimezoneInfo();
  }
  state.timezone = candidate;
  state.info = typeof payload === "object" && payload ? { ...payload } : null;
  state.loaded = true;
  writeCachedTimezone(candidate);
  notify();
  return getTimezoneInfo();
}

export async function initTimezone({ force = false } = {}) {
  if (state.loaded && !force) return getTimezoneInfo();
  if (state.pending && !force) return state.pending;
  state.pending = (async () => {
    try {
      const response = await fetch("/api/v1/timezone", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.ok) {
        const payload = await response.json();
        return applyTimezoneConfig(payload);
      }
    } catch {
      // Offline or an older backend: keep the cached/UTC fallback.
    } finally {
      state.pending = null;
    }
    state.loaded = true;
    return getTimezoneInfo();
  })();
  return state.pending;
}

export function refreshTimezone() {
  return initTimezone({ force: true });
}

/** Format any instant in a specific zone; falls back safely on bad input. */
export function formatDateInZone(
  value,
  timeZone = state.timezone,
  { time = true, seconds = false } = {}
) {
  const date = validDate(value);
  if (!date) return "—";
  const zone = isUsableTimezone(timeZone) ? timeZone : DEFAULT_TIMEZONE;
  const options = { year: "numeric", month: "short", day: "2-digit" };
  if (time) {
    options.hour = "2-digit";
    options.minute = "2-digit";
    if (seconds) options.second = "2-digit";
  }
  try {
    return formatterFor(zone, options).format(date);
  } catch {
    return "—";
  }
}

export function formatDate(value, options = {}) {
  return formatDateInZone(value, state.timezone, options);
}

export function formatTime(value, { seconds = true } = {}) {
  const date = validDate(value);
  if (!date) return "—";
  try {
    return formatterFor(state.timezone, {
      hour: "2-digit",
      minute: "2-digit",
      ...(seconds ? { second: "2-digit" } : {}),
    }).format(date);
  } catch {
    return "—";
  }
}

export function formatDateTime(value) {
  return formatDate(value, { time: true });
}

function zoneParts(date, timeZone) {
  const parts = formatterFor(timeZone, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const result = {};
  for (const part of parts) {
    if (part.type !== "literal") result[part.type] = Number(part.value);
  }
  return result;
}

/** Offset of ``timeZone`` at ``date`` in seconds east of UTC. */
export function getTimezoneOffsetSeconds(date = new Date(), timeZone = state.timezone) {
  const zone = isUsableTimezone(timeZone) ? timeZone : DEFAULT_TIMEZONE;
  const instant = validDate(date);
  if (!instant) return 0;
  try {
    const parts = zoneParts(instant, zone);
    const asUTC = Date.UTC(
      parts.year,
      parts.month - 1,
      parts.day,
      parts.hour,
      parts.minute,
      parts.second
    );
    return Math.round((asUTC - Math.floor(instant.getTime() / 1000) * 1000) / 1000);
  } catch {
    return 0;
  }
}

export function formatUtcOffset(offsetSeconds) {
  const total = Math.trunc(Number(offsetSeconds) || 0);
  const sign = total >= 0 ? "+" : "-";
  const absolute = Math.abs(total);
  const hours = Math.floor(absolute / 3600);
  const minutes = Math.floor((absolute % 3600) / 60);
  return `UTC${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

/** ISO-8601 value in the configured zone, e.g. 2026-11-23T18:42. */
export function toLocalInputValue(value) {
  const date = validDate(value);
  if (!date) return "";
  try {
    const parts = zoneParts(date, state.timezone);
    return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
  } catch {
    return "";
  }
}

export function toLocalDateValue(value) {
  const date = validDate(value);
  if (!date) return "";
  try {
    const parts = zoneParts(date, state.timezone);
    return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}`;
  } catch {
    return "";
  }
}

/**
 * Interpret a datetime-local wall clock in the configured timezone and return
 * a UTC ISO string. Returns null for an empty/malformed value and throws a
 * RangeError when the local time does not exist because of a DST jump.
 */
export function localInputToUtcIso(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/.exec(text);
  if (!match) return null;
  const [, year, month, day, hour, minute, second = "00"] = match;
  const wall = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second)
  );
  if (!Number.isFinite(wall)) return null;

  let guess = wall;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const offsetMs = getTimezoneOffsetSeconds(new Date(guess), state.timezone) * 1000;
    const next = wall - offsetMs;
    if (next === guess) break;
    guess = next;
  }
  const check = zoneParts(new Date(guess), state.timezone);
  if (
    check.year !== Number(year) ||
    check.month !== Number(month) ||
    check.day !== Number(day) ||
    check.hour !== Number(hour) ||
    check.minute !== Number(minute)
  ) {
    throw new RangeError(
      `That local time does not exist in ${state.timezone} because of a daylight-saving transition. Choose another time.`
    );
  }
  return new Date(guess).toISOString();
}

export function localDateEndOfDayToUtcIso(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  return localInputToUtcIso(`${text.slice(0, 10)}T23:59:59`);
}

/** Format a datetime-local wall clock for a preview without saving it. */
export function formatLocalInput(value) {
  try {
    const iso = localInputToUtcIso(value);
    return iso ? formatDate(iso) : "";
  } catch {
    return "";
  }
}

export function formatLocalInputWithTimezone(value) {
  try {
    const iso = localInputToUtcIso(value);
    if (!iso) return "";
    const offset = formatUtcOffset(getTimezoneOffsetSeconds(new Date(iso)));
    return `${formatDate(iso)} (${state.timezone} · ${offset})`;
  } catch {
    return "";
  }
}
