import logging
from odoo import models, _, SUPERUSER_ID, fields, api
from ..services.webhook_service import (
    process_payment_links_notifications_for_invoice,
    process_ach_transactions_status_for_invoice,
)
from uuid import uuid4
from ..services.logging_service import log_payrillium_event
from ..services.mirillium import sync_existing_payment_links
_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    payrillium_configured = fields.Boolean(
        string="Woodforest Configured",
        compute="_compute_payrillium_configured",
        store=False,
        help="Indicates if Woodforest is configured and ready to use"
    )

    @api.depends()
    def _compute_payrillium_configured(self):
        """Check if Woodforest is installed and configured"""
        config = self.env['payrillium.config'].sudo().search([], limit=1)
        is_configured = bool(config and config.installed and config.token)
        for record in self:
            record.payrillium_configured = is_configured

# ─────────────────────────────────────────────
#  Cron to check payment status
# ─────────────────────────────────────────────
    def cron_check_payment_status(self):
        """
        Cron runs from ir.cron. It only looks for invoices that:
        - have an active payrillium.payment.link that hasn't been checked in the last 15 minutes, or
        - are related to pending ACH transactions (reference ilike 'ACHINV-%', state = pending)
        Then it calls action_get_payment_status_now() for each invoice (executed as SUPERUSER).
        """
        execution_id = f"cron-payment-status-{uuid4()}"

        PayLink = self.env['payrillium.payment.link'].sudo()
        Transaction = self.env['payment.transaction'].sudo()

        # Only check links that haven't been checked in the last 15 minutes
        from datetime import timedelta
        cutoff_time = fields.Datetime.now() - timedelta(minutes=15)

        link_invoice_ids = PayLink.search([
            ('status', '=', 'active'),
            '|',
            ('last_check_date', '=', False),
            ('last_check_date', '<', cutoff_time)
        ]).mapped('invoice_id.id') or []

        ach_txs = Transaction.search([
            ('reference', 'ilike', 'ACHINV-%'),
            ('state', 'in', ['pending']),
        ])
        ach_invoice_ids = ach_txs.mapped('invoice_ids.id') or []

        invoice_ids = list(set(link_invoice_ids + ach_invoice_ids))

        log_payrillium_event(
            execution_id,
            "cron.payment_status",
            "start",
            {"candidates": invoice_ids},
            success=True,
            env=self.env,
        )

        if not invoice_ids:
            totals = {"invoices": 0, "processed_invoices": 0, "links_processed": 0, "ach_processed": 0, "links_total": 0,
                      "ach_total": 0, "errors": 0, "per_invoice": []}
            log_payrillium_event(
                execution_id,
                "cron.payment_status",
                "finish",
                totals,
                success=True,
                env=self.env,
            )
            _logger.debug(
                "cron_check_payment_status: no invoice ids found (no active links nor ACH pending txs).")
            return True

        domain = [
            ('id', 'in', invoice_ids),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('state', '=', 'posted'),
        ]
        invoices = self.search(domain)

        if not invoices:
            totals = {"invoices": 0, "processed_invoices": 0, "links_processed": 0, "ach_processed": 0, "errors": 0,
                      "per_invoice": []}

            log_payrillium_event(
                execution_id,
                "cron.payment_status",
                "finish",
                totals,
                success=True,
                env=self.env,
            )
            return True

        su_env = self.env(user=SUPERUSER_ID)

        totals = {
            "invoices": len(invoices),
            "processed_invoices": 0,
            "links_processed": 0,
            "ach_processed": 0,
            "links_total": 0,
            "ach_total": 0,
            "errors": 0,
        }
        per_invoice = []

        _logger.info("cron_check_payment_status: found %d invoices to check (ids=%s)", len(
            invoices), invoices.ids)
        for inv in invoices:
            try:
                res = inv.with_env(su_env).with_context(
                    from_cron=True,
                    cron_execution_id=execution_id,
                ).action_get_payment_status_now()

                totals["processed_invoices"] += 1

                if isinstance(res, dict):
                    pl = int(res.get("processed_links") or 0)
                    tl = int(res.get("total_links") or 0)
                    pa = int(res.get("processed_ach") or 0)
                    ta = int(res.get("total_ach") or 0)

                    totals["links_processed"] += pl
                    totals["ach_processed"] += pa
                    totals["links_total"] += tl
                    totals["ach_total"] += ta

                    per_invoice.append({
                        "invoice_id": inv.id,
                        "invoice_number": inv.name,
                        "customer": inv.partner_id.display_name,
                        "links": {"processed": pl, "total": tl, "ok": bool(res.get("ok_links", True))},
                        "ach":   {"processed": pa, "total": ta, "ok": bool(res.get("ok_ach", True))}
                    })

            except Exception:
                totals["errors"] += 1
                _logger.exception(
                    "cron_check_payment_status: error processing invoice %s", inv.id)

        payload = {
            **totals,
            "per_invoice": per_invoice,
        }

        log_payrillium_event(
            execution_id,
            "cron.payment_status",
            "finish",
            payload,
            success=(totals["errors"] == 0),
            env=self.env,
        )

        # Note: Removed global sync_existing_payment_links() to reduce API calls
        # Individual links are already updated during processing with last_check_date
        # Full sync should be done manually via "Sync Payment Links" button if needed

        return True

# ─────────────────────────────────────────────
#  Action to check payment status
# ─────────────────────────────────────────────
    def action_get_payment_status_now(self):

        self.ensure_one()
        is_cron = bool(self.env.context.get("from_cron"))
        ok_links, processed_links, total_links = True, 0, 0
        ok_ach, processed_ach, total_ach = True, 0, 0

        if self.move_type not in ("out_invoice", "out_refund"):
            return ({"ok_links": True, "processed_links": 0, "total_links": 0,
                     "ok_ach": True, "processed_ach": 0, "total_ach": 0, "invoice_id": self.id}
                    if is_cron else {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {"title": "Get Payment Status", "message": "Unsupported move type.", "type": "warning"}})

        if self.state != "posted":
            return ({"ok_links": True, "processed_links": 0, "total_links": 0,
                     "ok_ach": True, "processed_ach": 0, "total_ach": 0, "invoice_id": self.id}
                    if is_cron else {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {"title": "Get Payment Status", "message": "Invoice must be posted.", "type": "warning"}})

        PayLink = self.env['payrillium.payment.link'].sudo()
        Transaction = self.env['payment.transaction'].sudo()

        payment_links = PayLink.search(
            [('invoice_id', '=', self.id), ('status', '=', 'active')])
        ach_pending_txs = Transaction.search([
            ('reference', 'ilike', 'ACHINV-%'),
            ('state', 'in', ['pending']),
            ('invoice_ids', 'in', [self.id]),
        ])

        _logger.info("Links: %s", payment_links)
        _logger.info("ACH: %s", ach_pending_txs)

        if not payment_links and not ach_pending_txs:
            return ({"ok_links": True, "processed_links": 0, "total_links": 0,
                     "ok_ach": True, "processed_ach": 0, "total_ach": 0, "invoice_id": self.id}
                    if is_cron else {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {"title": "Get Payment Status", "message": "No active payment links or pending ACH transactions for this invoice.", "type": "info", "sticky": True},
            })

        _logger.info(" Checking status: %d payment links, %d ACH pending transactions",
                     len(payment_links), len(ach_pending_txs))

        # 1) Check links first. If any link payment processed -> stop everything.
        if payment_links:
            ok_links, processed_links, total_links = process_payment_links_notifications_for_invoice(
                self.env, self, payment_links)
            _logger.info("Links: ok=%s processed=%s total=%s",
                         ok_links, processed_links, total_links)

            # Update last_check_date for all checked links
            payment_links.write({'last_check_date': fields.Datetime.now()})

        # 2) Only if no link-payment found, check ACH pending transactions
        if ach_pending_txs:
            ok_ach, processed_ach, total_ach = process_ach_transactions_status_for_invoice(
                self.env, self, ach_pending_txs)
            _logger.info("ACH: ok=%s processed=%s total=%s",
                         ok_ach, processed_ach, total_ach)

        if is_cron:
            return {
                "invoice_id": self.id,
                "ok_links": ok_links, "processed_links": processed_links, "total_links": total_links,
                "ok_ach": ok_ach, "processed_ach": processed_ach, "total_ach": total_ach,
            }

        if processed_ach and processed_ach > 0:
            msg = _("Found and processed ACH payment.")
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"title": "Get Payment Status", "message": msg, "type": "success", "sticky": True}
            }

        # 3) Nothing found -> show summary
        total_processed = (processed_links or 0) + (processed_ach or 0)
        total_items = (total_links or 0) + (total_ach or 0)

        if total_items == 0:
            # Show different message if there are active links but no payments yet
            if payment_links:
                msg = _("%d payment link(s) are active, but no payments received yet. Customers can still pay using the link.") % len(
                    payment_links)
                msg_type = "info"
            else:
                msg = _("No payments found.")
                msg_type = "warning"

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"title": "Get Payment Status", "message": msg, "type": msg_type, "sticky": True}
            }

        msg = _("Processed %d/%d payment(s).") % (total_processed, total_items)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": "Get Payment Status", "message": msg, "type": "success", "sticky": True}
        }

    def action_get_payment_status_and_sync(self):
        """
        Get payment status for this invoice.
        Note: Removed global sync to reduce API calls. 
        Use the "Sync Payment Links" button in Settings if full sync is needed.
        """
        self.ensure_one()
        return self.action_get_payment_status_now()

