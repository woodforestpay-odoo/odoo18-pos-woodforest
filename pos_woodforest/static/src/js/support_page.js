/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class PayrilliumSupportPage extends Component {
  static template = "pos_woodforest.SupportPage";
  static props = false;

  setup() {
    this.action = useService("action");
    this.orm = useService("orm");
    this.notification = useService("notification");

    this.state = useState({
      dateFrom: this._daysAgo(3),
      dateTo: this._today(),
      terminals: [],
      loadingTerminals: true,
      downloadingTerminalId: null,
      downloadingServerLogs: false,
      cybersourceMerchantId: null,
      fetchingMerchantId: false,

      // Cybersource Search
      cs: {
        dateFrom: this._today(),
        dateTo: this._today(),
        odooRef: "",
        csId: "",
        amount: "",
        cardSuffix: "",
        loading: false,
        results: [],
        total: 0,
        error: null,
        searched: false,
        page: 1,
        pageSize: 25,
      },
    });

    onMounted(async () => {
      await Promise.all([
        this._loadTerminals(),
        this._fetchCybersourceMerchantId()
      ]);
    });
  }

  // ── Terminal Logs ───────────────────────────────────────────────────────

  async _loadTerminals() {
    try {
      this.state.loadingTerminals = true;
      this.state.terminals = await this.orm.searchRead(
        "payrillium.terminal",
        [],
        ["name", "serial", "terminal_merchant_id"],
      );
    } finally {
      this.state.loadingTerminals = false;
    }
  }

  async _fetchCybersourceMerchantId() {
    this.state.fetchingMerchantId = true;
    try {
      const configs = await this.orm.searchRead("payrillium.config", [], ["id", "cybersource_merchant_id"], { limit: 1 });
      if (configs.length > 0) {
        if (configs[0].cybersource_merchant_id) {
          // Already stored in DB — just display it
          this.state.cybersourceMerchantId = configs[0].cybersource_merchant_id;
        } else {
          // Not yet stored (e.g. existing install before this feature) — fetch once and save
          const result = await this.orm.call("payrillium.config", "action_fetch_cybersource_merchant_id", [configs[0].id]);
          if (result && result.success) {
            this.state.cybersourceMerchantId = result.cybersource_merchant_id;
          } else {
            this.state.cybersourceMerchantId = null;
          }
        }
      }
    } catch (e) {
      console.error("Error fetching cybersource merchant ID:", e);
      this.state.cybersourceMerchantId = null;
    } finally {
      this.state.fetchingMerchantId = false;
    }
  }

  async refreshCybersourceMerchantId() {
    this.state.fetchingMerchantId = true;
    try {
      const configs = await this.orm.searchRead("payrillium.config", [], ["id"], { limit: 1 });
      if (configs.length > 0) {
        const result = await this.orm.call("payrillium.config", "action_fetch_cybersource_merchant_id", [configs[0].id]);
        if (result && result.success) {
          this.state.cybersourceMerchantId = result.cybersource_merchant_id;
          this.notification.add(`Updated: ${result.cybersource_merchant_id}`, { type: "success", title: "Cybersource Merchant ID" });
        } else {
          this.notification.add(result?.message || "Failed to fetch", { type: "warning", title: "Cybersource Merchant ID" });
        }
      }
    } catch (e) {
      console.error("Error refreshing cybersource merchant ID:", e);
      this.notification.add(e.message || "Error", { type: "danger", title: "Refresh Error" });
    } finally {
      this.state.fetchingMerchantId = false;
    }
  }

  merchantIdStatus(terminal) {
    if (!terminal.terminal_merchant_id) {
      return { label: "Not fetched", cls: "text-muted", icon: "fa-question-circle", title: "" };
    }
    
    const isMatch = this.state.cybersourceMerchantId && terminal.terminal_merchant_id === this.state.cybersourceMerchantId;

    if (!this.state.cybersourceMerchantId) {
      return { label: terminal.terminal_merchant_id, cls: "text-muted", icon: "fa-minus-circle", title: "" };
    }
    if (isMatch) {
      return { label: terminal.terminal_merchant_id, cls: "text-success", icon: "fa-check-circle", title: "Matches backend configuration" };
    }
    return { label: terminal.terminal_merchant_id, cls: "text-danger", icon: "fa-exclamation-triangle", title: "Mismatch with backend configuration!" };
  }

  _today() {
    return new Date().toISOString().split("T")[0];
  }

  _daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().split("T")[0];
  }

  async downloadServerLogs() {
    this.state.downloadingServerLogs = true;
    try {
      const params = new URLSearchParams();
      if (this.state.dateFrom) params.set("date_from", this.state.dateFrom);
      if (this.state.dateTo) params.set("date_to", this.state.dateTo);
      const url = `/woodforest/support/download_server_logs?${params.toString()}`;
      const a = document.createElement("a");
      a.href = url;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      // Brief delay so user sees the spinner feedback
      await new Promise((r) => setTimeout(r, 1500));
    } finally {
      this.state.downloadingServerLogs = false;
    }
  }

  async downloadTerminalLogs(terminalId) {
    this.state.downloadingTerminalId = terminalId;
    try {
      const action = await this.orm.call(
        "payrillium.terminal",
        "action_download_terminal_logs",
        [terminalId],
      );
      if (action) {
        await this.action.doAction(action, {
          onClose: () => this._loadTerminals(),
        });
      }
    } finally {
      this.state.downloadingTerminalId = null;
    }
  }

  async syncTerminalMerchant(terminal) {
    terminal.syncing = true;
    terminal.syncResult = null;
    try {
      const result = await this.orm.call(
        "payrillium.terminal",
        "action_sync_merchant_ui",
        [terminal.id],
      );
      terminal.syncResult = result;
      if (result && result.status === 'success' && result.merchant_id) {
          terminal.terminal_merchant_id = result.merchant_id;
      } else {
          // If sync fails (e.g., offline), we mark it as unknown/not-fetched
          terminal.terminal_merchant_id = null;
      }
    } catch (error) {
      terminal.syncResult = { status: 'error', message: 'Connection Error' };
      terminal.terminal_merchant_id = null;
    } finally {
      terminal.syncing = false;
      setTimeout(() => {
        terminal.syncResult = null;
      }, 3500);
    }
  }

  async syncAllMerchants() {
    for (const t of this.state.terminals) {
      await this.syncTerminalMerchant(t);
    }
  }

  // ── Cybersource Search ──────────────────────────────────────────────────

  async searchCybersource(resetPage = true) {
    const cs = this.state.cs;
    cs.loading = true;
    cs.error = null;
    cs.results = [];
    cs.total = 0;
    cs.searched = false;

    // Ensure default sort exists
    cs.sortField = cs.sortField || "submitTimeUtc";
    cs.sortDir = cs.sortDir || "desc";

    if (resetPage) {
      cs.page = 1;
    }

    try {
      const offset = (cs.page - 1) * cs.pageSize;
      const limit = cs.pageSize;

      const raw = await fetch("/woodforest/support/cs_search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jsonrpc: "2.0",
          method: "call",
          id: Date.now(),
          params: {
            date_from: cs.dateFrom || "",
            date_to: cs.dateTo || "",
            odoo_ref: cs.odooRef || "",
            cs_id: cs.csId || "",
            amount: cs.amount || "",
            card_suffix: cs.cardSuffix || "",
            limit: limit,
            offset: offset,
            sort: `${cs.sortField}:${cs.sortDir}`,
          },
        }),
      });

      if (!raw.ok) {
        const text = await raw.text();
        throw new Error(text || `HTTP Error ${raw.status}`);
      }

      const json = await raw.json();

      // Odoo returns `error` object instead of `result` if Python raises an exception
      if (json.error) {
        throw new Error(
          json.error.data?.message || json.error.message || "Server Exception",
        );
      }

      const res = json.result;

      if (!res || !res.success) {
        // If res.message is an empty string, we want to know it's empty, not fall back to "Search failed."
        // Only fall back if it is truly undefined/null.
        const msg =
          res && res.message !== undefined && res.message !== null
            ? res.message || "Unknown error from server"
            : "Search failed.";
        cs.error = msg;
        cs.results = [];
        cs.total = 0;
        cs.searched = true; // Still marked as searched to clear skeletons
        this.notification.add(msg, {
          title: "Search Error",
          type: "danger",
          sticky: true,
        });
      } else {
        cs.results = res.results || [];
        cs.total = res.total || cs.results.length;
        cs.searched = true;
        if (cs.results.length === 0) {
          this.notification.add("No transactions found for these filters.", {
            type: "warning",
          });
        }
      }
    } catch (e) {
      console.error("CS Search Error:", e);
      const msg =
        e.message && e.message.includes("Expecting value")
          ? "Server returned an empty or invalid response. Please try again."
          : e.message || "Unexpected error.";
      cs.error = msg;
      cs.results = [];
      cs.searched = true;
      this.notification.add(msg, {
        title: "System Error",
        type: "danger",
      });
    } finally {
      cs.loading = false;
    }
  }

  nextPage() {
    const cs = this.state.cs;
    if (cs.page * cs.pageSize < cs.total) {
      cs.page += 1;
      this.searchCybersource(false);
    }
  }

  prevPage() {
    const cs = this.state.cs;
    if (cs.page > 1) {
      cs.page -= 1;
      this.searchCybersource(false);
    }
  }

  changePageSize(ev) {
    const newSize = parseInt(ev.target.value, 10);
    if (newSize) {
      this.state.cs.pageSize = newSize;
      this.searchCybersource(true);
    }
  }

  sortBy(field) {
    const cs = this.state.cs;
    if (cs.sortField === field) {
      cs.sortDir = cs.sortDir === "asc" ? "desc" : "asc";
    } else {
      cs.sortField = field;
      cs.sortDir = "desc"; // Default to desc when switching fields
    }
    this.searchCybersource(true); // reset page to 1
  }

  downloadCSVSearch() {
    const cs = this.state.cs;
    const params = new URLSearchParams();
    if (cs.dateFrom) params.set("date_from", cs.dateFrom);
    if (cs.dateTo) params.set("date_to", cs.dateTo);
    if (cs.odooRef) params.set("odoo_ref", cs.odooRef);
    if (cs.csId) params.set("cs_id", cs.csId);
    if (cs.amount) params.set("amount", cs.amount);
    if (cs.cardSuffix) params.set("card_suffix", cs.cardSuffix);

    const url = `/woodforest/support/cs_search_csv?${params.toString()}`;
    const a = document.createElement("a");
    a.href = url;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async openOdooTx(txId) {
    if (!txId) return;
    await this.action.doAction({
      type: "ir.actions.act_window",
      res_model: "payment.transaction",
      res_id: txId,
      views: [[false, "form"]],
      target: "current",
    });
  }

  async syncOdooTx(txId) {
    if (!txId) return;
    try {
      this.state.cs.loading = true;
      const result = await this.orm.call(
        "payment.transaction",
        "action_woodforest_check_status",
        [[txId]],
        {},
      );
      if (result && result.tag === "display_notification") {
        this.notification.add(result.params.message, {
          title: result.params.title || "Sync Status",
          type: result.params.type || "info",
        });
      } else {
        this.notification.add("Sync complete.", { type: "success" });
      }
      // re-search to update the row
      await this.searchCybersource(false);
    } catch (e) {
      this.notification.add(e.message || "Failed to sync transaction", {
        title: "Sync Error",
        type: "danger",
      });
    } finally {
      this.state.cs.loading = false;
    }
  }

  csStatusClass(status) {
    const s = (status || "").toUpperCase();
    if (["CAPTURED", "COMPLETED", "SOK"].includes(s)) return "cs_status_ok";
    if (["AUTHORIZED"].includes(s)) return "cs_status_auth";
    if (["PENDING", "TRANSMITTED"].includes(s)) return "cs_status_pending";
    return "cs_status_error";
  }

  formatSubmitTime(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }
}

registry
  .category("actions")
  .add("payrillium_support_page", PayrilliumSupportPage);
