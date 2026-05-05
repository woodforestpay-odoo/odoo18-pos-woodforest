/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

// Minimal patch — no Transactions injection here anymore (moved to burger menu)
patch(TicketScreen.prototype, {
  async onCreateNewOrder() {
    console.log(" Custom onCreateNewOrder from patch");
    await super.onCreateNewOrder();
  },
});
