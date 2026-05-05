/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { Component, onWillStart, onWillRender, useState } from "@odoo/owl";
import { TransactionDetailPopup } from "@pos_woodforest/js/transaction_detail_popup";

// Mirror the backend Selection field labels exactly
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

// Same badge colors as backend list view
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

// Odoo core state labels
const STATE_LABELS = {
  draft: "Draft",
  pending: "Pending",
  authorized: "Authorized",
  confirmed: "Confirmed",
  done: "Done",
  cancel: "Cancelled",
  error: "Error",
};

const STATE_CLASS = {
  authorized: "text-bg-success",
  confirmed: "text-bg-success",
  done: "text-bg-success",
  pending: "text-bg-warning",
  draft: "text-bg-secondary",
  cancel: "text-bg-secondary",
  error: "text-bg-danger",
};

export class TransactionListScreen extends Component {
  static template = "pos_woodforest.TransactionListScreen";
  static storeOnOrder = false;
  static props = {};

  setup() {
    this.pos = usePos();
    this.orm = useService("orm");
    this.dialog = useService("dialog");

    this.state = useState({
      transactions: [],
      loading: true,
      viewMode: "session",
      searchText: "",
      filterStatus: "all",
      filterTerminal: "all",
      // null = follow current cashier, number = manager override
      viewingAs: null,
      activeCashierId: null,
    });

    onWillStart(async () => {
      this.state.activeCashierId = this._getCurrentCashierId();
      await this.loadTransactions();
    });

    // Detect cashier changes while screen is open
    onWillRender(() => {
      const currentId = this._getCurrentCashierId();
      if (this.state.activeCashierId !== null && this.state.activeCashierId !== currentId) {
        this.state.activeCashierId = currentId;
        this.state.viewingAs = null; // Reset manager override
        Promise.resolve().then(() => this.loadTransactions());
      }
      // Ensure it's always up to date
      this.state.activeCashierId = currentId;
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────

  _getCurrentCashierId() {
    if (!this.isMultiUser) return false;
    const cashier = this.pos.get_cashier();
    return cashier?.id || false;
  }

  // ── Role detection ──────────────────────────────────────────────

  get isMultiUser() {
    return !!this.pos.config.module_pos_hr;
  }

  get isManager() {
    if (!this.isMultiUser) return false;
    
    // Read from the reactive state so OWL tracks changes
    const currentId = this.state.activeCashierId;
    if (!currentId) return false;

    const cashier = this.pos.get_cashier();
    if (!cashier) return false;

    // In Odoo 18, advanced_employee_ids is an array of Proxy objects, not raw IDs
    const advancedEmployees = this.pos.config.advanced_employee_ids || [];
    const advancedIds = advancedEmployees.map(e => typeof e === "object" ? e.id : e);
    
    console.log("POS Woodforest - isManager Check:");
    console.log(" - Current Cashier ID:", currentId);
    console.log(" - Cashier Name:", cashier.name);
    console.log(" - Cashier Role:", cashier.role);
    console.log(" - Mapped Advanced Rights IDs:", advancedIds);

    if (advancedIds.includes(currentId)) {
      console.log(" -> Result: TRUE (in Advanced Rights)");
      return true;
    }

    if (cashier.role === "manager") {
      console.log(" -> Result: TRUE (has manager role)");
      return true;
    }

    console.log(" -> Result: FALSE");
    return false;
  }

  // ── Titles & labels ────────────────────────────────────────────

  get viewTitle() {
    const scope = this.state.viewMode === "mine" ? "All Transactions" : "This Session";
    const effectiveId = this.effectiveViewingAs;
    const myCashierId = this._getCurrentCashierId();
    if (this.isManager && effectiveId && effectiveId !== myCashierId) {
      const emp = this.availableCashiers.find((e) => e.id === effectiveId);
      if (emp) return `${emp.name} — ${scope}`;
    }
    return scope === "All Transactions" ? "All My Transactions" : "This Session Transactions";
  }

  get isMyMode() {
    return this.state.viewMode === "mine";
  }

  get toggleLabel() {
    return this.state.viewMode === "mine" ? "All Transactions" : "This Session";
  }

  // ── Data for dropdowns ─────────────────────────────────────────

  get availableTerminals() {
    const terminals = new Set(
      this.state.transactions.map((t) => t.terminal_short).filter((t) => t),
    );
    return Array.from(terminals).sort();
  }

  get availableCashiers() {
    try {
      const employees = this.pos.models["hr.employee"]?.getAll() || [];
      return employees.map((e) => ({ id: e.id, name: e.name })).sort((a, b) =>
        a.name.localeCompare(b.name),
      );
    } catch {
      return [];
    }
  }

  /** The effective employee ID to filter by (manager override or current cashier). */
  get effectiveViewingAs() {
    if (this.isManager && this.state.viewingAs !== null) {
      return this.state.viewingAs;
    }
    return this._getCurrentCashierId();
  }

  // ── Employee resolution for backend query ──────────────────────

  _resolveEmployeeId() {
    if (!this.isMultiUser) return false;
    return this.effectiveViewingAs;
  }

  // ── Client-side filters ────────────────────────────────────────

  get filteredTransactions() {
    return this.state.transactions.filter((tx) => {
      // Text Search (Reference or Order name)
      if (this.state.searchText) {
        const search = this.state.searchText.toLowerCase();
        const refMatch =
          tx.reference && tx.reference.toLowerCase().includes(search);
        const orderMatch =
          tx.pos_order_name && tx.pos_order_name.toLowerCase().includes(search);
        if (!refMatch && !orderMatch) return false;
      }

      // Status Filter
      if (this.state.filterStatus !== "all") {
        const status = (tx.transaction_status || "none").toLowerCase();
        let statusGroup = "other";
        if (status === "authorized" || status === "captured")
          statusGroup = "approved";
        else if (status === "auth_failed" || status === "declined")
          statusGroup = "declined";
        else if (
          status === "cancelled" ||
          status === "voided" ||
          status === "reversed"
        )
          statusGroup = "cancelled";
        else if (
          status === "error" ||
          status === "device_error" ||
          status === "capture_failed" ||
          status === "void_failed" ||
          status === "reversal_failed"
        )
          statusGroup = "error";

        if (this.state.filterStatus !== statusGroup) return false;
      }

      // Terminal Filter
      if (this.state.filterTerminal !== "all") {
        if (tx.terminal_short !== this.state.filterTerminal) return false;
      }

      return true;
    });
  }

  // ── Data loading ───────────────────────────────────────────────

  async loadTransactions() {
    this.state.loading = true;
    try {
      const sessionId = this.pos.session?.id || this.pos.pos_session?.id;
      if (!sessionId) {
        this.state.transactions = [];
        return;
      }

      const employeeId = this._resolveEmployeeId();

      let txs;
      if (this.state.viewMode === "mine") {
        const configId = this.pos.config?.id;
        txs = await this.orm.call(
          "payment.transaction",
          "get_my_transactions",
          [configId, employeeId],
        );
      } else {
        txs = await this.orm.call(
          "payment.transaction",
          "get_session_transactions",
          [sessionId, employeeId],
        );
      }
      this.state.transactions = txs;
    } catch (e) {
      console.error("Failed to load transactions:", e);
      this.state.transactions = [];
    } finally {
      this.state.loading = false;
    }
  }

  // ── Event handlers ─────────────────────────────────────────────

  async onClickRefresh() {
    await this.loadTransactions();
  }

  async onToggleView() {
    this.state.viewMode =
      this.state.viewMode === "session" ? "mine" : "session";
    await this.loadTransactions();
  }

  async onSwitchCashier(ev) {
    const newId = parseInt(ev.target.value) || false;
    if (newId) {
      this.state.viewingAs = newId;
      await this.loadTransactions();
    }
  }

  goBack() {
    this.pos.showScreen("ProductScreen");
  }

  // ── Formatters ─────────────────────────────────────────────────

  getPaymentStatusLabel(tx) {
    return (
      PAYMENT_STATUS_LABELS[tx.transaction_status] ||
      tx.transaction_status ||
      "—"
    );
  }
  getPaymentStatusClass(tx) {
    return PAYMENT_STATUS_CLASS[tx.transaction_status] || "text-bg-secondary";
  }

  getStateLabel(tx) {
    return STATE_LABELS[tx.state] || tx.state || "—";
  }
  getStateClass(tx) {
    return STATE_CLASS[tx.state] || "text-bg-secondary";
  }

  formatTime(tx) {
    if (!tx.create_date) return "";
    const d = new Date(tx.create_date);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  formatAmount(tx) {
    return this.env.utils.formatCurrency(tx.amount || 0);
  }

  formatCardType(tx) {
    if (!tx.card_type || tx.card_type === "N/A" || tx.card_type === false)
      return "";
    return tx.card_type;
  }

  shortRef(tx) {
    if (!tx.reference) return "—";
    const ref = String(tx.reference);
    return ref.length > 16 ? "…" + ref.slice(-14) : ref;
  }

  formatTerminal(tx) {
    return tx.terminal_short || "";
  }

  formatOrder(tx) {
    return tx.pos_order_name || "";
  }

  formatSession(tx) {
    return tx.session_name || "";
  }

  formatCashier(tx) {
    return tx.employee_name || "";
  }

  onClickTransaction(tx) {
    this.dialog.add(TransactionDetailPopup, { transaction: tx });
  }
}

registry
  .category("pos_screens")
  .add("TransactionListScreen", TransactionListScreen);
