/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PayrilliumAPI } from "@pos_woodforest/js/api_service";
import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

patch(PosOrder.prototype, {
  async add_product(product, options) {
    const result = await super.add_product(product, options);

    try {
      const rpc = this.env.services.rpc;
      // Try to get sessionId from pos service
      const posService = this.env?.services?.pos || this.pos;
      const sessionId = posService?.session?.id || null;
      console.error("[BASKET-DIAG] add_product direct →", Date.now());
      await PayrilliumAPI.showBasket(this, "display", sessionId);
    } catch (error) {
      console.error("Error synchronizing with terminal:", error);
    }

    return result;
  },
});
