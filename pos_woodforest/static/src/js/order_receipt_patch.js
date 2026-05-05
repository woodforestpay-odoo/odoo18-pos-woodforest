/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

patch(OrderReceipt.prototype, {
  setup() {
    super.setup();
    console.log(this.props, "this.props");

    const order = this.props.order || this.props.data;

    this.payrilliumInfo = order?.payrilliumInfo || {};
  },

  get receipt() {
    return {
      ...super.receipt,
      payrilliumInfo: this.payrilliumInfo,
    };
  },
});
