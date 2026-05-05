/** @odoo-module **/

import { Navbar } from "@point_of_sale/app/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { payrilliumBus, payrilliumConsole } from "./utils";
import { rpc as rpcRequest } from "@web/core/network/rpc";
import { onMounted } from "@odoo/owl";

const console = payrilliumConsole;

patch(Navbar.prototype, {
  setup() {
    super.setup(...arguments);

    this.orm = useService("orm");
    this.notification = useService("notification");
    this.dialog = useService("dialog");

    if (!this.pos.terminalStatus) {
      this.pos.terminalStatus = "unknown";
    }

    payrilliumBus.addEventListener("payrillium:terminal_offline", () => {
      console.log("Terminal went offline");
      this.pos.terminalStatus = "offline";
      this.render();
    });

    payrilliumBus.addEventListener("payrillium:terminal_online", () => {
      this.pos.terminalStatus = "online";
      this.render();
    });

    // Reemplazamos polling manual por un ping en el onMounted
    onMounted(() => {
      this.checkTerminalStatus(true); // silent — no toast on mount
    });
  },

  get terminalStatusText() {
    if (this.pos.terminalStatus === "pinging") return " Checking Terminal...";
    if (this.pos.terminalStatus === "online") return " Terminal Online";
    if (this.pos.terminalStatus === "busy") return " Terminal Busy";
    return " Terminal Offline";
  },

  get terminalCssClass() {
    if (this.pos.terminalStatus === "pinging") return "info";
    if (this.pos.terminalStatus === "online") return "success";
    if (this.pos.terminalStatus === "busy") return "warning";
    return "danger";
  },

  get terminalIsChecking() {
    return this.pos.terminalStatus === "pinging" || !!this._pingCooldown;
  },

  get showToggleProductView() {
    if (!this.pos || !this.pos.mainScreen || !this.pos.mainScreen.component) {
      return false;
    }
    return super.showToggleProductView;
  },

  get customerFacingDisplayButtonIsShown() {
    if (!this.pos || !this.pos.config) {
      return false;
    }
    return super.customerFacingDisplayButtonIsShown;
  },

  get terminalIsVisible() {
    if (
      !this.pos ||
      !this.pos.config ||
      !this.pos.config.payrillium_terminal_serial
    ) {
      return false;
    }

    const mainScreen = this.pos.mainScreen || {};
    const currentScreen = mainScreen.name || mainScreen.component?.name || mainScreen.component?.constructor?.name || "OtherScreen";

    return currentScreen === "ProductScreen" || currentScreen === "PaymentScreen";
  },

  async onClickRecoverPayments() {
    if (
      !this.pos.global_stuck_transactions ||
      this.pos.global_stuck_transactions.length === 0
    ) {
      return;
    }

    const { RecoveryService } = require("@pos_woodforest/js/recovery_popup");

    if (RecoveryService) {
      const tx = this.pos.global_stuck_transactions[0];
      // Instantiate the service with the required environment variables
      const recoveryService = new RecoveryService({
        pos: this.pos,
        services: {
          orm: this.orm,
          notification: this.notification,
          ui: this.env.services.ui,
        },
      });
      // Process the first transaction automatically
      await recoveryService.processTransaction(tx);
    } else {
      this.notification.add("Recovery Service component not loaded.", {
        type: "danger",
      });
    }
  },

  async checkTerminalStatus(silent = false) {
    if (this.pos.terminalStatus === "pinging" || this._pingCooldown) return;

    const MAX_ATTEMPTS = 5;
    const RETRY_DELAY_MS = 5000;

    this.pos.terminalStatus = "pinging";
    this.render();

    const sessionId = this.pos.session?.id || null;
    // Single executionId for the entire ping cycle — groups all retries in logs
    const executionId = `check_conn_${Date.now()}`;
    let finalStatus = "offline";
    let finalMessage = "Terminal is offline";

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        console.log(`[Terminal Ping] Attempt ${attempt}/${MAX_ATTEMPTS} (${executionId})...`);
        const result = await rpcRequest("/woodforest/terminal/ping", {
          sessionId,
          executionId,
        });

        if (result?.status === "online") {
          finalStatus = "online";
          finalMessage = "Terminal is ready";
          console.log(`[Terminal Ping] Online on attempt ${attempt}`);
          break;
        } else if (result?.status === "busy") {
          finalStatus = "busy";
          finalMessage = "Terminal is busy processing...";
          console.log(`[Terminal Ping] Busy on attempt ${attempt}`);
          break;
        }
        // offline — continue to next attempt
        console.warn(`[Terminal Ping] Offline on attempt ${attempt}, ${attempt < MAX_ATTEMPTS ? "retrying in 5s..." : "giving up."}`);
      } catch (error) {
        console.warn(`[Terminal Ping] Error on attempt ${attempt}:`, error.message || error);
      }

      // Wait before next attempt (unless last attempt)
      if (attempt < MAX_ATTEMPTS) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
      }
    }

    this.pos.terminalStatus = finalStatus;
    if (!silent) {
      const typeMap = { online: "success", busy: "warning", offline: "danger" };
      this.notification.add(finalMessage, { type: typeMap[finalStatus] || "danger" });
    }

    this.render();
    // Cooldown after full cycle to prevent spamming
    this._pingCooldown = true;
    setTimeout(() => {
      this._pingCooldown = false;
      this.render();
    }, 5000);
  },
});
