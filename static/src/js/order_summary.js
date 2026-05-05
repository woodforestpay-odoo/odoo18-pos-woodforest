/** @odoo-module **/

import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";
import { PayrilliumAPI } from "@pos_woodforest/js/api_service";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

patch(OrderSummary.prototype, {
  setup() {
    super.setup(...arguments);
    this.orm = useService("orm");
    this._posService = this.pos || this.env.services.pos;
  },

  async updateSelectedOrderline(props) {
    const result = await super.updateSelectedOrderline(props);
    console.log(" Orderline :", this.currentOrder.get_selected_orderline());
    this.pos.debouncedSyncBasket();
    return result;
  },
});
