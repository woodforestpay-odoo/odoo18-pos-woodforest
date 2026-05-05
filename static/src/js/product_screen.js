/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PayrilliumAPI } from "@pos_woodforest/js/api_service";
import { useService } from "@web/core/utils/hooks";
import { useBarcodeReader } from "@point_of_sale/app/barcode/barcode_reader_hook";
import {
  generateExecutionId,
  payrilliumConsole,
} from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

const _orig_barcodeProductAction =
  ProductScreen.prototype._barcodeProductAction;
const _orig_barcodeGS1Action = ProductScreen.prototype._barcodeGS1Action;

patch(ProductScreen.prototype, {
  setup() {
    super.setup(...arguments);
    this._posService = this.pos || this.env.services.pos;

    // 18.3: When entering the product screen (new order), abort any stuck
    // terminal payment so the terminal is clean for the next transaction.
    const { onMounted } = require("@odoo/owl");
    onMounted(() => {
      // 18.3: Temporarily disabled (abort terminal bug pending fix from provider)
      // try {
      //   const sessionId = this._posService.session?.id || null;
      //   if (sessionId) {
      //     // Prefix "ps_mount_" so the log distinguishes these automatic
      //     // screen-mount aborts from cashier-initiated payment cancellations.
      //     const execId = `ps_mount_${generateExecutionId()}`;
      //     PayrilliumAPI.abortTerminal(sessionId, execId).catch((e) => {
      //       console.warn("[ProductScreen] abortTerminal on mount failed:", e);
      //     });
      //   }
      // } catch (e) {
      //   console.warn("[ProductScreen] abortTerminal error:", e);
      // }
    });
  },

  async addProductToOrder(product) {
    console.log("Adding product to order:", product);
    await super.addProductToOrder(...arguments);
    this.pos.debouncedSyncBasket();
  },

  async _barcodeProductAction(code) {
    console.log("Barcode product action fired, code:", code);
    await _orig_barcodeProductAction.call(this, code);
    this.pos.debouncedSyncBasket();
  },

  async _barcodeGS1Action(parsed_results) {
    console.log("Barcode GS1 action fired, parsed_results:", parsed_results);
    await _orig_barcodeGS1Action.call(this, parsed_results);
    this.pos.debouncedSyncBasket();
  },

  async deleteOrders(orders) {
    const res = await super.deleteOrders(...arguments);
    try {
      if (res) {
        // Empty basket is still immediate cleanup
        const sessionId = this._posService.session?.id || null;
        await PayrilliumAPI.showEmptyBasket("display", sessionId);
      }
    } catch (e) {
      console.error("Woodforest cleanup after deleteOrders failed:", e);
    }
    return res;
  },
});
