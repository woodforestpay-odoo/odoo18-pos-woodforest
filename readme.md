# pos_woodforest — Woodforest Payment Terminal Integration

Odoo 18 POS module integrating the Woodforest payment terminal (A920Pro) via the Mirillium cloud gateway.

---

## Payment Transaction Lifecycle

### How a payment reference is created

A unique `earlyPaymentRef` is generated in the browser **before** any terminal call is made. This reference is:

- Stored in Odoo as `payment_transaction.reference`
- Sent to the terminal (and to Cybersource) as `payment_id` / `clientReferenceInformation.code`

Because both Odoo and Cybersource share this same reference, it is always possible to reconcile a transaction even in error scenarios.

---

### Normal payment flow (success path)

```
1. Cashier clicks "Validate Order"
2. earlyPaymentRef generated  →  payment.transaction saved as state=pending
3. showAmountOnTerminal       →  terminal shows basket + amount
4. handleTip                  →  customer selects tip (if enabled)
5. authorizePayment            →  terminal contacts Cybersource
   - earlyPaymentRef sent as clientReferenceInformation.code
   - Cybersource returns provider_reference (Cybersource transaction ID)
6. payment.transaction updated →  state=done, transaction_status=captured
7. POS order validated         →  pos.payment created, linked to POS order
8. pos_order.create hook       →  payment.transaction.pos_order_id backfilled
```

---

### What happens if the browser closes during auth (step 5)

Because the transaction is saved as `pending` **before** the auth call (step 2), the record always exists in Odoo. When the cashier/manager investigates:

- The transaction appears in **Woodforest Transactions** as `pending`
- Click **Sync Status** — Odoo queries Cybersource using this fallback chain:
  1. `provider_reference` (Cybersource ID, if already received)
  2. `payrillium_card_token` (if tokenized payment)
  3. `reference` (earlyPaymentRef = `clientReferenceInformation.code`)
- Cybersource returns the real status → Odoo updates the record accordingly

---

### Transaction status reference

| `transaction_status` | Meaning |
|---|---|
| `none` | Object created, terminal not yet contacted |
| `pending` | Sent to terminal, awaiting response |
| `authorized` | Auth hold placed, not yet captured |
| `captured` | Payment fully settled |
| `auth_failed` | Terminal responded but auth was rejected |
| `reversed` | Authorization reversed (void) |
| `reversal_failed` | Void attempted but failed — manual action required |
| `error` | Unexpected error, check `state_message` |

---

## Woodforest Transactions View — Button Logic

### Button visibility rules

The buttons shown in a transaction's detail view depend entirely on what data is available.

| Condition | Buttons shown |
|---|---|
| `pos.payment` exists with `transaction_id = provider_reference` | **Go to POS Order** |
| No `pos.payment`, but `provider_reference` IS set | **Sync Status** + **Cancel/Revert Purchase** (managers) |
| No `pos.payment`, `provider_reference` is EMPTY | **Sync Status** only |

> **Why no "Cancel/Revert" when `provider_reference` is empty?**  
> Without the Cybersource transaction ID we have nothing to void. The first step is always **Sync Status** to recover that ID.

---

### Scenario 1 — Normal completed sale (transaction linked to a POS Order)

The transaction has a `pos.payment` record. The money landed.

**Button:** `Go to POS Order`  
→ Opens the POS Order. Use Odoo's native **Return Products** button to issue a refund.

Before opening the return wizard, Odoo **live-calls Cybersource** to confirm the payment is in a returnable state (`AUTHORIZED`, `CAPTURED`, or `COMPLETED`). If Cybersource is unreachable, the return is **blocked** and the reason is posted to the POS Order chatter.

---

### Scenario 2 — Orphan transaction WITH a Cybersource ID (`provider_reference` is set)

This happens when: the terminal charged the card, Cybersource responded with an ID, but the browser closed before the POS order was confirmed.

**Buttons:** `Sync Status` + `Evaluate & Execute Action`

**Recommended workflow for the operator:**
1. Click **Sync Status** → confirms the current live state in Cybersource.
2. Click **Evaluate & Execute Action** → wizard analyzes the Cybersource `applications` array using the 4-Group Logic:
   - **GROUP 1 (Settled):** Captured funds (`ics_bill`). → **Refund**
   - **GROUP 2 (Authorized Only):** Held funds (`ics_auth`). → **Void**
   - **GROUP 3 (Reversed/Refunded):** Already cancelled. → **Blocked** (No action needed)
   - **GROUP 4 (Non-Actionable):** No financial movement (e.g. `ics_dcc`). → **Blocked**

---

### Scenario 3 — Orphan transaction WITHOUT a Cybersource ID (`provider_reference` is empty, state = pending)

This happens when: the browser closed **after** sending the auth request but **before** receiving Cybersource's response. The card may or may not have been charged.

**Button:** `Sync Status` only

**Recommended workflow for the operator:**
1. Click **Sync Status**
   - Odoo queries Cybersource's Transaction Search Service (TSS) using the `reference` (earlyPaymentRef).
   - **If Cybersource finds the payment:** extracts the real ID → updates `provider_reference` → "Evaluate & Execute Action" button becomes visible → proceed as Scenario 2.
   - **If Cybersource says "not found":** the terminal never completed the transaction → the transaction can be safely closed/archived.
2. If Cybersource is unreachable: wait and retry.

---

### Evaluate & Execute Action wizard (detail)

The wizard **always parses the live applications array** from Cybersource to prevent human error or double-voids. It enforces a strict 4-Group Application State Engine:

| Application State (Cybersource) | Odoo Action Permitted |
|---|---|
| **GROUP 1 (Settled):** `ics_bill`, `ics_ap_sale`, `ics_pin_debit_purchase` | **Refund** — returns captured funds (or instant debit purchases) |
| **GROUP 2 (Authorized Only):** `ics_auth`, `ics_incremental_auth` | **Void** — releases the authorization hold |
| **GROUP 3 (Returned):** `ics_credit`, `ics_void`, `ics_auth_reversal` | **Blocked** — Already reversed/refunded |
| **GROUP 4 (Non-Actionable):** `ics_dcc`, `ics_score`, `ics_dav`, `CANCELLED` | **Blocked** — No financial capture occurred or transaction was globally cancelled |

> **Note on UI:** The wizard provides a rich HTML explanation detailing exactly *why* an action is recommended, relying on explicit `reasonCode` and `rFlag` evidence directly from Cybersource.

---

### Split Payments & Token Usage
- Woodforest terminal interactions are highly optimized for split payments. The terminal basket screen (`showBasket`) is only invoked for the very first Woodforest line to avoid redundant flashes or "Terminal Busy" errors, and cleared (`showEmptyBasket`) only after the final line.
- Split payments using saved payment tokens correctly charge the specific line amount rather than the full order total.

---

## Refund from POS Order (normal flow)

When a manager clicks **Return Products** on a confirmed POS Order:

1. Odoo calls the overridden `PosOrder.refund()` method
2. Finds the linked `payment.transaction` via `pos.payment.transaction_id`
3. **Direct live call to Cybersource** using `get_payment_status()` — the cached `transaction_status` field is intentionally ignored
4. If Cybersource is **unreachable** → return BLOCKED + reason posted to POS Order chatter
5. If Cybersource returns **`success: false`** (payment not found, API error) → return BLOCKED + Cybersource message posted to chatter
6. If status is **not confirmed** (not AUTHORIZED/CAPTURED/COMPLETED) → return BLOCKED + live status posted to chatter
7. If status **is confirmed** → return authorized, success note posted to chatter, Odoo creates the negative POS order

> **Every blocked return leaves a permanent record in the chatter** explaining exactly why it was blocked and what the operator should do next.

---


## Key model relationships

```
payment.transaction
  ├── reference              = earlyPaymentRef (= clientReferenceInformation in Cybersource)
  ├── provider_reference     = Cybersource transaction ID (set after auth response)
  ├── pos_order_id           = linked POS order (backfilled by pos_order.create hook)
  ├── pos_order_uid          = pos.order.pos_reference (used to backlink orphan transactions)
  ├── payrillium_terminal_id = terminal used
  └── card_type              = DEBIT | CREDIT (from terminal edcType field)

pos.payment
  └── transaction_id         = provider_reference (used to detect completed POS orders)
```

---

## Technical notes

### Why `pos_order_id` may be NULL initially

The `payment.transaction` is saved **before** the POS order exists in the database. The `pos_order.create` hook in `pos_order.py` automatically backfills `pos_order_id` via `pos_order_uid` when the order is finally confirmed.

### Card type (DEBIT vs CREDIT)

The initial JS `cardType = "CREDIT"` is only a default to initiate the terminal flow. The terminal's `edcType` from the auth response always takes precedence and is what gets stored in `card_type` and shown on the receipt.

---

## Future Improvements

### 1. Auto-Sync Cron Job (Self-Healing)
**Problem:** Transactions interrupted mid-auth remain as `pending` in Odoo indefinitely.  
**Solution:** Odoo Scheduled Action that runs every 10–15 minutes. Finds all `pending` transactions older than 5 minutes and silently calls `action_woodforest_check_status()`. Makes the system self-healing without manual intervention.

### 2. Tokenization Resiliency
**Problem:** Token creation is tightly coupled to the payment auth flow. If interrupted, the token may not be saved in Odoo even if Cybersource generated it.  
**Solution:** Independent token synchronization mechanism that ensures tokens are always captured and linked to the customer regardless of payment flow interruptions.

### 3. Fail-Closed Return Guard (Explicit Error Messaging)
**Problem:** If Cybersource is unreachable during a Return, the current behavior is ambiguous.  
**Solution:** Hard block on any Return/Refund action if Cybersource cannot confirm the payment. Show a clear error: *"No se pudo verificar el estado del pago. La devolución ha sido bloqueada. Intente de nuevo o contacte soporte."*

### 4. Check Status as Server Action (not per-order button)
**Problem:** "Check Woodforest Status" appears on every POS Order regardless of state, creating noise.  
**Solution:** Remove the per-order button. Replace with an **Odoo Server Action** on the Woodforest Transactions list view. Selecting a single transaction and using Actions → "Check Status" will:
- Query Cybersource for the real status
- If status changed: navigate to the transaction detail and post in chatter with old → new status
- If status unchanged: show a brief notification, no navigation
- If connection error / timeout: navigate to the transaction and post the error in chatter

### 5. Smart Filters in Transactions View
Predefined filters to speed up daily operations:
- **Orphaned** — transactions with no linked `pos.payment`
- **Stuck Pending** — `state=pending` older than 5 minutes
- **Status Mismatch** — Odoo state disagrees with `transaction_status` (e.g., `done` + `reversed`)
- **No Cybersource ID** — `provider_reference` is empty

### 6. Accurate 'Authorized' vs 'Captured' Status *(Next Version)*
**Problem:** Currently, when Mirillium returns a payment status of "Authorized", the webhook handler incorrectly calls `tx._set_done()`, treating it as fully captured. If the batch capture fails overnight, Odoo will incorrectly report inflated collected revenue.  
**Solution:** Modify `webhook_service.py` so that `pending` or `authorized` statuses correctly trigger `tx._set_pending()` or `tx._set_authorized()`, and only `done`, `success`, or `captured` trigger `tx._set_done()`.

### 7. Daily Reconciliation Report *(Next Version)*
A dedicated end-of-day view showing transactions in Odoo alongside the corresponding Cybersource status — side by side. Discrepancies highlighted in red so the manager can close the day with full confidence that every peso collected matches what the bank recorded.

## Production Security Recommendations

### Security & Access Rights
During testing, all diagnostic and recovery actions are generally accessible to `base.group_user` (standard internal users), to help validate the whole flow quickly.

For production deployment, it is highly recommended to review the file `/security/ir.model.access.csv` and adjust the permissions for the following critical operations:
* **Evaluate & Execute Action Wizard** (`model_payrillium_payment_action_wizard`): Consider restricting this to `point_of_sale.group_pos_manager` or `account.group_account_manager` to prevent unauthorized cashiers from forcing manual refunds on orphaned transactions.
* **Woodforest Refund Wizard** (`model_woodforest_refund_wizard`): Similar restrictions should be evaluated.

By default, the system currently allows standard users to perform these actions to simplify the testing phase.


Pay by link test (C:\ngrok-v3-stable-windows-amd64\ngrok.exe http 8069)
