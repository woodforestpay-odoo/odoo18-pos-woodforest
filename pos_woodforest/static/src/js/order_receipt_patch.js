/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { payrilliumConsole } from "@pos_woodforest/js/utils";
import { usePos } from "@point_of_sale/app/store/pos_hook";

const console = payrilliumConsole;

patch(OrderReceipt.prototype, {
  setup() {
    super.setup();
    this.pos = usePos();
    console.log(this.props, "this.props");

    const order = this.props.order || this.props.data;

    this.payrilliumInfo = order?.payrilliumInfo || {};
  },

  get receipt() {
    return {
      ...super.receipt,
      payrilliumInfo: this.payrilliumInfo,
      receiptFontSize: this.pos.config.payrillium_receipt_font_size || 'normal',
    };
  },
});
