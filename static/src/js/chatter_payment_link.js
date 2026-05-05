/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Message } from "@mail/core/common/message_model";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

// Patch the Message model to handle payment links
patch(Message.prototype, {
  /**
   * Override the copyLink method to handle payment links specially
   */
  async copyLink() {
    // Payment link message: body has "Payment link created" and Cybersource URL(s).
    // Backend stores truncated URL visible + full URL in <span style="display:none;">.
    // We take the longest matching URL so we get the full one (hidden span), not the truncated.
    if (
      this.body &&
      (this.body.includes("Payment link created") ||
        this.body.includes("cybersource.com"))
    ) {
      const urlRegex =
        /https:\/\/ebc(?:test)?\.cybersource\.com(:\d+)?[^"'\s<>]+/g;
      const allMatches = this.body.match(urlRegex);
      if (allMatches && allMatches.length > 0) {
        const paymentLink = allMatches.reduce((a, b) =>
          a.length >= b.length ? a : b,
        );
        let notification = _t("Payment Link Copied!");
        let type = "info";
        try {
          await browser.navigator.clipboard.writeText(paymentLink);
        } catch {
          notification = _t("Payment Link Copy Failed (Permission denied?)!");
          type = "danger";
        }
        this.store.env.services.notification.add(notification, { type });
        return;
      }
    }

    return super.copyLink();
  },
});
