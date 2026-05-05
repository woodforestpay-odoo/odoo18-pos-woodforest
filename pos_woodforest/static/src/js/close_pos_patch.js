/** @odoo-module **/

import { ClosePosPopup } from "@point_of_sale/app/navbar/closing_popup/closing_popup";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

patch(ClosePosPopup.prototype, {
  /**
   * SAFETY BLOCK (V3.6.2): Prevents closing the session if there are
   * stuck Woodforest transactions that need recovery.
   */
  async closeSession() {
    if (
      this.pos.global_stuck_transactions &&
      this.pos.global_stuck_transactions.length > 0
    ) {
      this.dialog.add(AlertDialog, {
        title: _t("Action Required: Stuck Transactions"),
        body: _t(
          "You cannot close the session while there are pending Woodforest transactions.\n\n" +
            "Please use the red 'Recover Payments' button in the navbar to resolve them first.",
        ),
      });
      return;
    }
    return super.closeSession(...arguments);
  },
});
