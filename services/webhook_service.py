from odoo import api
from uuid import uuid4
import logging
from ..services.logging_service import log_payrillium_event
from ..services.mirillium import patch_payment_link
from ..services.mirillium.api import fetch_payment_link_notifications, fetch_ach_transaction_status

_logger = logging.getLogger(__name__)


def process_webhook_payload(env, event_type, data):
    execution_id = str(uuid4())
    try:
        _logger.info(f"[Webhook] Received event_type: {event_type}")
        log_payrillium_event(execution_id, event_type,
                             "request", data, env=env)

        if event_type == 'payByLink.merchant.payment':
            return _handle_pay_by_link(env, data, execution_id)
        elif event_type == 'ACH_PAYMENT_STATUS_UPDATE':
            return _handle_ACH_PAYMENT_STATUS_UPDATE(env, data, execution_id)
        elif event_type == 'manual.token.payment':
            return _handle_manual_token_payment(env, data, execution_id)

        else:
            _logger.warning(f"Unhandled event_type: {event_type}")
            return None

    except Exception as e:
        _logger.error(f"Error processing event {event_type}: {e}")
        log_payrillium_event(execution_id, event_type, "response",
                             None, success=False, error_message=str(e), env=env)
        return False


def _handle_pay_by_link(env, data, execution_id):
    _logger.info("Handling payByLink.customer.payment")
    _logger.info(f"  - data: {data}")

    link_id = data.get('linkId')
    provider_reference = data.get('transactionId')
    amount = data.get('totalAmount')
    currency = data.get('currency', 'USD')

    _logger.info(f"  - link_id: {link_id}")
    _logger.info(f"  - provider_reference: {provider_reference}")
    _logger.info(f"  - amount: {amount}")
    _logger.info(f"  - currency: {currency}")

    payment_link = env['payrillium.payment.link'].sudo().search([
        ('payment_link_id', '=', link_id)
    ], limit=1)

    _logger.info(f"  - payment_link found: {payment_link}")

    if not payment_link:
        _logger.error(f"No payment link found for linkId: {link_id}")
        return False

    invoice = payment_link.invoice_id
    _logger.info(f"  - invoice found: {invoice}")

    if not invoice or not invoice.exists():
        _logger.error("Invoice not found")
        return False
    if invoice.state == 'cancel':
        _logger.warning("Invoice is canceled")
        return False
    if invoice.state not in ['posted']:
        _logger.warning("Invoice is not posted")
        return False

    currency_symbol_map = {'$': 'USD', '€': 'EUR'}
    currency_code = currency_symbol_map.get(currency, currency)
    currency_id = env['res.currency'].sudo().search(
        [('name', '=', currency_code)], limit=1).id

    _logger.info(f"  - currency_code: {currency_code}")
    _logger.info(f"  - currency_id: {currency_id}")

    provider = env['payment.provider'].sudo().search(
        [('code', '=', 'woodforest')], limit=1)
    _logger.info(f"  - provider found: {provider}")

    if not provider:
        _logger.error("Payment provider not found")
        return False

    payment_method = env['pos.payment.method'].sudo().search([
        ('payment_provider_id', '=', provider.id)
    ], limit=1)

    _logger.info(f"  - payment_method found: {payment_method}")

    base_ref = f"INVLINK-{invoice.id}-{link_id}"
    Transaction = env['payment.transaction'].sudo()
    count = Transaction.search_count([('reference', 'ilike', f"{base_ref}%")])
    reference = f"{base_ref}-{count + 1}" if count else base_ref
    is_duplicate = count > 0

    _logger.info(f"  - base_ref: {base_ref}")
    _logger.info(f"  - count: {count}")
    _logger.info(f"  - reference: {reference}")
    _logger.info(f"  - is_duplicate: {is_duplicate}")

    tx_vals = {
        'reference': reference,
        'provider_reference': provider_reference,
        'amount': amount,
        'currency_id': currency_id,
        'partner_id': invoice.partner_id.id,
        'state': 'draft',
        'payment_method_id': payment_method.id,
        'provider_id': provider.id,
        'invoice_ids': [(6, 0, [invoice.id])],
        'is_duplicate': is_duplicate,
    }

    _logger.info(f"  - Creating transaction with vals: {tx_vals}")

    try:
        tx = Transaction.create(tx_vals)
        _logger.info(f"  - Transaction created: {tx}")

        if is_duplicate:
            _logger.info(f"  - Setting transaction as canceled (duplicate)")
            tx._set_canceled()
        else:
            _logger.info(f"  - Setting transaction as done")
            tx._set_done()
            _logger.info(f"  - Transaction state after _set_done: {tx.state}")
            _logger.info(
                f"  - Transaction payment_id after _set_done: {tx.payment_id}")

    except Exception as e:
        _logger.error(f"  Error in _handle_pay_by_link: {e}")
        raise

    links = env['payrillium.payment.link'].sudo().search([
        ('invoice_id', '=', invoice.id),
    ])

    for item in links:
        try:
            patch_payment_link(item, "INACTIVE")
            log_payrillium_event(execution_id, 'payByLink.customer.payment', " try-inactive-duplicate", {
                "status": "inactive",
                "link": item.payment_link_id
            }, success=True)
            item.write({'status': 'inactive'})

        except Exception as e:
            _logger.exception("Failed to patch external status for link %s: %s", getattr(
                item, 'payment_link_id', item.id), e)

    log_payrillium_event(execution_id, 'payByLink.customer.payment', "response", {
        "status": "handled",
        "tx_id": tx.id
    }, success=True, env=env)

    return True


def _handle_ACH_PAYMENT_STATUS_UPDATE(env, data, execution_id):
    _logger.info("Handling ACH_PAYMENT_STATUS_UPDATE")

    provider_reference = data.get("id")
    if not provider_reference:
        _logger.error("Missing transaction id in ACH webhook")
        return False

    status = data.get("processorInformation", {}).get(
        "eventStatus", "").upper()

    Transaction = env['payment.transaction'].sudo()
    tx = Transaction.search(
        [('provider_reference', '=', provider_reference)], limit=1)

    if not tx:
        _logger.error(
            f"No transaction found with provider_reference={provider_reference}")
        return False

    _logger.info(
        f"Found transaction {tx.id} with state={tx.state}, ACH status={status}")

    if tx.state == "pending" and status in ["TRANSMITTED", "COMPLETED"]:
        tx._set_done()
        _logger.info(f"Transaction {tx.id} moved from PENDING to DONE")

        log_payrillium_event(
            execution_id,
            'ACH_PAYMENT_STATUS_UPDATE',
            "response",
            {"status": "done", "tx_id": tx.id},
            success=True,
            env=env
        )
        return True

    _logger.warning(
        f"No state change applied for tx {tx.id} with status={status}")
    return False


def _handle_manual_token_payment(env, data, execution_id):
    _logger.info("Handling manual.token.payment")

    provider_reference = data.get(
        'cybersource_tx_id') or data.get('transactionId')
    amount = data.get('amount')
    currency = data.get('currency', 'USD')
    invoice_id = data.get('invoice_id')
    partner_id = data.get('partner_id')
    status = data.get('status', '').lower()

    if not invoice_id:
        _logger.error("Missing invoice_id")
        return False

    invoice = env['account.move'].sudo().browse(invoice_id)
    if not invoice or not invoice.exists():
        _logger.error("Invoice not found")
        return False
    if invoice.state == 'cancel':
        _logger.warning("Invoice is canceled")
        return False
    if invoice.state not in ['posted']:
        _logger.warning("Invoice is not posted")
        return False

    # Currency & provider
    currency_symbol_map = {'$': 'USD', '€': 'EUR'}
    currency_code = currency_symbol_map.get(currency, currency)
    currency_id = env['res.currency'].sudo().search(
        [('name', '=', currency_code)], limit=1).id

    provider = env['payment.provider'].sudo().search(
        [('code', '=', 'woodforest')], limit=1)
    if not provider:
        _logger.error("Provider not found")
        return False

    payment_method = env['pos.payment.method'].sudo().search([
        ('payment_provider_id', '=', provider.id)
    ], limit=1)
    type_payment = data.get('type_payment')

    prefix = "TOKINV" if type_payment in ['TOKENIZED_CARD', 'tokenized_credit', 'tokenized_debit'] else "ACHINV"
    base_ref = f"{prefix}-{invoice.id}-{provider_reference}"

    tx_model = env['payment.transaction'].sudo()
    count = tx_model.search_count([('reference', 'ilike', f"{base_ref}%")])
    reference = f"{base_ref}-{count + 1}" if count else base_ref
    is_duplicate = count > 0

    tx = tx_model.create({
        'reference': reference,
        'provider_reference': provider_reference,
        'amount': amount,
        'currency_id': currency_id,
        'partner_id': partner_id or invoice.partner_id.id,
        'state': 'draft',
        'payment_method_id': payment_method.id,
        'provider_id': provider.id,
        'invoice_ids': [(6, 0, [invoice.id])],
        'is_duplicate': is_duplicate,
    })

    _logger.info(f"Transaction created: {tx}")

    if is_duplicate:
        tx._set_canceled()
    else:
        if status in ["authorized", "done", "success"]:
            tx._set_done()
        elif status in ["pending"]:
            tx._set_pending()
        else:
            _logger.warning(f"Unhandled payment status: {status}")
            tx._set_error()

    log_payrillium_event(execution_id, 'manual.token.payment', "response", {
        "status": "handled",
        "tx_id": tx.id
    }, success=True, env=env)

    return True


# ─────────────────────────────────────────────
#  Sync invoice payments from mirillium
# ─────────────────────────────────────────────
def process_payment_links_notifications_for_invoice(env, invoice, payment_links):
    """
    Process ALL notifications/payments for the given invoice's payment links.

    Behavior:
    - For each payment link, fetch notifications from Mirillium.
    - For each notification (and/or payment item), call process_webhook_payload(...).
    - Processes ALL items; it does NOT early-exit after the first success.
    - Accumulates counts across all links and items.

    Returns:
      (ok: bool, processed_count: int, total_count: int)
        ok: True if the overall loop ran without unhandled exceptions
            (individual item failures are tolerated and logged).
        processed_count: number of items for which process_webhook_payload returned truthy.
        total_count: total items inspected across all links.
    """
    execution_id = str(uuid4())
    total_processed = 0
    total_items = 0
    try:
        if not payment_links:
            log_payrillium_event(execution_id, "pullSync.links", "response", {
                                 "message": "No payment links provided"}, success=True, env=env)
            return True, 0, 0

        for link in payment_links:
            link_id = link.payment_link_id
            if not link_id:
                _logger.warning(
                    "Skipping link record %s without payment_link_id", link.id)
                continue

            res = fetch_payment_link_notifications(invoice, link_id, env)
            if not res.get("success"):
                _logger.warning(
                    "Failed fetching notifications for link %s: %s", link_id, res.get("message"))
                log_payrillium_event(execution_id, "payment/link_notifications", "response", {
                                     "link_id": link_id, "message": res.get("message")}, success=False, env=env)
                continue

            notifications = res.get("webhook_notifications", []) or []
            total_items += len(notifications)

            # Process webhook_notifications array; same early-exit behavior
            for n in notifications:
                event_type = n.get("eventType") or (
                    n.get("metadata") or {}).get("eventType")
                payload = (n.get("metadata") or {}).get(
                    "payload", {}).get("data") or {}
                if not event_type:
                    _logger.warning(
                        "Webhook notification without eventType for link %s: %s", link_id, n)
                    continue
                try:
                    ok = process_webhook_payload(env, event_type, payload)
                    if ok:
                        total_processed += 1
                        log_payrillium_event(execution_id, "pullSync.links", "info", {
                                             "link_id": link_id, "message": "Found and processed payment via webhook_notifications"}, success=True, env=env)
                except Exception:
                    _logger.exception(
                        "Error processing webhook notification for link %s", link_id)
                    # continue on error

        # finished all links without finding a processed payment
        log_payrillium_event(execution_id, "pullSync.links", "response", {
                             "processed": total_processed, "total": total_items}, success=True, env=env)
        return True, total_processed, total_items

    except Exception as e:
        _logger.exception(
            "Error in process_payment_links_notifications_for_invoice for invoice %s: %s", invoice.id, e)
        log_payrillium_event(execution_id, "pullSync.links", "response",
                             None, success=False, error_message=str(e), env=env)
        return False, 0, 0


def process_ach_transactions_status_for_invoice(env, invoice, ach_pending_txs):
    """
    Process ALL pending ACH transactions for the given invoice.

    Behavior:
    - For each pending ACH tx, call the transactionStatus endpoint.
    - Build the handler payload and call process_webhook_payload(env, 'ACH_PAYMENT_STATUS_UPDATE', ...).
    - Processes ALL transactions; it does NOT early-exit after the first success.
    - Accumulates counts across all transactions.

    Returns:
      (ok: bool, processed_count: int, total_count: int)
        ok: True if the overall loop ran without unhandled exceptions
            (individual item failures are tolerated and logged).
        processed_count: number of transactions for which the handler returned truthy.
        total_count: total pending ACH transactions inspected.
    """

    execution_id = str(uuid4())
    total_processed = 0
    total_items = 0
    try:
        if not ach_pending_txs:
            log_payrillium_event(execution_id, "pullSync.ach", "response", {
                                 "message": "No ACH pending transactions provided"}, success=True, env=env)
            return True, 0, 0

        for tx in ach_pending_txs:
            payment_id = tx.provider_reference or tx.reference or None
            execution_id = execution_id + "_" + str(payment_id)

            if not payment_id:
                _logger.warning(
                    "Skipping ACH tx %s without provider_reference/reference", tx.id)
                continue

            total_items += 1
            res = fetch_ach_transaction_status(invoice, payment_id, env)
            if not res.get("success"):
                _logger.warning(
                    "Failed fetching ACH status for payment %s: %s", payment_id, res.get("message"))
                log_payrillium_event(execution_id, "payment/transaction_status", "response", {
                                     "payment_id": payment_id, "message": res.get("message")}, success=False, env=env)
                continue

            data = res.get("data") or {}
            remote_status = (data.get("status") or (
                data.get("metadata") or {}).get("status") or "").upper()

            payload_for_handler = {
                "id": payment_id,
                "processorInformation": {"eventStatus": remote_status}
            }

            try:
                ok = process_webhook_payload(
                    env, "ACH_PAYMENT_STATUS_UPDATE", payload_for_handler)
                if ok:
                    total_processed += 1
                    log_payrillium_event(execution_id, "pullSync.ach", "info", {
                                         "payment_id": payment_id, "message": "Found and processed ACH payment"}, success=True, env=env)
            except Exception:
                _logger.exception(
                    "Error processing ACH status for payment %s", payment_id)

        # finished all ACH items without finding a processed payment
        log_payrillium_event(execution_id, "pullSync.ach", "response", {
                             "processed": total_processed, "total": total_items}, success=True, env=env)
        return True, total_processed, total_items

    except Exception as e:
        _logger.exception(
            "Error in process_ach_transactions_status_for_invoice for invoice %s: %s", invoice.id, e)
        log_payrillium_event(execution_id, "pullSync.ach", "response",
                             None, success=False, error_message=str(e), env=env)
        return False, 0, 0

