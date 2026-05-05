/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

// Same labels as backend Selection field
const PAYMENT_STATUS_LABELS = {
  none: "Not Initiated",
  in_progress: "Processing",
  authorized: "Approved",
  auth_failed: "Declined",
  cancelled: "Cancelled",
  declined: "Declined",
  device_error: "Terminal Error",
  terminal_busy: "Terminal Busy",
  captured: "Approved",
  capture_failed: "Needs Review",
  voided: "Voided",
  void_failed: "Needs Review",
  reversed: "Reversed",
  reversal_failed: "Needs Review",
  credit_refund_pending: "Refund Pending",
  credit_refunded: "Refunded",
  debit_refund_pending: "Refund Pending",
  debit_refunded: "Refunded",
  refunded: "Refunded",
  error: "Error",
};

const PAYMENT_STATUS_CLASS = {
  authorized: "text-bg-success",
  captured: "text-bg-success",
  in_progress: "text-bg-warning",
  cancelled: "text-bg-secondary",
  none: "text-bg-secondary",
  declined: "text-bg-danger",
  auth_failed: "text-bg-danger",
  device_error: "text-bg-danger",
  terminal_busy: "text-bg-warning",
  error: "text-bg-danger",
  voided: "text-bg-info",
  reversed: "text-bg-info",
  refunded: "text-bg-primary",
  credit_refunded: "text-bg-primary",
  debit_refunded: "text-bg-primary",
};

export class TransactionDetailPopup extends Component {
  static template = "pos_woodforest.TransactionDetailPopup";
  static components = { Dialog };
  static props = {
    transaction: { type: Object },
    close: { type: Function },
  };

  setup() {
    this.orm = useService("orm");
    this.state = useState({
      checking: false,
      checkMessage: null,
      checkMessageType: null, // 'success', 'warning', 'danger'
    });
  }

  get tx() {
    return this.props.transaction;
  }

  get paymentStatusLabel() {
    return (
      PAYMENT_STATUS_LABELS[this.tx.transaction_status] ||
      this.tx.transaction_status ||
      "—"
    );
  }
  get paymentStatusClass() {
    return (
      PAYMENT_STATUS_CLASS[this.tx.transaction_status] || "text-bg-secondary"
    );
  }

  // status_summary is the single source of truth — computed by the backend
  get friendlyMessage() {
    return this.state.checkMessage || this.tx.status_summary || null;
  }

  get friendlyMessageStyle() {
    const type = this.state.checkMessageType;
    if (type === "success") return "background: #d1e7dd; border-left: 4px solid #198754;";
    if (type === "danger") return "background: #f8d7da; border-left: 4px solid #dc3545;";
    return "background: #fff3cd; border-left: 4px solid #ffc107;";
  }

  get formattedAmount() {
    return this.env.utils.formatCurrency(this.tx.amount || 0);
  }
  get formattedTip() {
    if (!this.tx.tip_amount) return null;
    return this.env.utils.formatCurrency(this.tx.tip_amount);
  }
  get formattedBase() {
    const base = (this.tx.amount || 0) - (this.tx.tip_amount || 0);
    return this.env.utils.formatCurrency(base);
  }
  get formattedDate() {
    if (!this.tx.create_date) return "—";
    return new Date(this.tx.create_date).toLocaleString();
  }

  async onClickCheckStatus() {
    if (this.state.checking) return;
    this.state.checking = true;
    this.state.checkMessage = null;
    this.state.checkMessageType = null;

    try {
      const result = await this.orm.call(
        "payment.transaction",
        "action_woodforest_check_status",
        [this.tx.id],
        { context: { silent: true } },
      );

      if (result?.success) {
        // Update local tx data so the modal refreshes
        const newStatus = result.engine_internal_status;
        if (newStatus) {
          this.tx.transaction_status = newStatus;
        }
        this.state.checkMessage = result.message || "Status verified.";
        // Determine message type by engine status
        const successStates = ["captured", "authorized", "voided", "refunded", "credit_refunded", "debit_refunded"];
        const warningStates = ["in_progress", "auth_hold"];
        if (successStates.includes(newStatus)) {
          this.state.checkMessageType = "success";
        } else if (warningStates.includes(newStatus)) {
          this.state.checkMessageType = null; // default yellow
        } else {
          this.state.checkMessageType = "danger";
        }
      } else {
        this.state.checkMessage = result?.message || "Could not verify status. Try again.";
        this.state.checkMessageType = "danger";
      }
    } catch (error) {
      console.error("[CheckStatus] RPC error:", error);
      this.state.checkMessage = "Connection error. Try again.";
      this.state.checkMessageType = "danger";
    } finally {
      this.state.checking = false;
    }
  }
}
