/** @odoo-module */

import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";
import { patch } from "@web/core/utils/patch";

patch(ReceiptScreen.prototype, {
  isContinueSplitting() {
    // Allow if Restaurant OR (Retail AND Split Bill enabled)
    // AND there is an original splitted order
    const isAllowed =
      this.pos.config.module_pos_restaurant || this.pos.config.iface_splitbill;

    if (isAllowed && this.currentOrder.originalSplittedOrder) {
      const o = this.currentOrder.originalSplittedOrder;
      return !o.finalized && o.lines.length;
    } else {
      return false;
    }
  },

  get orderAmountPlusTip() {
    // Call Odoo's standard getter first (handles Odoo tip product)
    const base = super.orderAmountPlusTip;

    // If Odoo already found a tip product on the order, respect it
    const order = this.currentOrder;
    const tip_product_id = this.pos.config.tip_product_id?.id;
    const tipLine = order
      .get_orderlines()
      .find((line) => tip_product_id && line.product_id.id === tip_product_id);
    if (tipLine) return base;

    // Check for terminal tip stored in _all_payment_data
    const allPaymentData = order.get_all_payment_data?.() || [];
    const terminalTip = allPaymentData.reduce(
      (sum, info) => sum + (info._tipAmount || 0),
      0,
    );
    if (terminalTip <= 0) return base;

    const orderTotal = order.get_total_with_tax();
    const orderStr = this.env.utils.formatCurrency(orderTotal);
    const tipStr = this.env.utils.formatCurrency(terminalTip);
    return `${orderStr} + ${tipStr} tip`;
  },
});
