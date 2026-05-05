/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { mapTSSDataToReceipt } from "@pos_woodforest/js/utils";

export class RecoveryService {
  constructor(env) {
    this.pos = env.pos;
    this.orm = env.services.orm;
    this.notification = env.services.notification;
    this.ui = env.services.ui;
  }

  // ─── PUBLIC ENTRY POINT ──────────────────────────────────────────────────

  async processTransaction(tx) {
    this.ui.block({ message: _t("Verifying payment status...") });
    try {
      // ── STEP A: Call Cybersource to get the LIVE status of this transaction ──
      const tx_status = await this.orm.call(
        "payment.transaction",
        "action_woodforest_check_status",
        [tx.id],
        { context: { silent: true } },
      );

      const engineVerdict = (
        tx_status?.engine_internal_status ||
        tx_status?.params?.engine_internal_status ||
        ""
      ).toLowerCase();
      const liveData = tx_status.status_data || tx_status?.params?.status_data;

      // ── STEP B: Re-read tx from DB — action_woodforest_check_status may have
      // updated provider_reference and pos_receipt_data that the stale tx object
      // (loaded at POS startup) doesn't have.
      try {
        const freshFields = await this.orm.call(
          "payment.transaction",
          "read",
          [[tx.id]],
          { fields: ["provider_reference", "pos_receipt_data", "amount"] },
        );
        if (freshFields?.[0]) {
          tx.provider_reference = freshFields[0].provider_reference || false;
          tx.pos_receipt_data = freshFields[0].pos_receipt_data || false;
          // Use the DB amount — this is what we actually authorized
          tx.amount = freshFields[0].amount;
        }
      } catch (e) {
        console.warn("[Recovery] Could not re-read tx after status check:", e);
      }

      // ── STEP C: Also verify the ACTUAL amount Cybersource charged ──
      // liveData contains the TSS response with orderInformation.amountDetails
      let cybersourceAmount = tx.amount; // fallback to our DB amount
      if (liveData) {
        const liveAmount = parseFloat(
          liveData?.orderInformation?.amountDetails?.totalAmount ||
            liveData?.orderInformation?.amountDetails?.authorizedAmount ||
            0,
        );
        if (liveAmount > 0) {
          cybersourceAmount = liveAmount;
          if (Math.abs(cybersourceAmount - tx.amount) > 0.01) {
            console.warn(
              `[Recovery] Amount mismatch! DB=$${tx.amount}, Cybersource=$${cybersourceAmount}. Using Cybersource amount.`,
            );
          }
        }
      }
      // Store verified amount on tx for use in reconstruction
      tx._verifiedAmount = cybersourceAmount;

      // ── REFUND RECOVERY ─────────────────────────────────────────────────
      // Refunds don't need order reconstruction — the original order already exists.
      // We just need to verify the refund status and notify the cashier.
      const isRefundTx = tx.reference && /r(d*)$/.test(tx.reference);

      if (isRefundTx) {
        const refAmount = this.pos.env.utils.formatCurrency(tx.amount || 0);
        if (["captured", "refunded", "credit_refunded", "debit_refunded"].includes(engineVerdict)) {
          // Refund confirmed by Cybersource
          try {
            await this.orm.write("payment.transaction", [tx.id], {
              state: "done",
              transaction_status: engineVerdict === "captured" ? "credit_refunded" : engineVerdict,
            });
          } catch (e) {
            console.warn("[Recovery] Failed to update refund tx:", e);
          }
          this.notification.add(
            _t("Refund of " + refAmount + " was confirmed by the bank."),
            { type: "success" },
          );
        } else if (["error", "voided", "reversed", "cancel"].includes(engineVerdict)) {
          // Refund was NOT processed
          try {
            await this.orm.write("payment.transaction", [tx.id], {
              state: "error",
              transaction_status: "error",
            });
          } catch (e) {
            console.warn("[Recovery] Failed to update refund tx:", e);
          }
          this.notification.add(
            _t("Refund of " + refAmount + " was NOT processed. The customer was not charged back."),
            { type: "warning", sticky: true },
          );
        } else {
          this.notification.add(
            _t("Refund of " + refAmount + " is still being processed. Please wait and try again."),
            { type: "warning" },
          );
          return; // Don't remove — still in progress
        }
        this.removeTransactionFromList(tx.id);
        return;
      }

      // ── AUTH RECOVERY (original flow) ───────────────────────────────────
      if (["captured"].includes(engineVerdict)) {
        // Money was captured — always reconstruct (single or multi-line)
        await this.attemptReconstruction(tx, liveData, "captured");
      } else if (engineVerdict === "auth_hold") {
        // Authorized but not captured — do not reconstruct, manager must void
        this.notification.add(
          _t(
            "Payment authorized but not captured. A void may be needed. Please check with a manager.",
          ),
          { type: "warning", sticky: true },
        );
        this.removeTransactionFromList(tx.id);
      } else if (engineVerdict === "refunded") {
        this.notification.add(_t("This payment was already refunded."), {
          type: "info",
        });
        this.removeTransactionFromList(tx.id);
      } else if (
        ["error", "voided", "reversed", "cancel"].includes(engineVerdict)
      ) {
        // Stuck tx failed — but sister txs may have succeeded (multi-line partial fail).
        // attemptReconstruction handles the "no sisters" case gracefully.
        await this.attemptReconstruction(tx, liveData, "failed");
      } else {
        this.notification.add(_t("Still processing. Please wait."), {
          type: "warning",
        });
      }
    } catch (e) {
      console.error("[Recovery Error]", e);
      this.notification.add(_t("Could not verify payment. Please try again."), {
        type: "danger",
      });
    } finally {
      this.ui.unblock();
    }
  }

  // ─── MAIN RECONSTRUCTION ─────────────────────────────────────────────────

  /**
   * Rebuild a POS order from a stuck payment transaction's snapshot.
   *
   * Multi-line handling:
   * - Queries "sister" payment.transactions (same pos_order_uid, already done)
   * and adds them as DONE payment lines (money already collected).
   * - Adds the stuck tx as DONE (verdict="captured") or RETRY (verdict="failed").
   *
   * Single-line fallback:
   * - verdict="captured" → same flow (auto-validate → receipt)
   * - verdict="failed" + no sisters → just remove from list + notify
   *
   * @param {Object} tx - Stuck payment.transaction read object (refreshed after status check)
   * @param {Object} tssData - Live Cybersource TSS data for the stuck tx (may be null)
   * @param {string} verdict - "captured" | "failed"
   */
  async attemptReconstruction(tx, tssData = null, verdict = "captured") {
    const isSuccess = verdict === "captured";

    if (!tx.pos_order_snapshot_json) {
      this.notification.add(
        _t(
          "Payment of $" +
            tx.amount +
            " found, but item details could not be restored. Please note ID: " +
            (tx.provider_reference || tx.reference),
        ),
        { type: "warning", sticky: true },
      );
      this.removeTransactionFromList(tx.id);
      return;
    }

    try {
      // ── 1. PARSE SNAPSHOT ───────────────────────────────────────────────
      const snapshot = JSON.parse(tx.pos_order_snapshot_json);
      const snapshotOrder = snapshot["pos.order"]?.[0];
      if (!snapshotOrder) throw new Error("Order data missing in snapshot");

      // ── 2. FETCH SISTER TRANSACTIONS ────────────────────────────────────
      // Sisters = other done/authorized txs for the same POS order.
      // tip_amount and pos_receipt_data are stored on each tx at auth time.
      let sisterTxs = [];
      if (tx.pos_order_uid) {
        sisterTxs = await this.orm.call(
          "payment.transaction",
          "search_read",
          [
            [
              ["pos_order_uid", "=", tx.pos_order_uid],
              ["id", "!=", tx.id],
              ["state", "in", ["done", "authorized"]],
            ],
          ],
          {
            fields: [
              "id",
              "amount",
              "provider_reference",
              "reference",
              "tip_amount",
              "pos_receipt_data",
            ],
          },
        );
      }

      console.log(
        `[Recovery] Stuck: ${tx.reference} | Sisters: ${sisterTxs.length} | Verdict: ${verdict}`,
      );

      // ── Edge case: single-line fail → nothing to recover ────────────────
      if (!isSuccess && sisterTxs.length === 0) {
        this.notification.add(_t("Payment not completed."), { type: "info" });
        this.removeTransactionFromList(tx.id);
        return;
      }

      // ── 3. GET OR REUSE EXISTING ORDER ──────────────────────────────────
      // If the order was restored from IndexedDB (after F5/refresh), reuse it.
      // This avoids localDeleteCascade which crashes Owl because
      // PaymentScreen.currentOrder reads from props.orderUuid, not pos.get_order().
      const orderModel = this.pos.models["pos.order"];
      let recovery_order = null;
      let isReused = false;

      // Try to find existing order by snapshot ID or UUID
      let existing = orderModel.get(snapshotOrder.id);
      if (!existing && snapshotOrder.uuid) {
        existing = orderModel.getBy("uuid", snapshotOrder.uuid);
      }

      if (existing) {
        // Reuse — clear its stale payment lines (they have wrong amounts/statuses from IndexedDB)
        recovery_order = existing;
        isReused = true;
        const staleLines = [...recovery_order.payment_ids];
        for (const line of staleLines) {
          recovery_order.remove_paymentline(line);
        }
        console.log(
          `[Recovery] Reusing existing order ${recovery_order.uuid} — cleared ${staleLines.length} stale payment lines`,
        );
      } else {
        // Full crash / IndexedDB lost — create new order
        recovery_order = this.pos.add_new_order();
        console.log(
          "[Recovery] Creating new recovery order (no existing order found)...",
        );
      }

      // Make it the active order
      this.pos.set_order(recovery_order);

      // ── 4. RESTORE ORDER METADATA ──────────────────────────────────────
      if (snapshotOrder.partner_id) {
        const partner = this.pos.models["res.partner"].get(
          snapshotOrder.partner_id,
        );
        if (partner) recovery_order.set_partner(partner);
      }
      if (snapshotOrder.fiscal_position_id) {
        const fp = this.pos.models["account.fiscal.position"].get(
          snapshotOrder.fiscal_position_id,
        );
        if (fp) recovery_order.fiscal_position_id = fp;
      }

      // ── 5. RECONSTRUCT PRODUCT LINES (only for new orders) ──────────────
      // When reusing, the product lines are already correct from IndexedDB.
      if (!isReused) {
        console.log("[Recovery] Reconstructing product lines...");
        for (const line of snapshot["pos.order.line"] || []) {
          const product = this.pos.models["product.product"].get(
            line.product_id,
          );
          if (product) {
            const taxes = (line.tax_ids || [])
              .map((id) => this.pos.models["account.tax"].get(id))
              .filter(Boolean);
            await this.pos.addLineToOrder(
              {
                product_id: product,
                qty: line.qty,
                price_unit: line.price_unit,
                discount: line.discount,
                tax_ids: taxes.map((t) => ["link", t]),
              },
              recovery_order,
              { merge: false },
              false,
            );
          }
        }
      }

      recovery_order.is_recovery_order = true;

      // ── 5b. RESTORE RECEIPT DATA FROM SNAPSHOT ───────────────────────────
      // The snapshot contains _all_payment_data with full terminal receipt info
      // (card type, approval code, entry mode, etc.) for each sister payment.
      if (snapshotOrder._all_payment_data) {
        try {
          const allData =
            typeof snapshotOrder._all_payment_data === "string"
              ? JSON.parse(snapshotOrder._all_payment_data)
              : snapshotOrder._all_payment_data;
          recovery_order._all_payment_data = allData;
          console.warn(
            `[Recovery] Restored _all_payment_data: ${allData.length} entries from snapshot`,
          );
        } catch (e) {
          console.warn(
            "[Recovery] Failed to parse _all_payment_data from snapshot:",
            e,
          );
        }
      }

      // ── 6. GET WOODFOREST PAYMENT METHOD ────────────────────────────────
      const PaymentMethod = this.pos.models["pos.payment.method"].find(
        (p) => p.use_payment_terminal === "woodforest",
      );
      if (!PaymentMethod)
        throw new Error("Woodforest Payment Method not found.");

      // ── 7. ADD SISTER PAYMENT LINES (already paid) ──────────────────────
      // Each sister tx.amount already includes tip (base + tip_amount stored at auth).
      // We mark each line DONE so the order correctly reflects prior payments.
      for (const sister of sisterTxs) {
        console.log(
          `[Recovery] Adding sister: ${sister.reference} | $${sister.amount} (tip: $${sister.tip_amount || 0})`,
        );
        const sisterLine = recovery_order.add_paymentline(PaymentMethod);
        // Set BASE amount (without tip) — TIP FINALIZE in validateOrder will add tip from DB
        const sisterBase = parseFloat(
          ((sister.amount || 0) - (sister.tip_amount || 0)).toFixed(2),
        );
        sisterLine.set_amount(sisterBase);
        sisterLine.set_payment_status("done");
        // Sisters always have provider_reference (Cybersource ID) since they were authorized
        if (sister.provider_reference) {
          sisterLine.transaction_id = sister.provider_reference;
        }
        sisterLine._terminalTip = sister.tip_amount || 0; // show tip in PaymentScreen UI
        sisterLine._isRecoveryLine = true; // Guard: prevent stale cleanup (18.4) from removing this
        console.warn(
          `[Recovery] Sister line created: base=$${sisterBase}, dbAmount=$${sister.amount}, tip=$${sister.tip_amount || 0}, tx=${sisterLine.transaction_id}, status=${sisterLine.payment_status}`,
        );
      }

      // ── 8. ADD STUCK TX PAYMENT LINE ────────────────────────────────────
      const verifiedAmount = tx._verifiedAmount || tx.amount;
      console.log(
        `[Recovery] Adding stuck tx (${verdict}): ${tx.reference} | DB=$${tx.amount}, verified=$${verifiedAmount}`,
      );
      const stuckLine = recovery_order.add_paymentline(PaymentMethod);
      stuckLine._isRecoveryLine = true; // Guard: prevent stale cleanup (18.4) from removing this

      // Set transaction_id from the fresh provider_reference (re-read after status check)
      if (tx.provider_reference) {
        stuckLine.transaction_id = tx.provider_reference;
      }
      console.warn(
        `[Recovery] Stuck line tx_id=${stuckLine.transaction_id} (type: ${typeof stuckLine.transaction_id}), provider_ref=${tx.provider_reference}`,
      );

      if (isSuccess) {
        // ── CALCULATE BASE vs TIP for the stuck line ────────────────────
        // Cybersource tells us the TOTAL authorized ($2.40) but NOT the tip breakdown.
        // We derive it: base = orderTotal - sistersBase, tip = verifiedAmount - base.
        const orderProductsTotal = parseFloat(
          (snapshotOrder.amount_total || 0).toFixed(2),
        );
        const sistersBaseTotal = sisterTxs.reduce(
          (sum, s) =>
            sum +
            parseFloat(((s.amount || 0) - (s.tip_amount || 0)).toFixed(2)),
          0,
        );
        const stuckBase = parseFloat(
          Math.max(0, orderProductsTotal - sistersBaseTotal).toFixed(2),
        );
        const stuckTip = parseFloat(
          Math.max(0, verifiedAmount - stuckBase).toFixed(2),
        );
        console.warn(
          `[Recovery] Stuck captured: verified=$${verifiedAmount}, base=$${stuckBase}, tip=$${stuckTip} (order=$${orderProductsTotal}, sistersBase=$${sistersBaseTotal})`,
        );

        // Set BASE amount (without tip) — TIP FINALIZE will add tip from DB
        stuckLine.set_amount(stuckBase);
        stuckLine.set_payment_status("done");
        stuckLine._terminalTip = stuckTip;

        // Attach receipt data for the stuck tx to _all_payment_data only.
        // Do NOT call set_extra_payment_data — the receipt template renders BOTH
        // extra_payment_data AND _all_payment_data, which would create a duplicate entry.
        if (!recovery_order._all_payment_data) {
          recovery_order._all_payment_data = [];
        }

        if (tssData) {
          const receiptData = mapTSSDataToReceipt(tssData);
          receiptData._amount = stuckBase;
          receiptData._tipAmount = stuckTip;
          recovery_order._all_payment_data.push(receiptData);
        } else if (tx.pos_receipt_data) {
          try {
            const parsed = JSON.parse(tx.pos_receipt_data);
            parsed._amount = stuckBase;
            parsed._tipAmount = stuckTip;
            recovery_order._all_payment_data.push(parsed);
          } catch (e) {
            console.warn("[Recovery] Failed to parse pos_receipt_data:", e);
          }
        } else {
          recovery_order._all_payment_data.push({
            _amount: stuckBase,
            _tipAmount: stuckTip,
            transactionId: tx.provider_reference || tx.reference,
            status: "AUTHORIZED",
          });
        }

        // ── Write tip_amount + transaction_status to DB ────────────────
        // tip_amount is needed so TIP FINALIZE can pick it up.
        // transaction_status marks it as no longer in-progress.
        try {
          await this.orm.write("payment.transaction", [tx.id], {
            transaction_status: "captured",
            tip_amount: stuckTip,
          });
          console.warn(
            `[Recovery] Updated tx ${tx.id}: status→captured, tip_amount→$${stuckTip}`,
          );
        } catch (e) {
          console.warn("[Recovery] Failed to update tx:", e);
        }
      } else {
        // Money was NOT captured: set the retry line to what is genuinely owed.
        // Recalculate from order product total minus sisters' BASE amounts only.
        const retryOrderTotal = parseFloat(
          (snapshotOrder.amount_total || 0).toFixed(2),
        );
        const retrySistersBase = sisterTxs.reduce(
          (sum, s) =>
            sum +
            parseFloat(((s.amount || 0) - (s.tip_amount || 0)).toFixed(2)),
          0,
        );
        const retryBaseAmount = parseFloat(
          Math.max(0, retryOrderTotal - retrySistersBase).toFixed(2),
        );
        console.log(
          `[Recovery] Retry: order($${retryOrderTotal}) - sistersBase($${retrySistersBase}) = $${retryBaseAmount}`,
        );
        stuckLine.set_amount(retryBaseAmount);
        stuckLine.set_payment_status("retry");
        console.warn(
          `[Recovery] Retry line: amount=$${retryBaseAmount}, tx=${stuckLine.transaction_id}, status=${stuckLine.payment_status}`,
        );
      }

      // ── FINAL STATE LOG ──
      const allLines = recovery_order.payment_ids;
      console.warn(
        `[Recovery] FINAL ORDER STATE: ${allLines.length} payment lines, order_total=$${recovery_order.get_total_with_tax()}, due=$${recovery_order.get_due()}`,
      );
      for (let i = 0; i < allLines.length; i++) {
        const pl = allLines[i];
        console.warn(
          `[Recovery] line[${i}] amount=$${pl.get_amount()}, status=${pl.payment_status}, tx=${pl.transaction_id || "(none)"}, _terminalTip=${pl._terminalTip || 0}`,
        );
      }

      // ── 9. NAVIGATE ─────────────────────────────────────────────────────
      if (isSuccess) {
        // Set flag so PaymentScreen auto-triggers validateOrder on mount.
        // This goes through the full Odoo flow:
        // validateOrder → TIP FINALIZE → _finalizeValidation → sync → ReceiptScreen → order close
        recovery_order._autoValidateOnMount = true;
      }

      // Navigate to PaymentScreen — this uses the order's UUID via props.orderUuid.
      // Since we REUSE the existing order (or create a new one), the UUID is always valid.
      this.pos.showScreen("PaymentScreen", { orderUuid: recovery_order.uuid });
      this.removeTransactionFromList(tx.id);

      if (!isSuccess) {
        this.notification.add(
          _t(
            "Previous payments recovered (" +
              sisterTxs.length +
              " of " +
              (sisterTxs.length + 1) +
              " lines). Please complete the remaining payment.",
          ),
          { type: "warning", sticky: true },
        );
      }
    } catch (e) {
      console.error("Reconstruction failed:", e);
      this.notification.add(
        _t("Could not restore order. Please contact a manager."),
        { type: "danger", sticky: true },
      );
      this.removeTransactionFromList(tx.id);
    }
  }

  // ─── HELPERS ─────────────────────────────────────────────────────────────

  removeTransactionFromList(tx_id) {
    this.pos.global_stuck_transactions =
      this.pos.global_stuck_transactions.filter((t) => t.id !== tx_id);
  }
}
