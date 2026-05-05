/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ListController } from "@web/views/list/list_controller";
import { onMounted, useRef } from "@odoo/owl";

patch(ListController.prototype, {
  setup() {
    super.setup(...arguments);
    if (this.props.resModel === "payrillium.terminal") {
      this.rootRef = useRef("root");
      onMounted(() => {
        this._injectTerminalBanner();
      });
    }
  },

  _injectTerminalBanner() {
    const root = this.rootRef.el;
    if (!root) return;

    // Prevent double injection
    if (root.querySelector(".o_payrillium_list_banner")) return;

    const banner = document.createElement("div");
    banner.className =
      "o_payrillium_form_header o_payrillium_form_header_terminal o_payrillium_list_banner";
    banner.style.margin = "0";
    banner.style.borderRadius = "0";

    banner.innerHTML = `
 <div class="o_payrillium_form_header_inner">
 <span class="o_payrillium_form_header_icon">
 <i class="fa fa-calculator"></i>
 </span>
 <div>
 <h2 class="o_payrillium_form_header_title">Woodforest Terminals</h2>
 <p class="o_payrillium_form_header_sub">Manage your payment terminals, configure tips, and link to POS sessions.</p>
 </div>
 </div>
 `;

    // Inject at the top of the root element (usually .o_list_view)
    root.prepend(banner);
  },
});
