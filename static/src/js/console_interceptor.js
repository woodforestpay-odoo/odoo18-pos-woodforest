/** @odoo-module **/

/**
 * Global console interceptor for Payrillium
 * Reads ENVIRONMENT from backend config.py (dev/prod)
 * Disables console.log/warn when ENVIRONMENT="prod", keeps errors always visible
 */

// Store original console methods
const originalConsole = {
  log: console.log,
  warn: console.warn,
  error: console.error,
};

// Default to production (safe) until we fetch the config
let isDebugMode = false;

// Fetch environment from backend
fetch("/woodforest/config/environment", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    jsonrpc: "2.0",
    method: "call",
    params: {},
  }),
})
  .then((response) => response.json())
  .then((data) => {
    const environment = data.result?.environment || "prod";
    isDebugMode = environment === "dev";

    // Log once to confirm configuration loaded
    if (isDebugMode) {
      originalConsole.log(
        `[PAYRILLIUM] Console interceptor loaded - Environment: ${environment} (Debug mode ENABLED)`,
      );
    } else {
      originalConsole.log(
        `[PAYRILLIUM] Console interceptor loaded - Environment: ${environment} (Logs disabled)`,
      );
    }
  })
  .catch((error) => {
    // If fetch fails, default to production mode (no logs)
    originalConsole.error(
      "[PAYRILLIUM] Failed to fetch environment config, defaulting to production mode (logs disabled)",
      error,
    );
    isDebugMode = false;
  });

// Override console.log - only works in dev mode
console.log = function (...args) {
  if (isDebugMode) {
    originalConsole.log.apply(console, args);
  }
};

// Override console.warn - only works in dev mode
console.warn = function (...args) {
  if (isDebugMode) {
    originalConsole.warn.apply(console, args);
  }
};

// console.error ALWAYS works (critical for production debugging)
console.error = function (...args) {
  originalConsole.error.apply(console, args);
};
