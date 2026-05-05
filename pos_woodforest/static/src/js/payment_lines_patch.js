/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PaymentScreenPaymentLines } from "@point_of_sale/app/screens/payment_screen/payment_lines/payment_lines";
import {
  ConfirmationDialog,
  AlertDialog,
} from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { ask } from "@point_of_sale/app/store/make_awaitable_dialog";
import { onPatched, onMounted } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { payrilliumConsole } from "@pos_woodforest/js/utils";

const console = payrilliumConsole;

patch(PaymentScreenPaymentLines.prototype, {
  setup() {
    super.setup();
    // Get POS store for customer selection
    this.pos = usePos();
    // Get payment method name from PaymentScreen if available
    // We'll check it dynamically when needed

    // Inject checkbox into DOM after each render
    onPatched(() => {
      // Small delay to ensure DOM is fully updated
      setTimeout(() => {
        this._injectSaveCardCheckboxes();
        this._injectTipBadges();
      }, 10);
    });

    // Also inject on initial mount (onPatched only fires on re-renders)
    onMounted(() => {
      setTimeout(() => {
        this._injectSaveCardCheckboxes();
        this._injectTipBadges();
      }, 10);
    });
  },

  /**
   * Inject save card checkboxes into the DOM for selected payment lines
   * This is a fallback if template inheritance doesn't work
   */
  _injectSaveCardCheckboxes() {
    // FIRST: Remove ALL existing checkboxes to avoid duplicates/superposition
    const allExistingCheckboxes = document.querySelectorAll(
      ".save-card-checkbox-container",
    );
    allExistingCheckboxes.forEach((cb) => {
      console.log(
        " Removing existing checkbox:",
        cb.getAttribute("data-line-uuid"),
      );
      cb.remove();
    });

    // Get all payment lines from props
    const paymentLines = this.props.paymentLines || [];

    // IMPORTANT: Initialize save_card state for ALL terminal payment lines
    // This ensures the state persists even when lines are not selected
    paymentLines.forEach((line) => {
      if (this.isCardTerminalPayment(line)) {
        this.initializeSaveCardState(line);
        console.log(" Checking state for terminal payment line:", {
          lineUuid: line.uuid,
          methodName: line.payment_method_id?.name,
          isSelected: line.isSelected(),
          save_card: line.save_card,
        });
      }
    });

    // Find the currently SELECTED payment line that should show the checkbox
    const selectedLine = paymentLines.find(
      (line) => line.isSelected() && this.shouldShowSaveCardCheckbox(line),
    );

    if (!selectedLine) {
      // No selected line that should show checkbox - all checkboxes already removed
      console.log(
        "ℹ No selected card terminal payment line - no checkbox to show",
      );
      return;
    }

    // Only process the SELECTED line to avoid superpositions
    const methodName = selectedLine.payment_method_id?.name || "";
    const selectedLines = Array.from(
      document.querySelectorAll(".paymentline.selected"),
    );
    const lineElement = selectedLines.find((el) => {
      const nameElement = el.querySelector(".payment-name");
      return nameElement && nameElement.textContent.trim() === methodName;
    });

    if (lineElement) {
      // Check if checkbox already exists (shouldn't, but just in case)
      const existingCheckbox = lineElement.parentElement?.querySelector(
        `.save-card-checkbox-container[data-line-uuid="${selectedLine.uuid}"]`,
      );

      if (existingCheckbox) {
        // Update existing checkbox state
        const checkbox = existingCheckbox.querySelector(
          'input[type="checkbox"]',
        );
        if (checkbox) {
          checkbox.checked = this.getSaveCardState(selectedLine);
          console.log(
            " Checkbox state updated for line:",
            selectedLine.uuid,
            "State:",
            this.getSaveCardState(selectedLine),
          );
        }
        return;
      }

      // Create checkbox container
      const checkboxContainer = document.createElement("div");
      checkboxContainer.className =
        "save-card-checkbox-container d-flex align-items-center px-3 py-2 border rounded-3 mt-1";
      checkboxContainer.setAttribute("data-line-uuid", selectedLine.uuid);
      if (this.ui.isSmall) {
        checkboxContainer.classList.add("bg-100");
      } else {
        checkboxContainer.classList.add("bg-200");
      }

      // Create checkbox
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "form-check-input me-2";
      checkbox.id = `save-card-${selectedLine.uuid}`;

      // Get current state from payment line object
      const currentState = this.getSaveCardState(selectedLine);
      checkbox.checked = currentState;

      // Store reference to payment line UUID (more reliable than object reference)
      checkbox.setAttribute("data-payment-line-uuid", selectedLine.uuid);

      // Also store object reference for immediate access
      checkbox._paymentLine = selectedLine;

      checkbox.addEventListener("change", async (ev) => {
        // Get payment line from stored reference or find it again
        let paymentLine = ev.target._paymentLine;
        const lineUuid = ev.target.getAttribute("data-payment-line-uuid");

        // If object reference is lost, find it again from props
        if (!paymentLine && lineUuid) {
          const paymentLines = this.props.paymentLines || [];
          paymentLine = paymentLines.find((line) => line.uuid === lineUuid);
          if (paymentLine) {
            ev.target._paymentLine = paymentLine; // Restore reference
          }
        }

        if (paymentLine) {
          await this.onSaveCardToggle(paymentLine, ev);
          // Force update state after toggle - read from payment line object
          setTimeout(() => {
            const newState = this.getSaveCardState(paymentLine);
            ev.target.checked = newState;
            console.log(" Checkbox state synced after toggle:", {
              lineUuid: paymentLine.uuid,
              state: newState,
              paymentLineSaveCard: paymentLine.save_card,
            });
          }, 50);
        }
      });

      // Create label
      const label = document.createElement("label");
      label.className = "form-check-label cursor-pointer mb-0";
      label.htmlFor = `save-card-${selectedLine.uuid}`;
      label.textContent = this.getSaveCardLabel();
      label.addEventListener("click", (ev) => {
        ev.preventDefault();
        checkbox.click();
      });

      checkboxContainer.appendChild(checkbox);
      checkboxContainer.appendChild(label);

      // Insert after the payment line div (as next sibling)
      // const parent = lineElement.parentElement;
      // if (parent) {
      // // Insert right after the payment line div
      // if (lineElement.nextSibling) {
      // parent.insertBefore(checkboxContainer, lineElement.nextSibling);
      // } else {
      // parent.appendChild(checkboxContainer);
      // }
      // }

      console.log(" Checkbox injected for SELECTED line:", {
        lineUuid: selectedLine.uuid,
        methodName: methodName,
        state: this.getSaveCardState(selectedLine),
        " STATE PERSISTED": selectedLine.save_card !== undefined,
      });
    } else {
      console.warn(
        " Could not find DOM element for payment line:",
        selectedLine.uuid,
        methodName,
      );
    }
  },

  /**
   * Inject tip badges on done payment lines that have a terminal tip.
   * Shows "+$X.XX tip" next to the base amount so the cashier knows
   * what was actually charged on each line.
   */
  _injectTipBadges() {
    // Remove all existing tip badges to avoid duplicates
    document.querySelectorAll(".wf-tip-badge").forEach((el) => el.remove());

    const paymentLines = this.props.paymentLines || [];
    const allLineElements = document.querySelectorAll(".paymentline");

    paymentLines.forEach((line, index) => {
      const tipAmount = line._terminalTip || 0;
      if (tipAmount <= 0 || line.payment_status !== "done") return;

      const lineEl = allLineElements[index];
      if (!lineEl) return;

      const amountEl = lineEl.querySelector(".payment-amount");
      if (!amountEl) return;

      const tipBadge = document.createElement("span");
      tipBadge.className = "wf-tip-badge text-muted ms-1";
      tipBadge.style.cssText = "font-size:0.7em; white-space:nowrap;";
      tipBadge.textContent = `+$${tipAmount.toFixed(2)} tip`;
      amountEl.appendChild(tipBadge);
    });
  },

  /**
   * Check if a payment line uses a card terminal payment method (Woodforest/Payrillium)
   * @param {Object} paymentLine - The payment line to check
   * @returns {boolean} - True if it's a card terminal payment
   */
  isCardTerminalPayment(paymentLine) {
    if (!paymentLine || !paymentLine.payment_method_id) {
      return false;
    }

    const paymentMethod = paymentLine.payment_method_id;
    const methodName = paymentMethod.name?.toLowerCase() || "";

    // Check for Woodforest/Payrillium payment methods by name
    const cardTerminalMethods = ["woodforest"];
    const isTerminalByName = cardTerminalMethods.some(
      (terminalMethod) => methodName === terminalMethod.toLowerCase(),
    );

    // Also check if use_payment_terminal is set (indicates terminal payment)
    const hasTerminal =
      paymentMethod.use_payment_terminal &&
      paymentMethod.use_payment_terminal !== false &&
      paymentMethod.use_payment_terminal !== "none";

    return isTerminalByName || hasTerminal;
  },

  /**
   * Get the current order from the POS
   * @returns {Object|null} - The current order or null
   */
  getCurrentOrder() {
    try {
      return this.pos.get_order();
    } catch (error) {
      console.warn("Could not get current order:", error);
      return null;
    }
  },

  /**
   * Get the customer from the current order
   * @returns {Object|null} - The customer/partner or null
   */
  getCurrentCustomer() {
    const order = this.getCurrentOrder();
    if (!order) {
      return null;
    }
    return order.get_partner ? order.get_partner() : null;
  },

  /**
   * Validate billing address for a customer
   * Returns list of missing required fields
   * @param {Object} customer - The customer/partner object
   * @returns {Object} - { isValid: boolean, missingFields: string[] }
   */
  validateBillingAddress(customer) {
    if (!customer) {
      return { isValid: false, missingFields: ["Customer"] };
    }

    const missingFields = [];

    // Split name into first_name and last_name
    const nameParts = (customer.name || "").trim().split(/\s+/);
    const firstName = nameParts[0] || "";
    const lastName = nameParts.slice(1).join(" ") || "";

    if (!firstName) {
      missingFields.push(_t("First Name"));
    }
    if (!lastName) {
      missingFields.push(_t("Last Name"));
    }

    // Email
    if (!customer.email || !customer.email.trim()) {
      missingFields.push(_t("Email"));
    }

    // Phone (check both phone and mobile)
    if (!customer.phone && !customer.mobile) {
      missingFields.push(_t("Phone"));
    }

    // Country (can be object with code, or array [id, name], or just id)
    let countryCode = "";
    if (customer.country_id) {
      if (typeof customer.country_id === "object") {
        countryCode = customer.country_id.code || customer.country_id[1] || "";
      } else if (Array.isArray(customer.country_id)) {
        countryCode = customer.country_id[1] || "";
      }
    }
    if (!countryCode) {
      missingFields.push(_t("Country"));
    }

    // Address Line 1 (street)
    if (!customer.street || !customer.street.trim()) {
      missingFields.push(_t("Address Line 1"));
    }

    // City
    if (!customer.city || !customer.city.trim()) {
      missingFields.push(_t("City"));
    }

    // State/Province (check both state_id and state)
    const stateName = customer.state_id?.name || customer.state || "";
    if (!stateName || !stateName.trim()) {
      missingFields.push(_t("State/Province"));
    }

    // ZIP
    const zipCode = customer.zip || customer.zipcode || "";
    if (!zipCode || !zipCode.trim()) {
      missingFields.push(_t("ZIP"));
    }

    return {
      isValid: missingFields.length === 0,
      missingFields: missingFields,
    };
  },

  /**
   * Initialize save_card property on payment line if not exists
   * This ensures the state persists across selections
   * @param {Object} paymentLine - The payment line
   */
  initializeSaveCardState(paymentLine) {
    if (!paymentLine) {
      return;
    }
    // Use Object.defineProperty to make it non-enumerable but persistent
    if (paymentLine.save_card === undefined) {
      // Set directly on the object - this should persist
      Object.defineProperty(paymentLine, "save_card", {
        value: false,
        writable: true,
        enumerable: true,
        configurable: true,
      });
      console.log(
        " Initialized save_card property on payment line:",
        paymentLine.uuid,
      );
    }
  },

  /**
   * Get the save card state for a payment line
   * @param {Object} paymentLine - The payment line
   * @returns {boolean} - True if save card is checked
   */
  getSaveCardState(paymentLine) {
    if (!paymentLine) {
      return false;
    }
    // Initialize if not exists
    this.initializeSaveCardState(paymentLine);

    // Read state - check both direct property and potential getter
    const state =
      paymentLine.save_card === true || paymentLine.save_card === "true";

    // Debug log to verify state is being read correctly
    if (paymentLine.isSelected && paymentLine.isSelected()) {
      console.log(" Reading save card state for selected line:", {
        lineUuid: paymentLine.uuid,
        methodName: paymentLine.payment_method_id?.name,
        save_card: paymentLine.save_card,
        save_cardType: typeof paymentLine.save_card,
        hasProperty: "save_card" in paymentLine,
        returning: state,
      });
    }
    return state;
  },

  /**
   * Handle checkbox toggle for saving card
   * @param {Object} paymentLine - The payment line
   * @param {Event} event - The checkbox change event
   */
  async onSaveCardToggle(paymentLine, event) {
    const checked = event.target.checked;

    // Ensure state is initialized
    this.initializeSaveCardState(paymentLine);

    console.log("Save card toggle:", {
      checked,
      lineUuid: paymentLine.uuid,
      methodName: paymentLine.payment_method_id?.name,
      currentState: paymentLine.save_card,
    });

    // If unchecking, just update the state
    if (!checked) {
      paymentLine.save_card = false;
      console.log("Save card unchecked for line:", paymentLine.uuid);
      return;
    }

    // If checking, validate that a customer is selected
    const customer = this.getCurrentCustomer();

    if (!customer || !customer.id) {
      // Uncheck the checkbox first
      event.target.checked = false;
      paymentLine.save_card = false;

      // Show popup asking to select customer (non-blocking)
      // Use ask() which is designed for ConfirmationDialog
      try {
        const result = await ask(this.dialog, {
          title: _t("Select Customer"),
          body: _t("To save a card, please select a customer first."),
          confirmLabel: _t("Select Customer"),
          cancelLabel: _t("Continue without saving"),
        });

        console.log("Dialog result:", result);
        console.log("this.pos available:", !!this.pos);
        console.log(
          "this.pos.selectPartner available:",
          !!(this.pos && this.pos.selectPartner),
        );

        if (result === true) {
          // User clicked "Select Customer" - open customer selection screen
          console.log(
            " User wants to select customer - opening customer selection screen",
          );

          if (this.pos && this.pos.selectPartner) {
            this.pos.selectPartner();
            console.log(" Customer selection screen opened");
          } else {
            console.warn(" pos.selectPartner() not available");
          }
          // The checkbox remains unchecked - user can select customer and check again
        } else {
          // User clicked "Continue without saving" or closed the dialog
          console.log(
            "ℹ User continues without saving card (result:",
            result,
            ")",
          );
        }
      } catch (error) {
        // Dialog was closed/cancelled - continue without saving
        console.log(
          "Dialog closed with error - continuing without saving card:",
          error,
        );
      }
    } else {
      // Customer is selected, validate billing address before allowing save
      const validation = this.validateBillingAddress(customer);

      if (!validation.isValid) {
        // Uncheck the checkbox - billing address is incomplete
        event.target.checked = false;
        paymentLine.save_card = false;

        // Show notification with missing fields
        const missingFieldsList = validation.missingFields.join(", ");
        const message = _t(
          "Cannot save card. Please complete the billing address for customer '%s'. Missing fields: %s",
          customer.name || _t("Customer"),
          missingFieldsList,
        );

        // Show alert dialog with the missing fields
        this.dialog.add(AlertDialog, {
          title: _t("Incomplete Billing Address"),
          body: message,
        });

        console.warn(" Billing address validation failed:", {
          customerId: customer.id,
          customerName: customer.name,
          missingFields: validation.missingFields,
        });

        return;
      }

      // Billing address is complete, allow saving
      // Ensure property exists and set it
      this.initializeSaveCardState(paymentLine);
      paymentLine.save_card = true;

      // Verify it was saved
      console.log(" Save card checked for line:", {
        lineUuid: paymentLine.uuid,
        methodName: paymentLine.payment_method_id?.name,
        stateSaved: paymentLine.save_card,
        verified: paymentLine.save_card === true,
        " STATE PERSISTED ON PAYMENT LINE OBJECT": true,
        " Billing address validated": true,
      });

      // Force a re-render to update the checkbox
      this._injectSaveCardCheckboxes();
    }
  },

  /**
   * Check if save card checkbox should be shown for a payment line
   * @param {Object} paymentLine - The payment line
   * @returns {boolean} - True if checkbox should be shown
   */
  shouldShowSaveCardCheckbox(paymentLine) {
    // Only show for selected card terminal payment lines
    if (!paymentLine || !paymentLine.isSelected()) {
      console.log(
        "shouldShowSaveCardCheckbox: FALSE - line not selected or missing",
        {
          hasLine: !!paymentLine,
          isSelected: paymentLine?.isSelected?.(),
        },
      );
      return false;
    }
    // Initialize state to ensure it persists
    this.initializeSaveCardState(paymentLine);
    const isTerminal = this.isCardTerminalPayment(paymentLine);
    const shouldShow = isTerminal;
    console.log("shouldShowSaveCardCheckbox:", {
      lineUuid: paymentLine.uuid,
      methodName: paymentLine.payment_method_id?.name,
      isSelected: paymentLine.isSelected(),
      isTerminal,
      save_card: paymentLine.save_card,
      shouldShow: shouldShow,
      " TEMPLATE CALLING THIS METHOD - CHECKBOX SHOULD RENDER": shouldShow,
    });
    return shouldShow;
  },

  /**
   * Handle label click to toggle checkbox
   * @param {Object} paymentLine - The payment line
   * @param {Event} event - The click event
   */
  onLabelClick(paymentLine, event) {
    event.preventDefault();
    const checkboxId = `save-card-${paymentLine.uuid}`;
    const checkbox = document.getElementById(checkboxId);
    if (checkbox) {
      checkbox.click();
    }
  },

  /**
   * Get the translated label for save card checkbox
   * @returns {string} - Translated label text
   */
  getSaveCardLabel() {
    return _t("Save card for this customer");
  },
});
