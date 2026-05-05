/** @odoo-module **/
import { EventBus } from "@odoo/owl";

const payrilliumBus = new EventBus();

export { payrilliumBus };

// Only enable logs when setPayrilliumDebugMode(true) is called (e.g. from setup_config when backend returns environment === "dev").
// Default false so production (config.py ENVIRONMENT="prod") never shows log/warn in the browser, even when opening POS at localhost.
let payrilliumDebugEnabled = false;

export function setPayrilliumDebugMode(enabled) {
  payrilliumDebugEnabled = !!enabled;
}

export const payrilliumConsole = {
  log: (...args) => {
    if (payrilliumDebugEnabled) console.log(...args);
  },
  warn: (...args) => {
    if (payrilliumDebugEnabled) console.warn(...args);
  },
  error: (...args) => {
    // Errors ALWAYS log (even in production) - critical for debugging
    console.error(...args);
  },
};

/**
 * Ensures the given value is a proper Error instance with a .stack property.
 * Prevents Odoo's crash manager from crashing on error.stack.split().
 */
export function ensureError(value) {
  if (value instanceof Error) return value;
  const error = new Error(
    typeof value === "object" && value !== null
      ? value.message || JSON.stringify(value)
      : String(value)
  );
  // Preserve original properties for downstream handlers
  if (typeof value === "object" && value !== null) {
    Object.assign(error, value);
  }
  error._originalValue = value;
  return error;
}

export function parseEMVData(
  emvTagsHex,
  options = { flatten: true, simpleDecode: true },
) {
  const knownTags = {
    "4F": "AID",
    50: "ApplicationLabel",
    "5F34": "PANSequence",
    "5F2A": "CurrencyCode",
    82: "AIP",
    84: "DFName",
    "9A": "TransactionDate",
    "9C": "TransactionType",
    "9F02": "AmountAuthorized",
    "9F03": "AmountOther",
    "9F06": "AIDAlt",
    "9F10": "IssuerAppData",
    "9F12": "ApplicationPreferredName",
    "9F1A": "TerminalCountryCode",
    "9F26": "ApplicationCryptogram",
    "9F27": "CryptogramInfo",
    "9F34": "CVM",
    "9F36": "ATC",
    "9F37": "UnpredictableNumber",
    "9F6E": "FormFactorIndicator",
    95: "TVR",
    70: "ResponseTemplate1",
    77: "ResponseTemplate2",
    "6F": "FCITemplate",
    A5: "FCIProprietary",
    61: "ApplicationTemplate",
    BF0C: "FCIIssuerDiscretionary",
  };
  function hexToBytes(h) {
    const out = [];
    for (let i = 0; i < h.length; i += 2)
      out.push(parseInt(h.substr(i, 2), 16));
    return Uint8Array.from(out);
  }
  function bytesToAscii(bytes) {
    return Array.from(bytes)
      .map((b) => String.fromCharCode(b))
      .join("");
  }
  function isConstructed(firstByte) {
    return (firstByte & 0x20) === 0x20; // bit 6
  }
  function readTag(s, i) {
    const b1 = parseInt(s.substr(i, 2), 16);
    let tag = s.substr(i, 2);
    i += 2;
    if ((b1 & 0x1f) === 0x1f) {
      // keep reading while MSB=1
      while (i + 2 <= s.length) {
        const b = parseInt(s.substr(i, 2), 16);
        tag += s.substr(i, 2);
        i += 2;
        if ((b & 0x80) === 0) break;
      }
    }
    return { tag: tag.toUpperCase(), next: i, firstByte: b1 };
  }
  function readLength(s, i) {
    const first = parseInt(s.substr(i, 2), 16);
    i += 2;
    if ((first & 0x80) === 0) {
      return { len: first, next: i }; // short form
    }
    const nBytes = first & 0x7f;
    let len = 0;
    for (let k = 0; k < nBytes; k++) {
      len = (len << 8) | parseInt(s.substr(i, 2), 16);
      i += 2;
    }
    return { len, next: i };
  }

  function decodeValue(tag, valueHex) {
    const bytes = hexToBytes(valueHex);
    if (options.simpleDecode) {
      const ascii = bytesToAscii(bytes);
      const printable = /^[\x20-\x7E]*$/.test(ascii);
      return printable ? ascii : valueHex;
    }
    // richer decoding examples (toggle simpleDecode=false to use)
    const t = tag.toUpperCase();
    if (t === "50" || t === "9F12" || t === "84") {
      const ascii = bytesToAscii(bytes);
      return /^[\x20-\x7E]*$/.test(ascii) ? ascii : valueHex;
    }
    if (t === "9A") {
      const yy = valueHex.slice(0, 2),
        mm = valueHex.slice(2, 4),
        dd = valueHex.slice(4, 6);
      return `20${yy}-${mm}-${dd}`;
    }
    if (t === "5F2A" || t === "9F1A") return parseInt(valueHex, 16);
    // default: keep hex
    return valueHex;
  }

  function parseTLV(s, start = 0, end = s.length) {
    const out = {};
    let i = start;
    while (i < end) {
      if (i + 2 > s.length) break;
      const { tag, next: iTag, firstByte } = readTag(s, i);
      i = iTag;

      if (i + 2 > s.length) break;
      const { len, next: iLen } = readLength(s, i);
      i = iLen;

      const vEnd = i + len * 2;
      if (vEnd > s.length) break; // safety

      const valueHex = s.substring(i, vEnd);
      i = vEnd;

      if (isConstructed(firstByte)) {
        out[tag] = parseTLV(valueHex, 0, valueHex.length);
      } else {
        out[tag] = decodeValue(tag, valueHex);
      }
    }
    return out;
  }

  const list = parseTLV(emvTagsHex);

  function flatten(obj, acc = {}) {
    for (const [k, v] of Object.entries(obj)) {
      if (v && typeof v === "object" && !Array.isArray(v)) {
        flatten(v, acc);
      } else {
        acc[k] = v;
      }
    }
    return acc;
  }

  const byTag = options.flatten ? flatten(list) : list;
  const result = {};
  for (const [t, v] of Object.entries(byTag)) {
    result[knownTags[t] || t] = v; // keep your friendly names
  }
  return result;
}

export const ARC_MEANING = {
  "00": "Approved",
  "01": "Referral",
  "05": "Do not honor / Declined",
  51: "Insufficient funds",
  54: "Expired card",
  91: "Issuer or switch inoperative",
  96: "System malfunction",
};

export const TX_STATE_TO_ODOO_STATE = {
  cancel: "cancel",
  cancelled: "cancel",
  declined: "error",
  device_error: "error",
  terminal_busy: "error",
  error: "error",
  authorized: "authorized",
  done: "done",
  draft: "draft",
  pending: "pending",
  captured: "done",
  in_progress: "pending",
  auth_failed: "error",
  voided: "cancel",
  reversed: "cancel",
  capture_failed: "error",
  void_failed: "error",
  reversal_failed: "error",
};

export const TX_STATE_TO_TRANSACTION_STATUS = {
  cancel: "cancelled",
  terminal_busy: "terminal_busy",
};
export function mapCVMCode(code) {
  const cvmMethods = {
    0: "Fail CVM processing",
    1: "Offline plaintext PIN",
    2: "Online enciphered PIN",
    3: "Offline plaintext PIN + signature (paper)",
    4: "Offline enciphered PIN",
    5: "Offline enciphered PIN + signature (paper)",
    8: "Enciphered PIN verified online",
    30: "Signature (paper)",
    31: "No CVM required",
  };
  const numericCode = parseInt(code, 10);
  return cvmMethods[numericCode] || "NONE";
}
export function generateExecutionId() {
  return Math.random().toString(36).substring(2, 10) + Date.now();
}

// ─────────────────────────────────────────────────────────────────────────────
// LAYER 1 — Communication Errors (MQTT / HTTP non-200)
// Fires when we can't reach the terminal at all.
// To add a specific rule: uncomment the line and fill mc + ui.
// Rules are evaluated top-to-bottom; first match wins.
// ─────────────────────────────────────────────────────────────────────────────
const COMM_ERROR_RULES = [
  // { test: (msg, code) => code === 409 && msg.includes("BUSY"), mc: "MC10-BUSY", ui: "Terminal busy, retrying..." },
  // { test: (msg, code) => code === 409 && msg.includes("SIGNATURE"), mc: "MC20-AUTH_ERROR", ui: "Auth/config issue (credentials/signature)." },
  // { test: (msg, code) => code === 401, mc: "MC20-AUTH_ERROR", ui: "Auth/config issue (credentials/signature)." },
  // { test: (msg, code) => code === 404, mc: "MC30-NOT_FOUND", ui: "Terminal not found (wrong terminal id)." },
  // { test: (msg, code) => code === 500, mc: "MC40-UNREACHABLE",ui: "Terminal unreachable or service error." },
  //
  // ↓ Catch-all — everything falls here until rules above are enabled
  {
    test: () => true,
    mc: "M99",
    ui: "Check Terminal Connection and try again.",
    txState: "error",
    isCancelled: false,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// LAYER 2-A — TCODE: PAX Terminal Errors
// Detected when data.data.message is a plain STRING (not an object).
// Examples: "2-Chip Read Error", "ABORTED"
//
// Each rule has:
// match — substring to find in the PAX message (compared uppercase)
// tcode — internal code for logs
// ui — message shown in the Odoo POS popup (status_summary)
// terminalTitle — title shown on the PAX device screen
// terminalMsg — message shown on the PAX device screen
//
// If no match → T99 fallback.
// ─────────────────────────────────────────────────────────────────────────────
const TCODE_RULES = [
  {
    match: "ABORTED",
    tcode: "T01",
    ui: "Operation Cancelled by User",
    terminalTitle: "Cancelled",
    terminalMsg: "Operation Cancelled",
    txState: "cancel",
    isCancelled: true,
  },
  {
    match: "USER ABORT",
    tcode: "T01",
    ui: "Operation Cancelled by User",
    terminalTitle: "Cancelled",
    terminalMsg: "Operation Cancelled",
    txState: "cancel",
    isCancelled: true,
  },
  {
    match: "2-CHIP READ ERROR",
    tcode: "T02",
    ui: "Card Chip Error \u2013 Please Try Again",
    terminalTitle: "Error",
    terminalMsg: "Card Chip Error / Try Again",
    txState: "device_error",
    isCancelled: false,
  },
  {
    match: "TAP TERMINATED",
    tcode: "T03",
    ui: "Tap Interrupted \u2013 Please Try Again",
    terminalTitle: "Error",
    terminalMsg: "Tap Error / Try Again",
    txState: "device_error",
    isCancelled: false,
  },
  {
    match: "SCREEN INACTIVITY",
    tcode: "T04",
    ui: "Tip Screen Timed Out \u2013 Please Try Again",
    terminalTitle: "Timed Out",
    terminalMsg: "No Input Detected",
    txState: "device_error",
    isCancelled: false,
  },
  // ↑ Add more known PAX errors here. Unknown ones fall to T99.
];

// ─────────────────────────────────────────────────────────────────────────────
// LAYER 2-B — CCODE: Cybersource Gateway Errors
// Detected when data.data.message is an OBJECT with rawBody.errorInformation.
//
// Each rule has: reason, ccode, ui, terminalTitle, terminalMsg
// If no match → falls to C99.
// ─────────────────────────────────────────────────────────────────────────────
const CCODE_MAP = [
  {
    reason: "EXPIRED_CARD",
    ccode: "C202",
    ui: "Declined \u2013 Expired/Invalid Card, Please Try Again",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "INVALID_ACCOUNT",
    ccode: "C203",
    ui: "Declined \u2013 Expired/Invalid Card, Please Try Again",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "INSUFFICIENT_FUND",
    ccode: "C204",
    ui: "Declined \u2013 Insufficient Funds",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "STOLEN_LOST_CARD",
    ccode: "C205",
    ui: "Declined \u2013 Card Cannot Be Used",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "CV_FAILED",
    ccode: "C206",
    ui: "Declined \u2013 Card Verification Failed, Please Try Again",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "EXCEEDS_CREDIT_FLOOR_LIMIT",
    ccode: "C207",
    ui: "Declined \u2013 Amount Exceeds Card Limit",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "GENERAL_DECLINE",
    ccode: "C208",
    ui: "Declined \u2013 Please Try a Different Card",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "PROCESSOR_DECLINED",
    ccode: "C209",
    ui: "Declined \u2013 Please Try a Different Card",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "UNAUTHORIZED_CARD",
    ccode: "C210",
    ui: "Declined \u2013 Card Not Accepted, Please Use a Different Card",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "declined",
    isCancelled: false,
  },
  {
    reason: "SYSTEM_ERROR",
    ccode: "C500",
    ui: "Connection issue. Please try again.",
    terminalTitle: "Declined",
    terminalMsg: "Transaction Failed",
    txState: "error",
    isCancelled: false,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// LAYER 2-C — GCODE: Gateway Transaction Status
// Detected when data.data.message is an OBJECT with a .status field
// that indicates a non-success outcome (REVERSED, VOIDED, DECLINED).
// These arrive when Cybersource auto-reverses or voids during auth.
//
// To add a new status: add a row below with { status, gcode, ui, terminalTitle, terminalMsg }.
// ─────────────────────────────────────────────────────────────────────────────
const GATEWAY_STATUS_MAP = [
  {
    status: "REVERSED",
    gcode: "G01",
    ui: "Payment Reversed \u2013 Please Try Again",
    terminalTitle: "Payment Reversed",
    terminalMsg: "Please Try Again",
    txState: "cancel",
    isCancelled: true,
  },
  {
    status: "VOIDED",
    gcode: "G02",
    ui: "Payment Voided \u2013 Please Try Again",
    terminalTitle: "Payment Voided",
    terminalMsg: "Please Try Again",
    txState: "cancel",
    isCancelled: true,
  },
  {
    status: "DECLINED",
    gcode: "G03",
    ui: "Payment Declined \u2013 Please Try Again",
    terminalTitle: "Payment Declined",
    terminalMsg: "Please Try Again",
    txState: "declined",
    isCancelled: false,
  },
  {
    status: "PARTIAL_AUTHORIZED",
    gcode: "G04",
    ui: "Partial Authorization \u2013 Please Try Again",
    terminalTitle: "Partial Auth",
    terminalMsg: "Please Try Again",
    txState: "declined",
    isCancelled: false,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// LAYER 0 — Cloud Errors (CLDS-*)
// These come from the Mirillium Cloud service itself, NOT the PAX terminal.
// They arrive in result.api_response.error_code as "CLDS-<CATEGORY>-<NUM>".
// ─────────────────────────────────────────────────────────────────────────────
const CLDS_CODE_MAP = [
  {
    code: "000001",
    clds: "CLDS-VAL-001",
    ui: "Payment setup issue. Please contact support.",
    terminalTitle: "Payment Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000002",
    clds: "CLDS-AUTH-002",
    ui: "Payment service unavailable. Please contact support.",
    terminalTitle: "Payment Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000003",
    clds: "CLDS-VAL-003",
    ui: "Payment setup issue. Please contact support.",
    terminalTitle: "Payment Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000004",
    clds: "CLDS-404-004",
    ui: "Terminal unavailable. Please contact support.",
    terminalTitle: "Terminal Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000005",
    clds: "CLDS-404-005",
    ui: "Terminal unavailable. Please contact support.",
    terminalTitle: "Terminal Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000006",
    clds: "CLDS-AUTH-006",
    ui: "Payment service unavailable. Please contact support.",
    terminalTitle: "Payment Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000007",
    clds: "CLDS-AUTH-007",
    ui: "Transaction already in progress. Please wait and try again.",
    terminalTitle: "Please Wait",
    terminalMsg: "Try Again Shortly",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000008",
    clds: "CLDS-404-008",
    ui: "Payment account unavailable. Please contact support.",
    terminalTitle: "Payment Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000009",
    clds: "CLDS-AUTH-009",
    ui: "Transaction could not be processed. Please try again.",
    terminalTitle: "Try Again",
    terminalMsg: "Please Retry",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000010",
    clds: "CLDS-AUTH-010",
    ui: "Payment service unavailable. Please contact support.",
    terminalTitle: "Payment Unavailable",
    terminalMsg: "Please Contact Staff",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000011",
    clds: "CLDS-BUSY-011",
    ui: "Terminal is busy. Please wait a moment and try again.",
    terminalTitle: "Please Wait",
    terminalMsg: "Terminal Busy",
    txState: "terminal_busy",
    isCancelled: false,
  },
  {
    code: "000012",
    clds: "CLDS-MQTT-012",
    ui: "Terminal is not responding. Check the connection and try again.",
    terminalTitle: "Connection Issue",
    terminalMsg: "Check Terminal",
    txState: "error",
    isCancelled: false,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// LAYER 1.5 — Terminal App Errors (PYRD-*)
// Caught by the terminal app wrapper.
// ─────────────────────────────────────────────────────────────────────────────
const PYRD_CODE_MAP = [
  {
    code: "000001",
    pyrd: "PYRD-VALIDATION_ERROR-000001",
    ui: "Invalid terminal request format.",
    terminalTitle: "Error",
    terminalMsg: "Invalid Request",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000002",
    pyrd: "PYRD-VALIDATION_ERROR-000002",
    ui: "Missing required field in request.",
    terminalTitle: "Error",
    terminalMsg: "Format Error",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000003",
    pyrd: "PYRD-VALIDATION_ERROR-000003",
    ui: "Bad request formatting.",
    terminalTitle: "Error",
    terminalMsg: "Format Error",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000004",
    pyrd: "PYRD-TIME_OUT-000004",
    ui: "Terminal screen timed out due to inactivity.",
    terminalTitle: "Timed Out",
    terminalMsg: "No Response",
    txState: "device_error",
    isCancelled: false,
  },
  {
    code: "000005",
    pyrd: "PYRD-TIME_OUT-000005",
    ui: "The terminal server timed out. Please retry.",
    terminalTitle: "Timed Out",
    terminalMsg: "Server Error",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000006",
    pyrd: "PYRD-PAX_ERROR-000006",
    ui: "Could not connect to the terminal's hardware.",
    terminalTitle: "Hardware Error",
    terminalMsg: "Hardware Error",
    txState: "device_error",
    isCancelled: false,
  },
  {
    code: "000007",
    pyrd: "PYRD-PAX_ERROR-000007",
    ui: "The payment terminal operation failed.",
    terminalTitle: "Hardware Error",
    terminalMsg: "Hardware Error",
    txState: "device_error",
    isCancelled: false,
  },
  {
    code: "000008",
    pyrd: "PYRD-GATEWAY_ERROR-000008",
    ui: "The payment gateway rejected the request.",
    terminalTitle: "Declined",
    terminalMsg: "Gateway Error",
    txState: "declined",
    isCancelled: false,
  },
  {
    code: "000009",
    pyrd: "PYRD-DEVICE_ERROR-000009",
    ui: "Lost connection to the terminal service.",
    terminalTitle: "Disconnected",
    terminalMsg: "Service Error",
    txState: "device_error",
    isCancelled: false,
  },
  {
    code: "000010",
    pyrd: "PYRD-CONFIG_ERROR-000010",
    ui: "Invalid terminal setup configuration.",
    terminalTitle: "Config Error",
    terminalMsg: "Config Error",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000011",
    pyrd: "PYRD-GENERAL-000011",
    ui: "Unexpected system failure.",
    terminalTitle: "System Error",
    terminalMsg: "System Error",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000012",
    pyrd: "PYRD-TERMINAL_BUSY-000012",
    ui: "Terminal is busy processing another request.",
    terminalTitle: "Busy",
    terminalMsg: "Please Wait",
    txState: "error",
    isCancelled: false,
  },
  {
    code: "000099",
    pyrd: "PYRD-GENERAL-000099",
    ui: "An unknown device error occurred.",
    terminalTitle: "Error",
    terminalMsg: "Unknown Error",
    txState: "error",
    isCancelled: false,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

const M9X_COUNTER_KEY = "payrillium_m9x_fail_count";

/**
 * Increment the M99/M98 consecutive-failure counter (sessionStorage).
 * Returns the new count after incrementing.
 */
function _incrementM9xCounter() {
  const current = parseInt(sessionStorage.getItem(M9X_COUNTER_KEY) || "0", 10);
  const next = current + 1;
  sessionStorage.setItem(M9X_COUNTER_KEY, String(next));
  return next;
}

/**
 * Reset the M99/M98 counter to 0 (called on successful responses).
 */
function _resetM9xCounter() {
  sessionStorage.removeItem(M9X_COUNTER_KEY);
}

/**
 * Normalized Error Object Shape
 * @typedef {Object} NormalizedError
 * @property {string} code - Internal reference code (e.g., T01, C202, CLDS-MQTT-012)
 * @property {string} ui - User-friendly message for POS popup
 * @property {string} logMessage - Detailed message for the backend transaction log
 * @property {string} terminalTitle - Title to display on the PAX hardware screen
 * @property {string} terminalMsg - Submessage to display on the PAX hardware screen
 * @property {string} layer - Architectural layer (CLDS, COMM, CCODE, GCODE, TCODE, PYRD, UNKNOWN)
 * @property {boolean} isCommError - Flag for network/HTTP reachability failures
 * @property {string} [txState] - The target Odoo pos.payment state (error, declined, cancel, device_error)
 * @property {boolean} [isCancelled] - Whether the user intentionally aborted
 */

/**
 * Resolve a CLDS error from the Mirillium Cloud service.
 * @returns {NormalizedError|null}
 */
function _resolveCldsCode(result) {
  const errorCode =
    result?.error_code ||
    result?.data?.error_code ||
    result?.data?.data?.error_code ||
    result?.api_response?.error_code ||
    "";
  if (!errorCode.startsWith("CLDS-")) return null;

  const numPart = errorCode.split("-").pop();
  const entry = CLDS_CODE_MAP.find((e) => e.code === numPart);

  if (!entry) {
    return {
      code: "CLDS-UNKNOWN",
      ui: `Cloud error: ${errorCode}. Check terminal connection.`,
      logMessage: `CLDS-UNKNOWN | ${errorCode}`,
      terminalTitle: "Error",
      terminalMsg: "Cloud Error",
      layer: "CLDS",
      isCommError: true,
      txState: "error",
      isCancelled: false,
    };
  }

  return {
    code: entry.clds,
    ui: entry.ui,
    logMessage: `${entry.clds} | ${errorCode}`,
    terminalTitle: entry.terminalTitle,
    terminalMsg: entry.terminalMsg,
    layer: "CLDS",
    isCommError: true,
    txState: entry.txState,
    isCancelled: entry.isCancelled,
  };
}

/**
 * Resolve a COMM error (HTTP protocol, server unreachable).
 * @returns {NormalizedError|null}
 */
function _resolveCommCode(result) {
  const outerMsg = (result?.message || "").toUpperCase();
  const httpCodeMatch = outerMsg.match(/^(\d{3})\s+(CLIENT|SERVER)\s+ERROR/);
  const httpCode = httpCodeMatch ? parseInt(httpCodeMatch[1]) : null;
  const isCommError =
    !!httpCodeMatch || (result?.status === "error" && !result?.data);

  if (!isCommError) return null;

  const rule = COMM_ERROR_RULES.find((r) => r.test(outerMsg, httpCode));
  return {
    code: rule.mc,
    ui: rule.ui,
    logMessage: rule.mc,
    terminalTitle: "Connection Error",
    terminalMsg: "Check Terminal",
    layer: "COMM",
    isCommError: true,
    txState: rule.txState,
    isCancelled: rule.isCancelled,
  };
}

/**
 * Resolve a PYRD error (Terminal wrapper app generic codes).
 * @returns {NormalizedError|null}
 */
function _resolvePyrdCode(result) {
  const errorCode =
    result?.error_code ||
    result?.data?.error_code ||
    result?.data?.data?.error_code ||
    "";
  if (!errorCode.startsWith("PYRD-")) return null;

  const numPart = errorCode.split("-").pop();
  const entry = PYRD_CODE_MAP.find((e) => e.code === numPart);

  if (!entry) return null;

  return {
    code: entry.pyrd,
    ui: entry.ui,
    logMessage: `${entry.pyrd} | ${errorCode}`,
    terminalTitle: entry.terminalTitle,
    terminalMsg: entry.terminalMsg,
    layer: "PYRD",
    isCommError: false,
    txState: entry.txState,
    isCancelled: entry.isCancelled,
  };
}

/**
 * Returns true if data.data.message is a plain string → PAX/TCODE error.
 * Returns false if it's an object → Cybersource/CCODE error.
 */
function _isPaxError(result) {
  const msg = result?.data?.data?.message;
  return typeof msg === "string";
}

/**
 * Resolve a TCODE error (PAX terminal string message, inactivity, or aborts).
 * @returns {NormalizedError|null}
 */
function _resolveTcode(result) {
  // 1. Check early abort via reason
  const reason = (result?.data?.reason || "").toUpperCase();
  if (reason.includes("ABORT")) {
    const rule = TCODE_RULES.find((r) => r.tcode === "T01");
    return {
      code: "T01",
      ui: rule.ui,
      logMessage: `T01 | ${result?.data?.reason}`,
      terminalTitle: rule.terminalTitle,
      terminalMsg: rule.terminalMsg,
      layer: "TCODE",
      isCommError: false,
      txState: rule.txState,
      isCancelled: rule.isCancelled,
    };
  }

  // 2. Check inactivity via state
  const dataType = (result?.data?.type || "").toUpperCase();
  const dataState = (
    result?.data?.state ||
    result?.data?.data?.state ||
    ""
  ).toUpperCase();
  if (
    dataType.includes("INNACTIVITY") ||
    dataType.includes("INACTIVITY") ||
    dataState.includes("INNACTIVITY") ||
    dataState.includes("INACTIVITY")
  ) {
    const rule = TCODE_RULES.find((r) => r.tcode === "T04");
    return {
      code: "T04",
      ui: rule?.ui || "Transaction timed out",
      logMessage: `T04 | Screen Inactivity`,
      terminalTitle: rule?.terminalTitle || "Timed Out",
      terminalMsg: rule?.terminalMsg || "No Response",
      layer: "TCODE",
      isCommError: false,
      txState: rule?.txState || "device_error",
      isCancelled: rule?.isCancelled || false,
    };
  }

  // 3. Fallback to raw string message checking
  if (!_isPaxError(result)) return null;

  const rawMsg = result?.data?.data?.message || "Unknown terminal error";
  const upper = rawMsg.toUpperCase();

  const rule = TCODE_RULES.find((r) => upper.includes(r.match));
  const tcode = rule ? rule.tcode : "T99";
  return {
    code: tcode,
    ui: rule ? rule.ui : "Terminal error. Please try again.",
    logMessage: `${tcode} | ${rawMsg}`,
    terminalTitle: rule?.terminalTitle || "Declined",
    terminalMsg: rule?.terminalMsg || "Transaction Failed",
    layer: "TCODE",
    isCommError: false,
    txState: rule ? rule.txState : "device_error",
    isCancelled: rule ? rule.isCancelled : false,
  };
}

/**
 * Resolve a CCODE error (Cybersource gateway object message).
 * @returns {NormalizedError|null}
 */
function _resolveCcode(result) {
  const errInfo = result?.data?.data?.message?.rawBody?.errorInformation;
  if (!errInfo) return null;

  const reason = errInfo.reason || "UNKNOWN";
  const message = errInfo.message || "Payment declined.";

  const entry = CCODE_MAP.find((e) => e.reason === reason);
  const ccode = entry ? entry.ccode : "C99";
  const ui = entry ? entry.ui : message;

  return {
    code: ccode,
    ui,
    logMessage: `${ccode} | ${reason} - ${message}`,
    terminalTitle: entry?.terminalTitle || "Declined",
    terminalMsg: entry?.terminalMsg || "Transaction Failed",
    layer: "CCODE",
    isCommError: false,
    txState: entry ? entry.txState : "declined",
    isCancelled: entry ? entry.isCancelled : false,
  };
}

/**
 * Resolve a GCODE error (Cybersource gateway transaction status).
 * @returns {NormalizedError|null}
 */
function _resolveGcode(result) {
  const msgObj = result?.data?.data?.message;
  if (typeof msgObj !== "object" || !msgObj) return null;

  const status = (msgObj.status || "").toUpperCase();
  const entry = GATEWAY_STATUS_MAP.find((e) => e.status === status);
  if (!entry) return null;

  return {
    code: entry.gcode,
    ui: entry.ui,
    logMessage: `${entry.gcode} | ${status}`,
    terminalTitle: entry.terminalTitle,
    terminalMsg: entry.terminalMsg,
    layer: "GCODE",
    isCommError: false,
    txState: entry.txState,
    isCancelled: entry.isCancelled,
  };
}

/**
 * Master resolver — orchestrates the linear checking pipeline.
 * @returns {NormalizedError|null}
 */
function getErrorDetails(result) {
  // ── Early exit: ONLY for truly internal/custom return formats ──
  // handleTip returns { success: true, data: { state: "SUCCESS", ... } }
  // These have NO result.status field at all.
  // API wrapper responses ALWAYS have status (200, 409, etc.) and MUST go
  // through the full resolver chain + fail-safe guard to catch aborts,
  // empty responses, and inner errors that the HTTP wrapper marks success.
  const hasStatusField = result?.status !== undefined;
  if (result?.success === true && !hasStatusField) {
    const state = (
      result?.data?.state ||
      result?.data?.data?.state ||
      ""
    ).toUpperCase();
    // Still check inner success — if inner data says false, don't exit early
    const innerFailed =
      result?.data?.success === false ||
      result?.data?.data?.success === false;
    if (!innerFailed && state !== "CANCELLED" && state !== "CANCELED") {
      return null; // Happy path — internal format confirmed success
    }
  }

  const explicitError =
    _resolveCldsCode(result) ||
    _resolveCommCode(result) ||
    _resolveCcode(result) ||
    _resolveGcode(result) ||
    _resolveTcode(result) ||
    _resolvePyrdCode(result);

  if (explicitError) return explicitError;

  // ── Fail-safe: check ALL levels for explicit failure ──
  // Responses nest success/failure at different levels depending on the endpoint.
  const isExplicitlyFailed =
    result?.success === false ||
    result?.data?.success === false ||
    result?.data?.data?.success === false ||
    result?.status === "error";

  if (isExplicitlyFailed) {
    const m9xCount = _incrementM9xCounter();
    const escalated = m9xCount >= 2;
    return {
      code: "M99",
      ui: escalated
        ? "Unexpected response from terminal. Contact Support immediately."
        : (result?.data?.error_message ||
          result?.data?.data?.error_message ||
          result?.error_message ||
          result?.message ||
          "Unknown error occurred."),
      logMessage: `UNKNOWN_FALLBACK | ${result?.data?.error_code || result?.data?.data?.error_code || result?.error_code || "no_code"} | attempt=${m9xCount}`,
      terminalTitle: escalated ? "Contact Support" : "Error",
      terminalMsg: escalated ? "Await merchant instructions" : "Please Try Again",
      layer: "UNKNOWN",
      isCommError: false,
      txState: "error",
      isCancelled: false,
    };
  }

  // ── Fail-safe final guard ──
  // If we reach here, no resolver detected an error AND no explicit failure flag.
  // But we must CONFIRM positive success before allowing it through.
  // Financial safety: better to reject an ambiguous response than approve it.
  const msg = result?.data?.data?.message || result?.data?.data?.data?.message;

  // ── Explicit negative: api_service already determined auth/capture failed ──
  // If auth_verified or capture_verified is explicitly false, do NOT allow
  // wrapper-level success flags to override that determination.
  const hasExplicitRejection =
    result?.auth_verified === false ||
    result?.capture_verified === false;

  const hasPositiveConfirmation =
    !hasExplicitRejection && (
    // Auth: responseCode "00" or status AUTHORIZED/SUCCESS
    (typeof msg === "object" && (
      msg?.responseCode === "00" ||
      msg?.status === "AUTHORIZED" ||
      msg?.status === "SUCCESS" ||
      msg?.status === "PENDING"
    )) ||
    // Capture: status PENDING with ID
    (typeof msg === "object" && !!msg?.id) ||
    // Verified flags set by api_service
    result?.auth_verified === true ||
    result?.capture_verified === true ||
    // API wrapper: success=true with clean error_code at any level
    (result?.data?.success === true &&
      (!result?.data?.error_code || result?.data?.error_code === "000000")) ||
    (result?.data?.data?.success === true &&
      (!result?.data?.data?.error_code || result?.data?.data?.error_code === "000000"))
    );

  if (!hasPositiveConfirmation) {
    const m9xCount = _incrementM9xCounter();
    const escalated = m9xCount >= 2;
    // Log for debugging — this catches unknown response formats
    payrilliumConsole.warn(
      `[FAIL-SAFE] No positive confirmation found in response. Treating as error. (attempt=${m9xCount})`,
      { result },
    );
    return {
      code: "M98",
      ui: escalated
        ? "Unexpected response from terminal. Contact Support immediately."
        : "Unexpected response from terminal. Payment not confirmed.",
      logMessage: `FAIL_SAFE_NO_POSITIVE | No explicit success indicators found | attempt=${m9xCount}`,
      terminalTitle: escalated ? "Contact Support" : "Not Confirmed",
      terminalMsg: escalated ? "Await merchant instructions" : "Please Try Again",
      layer: "UNKNOWN",
      isCommError: false,
      txState: "error",
      isCancelled: false,
    };
  }

  // Confirmed happy path — explicit positive indicators found
  _resetM9xCounter();
  return null;
}

/**
 * Check if the payment was cancelled by the user.
 * Driven entirely by the normalized error definitions.
 */
export function isCancelled(result) {
  const errorDetails = getErrorDetails(result);
  return errorDetails ? errorDetails.isCancelled === true : false;
}

/**
 * Check if the payment failed (not cancelled, but unsuccessful).
 * Driven entirely by the normalized error definitions.
 */
export function isFailed(result) {
  const errorDetails = getErrorDetails(result);
  return errorDetails ? errorDetails.isCancelled === false : false;
}

/**
 * Get a user-friendly message based on the result.
 * For application errors the terminal message is returned as-is.
 */
export function getPayrilliumMessage(result) {
  const details = getErrorDetails(result);
  if (details) return details.ui;
  return "Payment processed successfully.";
}

/**
 * Validate the result and throw an error if it failed or was cancelled.
 * Thrown errors carry user friendly payloads and layer information.
 * If allowCancelled is true, cancelled operations return instead of throwing.
 */
export function validatePayrilliumResponse(
  result,
  { allowCancelled = false } = {},
) {
  const details = getErrorDetails(result);

  // Happy path
  if (!details) {
    return { success: true };
  }

  // Layer 1: Communication failure (MC99, etc.)
  if (details.isCommError) {
    payrilliumConsole.log("Comm error →", details.code);
    // BUSY ≠ offline: the terminal is reachable but occupied.
    // Don't change the navbar indicator — let the retry wrapper handle it silently.
    if (details.txState !== "terminal_busy") {
      payrilliumBus.trigger("payrillium:terminal_offline");
    }
    const error = new Error(details.ui);
    error.payrilliumError = true;
    error.terminalConnectionError = details.txState !== "terminal_busy";
    error.mcCode = details.code;
    error.logMessage = details.logMessage;
    error.terminalTitle = details.terminalTitle;
    error.terminalMsg = details.terminalMsg;
    error.layer = details.layer;
    error.txState = details.txState;
    error.originalResponse = result;
    throw error;
  }

  // Cancellation (TCODE — ABORTED from PAX)
  if (details.isCancelled) {
    if (allowCancelled)
      return { cancelled: true, message: details.ui, txState: details.txState };
    const error = new Error(details.ui);
    error.payrilliumError = true;
    error.cancelled = true;
    error.mcCode = details.code;
    error.logMessage = details.logMessage;
    error.terminalTitle = details.terminalTitle;
    error.terminalMsg = details.terminalMsg;
    error.layer = details.layer;
    error.txState = details.txState;
    error.originalResponse = result;
    throw error;
  }

  // Application error (TCODE, GCODE, CCODE, PYRD, etc.)
  const error = new Error(details.ui);
  error.payrilliumError = true;
  error.mcCode = details.code;
  error.logMessage = details.logMessage;
  error.terminalTitle = details.terminalTitle;
  error.terminalMsg = details.terminalMsg;
  error.layer = details.layer;
  error.txState = details.txState;
  error.originalResponse = result;
  throw error;
}

/**
 * Derives the correct pos.payment.line state.
 * Uses getErrorDetails error dictionaries for mapping failure states.
 * Uses exact Cybersource success strings for mappings success states.
 */
export function updateTransactionState(step, response) {
  const errorDetails = getErrorDetails(response);

  // 1. Map failure states using the source of truth dictionary
  if (errorDetails) {
    return errorDetails.txState;
  }

  // 2. Happy Path
  const status = response?.data?.data?.message?.status?.toUpperCase() || "";

  switch (status) {
    case "PENDING":
      return "done";
    case "APPROVED":
    case "SUCCESS":
    case "SUCCESS_AUTH":
    case "AUTHORIZED":
      return step === "authorize" ? "authorized" : "done";
    default:
      return "draft";
  }
}
export async function logPayrilliumError(
  rpc,
  {
    executionId = "missing",
    step = "unspecified_step",
    kind = "response",
    success = false,
    errorMessage = "",
    payload = {},
  },
) {
  payrilliumConsole.log(
    " logWoodforestError",
    executionId,
    step,
    kind,
    success,
    errorMessage,
    payload,
  );
  payrilliumConsole.log(rpc, "rpc");

  try {
    await rpc("/woodforest/log", {
      execution_id: executionId,
      step,
      kind,
      success,
      error_message: errorMessage,
      payload,
    });
  } catch (e) {
    payrilliumConsole.warn(" Failed to log Woodforest error from JS", e);
  }
}

/**
 * Map card vendor codes to their corresponding names.
 * @type {Object}
 * @property {string} "001" - VISA
 * @property {string} "002" - MASTERCARD
 * @property {string} "003" - AMERICAN EXPRESS
 * @property {string} "004" - DISCOVER
 * @property {string} "005" - DINERS CLUB
 * @property {string} "006" - CARTE BLANCHE
 * @property {string} "007" - JCB
 * @property {string} "033" - VISA ELECTRON
 */
export const CARD_VENDOR = {
  "001": "VISA",
  "002": "MASTERCARD",
  "003": "AMERICAN EXPRESS",
  "004": "DISCOVER",
  "005": "DINERS CLUB",
  "006": "CARTE BLANCHE",
  "007": "JCB",
  "033": "VISA ELECTRON",
};

export const CARD_VENDOR_BY_METHOD = {
  VI: "VISA",
  MC: "MASTERCARD",
  AX: "AMERICAN EXPRESS",
  DI: "DISCOVER",
  DC: "DINERS CLUB",
  CB: "CARTE BLANCHE",
  JC: "JCB",
};

/**
 * Maps a Cybersource TSS (Transaction Search/Detail) response to the format
 * expected by the POS receipt (PAYRILLIUM_RECEIPT_FIELDS).
 * @param {Object} tssData - The JSON response from /tss/v2/transactions/{id}
 * @returns {Object} Receipt-compatible data
 */
/**
 * Normalizes card type from various sources (Terminal, Cybersource TSS)
 * @param {object} info - Transaction info from Cybersource or terminal message
 * @returns {string} - Normalized type: 'CREDIT', 'DEBIT', 'TOKENIZED_CREDIT', or 'TOKENIZED_DEBIT'
 */
export function normalizeCardType(info) {
  if (!info) return "N/A";

  // 1. Extract raw type from Cybersource TSS or Terminal
  const rawType = (
    info.paymentInformation?.paymentType?.type ||
    info.edcType ||
    info.cardType ||
    ""
  ).toUpperCase();

  // 2. Identification for cases where it's already a Token identifier string
  if (rawType === "TOKENIZED_DEBIT" || rawType === "SAVED DEBIT CARD")
    return "TOKENIZED_DEBIT";
  if (rawType === "TOKENIZED_CREDIT" || rawType === "SAVED CREDIT CARD")
    return "TOKENIZED_CREDIT";
  if (rawType === "CARD_PAYMENT" || rawType === "TOKENIZED_CARD")
    return "TOKENIZED_CARD";

  // 3. Strict mapping for Cybersource strings like "credit card" or "debit card"
  if (rawType.includes("DEBIT")) return "DEBIT";
  if (rawType.includes("CREDIT")) return "CREDIT";

  return rawType || "N/A";
}

export function mapTSSDataToReceipt(tssData) {
  const info = tssData || {};
  const card = info.paymentInformation?.card || {};
  const proc = info.processorInformation || {};
  const pos = info.pointOfSaleInformation || {};
  const order = info.orderInformation?.amountDetails || {};
  const emvTags = pos.emv?.tags || "";

  // Parse EMV tags if present
  const parsedEMV = emvTags ? parseEMVData(emvTags) : {};

  const cardMethod = info.paymentInformation?.paymentType?.method || "";
  const cardVendor =
    CARD_VENDOR[card.type] || CARD_VENDOR_BY_METHOD[cardMethod] || "N/A";

  const responseCode = proc.responseCode || "00";
  const responseCodeMeaning = ARC_MEANING[responseCode] || "Approved";

  return {
    _amount: parseFloat(order.totalAmount || 0),
    cardType: normalizeCardType(info),
    approvalCode: proc.approvalCode || "N/A",
    cardVendor: cardVendor,
    cardNumber: card.suffix ? `****${card.suffix}` : "N/A",
    transactionId: info.id || "N/A",
    terminalId: pos.terminalId || "N/A",
    entryMode: pos.entryMode?.toUpperCase() || "N/A",
    date: info.submitTimeUTC
      ? info.submitTimeUTC.replace("T", " ").replace("Z", "")
      : "N/A",
    status: info.applicationInformation?.status || "SUCCESS",
    CVM: "N/A", // TSS might not have the Odoo-mapped CVM code easily
    responseCode: responseCode,
    responseCodeMeaning: responseCodeMeaning,
    ...parsedEMV,
  };
}
