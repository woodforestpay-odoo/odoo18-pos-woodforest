/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { omit } from "@web/core/utils/objects";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PosPayment } from "@point_of_sale/app/models/pos_payment";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { accountTaxHelpers } from "@account/helpers/account_tax";
import { lt } from "@point_of_sale/utils";
import { floatIsZero, roundPrecision } from "@web/core/utils/numbers";
import { _t } from "@web/core/l10n/translation";
import { getPayrilliumMethodName } from "@pos_woodforest/js/setup_config";

//Config here the fields, order and label that you want to show
const PAYRILLIUM_RECEIPT_FIELDS = [
  { key: "transactionId", label: "Transaction ID" },
  { key: "cardType", label: "Card Type" },
  { key: "cardNumber", label: "Card Number" },
  { key: "entryMode", label: "Entry Mode" },
  { key: "TVR", label: "TVR (Terminal Verification Results)" },
  { key: "AID", label: "AID (Application Identifier)" },
  { key: "ATC", label: "ATC (Application Transaction Counter)" },
  { key: "ApplicationLabel", label: "APPLAB (Application Label)" },
  {
    key: "ApplicationLabel",
    label: "APPPN (Application Preferred Name)",
  },
  { key: "CVM", label: "CVM (Cardholder Verification Method)" },
  { key: "approvalCode", label: "Approval Code" },
  { key: "cardVendor", label: "Card Vendor" },
  { key: "date", label: "Date" },
  { key: "status", label: "Status" },
  { key: "responseCodeMeaning", label: "Status Code" },
  { key: "responseCode", label: "Response Code" },
  { key: "ApplicationCryptogram", label: "TC" },
];

patch(PosPayment.prototype, {
  _isOurPaymentMethod() {
    return this.payment_method_id?.name?.toLowerCase() === getPayrilliumMethodName();
  },

  canBeAdjusted() {
    if (this._isOurPaymentMethod()) {
      return false; // Prevent pos_restaurant from rendering "Adjust Amount"
    }
    return super.canBeAdjusted ? super.canBeAdjusted(...arguments) : false;
  },

  is_done() {
    if (this._isOurPaymentMethod()) {
      return (
        this.get_payment_status() === "done" ||
        this.get_payment_status() === "reversed"
      );
    }
    return super.is_done ? super.is_done(...arguments) : true;
  },
});

patch(PosOrder.prototype, {
  _isPayrilliumPayment(line) {
    return line.payment_method_id?.name?.toLowerCase() === getPayrilliumMethodName();
  },

  _hasPayrilliumPayment() {
    return this.payment_ids?.some((p) => this._isPayrilliumPayment(p)) || false;
  },

  is_covered() {
    const totalTendered = this.payment_ids.reduce(
      (sum, line) => sum + line.get_amount(),
      0,
    );
    return totalTendered >= this.get_total_with_tax() - 0.00001;
  },

  // Update the "Remaining" display as split payment lines are planned and paid.
  // Subtracts pending (unprocessed) WF lines from remaining so the UI shows $0
  // when all lines together cover the order, even before they are individually authorized.
  // IMPORTANT: We do NOT set order_has_zero_remaining here — that flag controls
  // Odoo's native is_paid(), which we guard separately in is_paid() below.
  get taxTotals() {
    const totals = super.taxTotals;
    const uncountedWoodforest = this.payment_ids.filter(
      (p) => !p.is_done() && !p.is_change && this._isPayrilliumPayment(p),
    );
    if (uncountedWoodforest.length > 0) {
      const sign = totals.order_sign || 1;
      const uncountedSum = uncountedWoodforest.reduce(
        (sum, p) => sum + sign * p.get_amount(),
        0,
      );
      totals.order_remaining = Math.max(
        0,
        totals.order_remaining - uncountedSum,
      );
      // Intentionally NOT touching order_has_zero_remaining — is_paid() controls that.
    }
    return totals;
  },

  // Block Odoo native auto-validate from firing mid split-payment.
  // Only true when ALL Woodforest lines are done/reversed.
  is_paid() {
    const hasWoodforest = this.payment_ids.some((p) =>
      this._isPayrilliumPayment(p),
    );
    if (!hasWoodforest) {
      return super.is_paid ? super.is_paid(...arguments) : false;
    }
    const allDone = this.payment_ids
      .filter((p) => this._isPayrilliumPayment(p) && !p.is_change)
      .every(
        (p) =>
          p.get_payment_status() === "done" ||
          p.get_payment_status() === "reversed",
      );
    if (!allDone) return false;
    return super.is_paid ? super.is_paid(...arguments) : true;
  },

  get_total_paid() {
    return roundPrecision(
      this.payment_ids.reduce((sum, paymentLine) => {
        if (
          paymentLine.is_done() ||
          (this._isPayrilliumPayment(paymentLine) &&
            paymentLine.payment_status === "done")
        ) {
          sum += paymentLine.get_amount();
        }
        return sum;
      }, 0),
      this.currency.rounding,
    );
  },

  export_for_printing() {
    const data = super.export_for_printing(...arguments);

    // Guard: only add Payrillium receipt data if this order has our payment method
    if (!this._hasPayrilliumPayment()) {
      return data;
    }

    const allPaymentData = this.get_all_payment_data?.() || [];
    const payrilliumPayments = allPaymentData.map((info, idx) => {
      const infoList = PAYRILLIUM_RECEIPT_FIELDS.filter(
        (f) => info[f.key] !== undefined && `${info[f.key]}` !== "",
      ).map((f) => ({ label: f.label, value: info[f.key] }));
      return {
        index: idx + 1,
        amount: info._amount || "",
        tipAmount: info._tipAmount || 0,
        infoList,
      };
    });
    const payrilliumInfo =
      allPaymentData.length > 0
        ? allPaymentData[allPaymentData.length - 1]
        : {};
    const payrilliumInfoList = PAYRILLIUM_RECEIPT_FIELDS.filter(
      (f) =>
        payrilliumInfo[f.key] !== undefined &&
        `${payrilliumInfo[f.key]}` !== "",
    ).map((f) => ({ label: f.label, value: payrilliumInfo[f.key] }));

    const tipProductId = this.config?.tip_product_id?.id;
    const sortedLines = this.getSortedOrderlines();
    const tipLines = tipProductId
      ? sortedLines.filter((l) => l.product_id?.id === tipProductId)
      : [];
    const nonTipLines = tipProductId
      ? sortedLines.filter((l) => l.product_id?.id !== tipProductId)
      : sortedLines;

    const receiptData = {
      ...data,
      payrilliumInfo,
      payrilliumInfoList,
      payrilliumPayments,
      extra_payment_data: this.get_extra_payment_data(),
    };

    if (tipLines.length > 0) {
      const tipAmount = tipLines.reduce(
        (sum, l) => sum + (l.get_all_prices?.()?.priceWithTax ?? 0),
        0,
      );
      const currency = this.config.currency_id;
      const company = this.company;
      const documentSign =
        this.lines.length === 0 ||
        !this.lines.every((l) =>
          lt(l.qty, 0, { decimals: currency.decimal_places }),
        )
          ? 1
          : -1;

      const baseLinesWithoutTip = nonTipLines.map((line) =>
        accountTaxHelpers.prepare_base_line_for_taxes_computation(
          line,
          line.prepareBaseLineForTaxesComputationExtraValues({
            quantity: documentSign * line.qty,
          }),
        ),
      );
      accountTaxHelpers.add_tax_details_in_base_lines(
        baseLinesWithoutTip,
        company,
      );
      accountTaxHelpers.round_base_lines_tax_details(
        baseLinesWithoutTip,
        company,
      );
      const taxTotalsBeforeTip = accountTaxHelpers.get_tax_totals_summary(
        baseLinesWithoutTip,
        currency,
        company,
        { cash_rounding: null },
      );
      const totalBeforeTip =
        documentSign *
        (taxTotalsBeforeTip.total_amount_currency -
          (taxTotalsBeforeTip.cash_rounding_base_amount_currency || 0));

      Object.assign(receiptData, {
        orderlines: nonTipLines.map((l) =>
          omit(l.getDisplayData(), "internalNote"),
        ),
        receiptTipInfo: {
          hasTip: true,
          tipAmount,
          totalBeforeTip,
          orderSign: documentSign,
          taxTotalsBeforeTip,
        },
      });
    } else {
      // No Odoo tip product — check for terminal tip stored in extra_payment_data
      const totalTerminalTip = allPaymentData.reduce(
        (sum, info) => sum + (info._tipAmount || 0),
        0,
      );
      if (totalTerminalTip > 0) {
        const baseTotals = receiptData.taxTotals || data.taxTotals;
        const documentSign = baseTotals?.order_sign || 1;
        const baseTotal =
          documentSign *
          (baseTotals?.order_total || receiptData.amount_total || 0);
        Object.assign(receiptData, {
          receiptTipInfo: {
            hasTip: true,
            tipAmount: totalTerminalTip,
            totalBeforeTip: baseTotal,
            orderSign: documentSign,
            taxTotalsBeforeTip: baseTotals, // tax is on pre-tip amount, same structure
          },
          taxTotals: {
            ...baseTotals,
            order_total: baseTotal + totalTerminalTip,
          },
        });
      } else {
        receiptData.receiptTipInfo = { hasTip: false };
      }
    }

    return receiptData;
  },

  set_extra_payment_data(data) {
    if (!this._all_payment_data) {
      this._all_payment_data = [];
    }
    let cleanData = {};
    try {
      cleanData =
        data && typeof data === "object"
          ? JSON.parse(JSON.stringify(data))
          : data || {};
    } catch (e) {
      console.warn("Failed to clone payment metadata safely:", e);
      cleanData = {};
    }
    this._all_payment_data.push(cleanData);
    this.extra_payment_data = cleanData;
  },
  get_all_payment_data() {
    return (
      this._all_payment_data ||
      (this.extra_payment_data ? [this.extra_payment_data] : [])
    );
  },
  get_extra_payment_data() {
    return this.extra_payment_data || {};
  },

  setup(vals) {
    super.setup(...arguments);

    // Defensive JSON parsing: Backend Text fields return as Strings,
    // but the UI expects Objects/Arrays to render the receipt.
    try {
      this.extra_payment_data =
        typeof vals.extra_payment_data === "string"
          ? JSON.parse(vals.extra_payment_data)
          : vals.extra_payment_data;
    } catch (e) {
      this.extra_payment_data = vals.extra_payment_data;
    }

    try {
      this._all_payment_data =
        typeof vals._all_payment_data === "string"
          ? JSON.parse(vals._all_payment_data)
          : vals._all_payment_data;
    } catch (e) {
      this._all_payment_data = vals._all_payment_data;
    }

    this._pendingTerminalTip = vals._pendingTerminalTip || 0;
    this.pending_terminal_transaction = vals.pending_terminal_transaction;
    this.transaction_id = vals.transaction_id;
  },

  serialize() {
    // Guard: purge ghost payment line references that would crash related_models.js
    // This happens when remove_paymentline deletes a record from OWL's Map
    // but the stale ID remains in the internal x2many array.
    try {
      const records = this.models?.["pos.payment"];
      if (records && this.payment_ids && Array.isArray(this.payment_ids)) {
        this.payment_ids = this.payment_ids.filter((p) => {
          // Each item in payment_ids is a reactive proxy; verify it's still alive
          try {
            return p && typeof p.serialize === "function";
          } catch (_) {
            return false;
          }
        });
      }
    } catch (e) {
      console.warn("[serialize] Ghost payment cleanup failed:", e);
    }

    const data = super.serialize(...arguments);
    // Use JSON.stringify for Text fields in backend safely to prevent sync crashes
    try {
      data.extra_payment_data =
        typeof this.extra_payment_data === "object"
          ? JSON.stringify(this.extra_payment_data)
          : this.extra_payment_data || "{}";
    } catch (e) {
      data.extra_payment_data = "{}";
    }
    try {
      data._all_payment_data = Array.isArray(this._all_payment_data)
        ? JSON.stringify(this._all_payment_data)
        : this._all_payment_data || "[]";
    } catch (e) {
      data._all_payment_data = "[]";
    }
    data.transaction_id = this.transaction_id;
    return data;
  },

  electronic_payment_in_progress() {
    return this.payment_ids.some(function (pl) {
      if (pl.payment_status) {
        return !["done", "reversed", "retry"].includes(pl.payment_status);
      } else {
        return false;
      }
    });
  },
});

patch(PaymentScreen.prototype, {

  addNewPaymentLine(paymentMethod) {
    if (
      this.pos.global_stuck_transactions &&
      this.pos.global_stuck_transactions.length > 0
    ) {
      this.env.services.notification.add(
        _t(
          "Please resolve pending transactions using the red 'Recover Payments' button first.",
        ),
        { type: "danger" },
      );
      return false;
    }
    return super.addNewPaymentLine(...arguments);
  },
});

