/** @odoo-module **/

import { ListController } from "@web/views/list/list_controller";
import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { useService } from "@web/core/utils/hooks";
import { onWillDestroy } from "@odoo/owl";
import { payrilliumConsole } from "./utils";

const console = payrilliumConsole;
console.log("sync_button.js");

export class CustomListController extends ListController {
  setup() {
    super.setup();
    this.orm = useService("orm");
    this.action = this.env.services.action;
    this.isDestroyed = false;

    onWillDestroy(() => {
      this.isDestroyed = true;
    });
  }

  async onCustomButtonClick() {
    if (this.isDestroyed) {
      return;
    }

    try {
      const result = await this.orm.call(
        "payrillium.payment.link",
        "action_sync_paybylink",
        [],
        {},
      );
      if (result && result.tag === "display_notification") {
        this.action.doAction(result);
      }
      setTimeout(async () => {
        if (!this.isDestroyed) {
          console.log("reload");
          await this.model.load();
        }
      }, 2000);
    } catch (error) {
      console.error("Error syncing payment links:", error);
      this.action.doAction({
        type: "ir.actions.client",
        tag: "display_notification",
        params: {
          title: "Sync Error",
          message: " An error occurred while syncing Pay by Links.",
          type: "danger",
          sticky: true,
        },
      });
    }
  }
}

export const CustomListView = {
  ...listView,
  Controller: CustomListController,
  buttonTemplate: "pos_woodforest.ListView.Buttons",
};

if (typeof window !== "undefined") {
  registry.category("views").add("custom_list_view", CustomListView);
}
