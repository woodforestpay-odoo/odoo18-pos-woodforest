version = "18-1.5.5"

### Release Summary

- **UI: FAQ Layout Enhancement (`faq_page.xml`, `faq_page.css`):**
  - **Improvement:** Refactored the terminal FAQ screen to use a stacked horizontal layout. The "Current Status" panel now spans the full top width with its child cards arranged horizontally, and the "Troubleshooting Actions" are positioned immediately below. 
  - **Responsive:** Added media queries to automatically stack the horizontal cards vertically on screens smaller than 1000px.

- **Critical: Crash Manager Shield (`payment_screen.js`, `utils.js`):**
  - **Bug:** The Odoo 18 base POS occasionally throws malformed, raw JavaScript objects (e.g., `{ code: 401, message: "Backend Invoice" }`) instead of standard `Error` instances during network micro-cuts or invoice generation failures. When the Woodforest error handler caught these and attempted to display them, Odoo's global Crash Manager crashed trying to read `error.stack.split()`, resulting in a fatal white "Oops!" screen that locked the terminal.
  - **Fix:** Added `ensureError()` utility in `utils.js` that converts any thrown value into a proper `Error` instance with a valid `.stack` property. All catch blocks in `payment_screen.js` now wrap errors with `ensureError()` before passing them to handlers. Added a fail-safe inner try/catch around `_handlePaymentError` — if it crashes internally, a basic AlertDialog is shown instead of the white screen.

- **Critical: Serialize Ghost Reference Fix (`order_patch.js`):**
  - **Bug:** Pressing "Validate" crashed with `TypeError: Cannot read properties of undefined (reading 'serialize')`. This occurred when `syncAllOrders` attempted to serialize an order containing a reference to a deleted payment line (ghost record) that no longer existed in OWL's reactive state.
  - **Fix:** Added a defensive filter in `serialize()` that purges ghost payment line references (entries where the reactive proxy is dead or `.serialize` is not a function) before calling `super.serialize()`. This prevents `related_models.js` from crashing on stale IDs.

- **Receipt Guard — Skip Custom Logic for Non-Payrillium Payments (`order_patch.js`):**
  - **Bug:** `export_for_printing()` ran all Payrillium-specific receipt logic (terminal receipt fields, tip recalculations, `_all_payment_data` processing) for every order, including pure Cash orders. This was unnecessary overhead and a potential source of errors on orders that have nothing to do with our payment method.
  - **Fix:** Added an early-return guard: `if (!this._hasPayrilliumPayment()) return data;` — orders without our payment method now skip all custom receipt logic and return the standard Odoo receipt data untouched.

- **Dynamic Payment Method Name (`order_patch.js`, `setup_config.js`):**
  - **Refactor:** Removed all hardcoded `"woodforest"` string comparisons from `order_patch.js`. Introduced `getPayrilliumMethodName()` in `setup_config.js` that reads the payment method name from the cached config (fallback: `"woodforest"`). Renamed `_isWoodforestPayment()` → `_isPayrilliumPayment()` and `PosPayment` checks now use `_isOurPaymentMethod()`. This makes the module portable — changing the payment method name in the backend automatically propagates everywhere.


version = "18-1.5.3"

### Release Summary

- **Tip Calculation Reliability (`api_service.js`):**
  - Restored missing `resultType` variable in tip response parsing. Tips selected on the terminal are now correctly identified and added to the final authorization payload (`amount = baseAmount + tipAmount`), preventing tip loss.
- **Improved $0 Line Validation (`payrillium_tip_option.py`):**
  - Suppressed the generic `Amount must be greater than 0` error modal from popping up unnecessarily when creating an empty tip line that is not explicitly assigned a negative value.
- **Terminal Busy State Mapping (`utils.js`, `payment_transaction.py`, views):**
  - Mapped specific `CLDS-TERMINAL_BUSY-000011` / `CLDS-BUSY-011` hardware errors explicitly to a new `terminal_busy` transaction status.
  - Transactions blocked by an active terminal now display a dedicated orange "Terminal Busy" badge (`decoration-warning`) rather than generic system errors.
- **Operator Cancel Flow Enhancements (`payment_screen.js`):**
  - Handled implicit UI-abort triggers: When "Cancel" is manually requested by the operator, the payment screen immediately throws a specific "Cancelled by operator" error, bypassing terminal payload validation for deterministic fallback.
  - Real-Time Recovery Flow: If an authorization is aborted mid-flight (leaving the transaction `in_progress`), POS issues an immediate background `fetch_stuck_transactions()` call. The POS Navbar then reacts and instantly displays the red "⚠️ Recover Payments" warning without requiring a hard reload.
- **Strict Session Binding (`utils.js`, `api_service.js`):**
  - All terminal interactions rigidly append the correct Odoo POS `sessionId`, ensuring that terminals assigned exclusively to active sessions isolate their own transactions securely.
- **Temporarily Disabled Automated Terminal Aborts (Pending Provider Fix):**
  - Due to a terminal-side bug, automated background `abortTerminal` polling calls have been commented out until the provider pushes a fix.
  - Disabled: Backend "Reset Terminal" action (`woodforest_terminal_views.xml`).
  - Disabled: Session startup cleanup abort (`patch_pos_store.js`).
  - Disabled: Product screen return cleanup abort (`product_screen.js`).
  - Disabled: Emergency abort during terminal connection losses (`payment_screen.js`).
  - Kept Active: Only the explicit, manual "Cancel" button during the payment validation loop remains functional.

version = "18-1.5.1"

### Release Summary

- **Deterministic Payrillium State Machine (utils.js):**
  - Refactored error handling to a data-driven configuration model using TX_STATE_TO_ODOO_STATE and TX_STATE_TO_TRANSACTION_STATUS mapping dictionaries.
  - Odoo transaction state and status are now exclusively derived from the assigned terminal txState code (e.g. T01 -> cancelled, G03 -> declined), eliminating hardcoded conditions directly in the frontend component.
- **Form View Alignment & Deduplication:**
  - Separated the concerns of \`state_message\` (human readable log with timestamps) and \`terminal_message\` (raw hardware/gateway error codes like "T01 | ABORTED") in the payment.transaction form view.
  - Transactions now maintain an appendable, timestamped history in the backend ("STATUS HISTORY") allowing for a clear audit trail of status changes without duplication.
- **Terminal UI Unblocking:**
  - Identified and removed blocking \`await\` calls for terminal cosmetic updates (e.g., refund approved message). Fire-and-forget logic using \`.catch()\` ensures the POS continues validation immediately.
  - Fixed Unhandled Promise rejection bugs from async terminal calls masked behind synchronous \`try-catch\` blocks.
- **Terminal Guide FAQ Page:**
  - Migrated the standalone HTML terminal_guide.html into a native Odoo backend page using OWL.
  - Registered under a new "FAQ" menu under Support with a complete CSS encapsulation to preserve print styles inside the web interface.

version = "17-1.0.0"
### Release Summary

.

- Support for tokenized payments and refunds against tokens.
- Stored token on transaction (payrillium_card_token).
- Unified spinner (\_withSpinner).
- Token wizard fix: preserves amount on return.
- Centralized prepareRefundData: extracts reconciliationId, approvalCode, status, date.
- Signed gateway requests using prepare_signed_request()/build_header_hash() (Authorization + timestamp).

version = "17-1.0.1"

### Release Summary

- Fixed issue with created date parsing and storage.
- Implemented automatic deactivation of payment links generated for the same invoice.
- Added dynamic version display in configuration view (reads from config.py).
- Fixed payment buttons visibility: buttons now only show for posted invoices, not for draft invoices.

version = "17-1.1.1"

### Release Summary

-Added support for manual token payments (handler for manual.token.payment) with structured logging (execution_id) for auditability.
-Implemented cron_check_payment_status:
-Detects candidate invoices (active payrillium.payment.link or pending ACH transactions).
-Executes action_get_payment_status_now as SUPERUSER for each candidate invoice and aggregates results per execution_id.
-Triggers sync_existing_payment_links after cron when link payments were processed.
-Implemented action_get_payment_status_now:
-Prioritizes processing of active pay-by-link records; only checks ACH pending transactions if no link payment was processed.
-Returns a machine-friendly dict when called from cron (from_cron=True) and UI notifications when called interactively.
-Added wrapper action_get_payment_status_and_sync to combine immediate status check + conditional link sync.
-Added backend Form and List views for managing manual payments and payment links (fields: execution_id, reference, amount, state, invoice_id, terminal_id, logs; list filters and batch actions included).
-Fixed invoice_id handling and robustified payment reference generation to prevent UnboundLocalError and avoid reference collisions (uses search_count on INVLINK-{invoice.id}-{link_id} to build unique references).
-Improved logging and error handling around sync step (sync_existing_payment_links) — failures are captured and logged as sync.links events with success=false.
-Added cron-specific tests/checklist and verification steps for manual deployment/run checks.

version = "18-1.1.1"

### Release Summary

Migration notes (Odoo 17 → Odoo 18)

- Replaced tree view type with list, since tree was deprecated in Odoo 18.
- Updated Kanban views: adjusted xpath expressions because the DOM structure and classes used in pos.config kanban views changed between versions.
- Account.account no longer has the company_id field.
  delete errorpop up
  -changed name order patch

version = "18-1.1.2"

### Release Summary

- Fixed 500 Server Error when processing payments:
  - Removed 'sessionId' field from payload sent to Mirillium API (it was causing errors)
  - 'sessionId' is now only used internally in Odoo to identify the correct terminal
- Fixed terminal selection to use specific session instead of user:
  - Modified `_get_current_terminal()` to accept `session_id` parameter
  - Updated `proxy_to_terminal()` and `payrillium_payment_router()` to pass sessionId from requests
  - Now ensures each POS session sends requests to its own assigned terminal, preventing terminal confusion when multiple sessions are open
- Implemented automatic deletion of terminals that no longer exist in shopnet:
  - When synchronizing terminals, terminals not returned by shopnet API are automatically deleted from Odoo database
  - Checks for active POS sessions before deletion (skips deletion if terminal has active session)
  - Unassigns terminal from POS Config before deletion
  - Synchronization notification now shows: synchronized terminals, deleted terminals, and skipped terminals (with active sessions)
- Added terminal information logging in browser console:
  - Console logs now show which terminal (id, name, serial) each request is sent to
  - Helps with debugging and verification of correct terminal usage

version = "18-1.1.3"

### Release Summary

- Fixed payment tip handling and terminal-level tip configuration:
  - Fixed TypeError when accessing tip amount: changed `paymentLine.order.get_total_with_tax()` to `paymentLine.pos_order_id.get_total_with_tax()` in `api_service.js` and `terminal_service.js`
  - Improved tip selection logic: checks if tip was already selected from UI before showing terminal tip selection
  - Fixed tip application: properly applies tip from terminal to order using `posService.set_tip()` and updates payment line amount
  - Added terminal-level tip control: new `iface_tipproduct` field in `payrillium.terminal` model (default: true)
  - Terminal tip configuration: tips must be enabled in BOTH POS session AND terminal to show tip selection on terminal
  - UI improvements: hidden "Tips Enabled" field for "No Terminal" entries (serial='NONE')
  - Enhanced tip flow: if tip is selected from UI, terminal tip selection is skipped; if no tip in UI and both session/terminal allow tips, terminal prompts for tip
- Implemented synchronized payment token deletion:
  - Override `payment.token.unlink()` method to delete tokens from both Odoo database and Mirillium API
  - When a payment token is deleted in Odoo, it automatically attempts deletion in Mirillium (logs failure but doesn't block local deletion)
  - Added `delete_payment_token()` stub function in Mirillium API service (ready for actual API endpoint implementation)

version = "18-1.1.4"

### Release Summary

- Added Payment Tokens management tab in Partner form:
  - New "Payment Tokens" tab in res.partner form view (after Internal Notes tab)
  - Displays only active Payrillium payment tokens for the partner
  - Shows masked card information (payment_details field only)
  - Added "Disable" button per token to soft-delete tokens (sets active=False)
  - Implemented `action_disable_token()` method in payment.token model:
    - Calls Mirillium API `delete_payment_token()` to sync deletion (best effort)
    - Disables token locally (soft delete, no hard unlink)
    - Posts audit message to partner chatter
    - Shows success notification to user
  - Created computed field `payrillium_payment_token_ids` in res.partner to filter only Payrillium active tokens
  - Tokenization flow: Payment Instrument creation from terminal instrumentIdentifier is in progress (API integration pending)

version = "18-1.3.1"

### Release Summary

- Pay by Link and chatter: restored notifications and single message on generate; copy full Cybersource URL from chatter (regex picks longest URL); payment_link_wizard no longer uses useService in setup to avoid conflicts; controller generate_link returns JSON on errors.
- Backend assets: added utils.js to web.assets_backend for sync_button and notification_handler; CopyButton import updated to @web/core/copy_button/copy_button for Odoo 18.
- Logs: payrilliumDebugEnabled false by default; only enabled when backend /woodforest/environment returns "dev" (no console logs in production).
- Receipt: Payrillium terminal info (transaction ID, card type, etc.) shown in receipt before footer when available.

version = "18-1.3.2"

### Release Summary

- Receipt tip display: tip no longer shown as a product line (e.g. "Tips: 1.00 x $X"); shown in totals section instead.
- New receipt totals block when tip is present: Subtotal, Tax (per group), Total Before Tip, Tip (Gratuity), then TOTAL and payment lines.
- order_patch.js: export_for_printing excludes tip line from orderlines and adds receiptTipInfo (tipAmount, totalBeforeTip, taxTotalsBeforeTip) for template.
- order_receipt_template.xml: conditional block for receiptTipInfo.hasTip; default tax/total block hidden when tip section is shown.

version = "18-1.3.3"

### Release Summary

- Post-install: activation view now loads with correct styles on first paint. Post-install action returns client action `reload` with `action_id` to open Payrillium settings, so the browser does a full reload and backend assets (e.g. activation_panel.css) are applied without manual refresh.
- Activation UX: added helper text "Don't have a code yet?" under the "Activate with code" button, with "Request activation" as the secondary link (signposting for users without a code).
- Odoo 18 compatibility: `account.account` uses `company_ids` (Many2many) instead of `company_id`. Fixed `payrillium_wizard.submit_token()` to use `company_ids` in search domains and in create for receivable account, outstanding account and Payrillium bridge account (101401); added `Command` import for `company_ids` on create.

version = "18-1.3.4"

### Release Summary

- **Payment Processing (Double Charge Fix):**
  - Resolved critical issue where tipping via Odoo UI caused the total order amount to be charged for each payment line (double charge).
  - Modified `payment_screen.js` and `terminal_service.js` to use `paymentLine.get_amount()` instead of `order.get_total_with_tax()` for transactions.
- **Tip Testing Authentication:**
  - Fixed 401 Unauthorized error in "Test Tips" feature.
  - Corrected payload structure (nested "data" object) and authentication hash generation order in `payrillium_terminal.py`.
- **Payment Token Creation (POS):**
  - Improved `payrillium_token_create_from_auth`: added `card: {}` parameter.
  - Fixed `administrativeArea` issue: sends state code (e.g., 'GA') instead of name.
  - Implemented dynamic `card.type` mapping (lowercase vendor name).
  - Added visual success notification in POS upon successful token creation.
  - Enhanced API logging.
- **Payment Token Deletion:**
  - Implemented `delete_payment_token` in Mirillium API service.
  - Now sends DELETE request to `/api/v1/tokens/payment_instrument/{provider_ref}` when a token is disabled/deleted in Odoo.
- **Terminal UI Improvements:**
  - Added "Test Tips" button to Payrillium Terminal form view.
  - Redesigned tip configuration layout: compact 2-column grid with custom CSS to fix drag-handle width and alignment.
- **Double Charge Final Fix:**
  - Corrected `capturePayment` in `api_service.js` to use `paymentLine.get_amount()` instead of order total, preventing double charging on initial transaction request when tips are involved.

version = "18-1.3.5"

### Release Summary

- **Duplicate Prevention:**
  - Payment transactions are checked for duplicate `provider_reference` before creation, preventing double charges from retries or race conditions.
  - Payment tokens are checked for existing `provider_ref` per partner before creation, preventing redundant token records.
- **Intelligent Payment Status Polling:**
  - Payment link status polling now uses a server-side cache to reduce Mirillium API calls.
  - Polling interval is adaptive: frequent for recently created links, reduced for older ones.
  - Cron job aggregates results per `execution_id` for traceability.

version = "18-1.3.6"

### Release Summary

- **Early Transaction Recording:**
  - A `payment.transaction` record with `state='pending'` is now created _before_ the terminal interaction begins (before `showBasket`).
  - If the payment later fails (MQTT timeout, chip read error, cardholder abort), the record is updated to `state='error'` with the error message.
  - Ensures every payment attempt — successful or not — is captured in the database for audit and troubleshooting.
- **Debug-Only Console Logging:**
  - `payrilliumConsole` output is now suppressed in production environments.
  - Logs are only active when the backend `/woodforest/environment` returns `"dev"` or when the `?debug` URL parameter is present.
  - Critical errors (`console.error`) are always logged regardless of environment.
- **Enhanced Mirillium API Error Parsing:**
  - API error responses are now parsed with richer detail: response codes, meanings (from `ARC_MEANING` dictionary), and processor information.
  - Errors are logged to both the Odoo chatter (on the related record) and the dedicated `payrillium.log` table with execution ID for traceability.
- **Open Session Fix:**
  - Fixed terminal session detection: `_compute_session_status` now correctly identifies terminals with open POS sessions by checking `pos.session` records in `opened` or `opening_control` state.

version = "18-1.3.7"

### Release Summary

- **Inline Terminal-to-POS Assignment:**
  - Removed the popup wizard for assigning terminals to POS sessions.
  - Created a custom OWL field widget (`terminal_pos_selector`) that renders a native `<select>` dropdown directly in the terminal list view.
  - 1-click opens the dropdown immediately — no edit mode, no text cursor, no search input.
  - The widget handles its own save logic via ORM `write()`, shows a success toast on save, and rolls back on failure.
  - Dropdown only shows POS configurations that do **not** have an open session (filtered dynamically).
- **Session Locking:**
  - When a terminal's POS session is open, the assignment cell shows a lock icon (🔒) with a native tooltip: _"Cannot modify assignment while session is Open. Close the session first."_
  - The locked state has reduced opacity, `cursor: not-allowed`, and a subtle red highlight on hover.
- **Row Navigation Preserved:**
  - Removed `editable="bottom"` from the terminal list view.
  - Clicking any column other than "Assigned to" navigates to the terminal form view (standard Odoo behavior).
  - Only the `<select>` element in the "Assigned to" column intercepts the click via `stopPropagation`.
- **Compact Terminal List View:**
  - Reduced row padding (`td` padding: 2px vertical), badge sizes (0.75rem), and select height (24px) for a denser, data-grid appearance.
  - All compact styles are scoped to `.o_payrillium_terminal_list` — zero impact on other Odoo list views.
- **Session State Badges:**
  - Added `session_state_label` computed field showing real-time Odoo session state (Open, Closed, No Session, Opening, Closing).
  - Badges use semantic colors: Open = green, Closed = gray, No Session = blue, Opening/Closing = yellow.
- **Per-Terminal Tip Presets:**
  - Each terminal can now be configured with custom tip preset values (Amount mode: dollar amounts, Percentage mode: percentages).
  - Default presets are automatically created when a terminal is first configured (Amount: $5, $10, $20 / Percentage: 15%, 20%, 25%).
  - Terminal tip configuration is sent to the physical device during payment and overrides POS session defaults.
  - New `tip_options_summary` computed field displays configured presets in the terminal list (e.g., "$5 · $10 · $20").
- **Environment Display:**
  - The module version string in `config.py` now includes the active environment name (production / sandbox / development) for quick identification in logs and the About dialog.
- **New Files:**
  - `static/src/js/terminal_pos_selector.js` — Custom OWL field widget for inline POS assignment.
  - `static/src/xml/terminal_pos_selector.xml` — QWeb template for the widget.

version = "18-1.4.2"

### Release Summary

- **Cybersource Decision Engine:**
  - Implemented a pure, deterministic Decision Engine (`_cybersource_decide_action`) to evaluate transaction states safely before recommending Refounds or Voids.
  - Fixed a critical bug where CANCELLED global statuses were incorrectly recommended for Refund because of missing bill validations.
  - Added dedicated logic for PIN Debit (`ics_pin_debit_purchase`), recognizing single-message transactions automatically as Refundable upon success.
- **Payment Action Wizard UI Upgrade:**
  - Wizard now renders rich HTML explanations instead of plain text, showing clear section titles, evidence lists (reasonCode, rFlag), and explicit action rationale directly extracted from the Decision Engine.
- **Optimized Terminal Interactions (Split Payments):**
  - Grouped Woodforest payment lines during POS order validation so that `showBasket` is only called for the VERY FIRST Woodforest line, and `showEmptyBasket` is only sent for the LAST successful line.
  - Eliminates redundant terminal UI flashes and "Terminal Busy" errors between split payment lines.
- **Token Split Payment Fix:**
  - Adjusted tokenized payments to correctly charge `paymentLine.get_amount()` instead of the total order amount, ensuring split payments via saved cards work correctly.
- **Terminal UX & Speed Improvements:**
  - **Faster Checkout:** The shopping cart is now sent to the terminal instantly upon opening the Payment Screen, saving 2-3 seconds during payment validation.
  - **Fire-and-Forget Messages:** Terminal success and decline messages no longer block the POS, allowing the cashier to immediately see the result and proceed.
  - **Custom Loading States:** Added clear, descriptive step messages to the POS loading spinner ("Sending order...", "Awaiting tip...", "Processing payment...").
  - **Auto-Recovery:** Clicking "Recover" in the navbar now instantly verifies the first interrupted transaction without requiring an extra click in a popup menu.
  - **Automatic Terminal Cleanup:** Returning to the Product Screen automatically sends an abort command to the terminal, ensuring it's always ready for the next customer.
  - **Receipt Cleanups:** Fixed an issue where the printed receipt could duplicate payment blocks from previous failed or cancelled transaction attempts.
  - **Code Refactor & Performance:** Eliminated redundant database queries and async bottlenecks in the payment validation loop, making the entire flow significantly faster and more reliable.

version = "18-1.4.2"

### Release Summary

- **Decision Engine Hardening:**
  - Gate 1: Now cross-references global status with app-level evidence before short-circuiting. Prevents false finalization of successful transactions.
  - Gate 2: Validates reasonCode/rFlag on refund/void apps before declaring REFUNDED/VOIDED. Failed refunds no longer incorrectly finalize.
  - Gate 4: Introduced AUTH_HOLD status for auth-only transactions (capture failed). Prevents phantom order creation.
  - Fixed state mapping: PENDING/TRANSMITTED now correctly map to `captured` instead of `authorized`.
  - Fixed `CONFIRMED_STATUSES` bug: removed obsolete AUTHORIZED_ONLY, returns only allowed when payment is DONE/CAPTURED/COMPLETED.
- **Novice-Friendly Messages (all user-facing text simplified):**
  - Recovery toasters: replaced technical jargon with clear cashier guidance ("Payment found. Showing receipt.", "Payment authorized but not captured.", etc.).
  - Return flow: all "Return Blocked" messages reduced to single-line explanations ("Return not possible. No payment record found.", "Could not verify payment status.", etc.).
  - Refund creation: backend popup and chatter messages simplified ("Open 'Bakery Shop' and look for Order #601.").
  - No-session error: "Please open 'Bakery Shop' first, then try the refund again."
  - Sync notifications: "Payment found, order missing" instead of Cybersource-specific language.
- **Transaction Form View Redesign:**
  - Status bar now shows colored steps: Draft (grey), Pending (orange), Authorized (blue), Confirmed (green), Cancelled/Error (red).
  - "Check Payment Status" button (blue) replaces "Sync & Propose Action".
  - "Go to Order" button (green) visible only when a POS order is linked.
  - Status badge with semantic colors displayed next to Transaction Reference.
  - Smart button shows linked order name via statinfo widget.
  - New `status_summary` field ("Details") below Payment ID for novice users.
  - Logs & History section collapsed by default (HTML details/summary).
- **Payment Status Wizard Simplified:**
  - Window title: "Payment Status" (was "Sync & Propose Action").
  - Main message: "Payment found at the bank." or "No action available."
  - POS recovery note: "This payment can be recovered in the register."
  - Technical details hidden in collapsed accordion.
  - Buttons: "Void Payment", "Go to Register", "Close".
  - Payment not found now opens wizard popup instead of toaster notification.

version = "18-1.4.3"

### Release Summary

- **Tip Input Validation (Backend):**
  - `@api.onchange` added to `payrillium_tip_option.py`: percentage tips must be ≥ 1, amounts > 0. Invalid values reset to 0.0 with a warning popup before saving. `@api.constrains` stays as server-side safety net.

- **Cancel Button for Terminal Operations:**
  - "Cancel" button injected inside `.o_blockUI` overlay (Odoo spinner) during terminal operations via `requestAnimationFrame` retry loop in `_processPayrilliumPayment`.
  - First click disables button immediately ("⟳ Cancelling...") — prevents multiple abort requests.
  - **Tip selection:** visible immediately. **Auth (0–30s):** hidden. **Auth (+30s stuck):** "⚠ Emergency Cancel" appears as escape hatch. **Post-auth:** hidden, timer cleared.

- **Abort Flow Integration:**
  - `_payrilliumAborted` flag: reset at start of each payment, set in `_abortTerminal()`, checked after `handleTip`. If set, throws `cancelled` error → line → `retry`. Auth is never called.
  - `_setCancelVisible()` / `_showCancelAfterDelay(ms)` / `_clearCancelDelay()` helpers control button visibility.

- **Critical Bug Fix — False Positive Auth Approval on Abort (`utils.js`):**
  - **Bug:** Abort-during-auth response `{ "data": { "reason": "Aborted from InputAccount", "success": true } }` was treated as a successful payment (fake transaction created, "Approved" shown on terminal) because `isCancelled()` only checked `data.data.message` which was absent.
  - **Fix:** `isCancelled()` now also checks `data.reason` for "ABORT".

- **Double-Unblock Fix (`payment_screen.js`):**
  - Removed `uiService.unblock()` from `_abortTerminal()`. The `finally` block of `_processPayrilliumPayment` is now the sole unblock owner.

- **Recovery Flow on Abort During Auth:**
  - `_authWasStarted` flag set just before `authorizePayment()`. In the catch block, if `_payrilliumAborted && _authWasStarted`: transaction left as `in_progress` (not updated to error) so recovery flow verifies with Cybersource on next attempt.

- **Tip Display on Receipt:**
  - **Bug:** `receiptTipInfo.hasTip` was always `false` for terminal tips — `export_for_printing` only set `hasTip: true` for Odoo tip product lines.
  - **Fix (`payment_screen.js`):** After `storeTransactionMetadata`, `_tipAmount` is injected directly into the last `_all_payment_data` entry (not via a second `set_extra_payment_data` call, which created a duplicate receipt block).
  - **Fix (`order_patch.js`):** When no tip product lines, `export_for_printing` now sums `_tipAmount` from payment data and builds `receiptTipInfo` (`hasTip: true`, `totalBeforeTip`, `taxTotalsBeforeTip`) patching `taxTotals.order_total` to include the tip for the TOTAL line.
  - Receipt shows: Subtotal / Tax / Total Before Tip / Tip (Gratuity) / TOTAL. Multi-payment: `tipAmount` per section now populated.

version = "18-1.4.6"

### Release Summary

- **Recovery Flow Rewrite (get_partner Crash Fix):**
  - Root cause: `localDeleteCascade` deleted the original order while Owl's `PaymentScreen` still referenced it via `props.orderUuid`, causing `get_partner` crash on re-render.
  - Fix: recovery now **reuses the existing order** from IndexedDB instead of deleting and recreating. Eliminates the crash entirely.
  - Stale payment lines on the reused order are cleared before adding fresh recovery lines.

- **Recovery Tip Handling (Arithmetic Derivation):**
  - Tips for recovered sister lines are now derived from the `_all_payment_data` snapshot (dbAmount − baseAmount = tip).
  - Stuck transaction tip is calculated arithmetically: `verifiedCybersourceAmount − (orderTotal − sistersBaseSum) = stuckTip`.
  - `tip_amount` is written to `payment.transaction` so TIP FINALIZE processes it correctly.
  - `_terminalTip` set on all lines (sisters + stuck) so UI shows tip badges.

- **Stale Cleanup Guard:**
  - `_isRecoveryLine` flag prevents the stale-line cleanup in `payment_screen.js` (section 18.4) from removing legitimate recovery lines that lack a `transaction_id`.

- **Duplicate Receipt Entry Fix:**
  - Recovery was calling both `set_extra_payment_data()` AND `_all_payment_data.push()` for the stuck tx, causing the receipt template to render it twice (same TX ID, different amounts).
  - Fix: only `_all_payment_data.push()` is used. `set_extra_payment_data` removed from the captured block.

- **Tip Badge Display on Recovery Mount:**
  - `_injectTipBadges()` only ran on `onPatched` (re-renders), not on `onMounted` (initial mount). Recovery's `showScreen("PaymentScreen")` creates a fresh component where only `onMounted` fires.
  - Fix: added `onMounted` hook alongside `onPatched` in `payment_lines_patch.js`.

- **Dead Code Removal:**
  - Removed unused `_attachReceiptData` method from `recovery_popup.js`.
  - Removed stale transaction re-read that was already handled upstream.

version = "18-1.4.7"

### Release Summary

- **Async Polling Architecture (`main.py`, `api_service.js`):**
  - All Mirillium API calls (auth, capture, tip, basket, decline, approved, refund, void, card type) now run in background threads via `_start_async_job()` to avoid Odoo/Nginx proxy timeouts.
  - Frontend `_rpcWithPolling()` replaces direct `rpc()` calls: backend returns `{status: "polling", job_id}` immediately, frontend polls `/woodforest/poll` every 3s (max 180s) until job completes.
  - New backend `/woodforest/poll` endpoint returns job status (`pending`, `done`, `error`, `not_found`).
  - Thread-safe job store with `threading.Lock` and automatic cleanup of jobs older than `_MAX_AGE` seconds.
  - Backward compatible: if backend responds without polling (e.g. old flow), result is returned as-is.

- **Cumulative Status Log History (`payment_transaction.py`):**
  - All `state_message` writes now use `_append_status_log()` instead of direct overwrite.
  - Each entry is timestamped: `[MM/DD HH:MM] message`.
  - Applies to: Sync results, void, refund, status changes, connection errors, and "not found" responses.
  - Transaction form view label changed from "Last Status" to "Status History" with text widget.

- **Sync Preserves Original Transaction Status (`payment_transaction.py`):**
  - **Bug:** "Check Payment Status" on a cancelled/declined transaction would overwrite `transaction_status` to `error` when bank returned "not found".
  - **Fix:** If `transaction_status` is already terminal (`cancelled`, `declined`, `voided`, `reversed`, `refunded`, `auth_failed`), Sync preserves it. Only `in_progress`/`none` states are updated to `error`.

- **Terminal Decline Message (`payment_screen.js`):**
  - On payment failure, `showDeclineMessage()` is now called to display "Fail" on the terminal screen, giving the customer visual feedback.

- **Support Page — Download Spinners (`support_page.js`, `support_page.xml`):**
  - Server Logs: async download with 1.5s spinner flash on the button for visual feedback.
  - Terminal Logs: spinner while the wizard is being created; resets on wizard open or error via try/finally.
  - Both buttons disabled during download to prevent double clicks.

- **Terminal Log Download — No More New Tab (`terminal_log_wizard.py`):**
  - Changed `target: "new"` → `target: "self"` in `ir.actions.act_url`. Browser downloads the file without opening a new tab.

- **Session Name Auto-Update (`payrillium_terminal.py`):**
  - Changed `pos_config_name` from computed field to `related='pos_config_id.name'`. Terminal list auto-updates when POS config is renamed.

- **Return Product → Redirect to POS Session (`pos_order.py`):**
  - After return/refund, if an active POS session exists, user is redirected to `/pos/ui?config_id=X`.

- **Cancel Button Immediately Visible (`payment_screen.js`):**
  - Removed 30-second delay before showing Cancel button during payment authorization.

- **Execution ID on Tokenized Payments (`payment_screen.js`):**
  - `execution_id` now included in `transactionDataToSave` for tokenized (saved card) payments, enabling proper log correlation.

- **Transaction View Improvements (`woodforest_transaction_views.xml`):**
  - Form view reorganized: Transaction Information and Links & Context in two-column layout.
  - `state_message` field displayed with text widget for multi-line status history.
  - Added `execution_id` field to form view for debugging.

- **Decision Engine — PIN Debit Reversal Recognition (`payment_transaction.py`):**
  - **Bug:** `ics_pin_debit_reversal` was not mapped in any gate. A successful PIN debit reversal (response code "00") would fall through all gates to "Manual Review Required" instead of being recognized as a confirmed void.
  - **Fix:** Added `ics_pin_debit_reversal` to the `void_app` detection list (Gate #2). Now correctly returns "Void Confirmed" when the reversal is successful.

version = "18-1.4.8"

### Release Summary

- **QA-Approved Error Messages (`utils.js`):**
  - All TCODE and CCODE rules updated to match QA-specified "Declined – [Reason]" format.
  - T01: "Operation Cancelled by User", T02: "Declined – Card Chip Error, Please Try Again", T03: "Declined – Tap Interrupted, Please Try Again", T04: "Declined – Transaction Timed Out".
  - C202/C203: "Declined – Expired/Invalid Card", C204: "Declined – Insufficient Funds", C208/C209: "Declined – Please Try a Different Card", etc.
  - M99: "Check Terminal Connection and try again."

- **Terminal-Specific Error Display (`utils.js`, `payment_screen.js`):**
  - Each TCODE/CCODE rule now includes `terminalTitle` and `terminalMsg` fields for the PAX device screen.
  - All decline errors show "Declined / Transaction Failed" on terminal; ABORTED shows "Cancelled / Operation Cancelled".
  - `showDeclineMessage` in `payment_screen.js` now reads these fields from the error instead of hardcoded "Fail".

- **Refund Terminal Message (`payment_screen.js`):**
  - After a verified refund, the PAX terminal now displays "Refunded / Transaction Refunded - $X.XX" via `showApproved`.

- **Expanded STATUS_MAP (`payment_screen.js`):**
  - Error codes now map to proper `transaction_status` instead of generic "error": T01→`cancelled`, T02-T04/T99/M99/C500→`device_error`, C202-C210/C99→`declined`.

- **Tip Retry on Timeout (`payment_screen.js`):**
  - Tip selection now retries automatically on screen inactivity (T04) or communication errors instead of failing the entire payment.
  - Only real cancellations (customer presses Cancel) abort the flow. Shows "Tip timed out — retrying..." message.

- **Terminal ID Fix (`payment_screen.js`, `payment_transaction.py`):**
  - Frontend now sends `terminal_serial` alongside `terminal_id`.
  - Backend resolves terminal by serial when ID is missing (POS proxy serialization issue).
  - Sessions without a terminal assigned (tokenized payments) gracefully save `False`.

- **Terminal Short Display (`payment_transaction.py`, views):**
  - New computed field `terminal_short` shows "...XXXX" (last 4 digits of terminal serial).
  - Added to: backend list view, backend form view, POS transaction detail popup, `get_session_transactions` response.

- **Test Tips Error Banner (`payrillium_terminal.py`):**
  - "Test Tips" button no longer exposes raw Mirillium URLs in error notifications.
  - HTTP errors show "Terminal returned HTTP 500. Check terminal connection." instead of full URL.
  - Exception fallback shows "Could not communicate with terminal. Check connection and try again."

- **Backend Cleanup (`payment_transaction.py`):**
  - Removed duplicate `TERMINAL_CODE_MAP` / `FRIENDLY_MAP` from backend — frontend `utils.js` is the single source of truth.
  - Simplified `_compute_status_summary` to use frontend-provided summary or `STATUS_SUMMARY_MAP` defaults.
  - Cybersource decision engine now uses centralized `STATUS_SUMMARY_MAP`.

version = "18-1.4.9"

### Release Summary

- **Auth Decline Detection Fix (`utils.js`):**
  - `isFailed()` now checks `data.data.state`, `data.data.type`, and `message.status` for DECLINED, REVERSED, VOIDED, and PAX_ERROR responses.
  - Previously only checked `message.status === "DECLINED"` and `data.success === false`, missing all actual decline responses where `success: true` but `state: "DECLINED"`.
  - Fixes false-positive approvals where the POS printed a ticket for a declined transaction.

- **Gateway Status Mapping (`utils.js`):**
  - New `GATEWAY_STATUS_MAP` (Layer 2-C / GCODE) for REVERSED, VOIDED, DECLINED, and PARTIAL_AUTHORIZED auth responses.
  - Each status has a specific user message, terminal title, and terminal body (e.g. "Payment Reversed – Please Try Again").
  - New `_resolveGatewayStatus()` helper integrated into `getErrorDetails()` before `_resolveCcode` fallback.

- **Transaction Status Mapping (`payment_screen.js`):**
  - Auth failures now map to correct `transaction_status` based on gateway response: REVERSED→`reversed`, VOIDED→`voided`, DECLINED→`declined`, else `auth_failed`.
  - Backend `STATUS_SUMMARY_MAP` updated: `reversed` and `voided` now show "– Please Try Again" messages.

- **Refund Verification Fix (`api_service.js`):**
  - Added fallback data path for `msg` and `state` in `authorizePayment`, `refundDebit`, and `refundCapture` to handle both direct and wrapped polling response structures.
  - Added `response.status === 200` check alongside `"ok"` in all three verification methods (polling returns numeric 200, not string "ok").

- **Emoji Cleanup:**
  - Removed all emoji characters from console.log/warn/error messages across all JS files.

- **Transaction List View (In Development — Not included in this release):**
  - POS Transaction List screen with session/employee filtering and toggle is under active development.
  - Menu entry is currently hidden (`navbar_transactions.xml`). Will be enabled in a future version after QA.

- **Terminal App Versions:**
  - Desktop: 1.50.7
  - PaySDK: 1.24.2

version = "18-1.5.1"

### Release Summary (Session Tasks for Review)

1. **Asset Pipeline Compilation Fix (CSS):** Identified and removed external `@import` statements causing `web.assets_web` compilation failures. Applied strict `.faq-dashboard-wrapper` CSS scoping and purged `ir_attachment` DB tables to restore global Odoo 18 UI stability.
2. **Task-First Dashboard Migration (UI):** Converted the printable `faq_page.xml` document into a Troubleshooting Dashboard. Implemented a split-view layout prioritizing user decision mapping (Status Check) vs Actions (Troubleshooting Steps).
3. **QWeb Strict XML Parsing Override (XML):** Resolved an `Entity 'rarr' not defined` compilation crash in QWeb by replacing strict HTML `&rarr;` entities with the literal `→` UTF-8 character.
4. **Dashboard Hierarchy Balance (CSS/UI):** Condensed the dual YES/NO status cards into a unified decision check. Adjusted column weighting (32% vs 68% viewport) to draw user focus strictly toward the actionable troubleshooting column.
5. **Master Tray & Grid Reflow (Layout):** Wrapped the dashboard top section in a unified `.db-master-tray`. Enforced a strict 2x2 grid (`grid-template-columns: repeat(2, 1fr)`) for troubleshooting steps to prevent orphan elements, and merged the Icon Glossary and Rules into a side-by-side `.db-unified-reference` container.
6. **POS OWL Lifecycle Crash Fix (JS):** Resolved a hard crash during Odoo POS session initialization natively linked to a `ReferenceError`. Injected `import { onMounted } from "@odoo/owl";` in `navbar_patch.js` where the lifecycle hook was invoked unassigned.
7. **Terminal Interactive Pinging Loader (UX):** Added immediate visual feedback to the POS navbar terminal indicator. Modified `checkTerminalStatus()` to apply a `"pinging"` state to Odoo's reactive `pos` object, swapping the indicator text to "Checking Terminal..." and injecting a FontAwesome `.fa-spin` spinning icon during RPC await.

version = "18-1.5.2"

### Release Summary

1. **Critical: Double Refund Fix (`payment_screen.js`):**
   - **Bug:** Refund was executing twice against the terminal/processor. The setup wrapper's `validateOrder` called `_handleRefund` (success), then fell through to `_chainedValidate` which triggered the prototype `validateOrder` — which also detected `isRefund` and called `_handleRefund` again. Second refund failed with `CLDS-TERMINAL_BUSY-000011` and "Component is destroyed" errors.
   - **Root Cause:** The `_chainedValidate` wrapper introduced in 1.5.1 for `pos_loyalty` compatibility did not account for the refund path, which already calls `super.validateOrder()` internally from `_finalizeRefundTransaction`.
   - **Fix:** Added `return` after `_handleRefund` in the setup wrapper so it exits immediately after a successful refund. Added defense-in-depth: prototype `validateOrder` now filters refund lines by `payment_status !== "done" && !transaction_id`, preventing re-processing of already-completed refunds.

2. **M99/M98 Escalation — Stop Retry Loop (`utils.js`):**
   - When unknown errors (M99: explicit failure with unmapped code, M98: ambiguous response with no success confirmation) repeat **2+ times consecutively**, messages now escalate to prevent operators from retrying endlessly:
     - **POS popup:** "Unexpected response from terminal. Contact Support immediately."
     - **Terminal screen:** "Contact Support" / "Await merchant instructions"
   - Counter lives in `sessionStorage` (survives page refresh, clears on tab close). Resets to 0 on any successful response.

3. **Recovery Flow Restored — Auth Abort (`payment_screen.js`):**
   - **Bug:** When operator cancelled during auth, the transaction was marked `state: "cancel"` / `transaction_status: "cancelled"`, hiding it from the Recovery button.
   - **Fix:** Abort during auth now keeps `state: "pending"` / `transaction_status: "in_progress"` so Recovery detects it and lets the cashier verify with the processor.

4. **Recovery Flow Extended — Comm/Cloud Errors (`payment_screen.js`):**
   - When auth was already sent to the terminal but a communication or cloud error is received (HTTP 409/500, MQTT timeout, CLDS errors), the outcome is uncertain — the charge may have gone through.
   - These transactions now stay as `in_progress` for Recovery instead of being marked `error`.
   - Same logic applied to refund/void: new `_refundWasStarted` flag tracks when the refund request was sent. If a comm error occurs after that point, the transaction is marked `in_progress` and `fetch_stuck_transactions()` is called to immediately show the Recovery button.

5. **Navbar Reorder (`pos_terminal_status.xml`):**
   - Terminal status LED and Recovery button now appear **before** the hamburger menu (☰), not after it.
   - Changed XPath from `position="inside"` to `position="before"` targeting the `<Dropdown>` component.

6. **Recovery Button Redesign (`pos_terminal_status.xml`, `woodforest.css`):**
   - Replaced flat red rectangle with a compact pill-shaped button using gradient background, rounded corners, and a circular count badge.
   - Added subtle `pulse-recover` CSS animation (red glow pulse) to draw attention without being aggressive.

7. **Refund Abort → Recovery (`payment_screen.js`):**
   - **Bug:** Aborting a refund while it was in flight marked the transaction as `error` instead of `in_progress`, preventing the Recovery button from detecting it.
   - **Fix:** Added `this._payrilliumAborted` and `error.cancelled` to the `isUncertain` condition in the refund catch block. Aborted refunds now stay as `in_progress` for Recovery.

8. **Missing Session Data on Refund Transactions (`payment_screen.js`):**
   - Refund `_createPayrilliumTransaction` calls (early persistence, error catch, and finalize) were missing `execution_id` and `pos_session_id`.
   - These transactions were invisible in the POS session transaction list because `get_my_transactions` filters by session.
   - Added both fields to all 3 refund call sites + the token payment success call site.

9. **Refund Early Persistence as `in_progress` (`payment_screen.js`):**
   - Refund transactions were created with `transaction_status: "credit_refund_pending"` before sending to the terminal, but Recovery only detects `in_progress`.
   - Changed to `transaction_status: "in_progress"` to match the auth flow pattern. No snapshot needed since refund data lives in the original order.

10. **Refund Badge in POS Transaction List (`transaction_list_screen.xml`):**
    - Refund transactions (reference ending in `r` or `rd`) now display a subtle indigo `↩ Refund` pill badge next to the reference.
    - Amount is shown as negative (`-$13.80`) in indigo color to visually differentiate from charges at a glance.

version = "18-1.5.3"

### Release Summary

1. **Critical: Stale State Flags Between Payment Attempts (`payment_screen.js`):**
   - **Bug:** `_authWasStarted` and `_refundWasStarted` flags were never reset at the start of `_processPayrilliumPayment`. If a previous payment attempt reached the auth step and failed, the next attempt inherited `_authWasStarted = true`. If that second attempt then failed during the tip phase (e.g., Terminal Busy), the catch block incorrectly evaluated `_authWasStarted` as true and could mark the transaction as `in_progress` — creating a "zombie" transaction without a recovery snapshot.
   - **Fix:** Both flags are now explicitly reset to `false` at the beginning of each `_processPayrilliumPayment` call, alongside the existing `_payrilliumAborted = false` reset.

2. **Critical: Tip Selection Swallowing Generic Errors (`api_service.js`):**
   - **Bug:** In `showTipSelection`, the catch block only re-threw errors with specific Payrillium properties (`payrilliumError`, `mcCode`, `terminalConnectionError`). Generic errors from `rpc()` (session expired, network failure, Odoo 500) were silently caught and logged — causing the function to return `tipAmount = 0` as if the customer selected "No Tip". The payment then continued to auth with an incorrect amount.
   - **Fix:** All non-Payrillium errors are now re-thrown (`throw error`) so they propagate to the main catch block in `_processPayrilliumPayment`, which properly marks the transaction as error and shows a popup to the cashier.

3. **Sync Status Overwriting Refund Status (`payment_transaction.py`):**
   - **Bug:** When "Check Payment Status" was clicked on a credit refund transaction, Cybersource reported the refund as `PENDING` or `TRANSMITTED` (waiting for settlement). The state mapping converted this to `captured`, which displays as "Approved" — overwriting the correct `credit_refunded` ("Refunded") status set by the POS.
   - **Fix:** New `_resolve_sync_status()` method centralizes the transaction_status resolution for both manual and silent sync paths. It guards against overwriting refund statuses (`credit_refunded`, `debit_refunded`, `refunded`, `credit_refund_pending`, `debit_refund_pending`) with `captured` from the sync response.

4. **Role-Based Transaction Visibility (`security/woodforest_transaction_rules.xml`, views):**
   - Regular POS users (`group_pos_user`) now only see transactions they created.
   - POS managers (`group_pos_manager`) see all transactions and can search/group by "Cashier" in the Woodforest Transactions list view.
   - Implemented via `ir.rule` record rules on `payment.transaction`.

5. **POS Cashier-Level Auditing & Privacy:**
   - **Cashier Tracking:** Added `pos_employee_id` to transactions to capture the physical person selling, independent of the Odoo login user.
   - **Manager Dashboard:** Managers now have a new "Cashier" dropdown in the POS Transaction Screen to filter sales by employee.
   - **Privacy for Sellers:** Regular cashiers are now restricted to seeing only their own transactions (this session and history).
   - **Audit Trail:** Added "Cashier" columns to the POS transaction table and the Odoo backend list view for quick identification of who processed each payment.
6. **UI Polishing & Receipt Updates:**
   - **Transaction List Refactor:** Replaced the confusing "All Cashiers" dropdown with a clean "Profile Switcher" button that only appears for Managers (`pos.config.advanced_employee_ids`). The UI now gracefully adapts to single-user POS setups without errors.
   - **Controls Bar Styling:** Improved styling of the transaction list top bar by preventing text wrapping (`text-nowrap`) and standardizing button sizes, making the UI slimmer and more elegant.
   - **Receipt Cashier Info:** Explicitly injected the Cashier's name into the Terminal Information block at the bottom of the printed receipt, ensuring the seller is identifiable even if only the credit card slip is retained.
   - **Backend Filter Fix:** Fixed a logic bug in the backend query where filtering by employee erroneously returned all legacy transactions missing a `pos_employee_id`. The fallback now correctly targets `pos_order_id.employee_id`.
7. **OWL Reactivity & Odoo 18 Compatibility Fixes:**
   - **Cashier Switch Reactivity:** Made `activeCashierId` part of the reactive `useState` and utilized `onWillRender` to ensure the UI instantly redraws the Profile Switcher when the user switches cashiers mid-session.
   - **Strict Role Validation:** Removed the fallback that allowed Odoo Administrator users to bypass POS cashier restrictions, ensuring the UI strictly enforces the selected POS cashier's profile.
   - **Odoo 18 Proxy Resolution:** Fixed a bug where Odoo 18 returns an array of model Proxies for `pos.config.advanced_employee_ids` instead of integers, preventing the manager dropdown from appearing. Correctly mapped the proxies to their IDs.
