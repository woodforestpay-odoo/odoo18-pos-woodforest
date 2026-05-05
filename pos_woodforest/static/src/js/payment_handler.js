/** @odoo-module **/

import { parseEMVData, payrilliumConsole } from "@pos_woodforest/js/utils";
import { ARC_MEANING, CARD_VENDOR, mapCVMCode } from "./utils";
import { rpc } from "@web/core/network/rpc";

const console = payrilliumConsole;
export function buildPaymentReference() {
  const timestamp = Date.now();
  const random = Math.floor(Math.random() * 1000)
    .toString()
    .padStart(3, "0");
  return `${timestamp}${random}`;
}

export function isAuthorizationSuccessful(result) {
  return result.success === true && result.data?.state === "SUCCESS_AUTH";
}

export function getErrorMessage(result) {
  return result.data?.data?.message?.error || "Payment not approved";
}

export function storeTransactionMetadata(
  currentOrder,
  paymentLine,
  transactionId,
  message,
  cardType,
  parsedEMV,
  paymentProviderId,
) {
  const responseCode = message.responseCode || "N/A";
  console.log("responseCode", message);

  const responseCodeMeaning = ARC_MEANING[responseCode] || "N/A";
  const cvm = mapCVMCode(message.cvm) || "N/A";
  currentOrder.set_extra_payment_data({
    _amount: paymentLine.get_amount(),
    cardType: cardType || "N/A",
    approvalCode: message.approvalCode || "N/A",
    cardVendor: message.cardVendor || "N/A",
    cardNumber: message.cardNumber || "N/A",
    transactionId: message.transactionId || "N/A",
    terminalId:
      message.rawBody?.pointOfSaleInformation?.terminalId ||
      message.terminalId ||
      "N/A",
    entryMode: message.entryMode || "N/A",
    date: message.date
      ? message.date.replace("T", " ").replace("Z", "")
      : "N/A",
    status: message.status || "N/A",
    CVM: cvm || "N/A",
    responseCode: responseCode || "N/A",
    responseCodeMeaning: responseCodeMeaning || "N/A",
    ...parsedEMV,
  });
  paymentLine.set_payment_status("done");
  paymentLine.transaction_id = transactionId;
  paymentLine.provider_id = paymentProviderId;
}

export async function findRefundedOrderLine(order) {
  const orderLines = order.get_orderlines();

  // Look for any line that is a refund.
  // We check ol.refunded_orderline_id (standard Odoo)
  // OR ol.raw.refunded_orderline_id (fallback for backend-created refunds in Odoo 18)
  const refundedLine = orderLines.find(
    (ol) =>
      Boolean(ol.refunded_orderline_id) ||
      Boolean(ol.raw?.refunded_orderline_id),
  );

  if (!refundedLine) {
    throw new Error("Refunded product not found.");
  }

  // Extract the ID, prioritizing the linked object then falling back to the raw ID
  const refundedLineId =
    refundedLine.refunded_orderline_id?.id ||
    refundedLine.refunded_orderline_id ||
    refundedLine.raw?.refunded_orderline_id;

  if (!refundedLineId) {
    throw new Error("Refunded line ID not found.");
  }

  const [refundedLineBackend] = await rpc("/web/dataset/call_kw", {
    model: "pos.order.line",
    method: "read",
    args: [[refundedLineId], ["id", "order_id"]],
    kwargs: {},
  });

  if (!refundedLineBackend) {
    throw new Error("Refunded line backend not found.");
  }

  return {
    id: refundedLineBackend.id,
    order_id: refundedLineBackend.order_id?.[0] || null,
  };
}

export async function getOriginalTransaction(orderId) {
  console.log(
    `[Refund] Searching original transaction for order ID: ${orderId}`,
  );

  // 1. Get the payment from pos.payment
  // Filter: MUST have a transaction_id and MUST NOT be a change/redirection line (V3.7.0)
  const payments = await rpc("/web/dataset/call_kw", {
    model: "pos.payment",
    method: "search_read",
    args: [
      [
        ["pos_order_id", "=", orderId],
        ["transaction_id", "!=", false],
        ["is_change", "=", false],
      ],
      ["transaction_id", "amount", "is_change"],
    ],
    kwargs: {},
  });

  console.log("[Refund] Found pos.payment records:", payments);

  const realPayment = (payments || []).find((p) => p.transaction_id);
  const txRef = realPayment?.transaction_id;

  if (!txRef) {
    throw new Error(
      "Transaction ID for the original order not found (or filtered as change)",
    );
  }

  console.log(`[Refund] Using transaction_id for search: ${txRef}`);

  // 2. Get the transaction details using OR logic (V3.7.0)
  // This supports both native Cybersource IDs and Odoo reference fallbacks (Recovered Orders)
  const records = await rpc("/web/dataset/call_kw", {
    model: "payment.transaction",
    method: "search_read",
    args: [
      ["|", ["reference", "=", txRef], ["provider_reference", "=", txRef]],
      ["card_type", "provider_reference", "reference", "payrillium_card_token"],
    ],
    kwargs: { limit: 1 },
  });

  const trx = records?.[0];
  if (!trx) {
    throw new Error(
      `Original transaction details not found for txRef=${txRef}`,
    );
  }

  console.log("[Refund] Found payment.transaction:", trx);

  return {
    cardType: trx.card_type,
    tokenCardId: trx.payrillium_card_token,
    reference: trx.reference,
    provider_reference: trx.provider_reference,
    transaction_id: trx.provider_reference || trx.reference || txRef,
  };
}

export function prepareRefundData(result, originalData) {
  console.log("prepareRefundData - Input result:", result);

  const message = result.data?.data?.message || {};
  const request = result.data?.data?.request || {};
  const processor =
    originalData.cardType === "CREDIT"
      ? message.rawResponse.processorInformation
      : message.processorInformation;

  const emvRawTags =
    message.emvTags || request?.pointOfSaleInformation?.emv?.tags || "";

  const parsed = parseEMVData(emvRawTags);
  console.log("prepareRefundData - Parsed EMV data:", parsed);
  console.log(message, "message");
  console.log(message.rawResponse, "message.rawResponse");

  const submitTime =
    message?.submitTimeUtc || message?.rawResponse?.submitTimeUtc;
  const formattedDate = (submitTime ? submitTime : new Date().toISOString())
    .replace("T", " ")
    .replace("Z", "");

  let refundData = {};

  if (
    !["TOKENIZED_CARD", "TOKENIZED_CREDIT", "TOKENIZED_DEBIT"].includes(
      originalData.cardType,
    )
  ) {
    refundData = {
      transactionId: originalData?.transaction_id,
      // cardType:
      // result?.data?.data?.request?.paymentInformation?.paymentType?.subTypeName?.toUpperCase() ||
      // originalData?.cardType ||
      // "N/A",
      // cardNumber: "****",
      // entryMode:
      // result?.data?.data?.request?.pointOfSaleInformation?.entryMode?.toUpperCase() ||
      // "UNKNOWN",
      // approvalCode:
      // result?.data?.data?.message?.rawResponse?.processorInformation
      // ?.approvalCode || "N/A",
      // cardVendor: "N/A",
      date:
        result?.data?.data?.message?.rawResponse?.submitTimeUtc ||
        result?.data?.data?.message?.submitTimeUtc ||
        formattedDate,
      status:
        result?.data?.data?.message?.status ||
        result?.data?.data?.message?.rawResponse?.status ||
        result?.data?.state ||
        "N/A",
      isRefund: true,
      originalTransactionId: originalData?.transaction_id,
      terminalId:
        originalData?.terminalId ||
        result?.serial ||
        result?.data?.data?.message?.rawResponse?.clientReferenceInformation
          ?.code ||
        "N/A",
      ...parsed,
    };
  } else {
    //tokenize card
    refundData = {
      transactionId: originalData.transaction_id,
      // cardType:
      // result?.data?.refund_data?.raw?.data?.metadata?.paymentInformation?.card
      // ?.type ||
      // originalData.cardType ||
      // "N/A",
      // cardNumber: "****",
      // entryMode: "UNKNOWN",
      // approvalCode:
      // result?.data?.refund_data?.raw?.data?.metadata?.processorInformation
      // ?.approvalCode || "N/A",
      // cardVendor:
      // CARD_VENDOR[
      // result?.data?.refund_data?.raw?.data?.metadata?.paymentInformation
      // ?.tokenizedCard?.type
      // ] || "N/A",
      date:
        result?.data?.refund_data?.raw?.data?.metadata?.submitTimeUtc ||
        formattedDate,
      status:
        result?.data?.refund_data?.raw?.data?.metadata?.status ||
        result?.data?.refund_data?.status ||
        "N/A",
      isRefund: true,
      originalTransactionId: originalData.transaction_id,
      terminalId:
        originalData.terminalId ||
        result?.data?.refund_data?.raw?.data?.metadata
          ?.clientReferenceInformation?.code ||
        "N/A",
      reconciliationId:
        result?.data?.refund_data?.raw?.data?.metadata?.reconciliationId ||
        "N/A",
      networkTransactionId:
        result?.data?.refund_data?.raw?.data?.metadata?.processorInformation
          ?.networkTransactionId || "N/A",
    };
  }

  console.log("prepareRefundData - Final data:", refundData);
  return refundData;
}
