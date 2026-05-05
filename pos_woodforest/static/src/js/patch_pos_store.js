/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { PayrilliumAPI } from "@pos_woodforest/js/api_service";
import { payrilliumConsole } from "@pos_woodforest/js/utils";
import { debounce } from "@web/core/utils/timing";

const console = payrilliumConsole;

console.log(" Patch deleteOrders in PosStore");

const _superDeleteOrders = PosStore.prototype.deleteOrders;

patch(PosStore.prototype, {
  setup() {
    super.setup(...arguments);
    this.global_stuck_transactions = [];

    // V3.5.0: Centralized debounced basket sync.
    // We wait 600ms of inactivity before sending the basket to the terminal.
    this.debouncedSyncBasket = debounce(() => {
      this._syncBasketWithTerminal();
    }, 600);

    // Fetch stuck transactions asynchronously shortly after setup
    setTimeout(() => {
      if (this.session && this.session.id) {
        this.fetch_stuck_transactions();
      }
    }, 2000);

    // 18.3: Temporarily disabled (abort terminal bug pending fix from provider)
    // setTimeout(() => {
    //   try {
    //     const sessionId = this.session?.id || null;
    //     if (sessionId) {
    //       PayrilliumAPI.abortTerminal(sessionId).catch((e) => {
    //         console.warn("[PosStore] abortTerminal on open failed:", e);
    //       });
    //     }
    //   } catch (e) {
    //     console.warn("[PosStore] abortTerminal setup error:", e);
    //   }
    // }, 3000);
  },

  async fetch_stuck_transactions() {
    try {
      const stuck_txs = await this.env.services.orm.call(
        "payment.transaction",
        "get_stuck_transactions_for_config",
        [this.config.id],
      );
      this.global_stuck_transactions = stuck_txs || [];
      if (this.global_stuck_transactions.length > 0) {
        console.log(
          ` [Recovery] Found ${this.global_stuck_transactions.length} stuck 'in_progress' transactions from Woodforest!`,
        );
      }
    } catch (e) {
      console.warn(" [Recovery] Could not fetch stuck transactions:", e);
    }
  },
  async deleteOrders(orders, serverIds = []) {
    const result = await _superDeleteOrders.call(this, orders, serverIds);
    return result;
  },

  async _syncBasketWithTerminal() {
    const terminal = this.config.payrillium_terminal_serial;

    const isString = typeof terminal === "string";
    const hasTerminal = terminal && isString && terminal.length > 0;
    if (!hasTerminal) {
      console.log("No terminal assigned to session.");
      return;
    }

    const order = this.get_order?.();
    try {
      const sessionId = this.session?.id || null;
      if (order && order.get_orderlines?.().length) {
        console.error("[BASKET-DIAG] debouncedSync ->", Date.now());
        await PayrilliumAPI.showBasket({ order }, "display", sessionId);
      } else {
        await PayrilliumAPI.showEmptyBasket("display", sessionId);
      }
    } catch (error) {
      console.error("Payrillium: error synchronizing with terminal:", error);
    }
  },

  /**
   * SAFETY GUARD (V3.6.2): Prevents Owl lifecycle crashes when reactive updates
   * trigger re-renders of payment methods while the current order is temporarily undefined.
   */
  getPaymentMethodDisplayText(pm, order) {
    if (!order) {
      return pm.name;
    }
    return super.getPaymentMethodDisplayText(...arguments);
  },
});
