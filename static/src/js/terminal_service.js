/** @odoo-module **/

import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;
const TERMINAL_SETTLE_MS = 500; // Basket is fire-and-forget; minimal settle time

export async function showAmountOnTerminal(
  payrilliumAPI,
  paymentLine,
  executionId,
  sessionId = null,
) {
  const result = await payrilliumAPI.showBasket(
    paymentLine,
    executionId,
    sessionId,
  );
  await new Promise((resolve) => setTimeout(resolve, TERMINAL_SETTLE_MS));
  return result;
}

export async function selectCardType(
  payrilliumAPI,
  executionId,
  sessionId = null,
) {
  return await payrilliumAPI.requestCardType(executionId, sessionId);
}

export async function handleTip(
  payrilliumAPI,
  paymentLine,
  posService,
  executionId,
  sessionId = null,
  terminalConfig = {},
) {
  // Check if tips are enabled in POS session configuration
  const sessionTipsEnabled = posService.config.iface_tipproduct || false;

  // Check if tips are enabled in terminal configuration
  // If terminalConfig is empty or iface_tipproduct is undefined, default to true
  // Otherwise, use the actual value from terminal
  const terminalTipsEnabled =
    terminalConfig?.iface_tipproduct !== undefined &&
    terminalConfig?.iface_tipproduct !== null
      ? terminalConfig.iface_tipproduct
      : true; // Default to true if not set

  console.log(" handleTip - Tip configuration check:", {
    sessionTipsEnabled,
    terminalConfig,
    terminalTipsEnabled,
    terminalIfaceTipProduct: terminalConfig?.iface_tipproduct,
    tipMode: terminalConfig?.tip_mode || "amount",
  });

  // Tips must be enabled in BOTH session AND terminal to show tip selection
  if (!sessionTipsEnabled || !terminalTipsEnabled) {
    return {
      success: true,
      data: {
        state: "SUCCESS",
        data: {
          tipAmount: 0,
          message: sessionTipsEnabled
            ? "Tips not enabled on terminal"
            : "Tips not enabled in session",
        },
      },
    };
  }

  // Check if tip was already selected from the Odoo UI (tip product line).
  // When the cashier adds tip via UI, Odoo adds a product line that increases
  // the order total. The cashier then manually distributes amounts across
  // payment lines, so the tip is ALREADY included in each line's amount.
  // We return tipAmount: 0 so _processPayrilliumPayment does NOT add it again.
  const order = paymentLine.pos_order_id;
  const tipProductId = posService.config.tip_product_id;
  if (tipProductId) {
    const tipLine = order.lines.find(
      (line) => line.product_id && line.product_id.id === tipProductId.id,
    );
    if (tipLine && tipLine.price_unit && parseFloat(tipLine.price_unit) > 0) {
      console.log(
        " handleTip - Tip already added from UI, skipping terminal prompt. Tip product amount:",
        tipLine.price_unit,
      );
      return {
        success: true,
        data: {
          state: "SUCCESS",
          data: {
            tipAmount: 0,
            message: "Tip already included from UI (no extra amount to add)",
          },
        },
      };
    }
  }

  // No tip from UI — show tip selection on the terminal for THIS payment line.
  // Each payment line independently asks for tip.
  const tipMode = terminalConfig?.tip_mode || "amount";
  const tipOptions = terminalConfig?.tip_options;

  const tipAmount = await payrilliumAPI.showTipSelection(
    paymentLine,
    executionId,
    sessionId,
    tipMode,
    tipOptions,
  );

  // Apply tip directly to this payment line's internal memory only.
  // NOTE: We intentionally do NOT call posService.set_tip() here.
  // We also do NOT add this to paymentLine.set_amount() because Odoo uses that
  // to calculate the 'remaining' debt. Doing so would artificially fulfill the order.
  // We track it in paymentLine._terminalTip so _processPayrilliumPayment can use it later.
  if (tipAmount > 0) {
    paymentLine._terminalTip = tipAmount;
    console.log(
      ` handleTip - Terminal tip $${tipAmount} tracked internally. Line base amount unchanged at: $${paymentLine.get_amount()}`,
    );
  }

  // Return in the expected format
  return {
    success: true,
    data: {
      state: "SUCCESS",
      data: {
        tipAmount: tipAmount || 0,
        message:
          tipAmount > 0 ? "Tip selected from terminal" : "No tip selected",
      },
    },
  };
}

export async function authorizePayment(
  payrilliumAPI,
  paymentLine,
  cardType,
  tipAmount,
  paymentRef,
  executionId,
  sessionId = null,
) {
  const result = await payrilliumAPI.authorizePayment(
    paymentLine,
    cardType,
    tipAmount,
    paymentRef,
    executionId,
    sessionId,
  );
  return result;
}

export async function handleAuthorizationFailure(
  payrilliumAPI,
  paymentLine,
  cardType,
  result,
  paymentRef,
  executionId,
  transactionId,
  amountOverride = null,
  sessionId = null,
) {
  try {
    const isDebit = cardType === "DEBIT";
    const apiCall = isDebit
      ? payrilliumAPI.voidDebitAuthorize
      : payrilliumAPI.authReversal;
    const actionLabel = isDebit ? "Void Debit" : "Reversal";

    const cleanupResult = await apiCall.call(
      payrilliumAPI,
      {
        payment_id: `${paymentRef}${isDebit ? "vda" : "rv"}`,
        transaction_id: transactionId,
        totalAmount: amountOverride || paymentLine.get_amount().toFixed(2),
        executionId,
      },
      executionId,
      sessionId,
    );

    if (cleanupResult.reversal_verified === true) {
      console.log(` ${actionLabel} successfully verified.`);
      return {
        success: true,
        reversal_verified: true,
        data: {
          state: "REVERSED",
          data: { message: `Payment ${actionLabel.toLowerCase()} verified` },
        },
      };
    } else {
      console.error(` ${actionLabel} failed in validation.`);
      return {
        success: false,
        reversal_verified: false,
        data: {
          state: "REVERSAL_FAILED",
          data: {
            message:
              cleanupResult.message || `${actionLabel} failed validation`,
          },
        },
      };
    }
  } catch (error) {
    console.error(" Error during handleAuthorizationFailure:", error);
    return {
      success: false,
      reversal_verified: false,
      data: {
        state: "ERROR",
        data: {
          message: error.message || "Error during cleanup operation",
          type: "ERROR",
          originalError: error,
        },
      },
    };
  }
}

export async function captureCredit(
  payrilliumAPI,
  paymentLine,
  paymentRef,
  transactionId,
  executionId,
  message,
  sessionId = null,
) {
  const entryMode = message?.entryMode?.toUpperCase?.();
  const emvTags = ["CONTACT", "CONTACTLESS"].includes(entryMode)
    ? message?.emvTags || ""
    : undefined;

  const payload = {
    payment_id: `${paymentRef}c`,
    amount: paymentLine.get_amount().toFixed(2),
    transaction_id: transactionId,
    ...(emvTags !== undefined && { emv_tags: emvTags }),
  };

  console.log("payload", payload);

  const result = await payrilliumAPI.captureCreditPayment(
    payload,
    executionId,
    sessionId,
  );

  return result;
}

export async function processRefund(payrilliumAPI, params) {
  const {
    cardType,
    paymentId,
    transaction_id,
    amount,
    executionId,
    terminalId,
    tokenCardId,
    sessionId,
  } = params;

  console.log("Processing refund with parameters:", {
    cardType,
    paymentId,
    transaction_id,
    amount,
    executionId,
    tokenCardId,
  });

  // Naming convention: rd for Debit, r for others
  const suffix = cardType === "DEBIT" ? "rd" : "r";
  const basePayload = {
    payment_id: `${paymentId}${suffix}`,
    transaction_id,
  };

  let result;

  if (
    ["TOKENIZED_CARD", "TOKENIZED_CREDIT", "TOKENIZED_DEBIT"].includes(cardType)
  ) {
    // Tokenize card refund
    result = await payrilliumAPI.refundTokenize(
      { ...basePayload, amount, token_card_id: tokenCardId },
      executionId || null,
      sessionId || null,
    );
  } else if (cardType === "CREDIT") {
    // TODO: In future versions, a pre-request to Mirillium will be needed here
    // to determine if the transaction should be a 'voidCapture' (if not yet settled)
    // or a 'refundCapture' (if already settled).
    // Credit card refund
    result = await payrilliumAPI.refundCapture(
      { ...basePayload, amount },
      executionId || null,
      sessionId || null,
    );
  } else if (cardType === "DEBIT") {
    // Specific Debit card refund structure
    result = await payrilliumAPI.refundDebit(
      {
        ...basePayload,
        totalAmount: amount,
      },
      executionId || null,
      sessionId || null,
    );
  } else {
    // Fallback/Legacy Debit card refund
    result = await payrilliumAPI.refundDebit(
      {
        ...basePayload,
        totalAmount: amount,
        tips: "",
        cashback: "",
      },
      executionId || null,
      sessionId || null,
    );
  }

  return result;
}

export async function showSuccessMessage(
  payrilliumAPI,
  config,
  amount,
  executionId,
  sessionId = null,
  tipAmount = 0,
) {
  const settings = config?.approved || {
    title: "Approved",
    message: "{amount} Successfully Charged",
    timeout: "3",
  };
  let message;
  if (tipAmount > 0) {
    const base = typeof amount === "string" ? amount : String(amount);
    message = `${base} Charged (includes tip)`;
  } else {
    message = (settings.message || "").replace("{amount}", amount);
  }
  return await payrilliumAPI.showApproved(
    {
      title: settings.title,
      message: message,
      timeout: settings.timeout,
    },
    executionId,
    sessionId,
  );
}

export async function showDeclineMessage(
  payrilliumAPI,
  config,
  executionId,
  sessionId = null,
  { title, message } = {},
) {
  const settings = config?.decline || {};
  return await payrilliumAPI.showDecline(
    {
      title: title || settings.title || "Declined",
      message: message || settings.message || "Transaction failed",
      timeout: settings.timeout || "5",
    },
    executionId,
    sessionId,
  );
}
