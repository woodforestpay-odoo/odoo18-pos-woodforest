/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { CopyButton } from "@web/core/copy_button/copy_button";
import { rpc as rpcRequest } from "@web/core/network/rpc";

console.log("[PAYRILLIUM] payment_link_wizard.js loading...");

// In-memory cache to avoid duplicate link generation during session
const generatedLinksCache = new Map();

patch(CopyButton.prototype, {
  setup() {
    console.log("[PAYRILLIUM] CopyButton setup() called");
    super.setup();
    this.orm = useService("orm");
    this.notification = useService("notification");
    console.log("[PAYRILLIUM] Services injected:", {
      orm: !!this.orm,
      notification: !!this.notification,
    });
  },

  async onClick() {
    console.log("[PAYRILLIUM] onClick triggered, props:", this.props);

    const content = this.props.content;
    const isString = typeof content === "string" || content instanceof String;

    if (!isString || !content) {
      console.log("[PAYRILLIUM] Not a string, using default behavior");
      return super.onClick();
    }

    // Check if this is a payment link (Odoo standard or Payrillium/Cybersource)
    const isOdooPaymentLink =
      content.includes("/payment/pay") || content.includes("move_id=");
    const isPayrilliumLink =
      content.includes("/payByLink/pay") ||
      content.includes("ebc.cybersource.com");

    // If it's neither, use default behavior
    if (!isOdooPaymentLink && !isPayrilliumLink) {
      console.log("[PAYRILLIUM] Not a payment link, using default behavior");
      return super.onClick();
    }

    // For Payrillium links that are already generated, just copy and show notification
    // We can't extract invoice info from Cybersource URL, so we skip backend call
    if (isPayrilliumLink) {
      console.log(
        "[PAYRILLIUM] Payrillium/Cybersource link detected - copying directly",
      );
      await navigator.clipboard.writeText(content);
      this.notification.add("Payment link copied", { type: "success" });
      return;
    }

    // This is an Odoo payment link - generate Payrillium link and save to chatter
    console.log(
      "[PAYRILLIUM] Odoo payment link detected - generating Payrillium link",
    );

    try {
      const url = new URL(content, window.location.origin);
      const amount = url.searchParams.get("amount");
      const invoiceId =
        url.searchParams.get("move_id") || url.searchParams.get("invoice_id");

      if (!amount || !invoiceId) {
        console.log("[PAYRILLIUM] Missing amount or invoiceId, using default");
        return super.onClick();
      }

      console.log(
        "[PAYRILLIUM] Generating link for invoice:",
        invoiceId,
        "amount:",
        amount,
      );

      const cacheKey = `${invoiceId}_${amount}`;
      if (generatedLinksCache.has(cacheKey)) {
        const cachedLink = generatedLinksCache.get(cacheKey);
        console.log("[PAYRILLIUM] Using cached link");
        await navigator.clipboard.writeText(cachedLink);
        this.notification.add("Payment link copied (cached)", { type: "info" });
        return;
      }

      const resp = await rpcRequest("/woodforest/generate_link", {
        model: "account.move",
        id: invoiceId,
        amount: parseFloat(amount),
      });

      console.log("[PAYRILLIUM] Server response:", resp);

      if (!resp) {
        this.notification.add("Failed to generate link: empty response", {
          type: "danger",
        });
        return;
      }

      if (resp.success !== true) {
        const msg =
          resp.error || "Failed to generate payment link (server side)";
        this.notification.add(msg, { type: "danger" });
        return;
      }

      const newLink = resp.link;
      if (!newLink) {
        this.notification.add("Server reported success but returned no link", {
          type: "danger",
        });
        return;
      }

      generatedLinksCache.set(cacheKey, newLink);
      await navigator.clipboard.writeText(newLink);

      console.log("[PAYRILLIUM] Link copied successfully");

      // Show warning if it's an existing link, otherwise show success
      if (resp.warning) {
        console.log("[PAYRILLIUM] Showing warning notification");
        this.notification.add(resp.warning, { type: "warning" });
      } else {
        console.log("[PAYRILLIUM] Showing success notification");
        this.notification.add("Payment link copied", { type: "success" });
      }
    } catch (error) {
      console.error("[PAYRILLIUM] Error in onClick:", error);
      this.notification.add("Failed to generate/copy link", { type: "danger" });
    }
  },
});

console.log("[PAYRILLIUM] payment_link_wizard.js loaded ");
