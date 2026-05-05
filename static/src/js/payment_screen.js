/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { _t } from "@web/core/l10n/translation";
import {
  AlertDialog,
  ConfirmationDialog,
} from "@web/core/confirmation_dialog/confirmation_dialog";
import { makeAwaitable, ask } from "@point_of_sale/app/store/make_awaitable_dialog";
import { rpc as rpcRequest } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { roundPrecision } from "@web/core/utils/numbers";

import { PayrilliumAPI } from "@pos_woodforest/js/api_service";
import { loadPayrilliumConfig } from "@pos_woodforest/js/setup_config";
import {
  parseEMVData,
  generateExecutionId,
  validatePayrilliumResponse,
  updateTransactionState,
  logPayrilliumError,
  mapTSSDataToReceipt,
  normalizeCardType,
  payrilliumConsole,
  payrilliumBus,
  TX_STATE_TO_ODOO_STATE,
  TX_STATE_TO_TRANSACTION_STATUS,
  ensureError,
} from "@pos_woodforest/js/utils";
import {
  buildPaymentReference,
  isAuthorizationSuccessful,
  getErrorMessage,
  storeTransactionMetadata,
  findRefundedOrderLine,
  prepareRefundData,
  getOriginalTransaction,
} from "@pos_woodforest/js/payment_handler";

import {
  showAmountOnTerminal,
  selectCardType,
  handleTip,
  authorizePayment,
  handleAuthorizationFailure,
  captureCredit,
  processRefund,
  showSuccessMessage,
  showDeclineMessage,
} from "@pos_woodforest/js/terminal_service";
import { ConfigLoader } from "@pos_woodforest/js/config_loader";
import { CARD_VENDOR } from "./utils";
import { SavedCardSelectionPopup } from "@pos_woodforest/js/saved_card_selection_popup";

// TEMPORARY: Disabled payrilliumConsole override so all logs are visible in prod for debugging
// const console = payrilliumConsole;

console.log(" POS Woodforest - Payment Screen LOADING (Odoo 18)");

patch(PaymentScreen.prototype, {
  setup(...args) {
    super.setup(...arguments);
    this.uiService = this.env.services.ui;
    this.payrilliumAPI = PayrilliumAPI;
    this.dialog = useService("dialog");
    this.orm = useService("orm");
    this._posService = this.pos || this.env.services.pos;
    console.log(" Initializing Woodforest payment screen (Odoo 18)");
    window.__payrilliumTerminalBusy = false;
    window.__payrilliumAbort = () => this._abortTerminal();
    this._payrilliumAborted = false;

    // ── DIAGNOSTIC: Track mount/unmount cycles globally ──────────────
    if (!window.__psMountCount) window.__psMountCount = 0;
    if (!window.__psSetupCount) window.__psSetupCount = 0;
    window.__psSetupCount++;
    const setupN = window.__psSetupCount;
    console.warn(
      `[DIAG] PaymentScreen setup() #${setupN} at ${new Date().toISOString()}`,
    );

    try {
      onWillStart(async () => {
        console.warn(
          `[DIAG] onWillStart #${setupN} START at ${new Date().toISOString()}`,
        );
        const config = await this._loadConfiguration(this._posService);
        this._initializePaymentMethod(config);
        console.warn(
          `[DIAG] onWillStart #${setupN} END at ${new Date().toISOString()}`,
        );
      });

      onMounted(() => {
        window.__psMountCount++;
        const mountN = window.__psMountCount;
        console.warn(
          `[DIAG] onMounted #${mountN} (setup #${setupN}) at ${new Date().toISOString()}`,
        );

        // ── LOOP GUARD: If mounting too many times, something is wrong ──
        if (mountN > 3) {
          console.error(
            `[DIAG] PaymentScreen mounted ${mountN} times! Skipping cleanup to break potential loop.`,
          );
          return; // Break the loop — don't touch reactive state
        }

        // 18.4: Clear any stale Woodforest payment lines that have no transaction_id
        // and are not already done. This prevents confusion between old dangling
        // lines and newly selected payment methods when entering the PaymentScreen.
        // GUARD: Only clean once per order to prevent reactive re-mount loops.
        try {
          const order = this._posService.get_order?.();
          if (order && this.paymentMethodName && !order._staleLinesCleanedUp) {
            order._staleLinesCleanedUp = true; // Prevent re-running on re-mount
            const staleLines = order.payment_ids.filter((line) => {
              const isWoodforest =
                line.payment_method_id?.name?.toLowerCase() ===
                this.paymentMethodName;
              const isStale =
                !line.transaction_id && line.payment_status !== "done";
              const isRecovery = line._isRecoveryLine; // Never remove lines set up by recovery
              return isWoodforest && isStale && !isRecovery;
            });
            if (staleLines.length > 0) {
              console.warn(
                `[18.4] Found ${staleLines.length} stale line(s), cleaning up...`,
              );
              for (const line of staleLines) {
                console.warn("[18.4] Removing stale payment line:", line.cid);
                order.remove_paymentline(line);
              }
              console.warn(
                `[18.4] Cleared ${staleLines.length} stale Woodforest payment line(s).`,
              );
            }
          }
        } catch (e) {
          console.warn("[18.4] Could not clear stale payment lines:", e);
        }

        // 18.5: Clean up stale _all_payment_data entries (fix duplicate receipt issue)
        try {
          const order = this._posService.get_order?.();
          if (order) {
            const allData = order._all_payment_data || [];
            // Keep entries that belong to a DONE payment line
            const validTxIds = order.payment_ids
              .filter((p) => p.payment_status === "done" && p.transaction_id)
              .map((p) => p.transaction_id);
            const cleanData = allData.filter((d) =>
              validTxIds.includes(d.transactionId),
            );
            if (cleanData.length !== allData.length) {
              console.warn(
                `[18.5] Cleaned ${allData.length - cleanData.length} stale metadata entries from receipt.`,
              );
              order._all_payment_data = cleanData;
            }
          }
        } catch (e) {
          console.warn("[18.5] Could not clean _all_payment_data:", e);
        }

        // 18.6: Show basket directly on entering PaymentScreen (Speeds up payment validation)
        try {
          const order = this._posService.get_order?.();
          const terminal = this._posService.config.payrillium_terminal_serial;
          if (order && terminal) {
            const sessionId = this._posService.session?.id || null;
            const execId = generateExecutionId();
            order._terminalPending = true; // Set flag so ProductScreen knows to abort if we go back

            // Fire and forget, don't block mount
            showAmountOnTerminal(this.payrilliumAPI, order, execId, sessionId)
              .then(() => console.log("Basket sent to terminal on screen open"))
              .catch((e) => console.warn("Failed to show basket on mount:", e));
          }
        } catch (e) {
          console.warn("[18.6] Could not show basket on mount:", e);
        }

        // ── AUTO-VALIDATE: Recovery captured orders ─────────────────────
        // When recovery confirms all payments were captured, it sets this flag.
        // We auto-trigger validateOrder so the order goes through the full Odoo flow
        // (TIP FINALIZE → _finalizeValidation → sync → ReceiptScreen → order close)
        // without the cashier having to click Validate manually.
        const currentOrder = this._posService.get_order?.();
        if (currentOrder?._autoValidateOnMount) {
          delete currentOrder._autoValidateOnMount; // Prevent re-trigger on re-mount
          console.warn(
            "[Recovery] Auto-validating recovered order via native Odoo flow...",
          );
          setTimeout(() => {
            this.validateOrder?.(false);
          }, 300); // Small delay to ensure component is fully rendered
        }
      });

      onWillUnmount(() => {
        console.warn(
          `[DIAG] PaymentScreen UNMOUNTING (setup #${setupN}, total mounts so far: ${window.__psMountCount}) at ${new Date().toISOString()}`,
        );
      });
    } catch (error) {
      console.error(" Configuration error:", error);
      this._showConfigurationError();
    }

    // ── GUARANTEE: Our processing runs BEFORE pos_loyalty/pos_hr ──
    // When pos_loyalty loads after us, its patch sits on top of ours in the
    // prototype chain. It calls _isOrderValid() → is_paid() → false (our
    // lines are pending) → blocks, and our validateOrder NEVER executes.
    //
    // Fix: After all patches and setup complete, we replace the instance
    // method with a wrapper that processes pending Woodforest lines FIRST,
    // then calls the full chain. Since lines are "done" by then,
    // is_paid() returns true and everyone passes.
    const _chainedValidate = this.validateOrder; // = useAsyncLockedMethod(full chain)
    this.validateOrder = async (isForceValidate) => {
      // Guard: prevent double-fire
      if (this._woodforestValidating) return;

      const order = this.currentOrder;
      const total = order.get_total_with_tax();
      const isRefund = total < 0;

      // Remove stale $0 Woodforest lines that have no real transaction
      const zeroLines = (order?.payment_ids || []).filter((p) =>
        this._isPayrilliumPayment(p) &&
        p.get_amount() === 0 &&
        p.get_payment_status() !== "done" &&
        !p.transaction_id
      );
      for (const zl of zeroLines) {
        order.remove_paymentline(zl);
        console.warn("[validateOrder] Removed stale $0 Woodforest line:", zl.uuid);
      }

      const pendingWf = (order?.payment_ids || []).filter((p) => {
        return (
          this._isPayrilliumPayment(p) &&
          p.get_payment_status() !== "done" &&
          p.get_payment_status() !== "reversed" &&
          !p.transaction_id
        );
      });

      if (pendingWf.length === 0) {
        // No pending Woodforest lines — normal chain (pos_loyalty → base)
        return _chainedValidate.call(this, isForceValidate);
      }

      // ── From here: process Woodforest lines BEFORE the chain ──
      this._woodforestValidating = true;
      this.executionId = generateExecutionId();

      try {
        // Offline guard
        if (this.pos.terminalStatus === "offline") {
          this.dialog.add(AlertDialog, {
            title: _t("Terminal Offline"),
            body: _t(
              "The terminal is not responding. Please check the connection and try again.",
            ),
          });
          return;
        }

        // Pinging guard — block while terminal connectivity is being verified
        if (this.pos.terminalStatus === "pinging") {
          this.dialog.add(AlertDialog, {
            title: _t("Checking Terminal"),
            body: _t(
              "Terminal connectivity is being verified. Please wait for the check to complete.",
            ),
          });
          return;
        }

        if (isRefund) {
          const refundLine = pendingWf[0];
          if (refundLine) {
            await this._handleRefund(refundLine, total);
            // _finalizeRefundTransaction already calls super.validateOrder(),
            // so we must NOT fall through to _chainedValidate — that would
            // trigger the prototype validateOrder which detects isRefund again
            // and fires a SECOND refund request to the terminal.
            return;
          }
        } else {
          for (let i = 0; i < pendingWf.length; i++) {
            const line = pendingWf[i];
            const ept = line.payment_method_id?.use_payment_terminal;
            const lname = (
              line.payment_method_id?.name || ""
            ).toLowerCase();
            const hasTerminal =
              ept === "woodforest" ||
              ept === "payrillium" ||
              lname.includes("woodforest");
            const hasTokens =
              order.get_partner()?.payment_token_ids?.length > 0;

            if (!hasTerminal && !hasTokens) {
              this.dialog.add(AlertDialog, {
                title: "Payment method not available",
                body: "To use this payment method, you must either select a customer with saved cards or assign a terminal to this session.",
              });
              return;
            }

            const proceed = await this._promptSavedCardOrContinue(
              order,
              line,
              hasTerminal,
            );
            if (!proceed) {
              this.dialog.add(AlertDialog, {
                title: "Payment cancelled",
                body: "You must select a saved card or assign a terminal before proceeding.",
              });
              return;
            }

            const success = await this._processPayrilliumPayment(
              line,
              hasTerminal,
              i === 0,
              i === pendingWf.length - 1,
            );
            if (success === false) return;
          }
        }

        // All Woodforest lines are now "done". Call the full chain.
        // pos_loyalty: _isOrderValid() → is_paid() → true ✅ → coupon validation
        // Our validateOrder: sees no pending lines → tip finalize → super(true)
        // Base: _isOrderValid() → is_paid() → true ✅ → finalize
        return _chainedValidate.call(this, true);
      } catch (e) {
        const safeError = ensureError(e);
        try {
          await this._handlePaymentError(safeError);
        } catch (innerError) {
          // Last resort: show a basic alert so the POS never goes white
          console.error("[Woodforest] _handlePaymentError itself crashed:", innerError);
          try {
            this.dialog?.add?.(AlertDialog, {
              title: "Payment Error",
              body: safeError.message || "An unexpected error occurred.",
            });
          } catch (_) { /* truly nothing we can do */ }
        }
      } finally {
        this._woodforestValidating = false;
      }
    };
  },

  async _withSpinner(fn) {
    this.uiService.block();
    // Inject Cancel button INSIDE Odoo's o_blockUI div (it's a flex column, button appears below the message)
    let cancelBtn = null;
    const injectCancel = () => {
      const blockUI = document.querySelector(".o_blockUI");
      if (blockUI && !document.getElementById("payrillium_cancel_btn")) {
        cancelBtn = document.createElement("button");
        cancelBtn.id = "payrillium_cancel_btn";
        cancelBtn.textContent = "X Cancel";
        cancelBtn.style.cssText = `
 margin-top: 24px; padding: 12px 36px; background: #e53935; color: #fff;
 border: none; border-radius: 10px; font-size: 16px; font-weight: 700;
 cursor: pointer; letter-spacing: 0.5px; box-shadow: 0 4px 16px rgba(0,0,0,0.35);
 `;
        cancelBtn.onclick = () => this._abortTerminal();
        blockUI.appendChild(cancelBtn);
      } else if (!blockUI) {
        requestAnimationFrame(injectCancel);
      }
    };
    requestAnimationFrame(injectCancel);
    try {
      return await fn();
    } finally {
      this.uiService.unblock();
      if (cancelBtn) cancelBtn.remove();
    }
  },

  _setTerminalBusy(busy) {
    window.__payrilliumTerminalBusy = busy;
  },

  _setCancelVisible(visible) {
    const btn = document.getElementById("payrillium_cancel_btn");
    if (!btn) return;
    btn.style.opacity = visible ? "1" : "0";
    btn.style.pointerEvents = visible ? "auto" : "none";
    btn.title = visible ? "" : "";
  },

  async _abortTerminal() {
    // Prevent double-fire: disable button immediately on first click
    const btn = document.getElementById("payrillium_cancel_btn");
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fa fa-spinner fa-spin me-2"></i>Cancelling...';
      btn.style.opacity = "0.7";
      btn.style.cursor = "not-allowed";
    }
    // Update overlay message so cashier knows something is happening
    const msgEl = document.querySelector(".o_blockUI .o_message");
    if (msgEl) msgEl.innerHTML = "Cancelling terminal operation...";
    console.warn("[ABORT] User requested terminal abort from overlay.");
    this._payrilliumAborted = true; // Signal to _processPayrilliumPayment to exit after current await
    try {
      await this.payrilliumAPI.abortTerminal(
        this._posService.session?.id || null,
        this.executionId || null,
      );
    } catch (e) {
      console.warn("[ABORT] Could not send abort:", e);
    }
    // NOTE: do NOT call uiService.unblock() here.
    // _processPayrilliumPayment's finally block handles unblock once the flow exits.
    // Calling it here causes a double-unblock warning and breaks finalization.
  },

  /**
   * Updates the spinner message text while the UI is already blocked.
   * Uses bus.trigger directly to avoid incrementing the block counter.
   * @param {string} message
   */
  _setBlockMessage(message) {
    try {
      this.uiService.bus.trigger("BLOCK", { message });
    } catch (e) {
      // non-critical — spinner message update failed
    }
  },

  async _loadConfiguration(posService) {
    try {
      const config = await loadPayrilliumConfig(posService);
      console.log("config", config);
      // this._applyPayrilliumStyles();
      if (!config) throw new Error("No config loaded");
      this.paymentMethodName = config.name;
      this.paymentMethodColor = config.color;
      this.paymentMethodIcon = config.icon;
      this.receivableAccountId = config.receivableAccountId;
      this.paymentProviderId = config.paymentProviderId;
      this.terminalId = config.terminalId;
      this.terminalConfig = config.terminalConfig || {}; // Store terminal config including iface_tipproduct
      this.terminalMessages = await ConfigLoader.getTerminalMessages();
      console.log(" PaymentScreen - Terminal config and messages loaded:", {
        terminalConfig: this.terminalConfig,
        terminalId: this.terminalId,
        terminalMessages: this.terminalMessages,
      });
      return config;
    } catch (error) {
      console.error(" Error loading configuration:", error);
    }
  },
  /*
 _applyPayrilliumStyles() {
 const buttons = [
 ...document.querySelectorAll(".paymentmethod .payment-name"),
 ];
 for (const b of buttons) {
 if (b.textContent.trim().toLowerCase() === this.paymentMethodName) {
 const button = b.closest(".paymentmethod");
 button.classList.add("payrillium-method");
 button.style.backgroundColor = this.paymentMethodColor || "#003366";
 button.style.color = "white";
 const originalIcon = button.querySelector("img:not(.payrillium-icon)");
 if (originalIcon) {
 originalIcon.remove();
 }
 let icon = button.querySelector("img.payrillium-icon");
 if (!icon) {
 icon = document.createElement("img");
 icon.className = "payrillium-icon";
 button.insertBefore(icon, button.firstChild);
 }
 icon.src =
 this.paymentMethodIcon ||
 "/pos_woodforest/static/description/icon.png";
 icon.alt = "Woodforest";
 b.style.color = "white";
 b.style.fontWeight = "bold";
 }
 }
 },
 */

  _initializePaymentMethod(config) {
    console.log(" Configuring payment method with:", config);
    this.payrilliumAPI = PayrilliumAPI;
  },

  _showConfigurationError() {
    this.dialog.add(AlertDialog, {
      title: "Configuration error",
      body: "Could not initialize payment configuration. Check your settings.",
    });
  },

  async _createPayrilliumTransaction(params) {
    try {
      // Inject the current POS cashier so the transaction is tagged with who processed it
      if (!params.pos_employee_id) {
        const cashier = this.pos.get_cashier?.();
        if (cashier?.id) {
          params.pos_employee_id = cashier.id;
        }
      }
      const txId = await this.orm.call(
        "payment.transaction",
        "create_from_pos_woodforest",
        [params],
        {},
      );
      console.log(" payment.transaction created for Payrillium, ID:", txId);
      return txId;
    } catch (error) {
      console.error(" Error creating payment.transaction:", error);
      return null;
    }
  },

  /**
   * Orchestrates the full Payrillium payment process for a payment line.
   * Each step is delegated to a helper/service function for clarity and maintainability.
   * Throws an error if any step fails.
   *
   * @param {Object} paymentLine - The payment line to process.
   * @param {boolean} hasTerminal - True if using a terminal.
   * @param {boolean} isFirstWoodforest - True if first in group.
   * @param {boolean} isLastWoodforest - True if last in group.
   * @returns {Promise<boolean>} - Resolves true if payment is successful, throws otherwise.
   */
  async _processPayrilliumPayment(
    paymentLine,
    hasTerminal,
    isFirstWoodforest = true,
    isLastWoodforest = true,
  ) {
    // ── GUARD: Skip $0 lines — terminal rejects zero-amount transactions ──
    const lineAmount = paymentLine.get_amount();
    if (lineAmount === 0) {
      console.warn("[_processPayrilliumPayment] Skipping $0 line — terminal does not accept zero-amount transactions.");
      paymentLine.set_payment_status("done");
      return true;
    }

    // ── Duplicate sale guard (commented out — re-evaluate later) ──
    // if (isFirstWoodforest) {
    //   const dup = await this._checkRecentDuplicateOrder(
    //     paymentLine.pos_order_id,
    //   );
    //   if (dup) {
    //     const confirmed = await ask(this.dialog, {
    //       title: "\u26A0\uFE0F Possible Duplicate Sale",
    //       body: `A completed transaction for this exact amount was processed ${dup.minutes_ago} minute(s) ago (ref: ${dup.reference}). Do you want to continue?`,
    //       confirmLabel: "Yes, this is a new sale",
    //       cancelLabel: "Cancel",
    //     });
    //     if (!confirmed) return false;
    //   }
    // }
    this.uiService.block();
    // Inject Cancel button into o_blockUI once OWL renders it
    let _cancelBtn = null;
    const _injectCancel = () => {
      const blockUI = document.querySelector(".o_blockUI");
      if (blockUI && !document.getElementById("payrillium_cancel_btn")) {
        _cancelBtn = document.createElement("button");
        _cancelBtn.id = "payrillium_cancel_btn";
        _cancelBtn.innerHTML = '<i class="fa fa-times me-2"></i>Cancel';
        _cancelBtn.style.cssText = [
          "margin-top:28px",
          "padding:10px 32px",
          "background:rgba(255,255,255,0.15)",
          "color:#fff",
          "border:2px solid rgba(255,255,255,0.6)",
          "border-radius:8px",
          "font-size:15px",
          "font-weight:600",
          "cursor:pointer",
          "letter-spacing:0.3px",
          "backdrop-filter:blur(4px)",
          "transition:background 0.15s",
        ].join(";");
        _cancelBtn.onmouseenter = () =>
          (_cancelBtn.style.background = "rgba(229,57,53,0.85)");
        _cancelBtn.onmouseleave = () =>
          (_cancelBtn.style.background = "rgba(255,255,255,0.15)");
        _cancelBtn.onclick = () => this._abortTerminal();
        blockUI.appendChild(_cancelBtn);
      } else if (!blockUI) {
        requestAnimationFrame(_injectCancel); // retry until o_blockUI is rendered
      }
    };
    requestAnimationFrame(_injectCancel);
    // Reset flags for this payment attempt — prevents stale state from
    // a previous call from triggering false-positive recovery logic.
    this._payrilliumAborted = false;
    this._authWasStarted = false;
    this._refundWasStarted = false;
    const order = paymentLine.pos_order_id;
    const sessionId = this.pos.session?.id || this.pos.pos_session?.id || null;
    let tipAmount = 0; // Scope at function level for catch block access

    const resetTerminalTip = () => {
      if (tipAmount > 0 && order) {
        const oldTip = order._pendingTerminalTip || 0;
        order._pendingTerminalTip = parseFloat(
          Math.max(0, oldTip - tipAmount).toFixed(2),
        );
        console.log(
          ` [TIP REVERTED] $${tipAmount} removed from accumulator. (Old: $${oldTip} -> New: $${order._pendingTerminalTip})`,
        );
      }
      paymentLine._terminalTip = 0;
      tipAmount = 0;
    };

    try {
      this.executionId = generateExecutionId();
      this.currentPaymentLine = paymentLine;
      if (paymentLine.payment_token_id) {
        return await this._processPayrilliumTokenPayment(
          paymentLine,
          hasTerminal,
        );
      }

      // Initialize transactionDataToSave early so errors that happen before
      // authorizePayment (e.g. MQTT failures, ABORTED, chip errors) are still
      // recorded in the database. acquirer_reference and state will be updated
      // once we have a real authorization response.
      const earlyPaymentRef = buildPaymentReference();
      this.transactionDataToSave = {
        reference: earlyPaymentRef,
        provider_id: this.paymentProviderId,
        payment_method_id: paymentLine.payment_method_id.id,
        acquirer_reference: null,
        amount: parseFloat(paymentLine.get_amount().toFixed(2)),
        order_uid: paymentLine.pos_order_id.pos_reference,
        pos_session_id: sessionId,
        pos_config_id: this.pos.config.id,
        card_type: null,
        terminal_id: Array.isArray(
          this._posService.config.payrillium_terminal_id,
        )
          ? this._posService.config.payrillium_terminal_id[0]
          : this._posService.config.payrillium_terminal_id,
        terminal_serial:
          this._posService.config.payrillium_terminal_serial || null,
        state: "pending",
        transaction_status: "none",
        execution_id: this.executionId,
      };

      // 1. Show the payment amount and basket on the terminal
      // NOW DONE ON SCREEN MOUNT (18.6) to speed up payment validation.
      // if (isFirstWoodforest) {
      // this._setBlockMessage("Sending order to terminal...");
      // const showAmountResult = await showAmountOnTerminal(
      // this.payrilliumAPI,
      // paymentLine,
      // this.executionId,
      // sessionId
      // );
      // validatePayrilliumResponse(showAmountResult);
      // }

      const cardType = "CREDIT";
      // 3. Handle tip selection if enabled in POS configuration
      this._setBlockMessage("Awaiting tip selection...");
      let tipResult;
      this._setTerminalBusy(true);
      try {
        tipResult = await handleTip(
          this.payrilliumAPI,
          paymentLine,
          this._posService,
          this.executionId,
          sessionId,
          this.terminalConfig,
        );
      } catch (tipError) {
        this._setTerminalBusy(false);
        // Inactivity or comm error → show friendly popup, line goes to retry
        if (tipError.mcCode === "T04" || tipError.terminalConnectionError) {
          console.warn(
            `[TIP STOP] ${tipError.mcCode || "COMM_ERROR"} — stopping payment for retry.`,
          );
          // Preserve the original error with its mapped terminalTitle/terminalMsg
          // Just ensure it's marked as cancelled so the line goes to retry state
          tipError.cancelled = true;
          throw tipError;
        }
        throw tipError; // other errors (cancel, etc.) propagate normally
      } finally {
        this._setTerminalBusy(false);
      }

      // ── Abort check: if cashier cancelled during tip, stop here ──
      if (this._payrilliumAborted) {
        console.warn(
          "[ABORT] Payment cancelled by cashier after tip selection. Marking line as retry.",
        );
        const err = new Error("Operation cancelled by user.");
        err.cancelled = true;
        err.terminalTitle = "Cancelled";
        err.terminalMsg = "Operation Cancelled";
        throw err;
      }

      // Validate tip response — may throw on error/cancel
      validatePayrilliumResponse(tipResult);
      tipAmount = tipResult.data?.data?.tipAmount || 0;

      // handleTip already updated paymentLine.amount if there was a terminal tip.
      // Now update the Odoo order tip so it shows as Tip instead of Change.
      if (tipAmount > 0) {
        // tip_amount is persisted to DB on approval — no RAM accumulation needed.
        console.warn(
          `[TIP] $${tipAmount} tip selected. Will be saved to payment.transaction on approval.`,
        );
      }

      // The Odoo UI paymentLine MUST stay at the base debt amount (e.g. $1.00)
      // so the "Remaining" balance doesn't artificially drop to 0 due to tips.
      // However, the payment.transaction record in the backend MUST equal the real
      // bank charge (e.g. Base $1.00 + Tip $0.50 = $1.50) so Accounting matches perfectly.
      const baseAmount = paymentLine.get_amount();
      this.transactionDataToSave.amount = parseFloat(
        (baseAmount + tipAmount).toFixed(2),
      );
      console.warn(
        `[AUTH PREP] base=$${baseAmount}, tip=$${tipAmount}, total=$${this.transactionDataToSave.amount}`,
      );

      // ── Early persistence ──────────────────────────────────────────────────
      // Save the transaction as "pending" NOW, before sending the auth to the
      // terminal. The earlyPaymentRef is also sent to Cybersource as payment_id
      // (clientReferenceInformation.code), so if the browser closes during auth,
      // the record exists in Odoo and Sync Status can find it by that reference.
      const txId = await this._createPayrilliumTransaction(
        this.transactionDataToSave,
      );

      // ── Local Recovery Memory ──────────────────────────────────────────────
      order.pending_terminal_transaction = {
        reference: earlyPaymentRef,
        amount: this.transactionDataToSave.amount,
        method_id: paymentLine.payment_method_id.id,
      };

      // ── Recovery Snapshot (Cross-Device) ───────────────────────────────────
      // We save the snapshot exactly before authorizing, to ensure we catch tip selections
      if (txId) {
        try {
          // generate a multi-model snapshot for Odoo 18 loadData
          const orderData = order.serialize({});

          // MANUAL INJECTION: We add these fields here so they are preserved in
          // the recovery snapshot JSON, but they aren't sent to the Odoo backend
          // during standard sync.
          orderData.pending_terminal_transaction =
            order.pending_terminal_transaction || null;

          const snapshot = {
            "pos.order": [
              {
                ...orderData,
                id: order.id,
                partner_id: order.partner_id?.id || false,
                fiscal_position_id: order.fiscal_position_id?.id || false,
                pricelist_id: order.pricelist_id?.id || false,
              },
            ],
            "pos.order.line": order.lines.map((l) => ({
              ...l.serialize({}),
              id: l.id,
              order_id: order.id,
              product_id: l.product_id?.id,
              tax_ids: l.tax_ids.map((t) => t.id),
            })),
            "pos.payment": order.payment_ids.map((p) => ({
              ...p.serialize({}),
              id: p.id,
              pos_order_id: order.id,
              payment_method_id: p.payment_method_id?.id,
            })),
          };

          const snapshotJson = JSON.stringify(snapshot);
          await this.orm.call(
            "payment.transaction",
            "action_save_snapshot_and_progress",
            [txId, snapshotJson],
          );
          console.log(
            " [Recovery] Saved Order Snapshot (including tips) and updated status to in_progress.",
          );
        } catch (snapErr) {
          console.warn(" [Recovery] Failed to save order snapshot:", snapErr);
        }
      }

      // 4. Authorize the payment on the terminal
      // Cancel visible immediately — abort + recovery handles potential reversal.
      this._setCancelVisible(true);
      this._setBlockMessage("Processing payment request...");
      const paymentRef = earlyPaymentRef;
      this._setTerminalBusy(true);
      this._authWasStarted = true; // Flag: auth is now in flight — abort from here → recovery
      let paymentResult;
      try {
        paymentResult = await authorizePayment(
          this.payrilliumAPI,
          paymentLine,
          cardType,
          tipAmount,
          paymentRef,
          this.executionId,
          sessionId,
        );
      } finally {
        this._setTerminalBusy(false);
        this._setCancelVisible(false); // auth done — hide cancel button
      }

      // ── Operator abort: we generated the cancel, don't validate the response ──
      if (this._payrilliumAborted) {
        console.warn("[ABORT] Operator cancelled during auth. Ignoring terminal response.");
        const abortErr = new Error("Cancelled by operator.");
        abortErr.cancelled = true;
        abortErr.terminalTitle = "Cancelled";
        abortErr.terminalMsg = "Cancelled by Operator";
        abortErr.txState = "cancel";
        throw abortErr;
      }

      const _perfStart = performance.now();
      // Get transaction details from the response and update the early record
      const message = paymentResult.data?.data?.message || {};
      const transactionId = message.transactionId
        ? String(message.transactionId)
        : message.id
          ? String(message.id)
          : "";
      this.transactionDataToSave.acquirer_reference = transactionId;
      // 4. Capture key metadata for Odoo persistence
      this.transactionDataToSave.card_type = normalizeCardType(message);

      if (paymentResult.auth_verified === true) {
        this.transactionDataToSave.transaction_status = "authorized";
      } else {
        // Map gateway status to the correct transaction_status
        const gwStatus = (message.status || "").toUpperCase();
        const GW_STATUS_MAP = {
          REVERSED: "reversed",
          VOIDED: "voided",
          DECLINED: "declined",
        };
        this.transactionDataToSave.transaction_status =
          GW_STATUS_MAP[gwStatus] || "auth_failed";
      }
      validatePayrilliumResponse(paymentResult);

      // ── Post-auth policy checks ────────────────────────────────
      const edcType = (message.edcType || "").toUpperCase();
      const authStatus = message.status;

      if (authStatus === "PARTIAL_AUTHORIZED") {
        const approved = message.approvedAmount || "0.00";
        const requested = paymentLine.get_amount().toFixed(2);
        console.warn(
          ` POLICY: Partial auth detected ($${approved} of $${requested}), initiating Partial Reverse/Refund via VDA...`,
        );

        const revResult = await handleAuthorizationFailure(
          this.payrilliumAPI,
          paymentLine,
          "DEBIT", // Force VDA for partials
          paymentResult,
          paymentRef,
          this.executionId,
          transactionId,
          approved, // Pass the partial amount to reverse
          sessionId,
        );

        const revOk = revResult?.reversal_verified === true;
        this.transactionDataToSave.transaction_status = revOk
          ? "reversed"
          : "reversal_failed";

        let userMsg = `Partial payments are not accepted by this merchant.\nOnly $${approved} of $${requested} was approved.\n\n`;
        if (revOk) {
          userMsg += "The authorization has been automatically reversed.";
        } else {
          userMsg +=
            "CRITICAL: Automatic reversal failed. Please check Cybersource dashboard to manually reverse this partial hold.";
        }

        const err = new Error(userMsg);
        err.logMessage = `POLICY: PARTIAL_AUTH_REJECTED - approved=${approved}, requested=${requested}, PartialVoidDebitAuth_Result=${revOk ? "SUCCESS" : "FAILED - MANUAL ATTENTION REQUIRED"}`;
        throw err;
      }

      // 5. Detected card/transaction type and perform capture if needed
      if (edcType === "DEBIT") {
        console.log(
          " DEBIT Card handled as Sale. Marking transaction as Captured immediately.",
        );
        if (this.transactionDataToSave) {
          this.transactionDataToSave.state = "done";
          this.transactionDataToSave.transaction_status = "captured";
        }
      } else if (cardType === "CREDIT") {
        console.log(
          " CREDIT Card handled as Sale (capture:true). Marking transaction as Captured immediately.",
        );
        if (this.transactionDataToSave) {
          this.transactionDataToSave.state = "done";
          this.transactionDataToSave.transaction_status = "captured";
        }
      }
      // 6. Parse EMV tags and store transaction metadata
      // Use the actual edcType from the terminal response, fallback to hardcoded cardType
      const actualCardType = edcType || cardType;
      const parsed = message.emvTags ? parseEMVData(message.emvTags) : {};
      storeTransactionMetadata(
        this.currentOrder,
        paymentLine,
        transactionId,
        message,
        actualCardType,
        parsed,
        this.paymentProviderId,
      );
      // Add _tipAmount to the last entry in _all_payment_data (already created by storeTransactionMetadata).
      // Do NOT call set_extra_payment_data again — that would push a duplicate entry!
      if (tipAmount > 0) {
        const allData = this.currentOrder._all_payment_data;
        if (allData && allData.length > 0) {
          allData[allData.length - 1]._tipAmount = parseFloat(
            tipAmount.toFixed(2),
          );
        }
      }
      // 7. Create the payment.transaction record in Odoo
      if (this.transactionDataToSave) {
        // tip_amount saved on approval only — a declined auth never reaches this line.
        this.transactionDataToSave.tip_amount = roundPrecision(
          tipAmount,
          this.currentOrder.currency?.rounding || 0.01,
        );
        await this._createPayrilliumTransaction(this.transactionDataToSave);
        console.error(
          `[PERF] createTransaction: ${(performance.now() - _perfStart).toFixed(0)}ms`,
        );
      }

      // 8. Create payment.token if tokenization was requested and token info is available
      if (paymentLine.save_card === true) {
        await this._createPaymentTokenFromAuthResponse(
          paymentLine,
          paymentResult,
        );
      }

      paymentLine.provider_id = this.paymentProviderId || "";

      // Signal navbar that the terminal is responding
      payrilliumBus.trigger("payrillium:terminal_online");

      // Fire and forget: We do NOT await this. Waiting here blocks Odoo's ticket generation
      // for 6+ seconds while the terminal slowly acknowledges the message.
      try {
        const chargedAmount =
          this.transactionDataToSave?.amount || paymentLine.get_amount();
        const formattedAmount = this._posService.formatCurrency
          ? this._posService.formatCurrency(chargedAmount)
          : chargedAmount.toFixed(2);
        console.warn(
          `[showSuccess] Displaying approved amount: $${chargedAmount} (base=$${paymentLine.get_amount()}, tip=$${tipAmount})`,
        );

        showSuccessMessage(
          this.payrilliumAPI,
          this.terminalMessages,
          formattedAmount,
          this.executionId,
          sessionId,
          tipAmount,
        ).catch((e) => {
          console.warn(
            "Failed to show success message on terminal in background",
            e,
          );
        });
      } catch (e) {
        console.warn("Error preparing success message", e);
      }
      // 18.1: Removed showEmptyBasket from success path — terminal clears via abort on next order open.
      return true;
    } catch (error) {
      console.log(" errorPay", error);
      console.log(" transactionDataToSave", this.transactionDataToSave);

      // --- ERROR RECOVERY: REVERT TIP IF PAYMENT FAILED ---
      // If a tip was added during this failed payment attempt, we must revert it
      // from the order. Otherwise, the order total remains artificially inflated,
      // and the "remaining" balance will be wrong, getting the cashier stuck.
      try {
        resetTerminalTip();
        console.warn(
          `[ERROR RECOVERY] Tip reverted. _pendingTerminalTip=$${order._pendingTerminalTip || 0}, _terminalTip=$${paymentLine._terminalTip || 0}`,
        );
        // showDecline below handles the terminal visual on failure.
      } catch (recoveryError) {
        console.error(
          "Failed to recover order tip state after payment error:",
          recoveryError,
        );
      }

      // error.message → friendly text shown to cashier in UI
      // error.logMessage → CCODE/TCODE code + technical detail saved to DB
      const friendlyMessage = error.message || "Unknown error";

      // Auto-abort the terminal when we get a communication error (HTTP 500/409).
      // If payment already went through, abort simply does nothing.
      if (error.terminalConnectionError) {
        console.warn("[COMM ERROR] Sending abort to terminal to release it...");
        payrilliumBus.trigger("payrillium:terminal_offline");
        // 18.3: Temporarily disabled (abort terminal bug pending fix from provider)
        // try {
        //   await this.payrilliumAPI.abortTerminal(
        //     sessionId,
        //     this.executionId || null,
        //   );
        //   console.warn("[COMM ERROR] Abort sent successfully.");
        // } catch (abortErr) {
        //   console.warn(
        //     "[COMM ERROR] Abort failed (terminal may be idle):",
        //     abortErr,
        //   );
        // }
      }

      // If user aborted DURING or AFTER auth start → keep as in_progress.
      // We don't know if the charge went through, so Recovery must handle it.
      // The "Recover Payments" button will appear and let the cashier verify
      // with the processor whether the payment was actually captured.
      if (this._payrilliumAborted && this._authWasStarted) {
        console.warn(
          "[ABORT] Auth was in flight when cancelled. Keeping as in_progress for recovery.",
        );
        this.transactionDataToSave.state = "pending";
        this.transactionDataToSave.transaction_status = "in_progress";
      } else if (error.tipCancelled) {
        // User cancelled on the tip screen — no auth was ever sent
        console.warn(
          "[TIP CANCEL] User cancelled tip. Marking transaction as cancelled.",
        );
        this.transactionDataToSave.state =
          TX_STATE_TO_ODOO_STATE["cancel"] || "error";
        this.transactionDataToSave.transaction_status =
          TX_STATE_TO_TRANSACTION_STATUS["cancel"] || "cancelled";
      } else if (this._authWasStarted && (error.terminalConnectionError || error.isCommError)) {
        // Auth was sent but we got a comm/cloud error → outcome unknown.
        // We don't know if the charge went through → Recovery must resolve it.
        console.warn(
          "[RECOVERY] Auth was sent but comm error received. Keeping as in_progress for recovery.",
        );
        this.transactionDataToSave.state = "pending";
        this.transactionDataToSave.transaction_status = "in_progress";
      } else {
        // Use error.txState from utils.js as the primary source of truth.
        const txState = error.txState || "error";

        // Native Odoo state: map using centralized data-driven dictionary
        this.transactionDataToSave.state =
          TX_STATE_TO_ODOO_STATE[txState] || "error";

        // Payrillium transaction_status: use centralized mapping or fallback to txState natively
        this.transactionDataToSave.transaction_status =
          TX_STATE_TO_TRANSACTION_STATUS[txState] || txState;
      }
      // Save the raw terminal error code — this is the technical detail (TERMINAL RESPONSE in form view)
      this.transactionDataToSave.terminal_message =
        error.logMessage || friendlyMessage;
      // Save the human-readable message — this feeds state_message (STATUS HISTORY in form view)
      this.transactionDataToSave.error_message = friendlyMessage;
      // Save the user-friendly message so it's visible when reviewing later
      this.transactionDataToSave.status_summary = friendlyMessage;
      await this._createPayrilliumTransaction(this.transactionDataToSave);
      await logPayrilliumError(rpcRequest, {
        executionId: this.executionId,
        step: "error_process_payment",
        errorMessage: error.logMessage || friendlyMessage,
        terminalId: this.terminalId,
        payload: {
          error_type: error?.name || "Unknown",
          error_message: error?.message || friendlyMessage,
          transaction_status: this.transactionDataToSave?.transaction_status,
          acquirer_reference: this.transactionDataToSave?.acquirer_reference,
          amount: paymentLine.amount,
          method: paymentLine.payment_method_id?.name,
          order_uid: this.transactionDataToSave?.order_uid,
          aborted: this._payrilliumAborted || false,
          auth_started: this._authWasStarted || false,
          error_layer: error.layer || "Unknown",
        },
      });

      // Notify Store to refresh stuck transactions immediately if this was an abort in flight
      if (this.transactionDataToSave.transaction_status === "in_progress") {
        try {
          await this._posService.fetch_stuck_transactions();
        } catch (e) {
          console.warn("[Recovery] Failed to auto-refresh stuck transactions:", e);
        }
      }

      // If cashier explicitly aborted, override error with cancel message
      const isCashierAbort = this._payrilliumAborted;
      const formattedError = {
        payrilliumError: true,
        message: isCashierAbort
          ? "Cancelled by operator."
          : error?.message || "Payment processing error",
        originalResponse: error?.originalResponse || error?.response || error,
        cancelled: isCashierAbort || error?.cancelled || false,
        paymentLine,
      };
      paymentLine.set_payment_status("retry");
      // Show error-specific message on terminal (fire-and-forget)
      showDeclineMessage(
        this.payrilliumAPI,
        this.terminalMessages,
        this.executionId,
        sessionId,
        {
          title: isCashierAbort ? "Cancelled" : (error?.terminalTitle || "Declined"),
          message: isCashierAbort ? "Cancelled by Operator" : (error?.terminalMsg || "Transaction Failed"),
        },
      ).catch((e) => {
        console.warn(
          "Failed to show decline message on terminal in background",
          e,
        );
      });
      await this._handlePaymentError(ensureError(formattedError));
      return false;
    } finally {
      this.uiService.unblock();
      if (_cancelBtn) {
        _cancelBtn.remove();
        _cancelBtn = null;
      }
      this.currentPaymentLine = null;
    }
  },

  /**
   * Check if a completed transaction with the same order total exists in the last 60 minutes
   * within this session. Only matches state='done' so retries after errors are never blocked.
   * @param {Object} order - The current POS order
   * @returns {Object|null} - { minutes_ago, reference } if duplicate found, null otherwise
   */
  async _checkRecentDuplicateOrder(order) {
    try {
      const amount = parseFloat(order.get_total_with_tax().toFixed(2));
      const sessionId = this.pos.session?.id || null;
      if (!amount || !sessionId) return null;

      const result = await rpcRequest("/woodforest/check_duplicate", {
        amount,
        session_id: sessionId,
      });
      return result?.is_duplicate ? result : null;
    } catch (e) {
      console.warn("[DUP CHECK] Could not check for duplicate:", e);
      return null; // On error, allow payment to proceed
    }
  },

  _isPayrilliumPayment(paymentLine) {
    const lineName = paymentLine.payment_method_id?.name?.toLowerCase();
    const configName = this.paymentMethodName?.toLowerCase();
    return !!(configName && lineName === configName);
  },
  async validateOrder(force_validation) {
    window.__perfOuterStart = performance.now();
    this.executionId = generateExecutionId();
    const order = this.currentOrder;
    const total = order.get_total_with_tax();
    const lines = this.paymentLines;
    const isRefund = total < 0;

    // ── DIAGNOSTIC: Log all payment lines at validation start ──
    console.warn(
      `[validateOrder] START | total=${total}, isRefund=${isRefund}, lines=${lines.length}, _pendingTerminalTip=${order._pendingTerminalTip || 0}`,
    );
    for (const l of lines) {
      console.warn(
        ` [line] cid=${l.cid}, amount=${l.get_amount()}, status=${l.payment_status}, tx=${l.transaction_id || "(none)"}, _terminalTip=${l._terminalTip || 0}, method=${l.payment_method_id?.name}`,
      );
    }

    const flags = lines.map((l) => this._isPayrilliumPayment(l));
    const hasPayrilliumPayment = flags.some(Boolean);

    if (hasPayrilliumPayment) {
      // ── SINGLE FLAT LOOP: Collect pending Woodforest lines ONCE ──
      const pendingLines = lines.filter(
        (l, i) => flags[i] && l.payment_status !== "done" && !l.transaction_id,
      );

      console.warn(
        `[validateOrder] Woodforest pending lines: ${pendingLines.length}, done lines: ${lines.filter((l, i) => flags[i] && l.payment_status === "done").length}`,
      );

      // ── GUARD: Block if terminal is known to be offline ──
      if (pendingLines.length > 0 && this.pos.terminalStatus === "offline") {
        console.warn("[validateOrder] BLOCKED: Terminal is offline.");
        this.dialog.add(AlertDialog, {
          title: _t("Terminal Offline"),
          body: _t(
            "The terminal is not responding. Please check the connection and try again.",
          ),
        });
        return false;
      }

      if (isRefund) {
        // Refund path — process the first PENDING Woodforest line as refund.
        // Defense-in-depth: skip lines already marked done (e.g. if the setup
        // wrapper already processed this refund before calling us).
        const refundLine = lines.find(
          (l, i) => flags[i] && l.payment_status !== "done" && !l.transaction_id
        );
        if (refundLine) {
          try {
            await this._handleRefund(refundLine, total);
          } catch (error) {
            await this._handlePaymentError(error);
            return false;
          }
        }
      } else {
        // ── Process each pending line sequentially (no inner loop) ──
        for (let wfIdx = 0; wfIdx < pendingLines.length; wfIdx++) {
          const wfLine = pendingLines[wfIdx];
          const isFirstWoodforest = wfIdx === 0;
          const isLastWoodforest = wfIdx === pendingLines.length - 1;

          try {
            const _ept = wfLine.payment_method_id.use_payment_terminal;
            const _name = (wfLine.payment_method_id.name || "").toLowerCase();
            const hasTerminal =
              _ept === "woodforest" ||
              _ept === "payrillium" ||
              _name.includes("woodforest");
            const hasTokens =
              this.currentOrder.get_partner()?.payment_token_ids?.length > 0;

            if (!hasTerminal && !hasTokens) {
              this.dialog.add(AlertDialog, {
                title: "Payment method not available",
                body: "To use this payment method, you must either select a customer with saved cards or assign a terminal to this session.",
              });
              return false;
            }

            const proceed = await this._promptSavedCardOrContinue(
              order,
              wfLine,
              hasTerminal,
            );

            if (proceed) {
              console.warn(
                `[validateOrder] Processing line ${wfIdx + 1}/${pendingLines.length}: cid=${wfLine.cid}, amount=${wfLine.get_amount()}, isFirst=${isFirstWoodforest}, isLast=${isLastWoodforest}`,
              );
              const success = await this._processPayrilliumPayment(
                wfLine,
                hasTerminal,
                isFirstWoodforest,
                isLastWoodforest,
              );
              if (success === false) {
                console.warn(
                  `[validateOrder] Line ${wfIdx + 1} FAILED. Stopping validation.`,
                );
                return false;
              }
              console.warn(
                `[validateOrder] Line ${wfIdx + 1} SUCCESS. tx=${wfLine.transaction_id}, _terminalTip=${wfLine._terminalTip || 0}`,
              );
            } else {
              this.dialog.add(AlertDialog, {
                title: "Payment cancelled",
                body: "You must select a saved card or assign a terminal before proceeding with this payment.",
              });
              return false;
            }
          } catch (error) {
            await this._handlePaymentError(error);
            return false;
          }
        }
      }

      // After processing all terminal lines, commit tips from DB — source of truth.
      // tip_amount was saved per transaction on approval, so this works after any browser refresh.
      const doneWfLines = order.payment_ids.filter(
        (p) =>
          this._isPayrilliumPayment(p) &&
          p.payment_status === "done" &&
          p.transaction_id,
      );
      if (doneWfLines.length > 0) {
        try {
          const txIds = doneWfLines.map((p) => p.transaction_id);
          const transactions = await this.orm.call(
            "payment.transaction",
            "search_read",
            [[["provider_reference", "in", txIds]]],
            { fields: ["provider_reference", "tip_amount"] },
          );
          const rounding = this.currentOrder.currency?.rounding || 0.01;
          const tipByRef = {};
          let totalTip = 0;
          for (const tx of transactions) {
            const t = roundPrecision(tx.tip_amount || 0, rounding);
            tipByRef[tx.provider_reference] = t;
            totalTip += t;
          }
          totalTip = roundPrecision(totalTip, rounding);

          if (totalTip > 0) {
            console.warn(`[TIP FINALIZE] Total tip from DB: $${totalTip}`);
            // Add/update the tip product ORDER LINE for correct accounting.
            // This is what set_tip() does internally WITHOUT the payment line side effects
            // (set_tip() was creating fake "Online Payment" lines because all WF lines are done).
            const tipProductId = this.pos.config.tip_product_id?.id;
            if (tipProductId) {
              const tipProduct =
                this.pos.models["product.product"].get(tipProductId);
              if (tipProduct) {
                const existingTipLine = order
                  .get_orderlines()
                  .find((l) => l.product_id?.id === tipProductId);
                if (existingTipLine) {
                  existingTipLine.set_unit_price(totalTip);
                } else {
                  await this.pos.addLineToOrder(
                    { product_id: tipProduct, price_unit: totalTip },
                    order,
                    { merge: false },
                    false, // no configurator popup
                  );
                }
              }
            }
            // Always set the tip flags (receipt display + receipt screen banner):
            order.is_tipped = true;
            order.tip_amount = totalTip;

            for (const pl of doneWfLines) {
              const lineTip = tipByRef[pl.transaction_id] || 0;
              if (lineTip > 0) {
                const newAmount = roundPrecision(
                  pl.get_amount() + lineTip,
                  rounding,
                );
                pl.set_amount(newAmount);
                console.warn(
                  `[TIP FINALIZE] Line tx=${pl.transaction_id}: base $${pl.get_amount() - lineTip} + tip $${lineTip} = $${newAmount}`,
                );

                const allData = order._all_payment_data || [];
                const txId = pl.transaction_id;
                const metaEntry = allData.find(
                  (d) =>
                    d.transactionId && String(d.transactionId) === String(txId),
                );
                if (metaEntry) {
                  metaEntry._amount = newAmount;
                  metaEntry._tipAmount = lineTip;
                } else {
                  console.warn(
                    `[TIP FINALIZE] No metadata for tx=${txId}. Receipt may show wrong tip.`,
                  );
                }
              }
            }
          }
        } catch (e) {
          console.warn("Could not apply tips from DB:", e);
        }
      }

      // ── FINAL GUARD: Don't validate if there are still unprocessed Woodforest payments ──
      const stillPending = this.paymentLines.filter(
        (l, i) =>
          this._isPayrilliumPayment(l) &&
          l.payment_status !== "done" &&
          !l.transaction_id,
      );
      if (stillPending.length > 0) {
        console.warn(
          `[validateOrder] BLOCKED: ${stillPending.length} Woodforest line(s) still unprocessed.`,
        );
        for (const sp of stillPending) {
          console.warn(
            ` [blocked] cid=${sp.cid}, amount=${sp.get_amount()}, status=${sp.payment_status}`,
          );
        }
        this.dialog.add(AlertDialog, {
          title: _t("Payment incomplete"),
          body: _t(
            "There are pending Woodforest payments that have not been processed. Please complete or remove them before validating.",
          ),
        });
        return false;
      }

      console.warn(
        `[validateOrder] All Woodforest lines processed. Proceeding to super.validateOrder()`,
      );
    }

    const _perfSuper = performance.now();
    // Force validation=true: we already processed all Woodforest lines above.
    // This prevents pos_loyalty/_isOrderValid() from blocking when it checks
    // is_paid() — our lines are already done by this point.
    // This makes us independent of pos_loyalty/pos_hr without needing them
    // in __manifest__.py depends (which would break on instances without them).
    const res = await super.validateOrder(true);
    console.error(
      `[PERF] superValidate: ${(performance.now() - _perfSuper).toFixed(0)}ms`,
    );
    if (window.__perfOuterStart) {
      console.error(
        `[PERF] TOTAL auth→receipt: ${(performance.now() - window.__perfOuterStart).toFixed(0)}ms`,
      );
    }
    return res;
  },

  async _handleRefund(paymentLine, total) {
    return await this._withSpinner(async () => {
      // Declare outside try so catch block can access them for error logging
      let cardType = null;
      let reference = null;
      let transaction_id = null;
      let tokenCardId = null;

      try {
        // 1. Find the original refunded
        const executionId = generateExecutionId();
        const refundedLineBackend = await findRefundedOrderLine(
          this.currentOrder,
        );
        console.log(refundedLineBackend, "refundedLineBackend");

        const order_id = refundedLineBackend?.order_id;
        if (!order_id) throw new Error("Original order not found");
        const terminalId = this.terminalId;
        // 2. Get the original transaction
        const origTx = await getOriginalTransaction(order_id);
        cardType = origTx.cardType;
        tokenCardId = origTx.tokenCardId;
        reference = origTx.reference;
        transaction_id = origTx.transaction_id;

        console.log("getOriginalTransaction:", {
          cardType,
          terminalId,
          transaction_id,
          total,
          reference,
        });
        if (!cardType || !transaction_id || !(terminalId || tokenCardId)) {
          // terminalId may be a token or id of terminal used for payment
          throw new Error("Missing required refund data");
        }
        const amount = Math.abs(total).toFixed(2);
        console.log("Starting refund process for:", {
          cardType,
          transaction_id,
          amount,
          reference,
          tokenCardId,
        });
        // 3. Process the refund
        const sessionId = this._posService.session?.id || null;

        const isDebit = cardType === "DEBIT";
        const pendingStatus = isDebit
          ? "debit_refund_pending"
          : "credit_refund_pending";

        await this._createPayrilliumTransaction({
          reference: `${reference}${isDebit ? "rd" : "r"}`,
          provider_id: this.paymentProviderId,
          payment_method_id: paymentLine.payment_method_id.id,
          acquirer_reference: transaction_id,
          amount: parseFloat(amount),
          order_uid: this.currentOrder.uid,
          card_type: cardType,
          state: "pending",
          transaction_status: "in_progress",
          terminal_id: this.terminalId,
          execution_id: this.executionId || null,
          pos_session_id: sessionId,
        });

        // Evaluate whether to VOID or REFUND based on Cybersource application state
        let actionResult = { action: "refund" };
        try {
          actionResult = await this.orm.call(
            "payment.transaction",
            "get_pos_return_action",
            [transaction_id],
          );
          console.log(
            " [Decision Engine] POS Return Action for tx:",
            transaction_id,
          );
          console.log(" Gate triggered:", actionResult.gate);
          console.log(" Derived status:", actionResult.derived_status);
          console.log(" Recommended action:", actionResult.action);
          console.log(" Explanation:", actionResult.explanation_title);
          if (actionResult.explanation_lines) {
            actionResult.explanation_lines.forEach((line, i) =>
              console.log(` [${i + 1}]`, line),
            );
          }
        } catch (e) {
          console.warn(
            " [Decision Engine] Could not check return eligibility, defaulting to refund:",
            e,
          );
        }

        let result;
        // BLOCK if Decision Engine says no action is available (CANCELLED, VOIDED, REFUNDED, etc.)
        if (actionResult && actionResult.action === "none") {
          const reason =
            actionResult.explanation_title || "No action available";
          const lines = (actionResult.explanation_lines || []).join("\n");
          console.error(` [Decision Engine] BLOCKED — ${reason}\n${lines}`);
          throw new Error(
            ` Return Blocked by Decision Engine: ${reason}. ${lines}`,
          );
        }

        // Flag: refund/void request is about to be sent to the terminal.
        // If it fails with a comm error, we don't know if the money was returned.
        this._refundWasStarted = true;

        if (actionResult && actionResult.action === "void") {
          console.log(
            " [Decision Engine] → Processing VOID/Reversal (auth-only detected)",
          );
          const apiCall = isDebit
            ? this.payrilliumAPI.voidDebitAuthorize
            : this.payrilliumAPI.authReversal;

          result = await apiCall.call(
            this.payrilliumAPI,
            {
              payment_id: `${reference}${isDebit ? "vda" : "rv"}`,
              transaction_id: transaction_id,
              totalAmount: amount,
              executionId,
            },
            executionId,
            sessionId,
          );

          if (result && result.reversal_verified !== undefined) {
            result.refund_verified = result.reversal_verified;
          }
        } else {
          console.log(
            " [Decision Engine] → Processing REFUND (capture confirmed)",
          );
          result = await processRefund(this.payrilliumAPI, {
            cardType: cardType.toUpperCase(),
            paymentId: reference,
            transaction_id,
            amount,
            executionId,
            tokenCardId,
            sessionId,
          });
        }

        console.log("Refund result:", result);
        validatePayrilliumResponse(result);

        // Defense-in-depth: refund/void MUST be confirmed by the processor
        const isVerified = result?.refund_verified || result?.reversal_verified;
        if (!isVerified) {
          console.error(
            " [Safety Check] Refund/void NOT verified by processor:",
            result,
          );
          throw new Error(
            "Refund was not confirmed by the processor. The terminal may be offline or the transaction was rejected.",
          );
        }

        console.log(" Refund verified by processor");

        // Show refund confirmation on terminal (fire-and-forget)
        const formattedAmount = `$${parseFloat(Math.abs(amount)).toFixed(2)}`;
        this.payrilliumAPI
          .showApproved(
            {
              title: "Refunded",
              message: `Payment Refunded - ${formattedAmount}`,
              timeout: "5",
            },
            this.executionId,
            sessionId,
          )
          .catch((e) => {
            console.warn(
              "Failed to show refund message on terminal in background",
              e,
            );
          });

        // 4. Store refund data
        const refundData = prepareRefundData(result, {
          cardType,
          transaction_id,
          terminalId,
        });
        console.log("Prepared refund data:", refundData);
        this.currentOrder.set_extra_payment_data(refundData);
        const suffix = cardType === "DEBIT" ? "rd" : "r";
        // 5. Finalize refund transaction
        await this._finalizeRefundTransaction({
          paymentLine,
          cardType,
          transaction_id,
          payment_id: `${reference}${suffix}`,
          amount,
          executionId: this.executionId,
        });
        return true;
      } catch (error) {
        console.error("Refund error:", error);
        if (cardType && reference && transaction_id) {
          const isDebit = cardType === "DEBIT";
          // If refund/void was sent but outcome is uncertain → in_progress for recovery
          // Uncertain: comm/cloud error OR operator aborted while refund was in flight
          const isUncertain = this._refundWasStarted &&
            (error.terminalConnectionError || error.isCommError || this._payrilliumAborted || error.cancelled);
          const sessionId = this.pos.session?.id || null;
          await this._createPayrilliumTransaction({
            reference: `${reference}${isDebit ? "rd" : "r"}`,
            provider_id: this.paymentProviderId,
            payment_method_id: paymentLine.payment_method_id.id,
            acquirer_reference: transaction_id,
            amount: parseFloat(Math.abs(total).toFixed(2)),
            order_uid: this.currentOrder.uid,
            card_type: cardType,
            state: isUncertain ? "pending" : "error",
            transaction_status: isUncertain ? "in_progress" : "error",
            error_message: error.message || "Unknown refund error",
            terminal_id: this.terminalId,
            execution_id: this.executionId || null,
            pos_session_id: sessionId,
          });
          if (isUncertain) {
            console.warn(
              "[RECOVERY] Refund/void outcome uncertain. Marked as in_progress for recovery.",
            );
            try {
              await this._posService.fetch_stuck_transactions();
            } catch (e) {
              console.warn("[Recovery] Failed to auto-refresh stuck transactions:", e);
            }
          }
        } else {
          console.warn(
            " Cannot log error transaction to Odoo — original transaction data not available yet",
          );
        }
        throw error;
      }
    });
  },

  async _finalizeRefundTransaction({
    paymentLine,
    cardType,
    transaction_id,
    payment_id,
    amount,
    executionId,
  }) {
    paymentLine.transaction_id = transaction_id;
    paymentLine.set_payment_status("done");
    paymentLine.provider_id = this.paymentProviderId || "";
    console.log("paymentLine212", paymentLine);

    const isDebit = cardType === "DEBIT";
    const finalStatus = isDebit ? "debit_refunded" : "credit_refunded";

    await this._createPayrilliumTransaction({
      reference: payment_id,
      provider_id: this.paymentProviderId,
      payment_method_id: paymentLine.payment_method_id.id,
      acquirer_reference: transaction_id,
      amount: parseFloat(amount),
      order_pos_reference: paymentLine?.pos_order_id?.pos_reference,
      card_type: cardType,
      state: "done",
      transaction_status: finalStatus,
      terminal_id: this.terminalId,
      execution_id: this.executionId || null,
      pos_session_id: this._posService.session?.id || null,
    });
    if (this.currentOrder?.get_due?.() === 0) {
      return super.validateOrder(false);
    }
    const sessionId = this._posService.session?.id || null;
    try {
      const formattedAmount = this._posService.formatCurrency
        ? this._posService.formatCurrency(amount)
        : amount.toFixed(2);
      // 18.2: Fire and forget: We do NOT await this.
      showSuccessMessage(
        this.payrilliumAPI,
        this.terminalMessages,
        formattedAmount,
        executionId,
        sessionId,
      ).catch((e) => {
        console.warn(e);
      });
    } catch (e) {
      console.warn("Failed to show success message on terminal", e);
    }
    // 18.1: Removed showEmptyBasket from success path.
  },

  async _finalizeValidation() {
    const payrilliumLines = this.paymentLines.filter(
      (line) =>
        line.payment_method_id.name?.toLowerCase() === this.paymentMethodName,
    );
    for (const line of payrilliumLines) {
      // $0 lines are auto-marked done without terminal interaction — no transaction_id expected
      if (!line.transaction_id && line.get_amount() !== 0) {
        const message = `Woodforest payment without transaction ID: ${line.cid}`;
        console.error(message);
        throw new Error(message);
      }
    }
    const button = document.querySelector(".paymentmethods .selected-button");
    if (button && !button.classList.contains("payrillium-button")) {
      button.classList.add("payrillium-button");
      const icon = document.createElement("img");
      icon.src = "/pos_woodforest/static/description/icon.png";
      button.prepend(icon);
    }
    return await super._finalizeValidation();
  },

  /**
   * Handles the cancellation of a payment on the terminal
   * @param {Object} event - The deletion event
   * @returns {Promise<boolean>} - True if cancellation was successful, false otherwise
   */
  async deletePaymentLine(event) {
    const paymentLine = event?.detail;
    if (!paymentLine) {
      return super.deletePaymentLine(...arguments);
    }

    if (!this._isPayrilliumPayment(paymentLine)) {
      return super.deletePaymentLine(...arguments);
    }

    try {
      if (paymentLine.transaction_id) {
        const executionId = generateExecutionId();
        const result = await this.payrilliumAPI.voidCapture(rpcRequest, {
          transaction_id: paymentLine.transaction_id,
          execution_id: executionId,
        });

        validatePayrilliumResponse(result);
      }

      paymentLine.set_payment_status("retry");
      return super.deletePaymentLine(...arguments);
    } catch (error) {
      this._handlePaymentError(error);
      return false;
    }
  },

  /**
   * Handles the payment request process, including terminal interaction
   * @param {Object|Event} lineOrEvent - Payment line object or event containing payment details
   * @returns {Promise<boolean>} - True if payment was successful, false otherwise
   */
  async sendPaymentRequest(line) {
    const methodName = line.payment_method_id?.name?.toLowerCase();
    const isOurMethod =
      this.paymentMethodName &&
      methodName === this.paymentMethodName.toLowerCase();

    if (!isOurMethod) {
      if (super.sendPaymentRequest) {
        return super.sendPaymentRequest(line);
      }
      return super.send_payment_request?.(line);
    }

    // ── Remove $0 lines — terminal rejects zero-amount ──
    if (isOurMethod && line.get_amount() === 0 && line.payment_status !== "done") {
      console.warn("[sendPaymentRequest] Removing ghost $0 Woodforest line:", line.uuid);
      this.currentOrder.remove_paymentline(line);
      return true;
    }

    if (line.payment_status === "done" || line.transaction_id) {
      if (this.currentOrder?.get_due?.() === 0) {
        return super.validateOrder?.(false);
      }
      return true;
    }

    const order = this.currentOrder;
    order.select_paymentline(line);
    const { hasTerminal, hasTokens } =
      await this._checkPayrilliumAvailability(order);
    if (!hasTerminal && !hasTokens) {
      this.dialog.add(AlertDialog, {
        title: "Payment method not available",
        body: "To use this payment method, you must either select a customer with saved cards or assign a terminal to this session.",
      });
      if (order?.remove_paymentline) order.remove_paymentline(line);

      return false;
    }

    try {
      const total = order.get_total_with_tax();
      const isRefund = total < 0;
      if (line.payment_status === "retry" && !line.transaction_id) {
        // ── Offline guard (same as validate wrapper) ──
        if (this.pos.terminalStatus === "offline") {
          this.dialog.add(AlertDialog, {
            title: _t("Terminal Offline"),
            body: _t(
              "The terminal is not responding. Please check the connection and try again.",
            ),
          });
          return false;
        }
        await this._processPayrilliumPayment(line, hasTerminal);
        if (order?.get_due?.() === 0) {
          return super.validateOrder?.(false);
        }
        return true;
      }
      // Fresh Woodforest line — will be processed by validateOrder() when user clicks Validate.
      // Return true so Odoo treats the line as "handled" and doesn't remove it.
      return true;
    } catch (error) {
      console.error("Payment processing error:", error);
      await this._handlePaymentError(error);
      return false;
    }
  },

  async _handlePaymentError(error) {
    console.error("Payment error details:", error);

    // Ensure we have a structured error object
    const errorObj = typeof error === "string" ? { message: error } : error;

    // Log the full response for debugging
    if (errorObj.originalResponse) {
      console.log("Original terminal response:", errorObj.originalResponse);
    }

    // Use the friendly message for the UI popup
    const errorMessage =
      errorObj.message || // Friendly UI message ("Verify connection...")
      errorObj.originalResponse?.data?.data?.message ||
      errorObj.originalResponse?.message ||
      "An unexpected error occurred";

    // Set payment line status to retry if we have a payment line
    // and force the UI to focus on it so the cashier sees the Retry button.
    const paymentLine = errorObj.paymentLine || this.currentPaymentLine;
    if (paymentLine) {
      paymentLine.set_payment_status("retry");
      const order = paymentLine.pos_order_id || this.currentOrder;
      if (order && typeof order.select_paymentline === "function") {
        try {
          order.select_paymentline(paymentLine);
          console.log("Automatically focused the failed payment line.");
        } catch (e) {
          console.warn("Failed to auto-focus payment line:", e);
        }
      }
    }

    // Show popup using the same format as other Odoo modules
    this.dialog.add(AlertDialog, {
      title: errorObj.cancelled ? "Cancelled operation" : "Payment error",
      body: errorMessage,
    });
  },

  async _checkPayrilliumAvailability(order) {
    const terminal = this._posService.config.payrillium_terminal_serial;
    console.log("terminal", terminal);
    const partner = order.get_partner();

    const isString = typeof terminal === "string";
    console.log("isString", isString);

    const hasTerminal = terminal && isString && terminal.length > 0;
    if (hasTerminal) {
      console.log(" Terminal assigned to session.");
    }

    if (!partner || !partner.id) {
      console.warn(" No customer selected, and terminal assigned.");
      return { hasTerminal, hasTokens: false };
    }

    const tokens = await this.orm.call(
      "payment.token",
      "search_read",
      [
        [
          ["partner_id", "=", partner.id],
          ["active", "=", true],
          ["provider_id.code", "=", "woodforest"],
        ],
        ["id"],
      ],
      {},
    );

    const hasTokens = tokens.length > 0;
    if (!hasTokens) {
      console.warn(" Customer selected but has no active tokens.");
    }

    return { hasTerminal, hasTokens };
  },

  async _promptSavedCardOrContinue(order, paymentLine, hasTerminal) {
    console.log(" Checking for saved cards for customer...");

    const partner = order.get_partner();
    if (!partner || !partner.id) {
      console.log(" No customer assigned to order, skipping token check.");
      return true;
    }

    try {
      const tokens = await this.orm.call(
        "payment.token",
        "search_read",
        [
          [
            ["partner_id", "=", partner.id],
            ["active", "=", true],
            ["provider_id.code", "=", "woodforest"],
            ["token_type", "in", ["tokenized_credit", "tokenized_debit"]],
          ],
          ["id", "payment_details", "provider_ref", "token_type"],
        ],
        {},
      );

      console.log(` ${tokens.length} token(s) found.`);
      console.log(" Tokens data:", tokens);

      if (!tokens.length) {
        console.log(" No saved cards found for this customer.");
        return true;
      }

      // Map tokens to selection list format
      const tokenList = tokens.map((token) => {
        const label =
          token.payment_details || `****${token.provider_ref.slice(-4)}`;
        console.log(` Token ${token.id}: ${label}`);
        return {
          id: token.id,
          label: label,
          isSelected: false,
          item: token,
        };
      });

      console.log(" Token list for SavedCardSelectionPopup:", tokenList);
      console.log(
        " About to call makeAwaitable() with SavedCardSelectionPopup:",
        {
          title: _t("Select a saved card"),
          list: tokenList,
        },
      );

      const selectedToken = await makeAwaitable(
        this.dialog,
        SavedCardSelectionPopup,
        {
          title: _t("Select a saved card"),
          list: tokenList,
          newCardLabel: _t("Pay with another / new card"),
        },
      );

      console.log(" makeAwaitable() returned:", selectedToken);
      console.log(" selectedToken type:", typeof selectedToken);
      console.log(" selectedToken value:", selectedToken);

      // selectedToken meanings:
      // - token object → user picked a saved card
      // - null → user clicked "Pay with another / new card"
      // - undefined → user closed the popup (X / ESC) => cancel flow
      if (selectedToken === undefined) {
        console.log(
          " User closed saved-card popup without choosing; cancelling payment.",
        );
        return false;
      }

      if (selectedToken) {
        console.log(" Token selected:", selectedToken);
        paymentLine.payment_token_id = selectedToken;
        return true;
      } else {
        console.log(" No token selected or user cancelled");
        if (!hasTerminal) {
          console.log(" No terminal and user cancelled token selection.");
          return false;
        }
        console.log(" User chose to pay with another / new card (no token).");
        return true;
      }
    } catch (err) {
      console.warn(" Error retrieving or selecting saved cards:", err);
      return true;
    }
  },

  async _processPayrilliumTokenPayment(paymentLine, hasTerminal) {
    return await this._withSpinner(async () => {
      try {
        const order = paymentLine.pos_order_id || this.currentOrder;
        const token = paymentLine.payment_token_id;

        if (!token || !token.id || !token.provider_ref) {
          console.error(" Invalid token object in payment line:", token);
          throw new Error("Invalid payment token");
        }

        if (!order) {
          console.error(" No order found in paymentLine or currentOrder");
          throw new Error("No order found");
        }

        const amount = paymentLine.get_amount();
        const currency = "USD";

        console.log(" Sending backend request to /woodforest/token/authorize", {
          token_id: token.id,
          amount,
          currency,
          provider_id: this.paymentProviderId,
        });

        const result = await rpcRequest("/woodforest/token/authorize", {
          token_id: token.id,
          amount,
          currency,
          provider_id: this.paymentProviderId,
        });

        console.log("result", result);

        if (!result.success) {
          console.error(" Authorization failed:", result.message);
          throw new Error(result.message || "Authorization failed");
        }
        validatePayrilliumResponse(result.authorization_data);

        const data = result.authorization_data.data || {};

        const transactionId = data.token || "N/A";
        const clientRef = data.token || data.metadata?.id;
        if (!clientRef) {
          throw new Error(result.message || "Authorization failed");
        }
        console.log(" Authorization succeeded:", {
          transactionId,
          clientRef,
        });

        const metadata = result.authorization_data?.data?.metadata || {};
        const processor = metadata.processorInformation || {};
        const paymentInfo = metadata.paymentInformation || {};
        const tokenCard = paymentInfo.tokenizedCard || {};
        const formattedDate = (
          metadata.submitTimeUtc || new Date().toISOString()
        )
          .replace("T", " ")
          .replace("Z", "");

        const messageForReceipt = {
          approvalCode: processor.approvalCode || "N/A",
          cardVendor: CARD_VENDOR[tokenCard.type] || "N/A",
          cardNumber: "****",
          transactionId: processor.transactionId || transactionId || "N/A",
          terminalId: "N/A",
          entryMode: "TOKEN",
          date: formattedDate,
          status: metadata.status || "AUTHORIZED",
          rawBody: metadata,
        };
        const rawCardType = normalizeCardType(result.data?.data || {});
        const tokenizedCardType = ["CREDIT", "DEBIT"].includes(rawCardType)
          ? `tokenized_${rawCardType.toLowerCase()}`
          : "tokenized_credit";

        const parsedEMV = {};
        storeTransactionMetadata(
          order,
          paymentLine,
          transactionId,
          messageForReceipt,
          tokenizedCardType,
          parsedEMV,
          this.paymentProviderId,
        );

        this.transactionDataToSave = {
          reference: clientRef,
          provider_id: this.paymentProviderId,
          payment_method_id: paymentLine.payment_method_id.id,
          acquirer_reference: transactionId,
          amount: parseFloat(amount.toFixed(2)),
          order_uid: order.uid,
          card_type: tokenizedCardType,
          state: "done",
          payrillium_card_token: token.provider_ref,
          execution_id: this.executionId,
          pos_session_id: this._posService.session?.id || null,
        };

        console.log(" transactionDataToSave:", this.transactionDataToSave);
        paymentLine.transaction_id = transactionId;
        paymentLine.set_payment_status("done");
        await this._createPayrilliumTransaction(this.transactionDataToSave);
        console.log(" Payment transaction created successfully.");
        return true;
      } catch (error) {
        console.error(" Error processing token payment:", error);
        throw error;
      }
    });
  },

  /**
   * Create a payment.token from the auth response when tokenization was requested
   * @param {Object} paymentLine - The payment line
   * @param {Object} authResponse - The response from the auth endpoint
   */
  async _createPaymentTokenFromAuthResponse(paymentLine, authResponse) {
    try {
      console.log(" Checking auth response for token information...");

      // Extract token information from the auth response
      // Based on the logs, the structure is: response.data.data.message.rawBody.tokenInformation.instrumentIdentifier.id
      const rawBody = authResponse?.data?.data?.message?.rawBody;
      const tokenInfo = rawBody?.tokenInformation;
      const instrumentIdentifier = tokenInfo?.instrumentIdentifier;
      const message = authResponse?.data?.data?.message;

      console.log(" Token extraction:", {
        hasRawBody: !!rawBody,
        hasTokenInfo: !!tokenInfo,
        hasInstrumentIdentifier: !!instrumentIdentifier,
        instrumentIdentifierId: instrumentIdentifier?.id,
        cardNumber: message?.cardNumber,
        cardVendor: message?.cardVendor,
      });

      if (!instrumentIdentifier || !instrumentIdentifier.id) {
        console.log(
          " No token information found in auth response - tokenization may not have occurred",
        );
        return;
      }

      const tokenId = instrumentIdentifier.id;
      const cardNumber = message?.cardNumber || "****";
      const cardVendor = message?.cardVendor || "CARD";
      const order = paymentLine.pos_order_id || this.currentOrder;
      const partner = order.get_partner ? order.get_partner() : null;

      if (!partner || !partner.id) {
        console.warn(
          " ️ No customer/partner found - cannot create payment token",
        );
        return;
      }

      // Format payment_details similar to wizard: "VISA ****5228"
      const paymentDetails = `${cardVendor} ****${cardNumber}`;

      console.log(" Creating payment token from auth response:", {
        tokenId: tokenId,
        cardNumber: cardNumber,
        cardVendor: cardVendor,
        paymentDetails: paymentDetails,
        partnerId: partner.id,
        partnerName: partner.name,
        providerId: this.paymentProviderId,
      });

      // Call backend to create the payment.token
      // This will create a Payment Instrument in Cybersource using instrumentIdentifier + billTo
      const result = await rpcRequest("/woodforest/token/create_from_auth", {
        instrument_identifier_id: tokenId, // Changed from provider_ref to instrument_identifier_id
        payment_details: paymentDetails,
        partner_id: partner.id,
        provider_id: this.paymentProviderId,
        token_type: "tokenized_credit", // Default or determine from metadata
      });

      console.log("result2", result);

      if (result.success) {
        // Show notification if provided by backend
        if (result.notification) {
          this.env.services.notification.add(result.notification.message, {
            title: result.notification.title,
            type: result.notification.type || "success",
          });
        }

        if (result.already_exists) {
          console.log(
            " Payment token already exists (not creating duplicate):",
            {
              tokenId: result.token_id,
              paymentDetails: paymentDetails,
              message: result.message,
              " Card already saved for customer": partner.name,
            },
          );
        } else {
          console.log(" Payment token created successfully:", {
            tokenId: result.token_id,
            paymentInstrumentId: result.payment_instrument_id,
            paymentDetails: paymentDetails,
            " Token saved for customer": partner.name,
          });
        }
      } else {
        console.error(" Failed to create payment token:", result.message);
        // Show notification if billing address is incomplete
        if (result.missing_fields && result.missing_fields.length > 0) {
          const missingFieldsList = result.missing_fields.join(", ");
          this.dialog.add(AlertDialog, {
            title: _t("Cannot Save Card"),
            body: _t(
              "Cannot save card for customer '%s'. Please complete the billing address. Missing fields: %s",
              partner.name || _t("Customer"),
              missingFieldsList,
            ),
          });
        }
        // Don't throw - token creation failure shouldn't break the payment flow
      }
    } catch (error) {
      console.error(" Error creating payment token from auth response:", error);
      // Don't throw - token creation failure shouldn't break the payment flow
    }
  },
});

console.log(" POS Woodforest - Payment Screen READY (Odoo 18)");
