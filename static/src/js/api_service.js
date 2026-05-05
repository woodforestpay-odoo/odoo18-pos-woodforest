/** @odoo-module **/

import { ConfigLoader } from "@pos_woodforest/js/config_loader";
import {
  payrilliumConsole,
  validatePayrilliumResponse,
} from "@pos_woodforest/js/utils";
import { rpc } from "@web/core/network/rpc";

const console = payrilliumConsole;

// ─────────────────────────────────────────────
// TERMINAL_BUSY Retry Wrapper
// When the terminal responds BUSY (e.g., processing a previous txn),
// this retries the same RPC call up to MAX_RETRIES times with a delay.
// ─────────────────────────────────────────────
const BUSY_MAX_RETRIES = 3;
const BUSY_RETRY_DELAY_MS = 3000;

/**
 * Wraps an async function that calls the terminal. If the function throws
 * with txState === "terminal_busy", it retries automatically.
 * @param {Function} fn - async function to call (should return the response)
 * @param {string} label - human-readable action name for logging
 * @returns {Promise<*>} - the response from the successful attempt
 */
async function _withBusyRetry(fn, label = "terminal call") {
  for (let attempt = 1; attempt <= BUSY_MAX_RETRIES + 1; attempt++) {
    try {
      const result = await fn();

      // Detect BUSY from the response payload (for endpoints that don't call validatePayrilliumResponse)
      const errorCode =
        result?.error_code ||
        result?.data?.error_code ||
        result?.data?.data?.error_code || "";
      const isBusyResponse =
        (typeof errorCode === "string" && errorCode.includes("TERMINAL_BUSY")) ||
        result?.status === 409;

      if (isBusyResponse && attempt <= BUSY_MAX_RETRIES) {
        console.warn(
          `[BUSY RETRY] ${label}: terminal busy (response) on attempt ${attempt}/${BUSY_MAX_RETRIES}, retrying in ${BUSY_RETRY_DELAY_MS / 1000}s...`,
        );
        await new Promise((r) => setTimeout(r, BUSY_RETRY_DELAY_MS));
        continue;
      }

      return result;
    } catch (error) {
      if (error.txState === "terminal_busy" && attempt <= BUSY_MAX_RETRIES) {
        console.warn(
          `[BUSY RETRY] ${label}: terminal busy (thrown) on attempt ${attempt}/${BUSY_MAX_RETRIES}, retrying in ${BUSY_RETRY_DELAY_MS / 1000}s...`,
        );
        await new Promise((r) => setTimeout(r, BUSY_RETRY_DELAY_MS));
        continue;
      }
      // Not a busy error, or exhausted retries — propagate
      throw error;
    }
  }
}
console.log(" POS Payrillium - API Service LOADING");

// ─────────────────────────────────────────────
// Async Polling Helper
// Wraps rpc() to support backend polling for long Mirillium calls.
// If backend returns {status: "polling", job_id}, this function
// polls /woodforest/poll every intervalMs until the result is ready.
// ─────────────────────────────────────────────
async function _rpcWithPolling(
  url,
  params,
  intervalMs = 3000,
  maxWaitMs = 180000,
) {
  const startResponse = await rpc(url, params);

  // Backward compat: if backend responds normally (no polling), return as-is
  if (startResponse.status !== "polling" || !startResponse.job_id) {
    return startResponse;
  }

  // Backend started an async job — poll for result
  const jobId = startResponse.job_id;
  const deadline = Date.now() + maxWaitMs;
  console.log(`⏳ [polling] Job ${jobId} started for ${url}`);

  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    const poll = await rpc("/woodforest/poll", { job_id: jobId });

    if (poll.status === "done") {
      console.log(` [polling] Job ${jobId} completed`);
      return poll.data;
    }
    if (poll.status === "error") {
      console.warn(` [polling] Job ${jobId} errored`);
      return poll.data;
    }
    if (poll.status === "not_found") {
      console.warn(` [polling] Job ${jobId} not found (expired?)`);
      return { status: "error", message: "Job expired or not found" };
    }
    // status === "pending" → continue polling
  }

  console.error(
    `⏰ [polling] Job ${jobId} timed out after ${maxWaitMs / 1000}s`,
  );
  return {
    status: "error",
    message: `Payment request timed out after ${maxWaitMs / 1000}s`,
  };
}

// Helper function to get terminal info and log it
async function _logTerminalInfo(sessionId, actionName) {
  if (sessionId) {
    try {
      const result = await rpc("/woodforest/session/terminal", {
        sessionId: sessionId,
      });
      if (result.success && result.terminal) {
        console.log(` [${actionName}] Sending request to Terminal:`, {
          id: result.terminal.id,
          name: result.terminal.name,
          serial: result.terminal.serial,
          sessionId: sessionId,
        });
        return result.terminal;
      }
    } catch (error) {
      console.warn(`️ [${actionName}] Could not get terminal info:`, error);
    }
  } else {
    console.warn(
      `️ [${actionName}] No sessionId provided, using fallback terminal lookup`,
    );
  }
  return null;
}

export const PayrilliumAPI = {
  async showBasket(input, executionId = null, sessionId = null) {
    const order = input?.pos_order_id || input?.order || input;

    if (!order) {
      console.error("Invalid order input");
      return { success: false, message: "Invalid order input" };
    }

    const lines =
      order.get_orderlines?.() ||
      order.getOrderReceiptEnv?.()?.orderlines ||
      order.orderlines?.models ||
      order.orderlines ||
      [];

    if (!Array.isArray(lines)) {
      console.error("Could not resolve orderlines");
      return { success: false, message: "Invalid orderlines" };
    }

    console.log(lines, "lines");
    const imageBaseUrl = await ConfigLoader.getImageBaseUrl();
    console.log(imageBaseUrl, "imageBaseUrl");

    const products = lines.map((line) => ({
      id: line.product_id.id,
      sale_upc_code: line.product_id.barcode || "N/A",
      upc_code: line.product_id.barcode || "N/A",
      image: `${imageBaseUrl}/image/${line.product_id.id}` || "",
      name: line.product_id.display_name,
      qty: `${line.qty}`,
      price: `${line.get_unit_price().toFixed(2)}`,
      total: `${line.get_display_price().toFixed(2)}`,
      group_name: "DEFAULT",
    }));

    const discount = lines.reduce((acc, line) => {
      console.log(line.get_discount(), "line");

      const line_total = line.get_unit_price() * line.qty;
      return acc + (line_total * line.get_discount()) / 100;
    }, 0);
    console.log(discount, "discount");

    const payload = {
      products,
      currency: "USD",
      subtotal: order.get_total_without_tax().toFixed(2),
      tax: order.get_total_tax().toFixed(2),
      discount: discount.toFixed(2),
      items: `${lines.length}`,
      total: order.get_total_with_tax().toFixed(2),
      cash_discount: "0.00",
      non_cash_adjustment: "0.00",
      total_after_cash_discount: order.get_total_with_tax().toFixed(2),
      cash_discount_config_active: true,
      transaction_type: "sale",
    };
    console.log(" Starting showBasket request");

    try {
      await _logTerminalInfo(sessionId, "showBasket");
      const response = await _rpcWithPolling("/woodforest/proxy/basket", {
        kwargs: { ...payload, executionId, sessionId },
      });

      if (response.status === "error") {
        console.error("Basket request failed:", response.message);
        throw new Error(response.message);
      }

      return response;
    } catch (error) {
      console.error(" Error Error showBasket request:", error);
      return { status: "error", message: error.message };
    }
  },
  async showEmptyBasket(executionId = null, sessionId = null) {
    const payload = {
      products: [],
      currency: "USD",
      subtotal: "0.00",
      tax: "0.00",
      discount: "0.00",
      items: "0",
      total: "0.00",
      cash_discount: "0.00",
      non_cash_adjustment: "0.00",
      total_after_cash_discount: "0.00",
      cash_discount_config_active: true,
      transaction_type: "sale",
    };

    try {
      await _logTerminalInfo(sessionId, "showEmptyBasket");
      const response = await _rpcWithPolling("/woodforest/proxy/basket", {
        kwargs: { ...payload, executionId, sessionId },
      });
      if (response.status === "error") {
        console.error("Basket request failed:", response.message);
        throw new Error(response.message);
      }
      return response;
    } catch (error) {
      console.error(" Error during showEmptyBasket request:", error);
      return { status: "error", message: error.message };
    }
  },
  // 18.3: Sends /payment/abort to the terminal assigned to the given session.
  // Call this on session open or when returning to the product screen.
  async abortTerminal(sessionId = null, executionId = null) {
    try {
      const response = await rpc("/woodforest/terminal/abort", {
        sessionId,
        executionId,
      });
      console.log("[abortTerminal] result:", response);
      return response;
    } catch (error) {
      console.warn(
        "[abortTerminal] Could not send abort (terminal may be idle):",
        error,
      );
      return { success: false, message: error.message };
    }
  },
  async showApproved(payload, executionId = null, sessionId = null) {
    return _withBusyRetry(async () => {
      try {
        await _logTerminalInfo(sessionId, "showApproved");
        const response = await _rpcWithPolling("/woodforest/proxy/approved", {
          kwargs: { ...payload, executionId, sessionId },
        });
        return response;
      } catch (error) {
        if (error.payrilliumError) throw error;
        console.error(" Error during showApproved:", error);
        return { success: false, message: error.message };
      }
    }, "showApproved");
  },

  async showDecline(payload, executionId = null, sessionId = null) {
    return _withBusyRetry(async () => {
      try {
        await _logTerminalInfo(sessionId, "showDecline");
        const response = await _rpcWithPolling("/woodforest/proxy/decline", {
          kwargs: { ...payload, executionId, sessionId },
        });
        return response;
      } catch (error) {
        if (error.payrilliumError) throw error;
        console.error(" Error during showDecline:", error);
        return { success: false, message: error.message };
      }
    }, "showDecline");
  },

  async requestCardType(executionId = null, sessionId = null) {
    try {
      await _logTerminalInfo(sessionId, "requestCardType");
      const response = await _rpcWithPolling("/woodforest/proxy/card", {
        kwargs: { data: "", executionId, sessionId },
      });
      return response;
    } catch (error) {
      console.error(" Error during requestCardType:", error);
      return { success: false, message: error.message };
    }
  },

  async showTipSelection(
    paymentLine,
    executionId = null,
    sessionId = null,
    tipMode = "amount",
    tipOptionsFromBackend = null,
  ) {
    const lineAmount = paymentLine.get_amount();
    const isPercentMode = tipMode === "percent";

    // Use backend terminal config if provided and non-empty; otherwise fallback
    const defaultAmounts = [5.0, 10.0, 20.0];
    const defaultPercents = [15, 20, 25];
    const tipValues =
      Array.isArray(tipOptionsFromBackend) && tipOptionsFromBackend.length > 0
        ? tipOptionsFromBackend.map((v) => Number(v))
        : isPercentMode
          ? defaultPercents
          : defaultAmounts;

    // What the customer sees on the terminal
    const tipOptionsShow = isPercentMode
      ? tipValues.map((v) => `${v}%`)
      : tipValues.map((v) => `$${Number(v).toFixed(2)}`);
    tipOptionsShow.push("Custom", "No Tip");

    let tipAmount = 0;

    return _withBusyRetry(async () => {
      try {
        const payload = {
          title: "Select Tip",
          menu: tipOptionsShow,
          amount: lineAmount,
        };
        await _logTerminalInfo(sessionId, "showTipSelection");
        const tipResult = await _rpcWithPolling("/woodforest/proxy/tip", {
          kwargs: { ...payload, executionId, sessionId },
        });

        // ── Explicit cancel detection for tip screen ──
        const tipState = (tipResult?.data?.state || "").toUpperCase();
        const tipType = tipResult?.data?.type || "";
        if (
          tipState === "CANCELLED" ||
          tipState === "CANCELED" ||
          tipType.includes("Cancelled") ||
          tipType.includes("Canceled")
        ) {
          const cancelErr = new Error("Tip cancelled by customer.");
          cancelErr.tipCancelled = true;
          cancelErr.cancelled = true;
          cancelErr.terminalTitle = "Cancelled";
          cancelErr.terminalMsg = "Tip Cancelled";
          cancelErr.txState = "cancel";
          throw cancelErr;
        }

        // ── Single validation point ──
        validatePayrilliumResponse(tipResult);

        const resultType = tipResult?.data?.type || "";
        const resultData = tipResult?.data?.data;

        if (resultType?.includes("TipResultCustom")) {
          const raw = parseFloat(
            String(resultData?.value ?? 0).replace(",", "."),
          );
          if (!isNaN(raw) && raw > 0) {
            tipAmount = isPercentMode ? (lineAmount * raw) / 100 : raw;
          }
        } else if (resultType?.includes("TipResultOption")) {
          const index = resultData?.selection;
          if (typeof index === "number" && index >= 0) {
            if (index < tipValues.length) {
              const value = tipValues[index];
              tipAmount = isPercentMode ? (lineAmount * value) / 100 : value;
            } else {
              tipAmount = 0;
            }
          }
        }
      } catch (error) {
        // Re-throw cancellation, inactivity, and connection errors so they bubble up
        if (
          error.tipCancelled ||
          error.cancelled ||
          error.mcCode ||
          error.terminalConnectionError
        ) {
          throw error;
        }
        // Re-throw payrillium errors (including terminal_busy) for the retry wrapper
        if (error.payrilliumError) throw error;
        console.error(" Error during tip selection:", error);
        throw error; // Don't swallow — let _processPayrilliumPayment handle it
      }

      return tipAmount;
    }, "showTipSelection");
  },

  async authorizePayment(
    paymentLine,
    cardType,
    tipAmount,
    paymentRef,
    executionId = null,
    sessionId = null,
  ) {
    // Read save_card state from payment line (checkbox state)
    // If save_card is undefined or false, tokenizeCard will be false
    const shouldTokenize = paymentLine.save_card === true;

    // The physical terminal only understands a flat amount to charge.
    // It does not understand separate base vs tip fields for authorization.
    const totalAmount = paymentLine.get_amount() + (tipAmount || 0);

    const payload = {
      cardType: cardType,
      payment_id: paymentRef,
      amount: totalAmount.toFixed(2),
      tokenizeCard: shouldTokenize,
      capture: true, // true = Sale, false = Auth Only
      usePartialApprove: false, // true = Partial Approve, false = Full Approve
    };

    console.log(" authorizePayment - Request payload:", {
      payment_id: payload.payment_id,
      amount: payload.amount,
      tokenizeCard: payload.tokenizeCard,
      " Checkbox state (save_card)": paymentLine.save_card,
      " Will tokenize card": shouldTokenize,
    });

    return _withBusyRetry(async () => {
      try {
        await _logTerminalInfo(sessionId, "authorizePayment");
        const response = await _rpcWithPolling("/woodforest/payment/auth", {
          kwargs: { ...payload, executionId, sessionId },
        });

        // Log complete response from auth endpoint
        console.log(" AUTH ENDPOINT RESPONSE - Full response:", {
          status: response.status,
          success: response.success,
          data: response.data,
          message: response.message,
          " Response structure": JSON.stringify(response, null, 2),
        });

        // ── Set auth_verified flag for metadata (used by payment_screen) ──
        const msg =
          response?.data?.data?.message ||
          response?.data?.data?.data?.message ||
          {};
        const hasId = !!msg.transactionId || !!msg.id;
        const isApproved =
          msg.responseCode === "00" ||
          msg.status === "AUTHORIZED" ||
          msg.status === "SUCCESS";
        response.auth_verified =
          (response.status === "ok" ||
            response.status === 200 ||
            response.success) &&
          hasId &&
          isApproved;

        if (response.auth_verified) {
          console.log(" AUTH VERIFIED: ID and responseCode 00 confirmed.");
        } else {
          console.warn(" AUTH FAILURE/INVALID: Missing ID or bad responseCode.", {
            hasId,
            isApproved,
          });
        }

        // Log specific fields that might contain tokenization data
        if (response.data) {
          console.log(" AUTH ENDPOINT RESPONSE - Data object:", {
            state: response.data.state,
            data: response.data.data,
            message: response.data.message,
          });
        }

        // Store response data on payment line for later use
        if (response.data && paymentLine) {
          paymentLine._authResponse = response.data;
          console.log(" Stored auth response on payment line:", {
            lineUuid: paymentLine.uuid,
            hasAuthResponse: !!paymentLine._authResponse,
          });
        }

        // ── Single validation point: uses the mapping system (TCODE/CLDS/PYRD/etc.) ──
        // If the response contains an error, validatePayrilliumResponse will throw
        // with the correct terminalTitle, terminalMsg, and error layer from getErrorDetails.
        validatePayrilliumResponse(response);

        return response;
      } catch (error) {
        // If validatePayrilliumResponse threw, re-throw as-is (it has all error metadata)
        if (error.payrilliumError) throw error;
        console.error(" Error during authorizePayment:", error);
        return { status: "error", message: error.message };
      }
    }, "authorizePayment");
  },

  async captureCreditPayment(payload, executionId = null, sessionId = null) {
    try {
      await _logTerminalInfo(sessionId, "captureCreditPayment");
      const response = await _rpcWithPolling("/woodforest/payment/capture", {
        kwargs: { ...payload, executionId, sessionId },
      });

      // Log complete response from capture endpoint
      console.log(" CAPTURE ENDPOINT RESPONSE - Full response:", {
        status: response.status,
        success: response.success,
        data: response.data,
        message: response.message,
        " Response structure": JSON.stringify(response, null, 2),
      });

      const msg = response?.data?.data?.message || {};
      const hasId = !!msg.id;
      const isPending = msg.status === "PENDING";

      if (response.status === "ok" && hasId && isPending) {
        console.log(" CAPTURE VERIFIED: ID and status PENDING confirmed.");
        response.capture_verified = true;
      } else {
        console.warn(
          " CAPTURE FAILURE/INVALID: Missing ID or status not PENDING.",
          { hasId, isPending },
        );
        response.capture_verified = false;
        // If it's a 200 but failed business logic, we treat it as an error for the flow
        if (response.status === "ok") {
          response.status = "error";
          response.message =
            msg.userMessage || "Capture failed or returned invalid data.";
        }
      }

      if (response.status === "error") {
        console.error(" Capture request failed:", response.message);
      }
      return response;
    } catch (error) {
      console.error(" Error during captureCreditPayment:", error);
      return { status: "error", message: error.message };
    }
  },

  async authReversal(payload, executionId = null, sessionId = null) {
    try {
      await _logTerminalInfo(sessionId, "authReversal");
      const response = await _rpcWithPolling(
        "/woodforest/payment/auth_reversal",
        {
          kwargs: { ...payload, executionId, sessionId },
        },
      );

      // Log complete response from reversal endpoint
      console.log(" REVERSAL ENDPOINT RESPONSE - Full response:", {
        status: response.status,
        success: response.success,
        data: response.data,
        message: response.message,
        " Response structure": JSON.stringify(response, null, 2),
      });

      const msg = response?.data?.data?.message || {};
      const hasId = !!(msg.id || msg.reconciliationId);
      const isReversed = msg.status === "REVERSED";

      if (response.status === "ok" && hasId && isReversed) {
        console.log(" REVERSAL VERIFIED: status REVERSED confirmed.");
        response.reversal_verified = true;
      } else {
        console.warn(
          " REVERSAL FAILURE/INVALID: Missing ID or status not REVERSED.",
          { hasId, isReversed, status: msg.status },
        );
        response.reversal_verified = false;
        // If it's a 200 but failed business logic, we treat it as an error for the flow
        if (response.status === "ok") {
          response.status = "error";
          response.message =
            msg.userMessage || "Reversal failed or returned invalid data.";
        }
      }

      if (response.status === "error") {
        console.error(" Reversal request failed:", response.message);
      }
      return response;
    } catch (error) {
      console.error(" Error during authReversal:", error);
      return { status: "error", message: error.message };
    }
  },

  async voidDebitAuthorize(payload, executionId = null, sessionId = null) {
    try {
      await _logTerminalInfo(sessionId, "voidDebitAuthorize");
      const response = await _rpcWithPolling("/woodforest/payment/void_debit", {
        kwargs: { ...payload, executionId, sessionId },
      });

      // Log complete response from void_debit endpoint
      console.log(" VOID DEBIT ENDPOINT RESPONSE - Full response:", {
        status: response.status,
        success: response.success,
        data: response.data,
        message: response.message,
        " Response structure": JSON.stringify(response, null, 2),
      });

      const msg = response?.data?.data?.message || {};
      const hasId = !!(msg.id || msg.reconciliationId);
      const isReversed = msg.status === "REVERSED";

      if (response.status === "ok" && hasId && isReversed) {
        console.log(" VOID DEBIT VERIFIED: status REVERSED confirmed.");
        response.reversal_verified = true; // Use same flag as reversal for simpler handling
      } else {
        console.warn(
          " VOID DEBIT FAILURE/INVALID: Missing ID or status not REVERSED.",
          { hasId, isReversed, status: msg.status },
        );
        response.reversal_verified = false;
        if (response.status === "ok") {
          response.status = "error";
          response.message =
            msg.userMessage || "Void Debit failed or returned invalid data.";
        }
      }

      if (response.status === "error") {
        console.error(" Void Debit request failed:", response.message);
      }
      return response;
    } catch (error) {
      console.error(" Error during voidDebitAuthorize:", error);
      return { status: "error", message: error.message };
    }
  },

  async refundDebit(payload, executionId = null, sessionId = null) {
    return _withBusyRetry(async () => {
      try {
        await _logTerminalInfo(sessionId, "refundDebit");
        const response = await _rpcWithPolling(
          "/woodforest/payment/refund_debit",
          {
            kwargs: { ...payload, executionId, sessionId },
          },
        );

        console.log(" REFUND DEBIT ENDPOINT RESPONSE:", {
          status: response.status,
          success: response.success,
          data: response.data,
        });

        const msg =
          response?.data?.data?.message ||
          response?.data?.data?.data?.message ||
          {};
        const state = response?.data?.state || response?.data?.data?.state;
        const hasId = !!(msg.id || msg.reconciliationId);
        const isRefundStatusValid = [
          "AUTHORIZED",
          "CAPTURED",
          "PENDING",
          "COMPLETED",
        ].includes(msg.status);
        const isRefundSuccess = state === "SUCCESS_REFUND" && isRefundStatusValid;

        if (
          (response.status === "ok" || response.status === 200) &&
          hasId &&
          isRefundSuccess
        ) {
          console.log(" REFUND DEBIT VERIFIED: Valid status confirmed.");
          response.refund_verified = true;
        } else {
          console.warn(" REFUND DEBIT FAILURE/INVALID:", {
            hasId,
            isRefundSuccess,
            state,
            status: msg.status,
          });
          response.refund_verified = false;
          if (response.status === "ok" || response.status === 200) {
            response.status = "error";
            response.success = false;
            if (response.data) response.data.success = false;
            response.message =
              msg.userMessage || "Debit refund failed on processor.";
          }
        }

        if (response.status === "error") {
          console.error("refund request failed:", response.message);
        }
        return response;
      } catch (error) {
        if (error.payrilliumError) throw error;
        console.error(" Error during refundDebit:", error);
        return { status: "error", success: false, message: error.message };
      }
    }, "refundDebit");
  },

  async refundCapture(payload, executionId = null, sessionId = null) {
    return _withBusyRetry(async () => {
      try {
        await _logTerminalInfo(sessionId, "refundCapture");
        const response = await _rpcWithPolling("/woodforest/payment/refund", {
          kwargs: { ...payload, executionId, sessionId },
        });

        console.log(" REFUND CREDIT ENDPOINT RESPONSE:", {
          status: response.status,
          success: response.success,
          data: response.data,
        });

        const msg =
          response?.data?.data?.message ||
          response?.data?.data?.data?.message ||
          {};
        const state = response?.data?.state || response?.data?.data?.state;
        const hasId = !!(msg.id || msg.reconciliationId);
        const isRefundStatusValid = [
          "AUTHORIZED",
          "CAPTURED",
          "PENDING",
          "COMPLETED",
        ].includes(msg.status);
        const isRefundSuccess = state === "SUCCESS_REFUND" && isRefundStatusValid;

        if (
          (response.status === "ok" || response.status === 200) &&
          hasId &&
          isRefundSuccess
        ) {
          console.log(" REFUND CREDIT VERIFIED: Valid status confirmed.");
          response.refund_verified = true;
        } else {
          console.warn(" REFUND CREDIT FAILURE/INVALID:", {
            hasId,
            isRefundSuccess,
            state,
            status: msg.status,
          });
          response.refund_verified = false;
          if (response.status === "ok" || response.status === 200) {
            response.status = "error";
            response.success = false;
            if (response.data) response.data.success = false;
            response.message =
              msg.userMessage || "Credit refund failed on processor.";
          }
        }

        if (response.status === "error") {
          console.error("refund request failed:", response.message);
        }
        return response;
      } catch (error) {
        if (error.payrilliumError) throw error;
        console.error(" Error during refundCredit:", error);
        return { status: "error", success: false, message: error.message };
      }
    }, "refundCapture");
  },
  async refundTokenize(payload, executionId = null, sessionId = null) {
    try {
      await _logTerminalInfo(sessionId, "refundTokenize");
      const response = await rpc("/woodforest/refund_tokenize", {
        kwargs: { ...payload, executionId, sessionId },
      });
      if (response.status === "error") {
        console.error("refund request failed:", response.message);
        throw new Error(response.message);
      }
      return response;
    } catch (error) {
      console.error(" Error during refundTokenize:", error);
      return { success: false, message: error.message };
    }
  },

  async send_payment_request(cid, amount, executionId = null) {
    try {
      const response = await this.showBasket(amount, executionId);
      if (response.status === "error") throw new Error(response.message);

      return {
        cid,
        payment_status: response.status === "success" ? "done" : "retry",
        transaction_id: response.transaction_id || null,
        message: response.message,
      };
    } catch (error) {
      console.error(" Error in send_payment_request:", error);
      return {
        cid,
        payment_status: "retry",
        message: error.message,
      };
    }
  },
};

console.log(" POS Payrillium - API Service READY");
