/** @odoo-module **/
import { rpc } from "@web/core/network/rpc";
import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

export const ConfigLoader = {
  async getPaymentMethodName() {
    try {
      const result = await rpc("/woodforest/payment_method_name", {
        params: {},
      });
      return result.payment_method_name?.toLowerCase() || null;
    } catch (error) {
      console.error(" Error loading payment method name:", error);
      return null;
    }
  },

  async getPaymentMethodColor() {
    try {
      const result = await rpc("/woodforest/payment_method_color", {
        params: {},
      });
      return result.color || "#003366";
    } catch (error) {
      console.error(" Error loading payment method color:", error);
      return "#003366";
    }
  },

  async getPaymentMethodIcon() {
    try {
      const result = await rpc("/woodforest/payment_method_icon", {
        params: {},
      });
      return result.icon || "/pos_woodforest/static/description/icon.png";
    } catch (error) {
      console.error(" Error loading payment method icon:", error);
      return "/pos_woodforest/static/description/icon.png";
    }
  },

  async getImageBaseUrl() {
    try {
      const result = await rpc("/woodforest/image_base_url", {});
      return result?.image_base_url || "http://localhost:8069";
    } catch (error) {
      console.error(" Error loading image base URL:", error);
      return "http://localhost:8069";
    }
  },

  async getFullPaymentMethodData() {
    try {
      const result = await rpc("/woodforest/payment_method_data", {
        params: {},
      });
      console.log(result, "result");

      return result || {};
    } catch (error) {
      console.error(" Error loading full payment method data:", error);
      return {};
    }
  },

  async getTerminalFromSession(posService) {
    console.log("posService", posService);
    const sessionId = posService.session?.id;
    console.log("sessionId", sessionId);
    try {
      const result = await rpc("/woodforest/session/terminal", {
        sessionId: sessionId,
      });
      console.log("result", result);

      if (result?.success && result?.terminal) {
        return {
          id: result.terminal.id,
          // Preserve the actual value from terminal, default to true only if undefined/null
          iface_tipproduct:
            result.terminal.iface_tipproduct !== undefined &&
            result.terminal.iface_tipproduct !== null
              ? result.terminal.iface_tipproduct
              : true,
          tip_mode: result.terminal.tip_mode || "amount",
          tip_options: Array.isArray(result.terminal.tip_options)
            ? result.terminal.tip_options
            : [],
        };
      }
      return {};
    } catch (error) {
      console.error(" Error loading terminal from session:", error);
      return {};
    }
  },

  async getEnvironment() {
    try {
      const result = await rpc("/woodforest/environment", { params: {} });
      return result?.environment || null;
    } catch (error) {
      console.error(" Error loading environment:", error);
      return null;
    }
  },
  async getTerminalMessages() {
    try {
      const result = await rpc("/woodforest/config/messages", { params: {} });
      return result || null;
    } catch (error) {
      console.error(" Error loading terminal messages:", error);
      return null;
    }
  },
};
