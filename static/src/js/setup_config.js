/** @odoo-module **/

import { ConfigLoader } from "@pos_woodforest/js/config_loader";
import {
  payrilliumConsole,
  setPayrilliumDebugMode,
} from "@pos_woodforest/js/utils";
import { rpc } from "@web/core/network/rpc";

const console = payrilliumConsole;

let _configCache = null;

/**
 * Returns the configured payment method name (lowercase).
 * Safe to call before config is loaded — falls back to "woodforest".
 */
export function getPayrilliumMethodName() {
  return (_configCache?.name || "woodforest").toLowerCase();
}

/**
 * Loads all woodforest configuration for the POS.
 * Results are cached to prevent redundant RPC calls on re-renders.
 * @param {object} posService - POS service instance.
 * @returns {object} Object with all the required configuration.
 */
export async function loadPayrilliumConfig(posService) {
  if (_configCache) {
    return _configCache;
  }
  try {
    const environment = await ConfigLoader.getEnvironment(rpc);
    setPayrilliumDebugMode(environment === "dev");
    const fullData = await ConfigLoader.getFullPaymentMethodData(rpc);

    console.log(fullData, "fullData");

    const paymentMethodName = await ConfigLoader.getPaymentMethodName(rpc);
    const paymentMethodColor = await ConfigLoader.getPaymentMethodColor(rpc);
    const paymentMethodIcon = await ConfigLoader.getPaymentMethodIcon(rpc);
    const receivableAccountId = fullData.receivable_account_id;
    const paymentProviderId = fullData.payment_provider_id;
    const terminalData = await ConfigLoader.getTerminalFromSession(posService);
    const terminalId = terminalData?.id || terminalData || {};
    const terminalConfig = terminalData || {}; // Store full terminal config including iface_tipproduct

    console.log(" Configuration loaded:", {
      name: paymentMethodName,
      color: paymentMethodColor,
      icon: paymentMethodIcon,
      paymentProviderId,
      receivableAccountId,
      terminalId,
      terminalConfig,
      environment,
    });

    _configCache = {
      name: paymentMethodName,
      color: paymentMethodColor,
      icon: paymentMethodIcon,
      receivableAccountId,
      paymentProviderId,
      terminalId,
      terminalConfig,
      environment,
    };
    return _configCache;
  } catch (error) {
    console.error(" Error loading configuration:", error);
    return null;
  }
}
