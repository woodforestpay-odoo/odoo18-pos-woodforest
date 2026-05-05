from odoo import models, api, fields, _
import logging
from odoo.exceptions import UserError
from odoo.osv.expression import AND
from ..services.mirillium.api import get_payment_status
from odoo.tools.float_utils import float_compare
_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"
    
    is_recovery_order = fields.Boolean(string="Is Recovery Order", default=False, copy=False)
    is_superseded = fields.Boolean(string="Is Superseded", default=False, copy=False, help="This order was interrupted and replaced by a recovery order.")
    superseded_order_id = fields.Many2one('pos.order', string="Superseded Order", readonly=True, copy=False)
    recovery_order_id = fields.Many2one('pos.order', string="Recovery Order", readonly=True, copy=False)

    # Receipt and Transaction Metadata
    extra_payment_data = fields.Text(string="Extra Payment Data", help="Serialized transaction metadata for the receipt.")
    _all_payment_data = fields.Text(string="All Payment Data", help="Serialized list of all transaction metadata (for split payments).")
    transaction_id = fields.Char(string="Transaction ID", help="Legacy field for terminal transaction ID stored on the order.")

    def action_pos_order_paid(self):
        self.ensure_one()
        if self.is_superseded:
            raise UserError(_("Cannot finalize this order because it has been superseded by a recovery order (%s).") % (self.recovery_order_id.name or "N/A"))
        return super(PosOrder, self).action_pos_order_paid()

    def action_mark_superseded(self, recovery_order_id):
        self.ensure_one()
        self.write({
            'is_superseded': True,
            'recovery_order_id': recovery_order_id,
        })
        self.message_post(body=_("This order has been superseded by recovery order: %s") % self.env['pos.order'].browse(recovery_order_id).name)

    @api.model_create_multi
    def create(self, vals_list):
        """After creating POS orders, auto-link any orphan Woodforest transactions
        that were created before the order was saved."""
        orders = super().create(vals_list)
        for order in orders:
            # V3.6.1: Robust backlinking for native recovery
            # recovered payment_line.transaction_id contains tx.reference
            payment_tx_ids = [p.transaction_id for p in order.payment_ids if p.transaction_id]
            
            if not payment_tx_ids and not order.pos_reference:
                continue

            domain = [
                ('provider_id.code', '=', 'woodforest'),
                ('pos_order_id', '=', False),
            ]
            
            # Match by reference (most common in reconstruction) or provider_reference
            tx_match = ['|', ('reference', 'in', payment_tx_ids), ('provider_reference', 'in', payment_tx_ids)]
            
            if order.pos_reference:
                domain = AND([domain, ['|', ('pos_order_uid', '=', order.pos_reference)] + tx_match])
            else:
                domain = AND([domain, tx_match])

            orphans = self.env['payment.transaction'].search(domain)
            if orphans:
                _logger.info(
                    "Backlinking %d orphan Woodforest transaction(s) to POS order %s (ID %d) via Native Sync",
                    len(orphans), order.pos_reference or 'N/A', order.id
                )
                orphans.write({
                    'pos_order_id': order.id,
                    'transaction_status': 'captured',
                    'state': 'done',
                    'last_state_change': fields.Datetime.now(),
                })
                
                # V3.6.1: Mark original order as superseded if found
                for tx in orphans:
                    if tx.pos_order_uid:
                        # tx.pos_order_uid stores the pos_reference of the interrupted order
                        old_order = self.env['pos.order'].search([
                            '|', ('pos_reference', '=', tx.pos_order_uid), ('uuid', '=', tx.pos_order_uid),
                            ('id', '!=', order.id)
                        ], limit=1)
                        if old_order and not old_order.is_superseded:
                            old_order.action_mark_superseded(order.id)
                            _logger.info("  [Native Sync] Marked Original Order %s as superseded by %s", old_order.name, order.name)
                
                # V3.7.4: Finalize fully paid recovery orders natively
                if order.state == 'draft':
                    totally_paid_or_more = float_compare(order.amount_paid, order.amount_total, precision_rounding=order.currency_id.rounding)
                    _logger.info("  [Recovery] Checking if order %s can be finalized (Paid: %s, Total: %s)", order.name, order.amount_paid, order.amount_total)
                    
                    if totally_paid_or_more >= 0:
                        try:
                            _logger.info("  [Recovery Finalization] Order %s is fully paid. Calling action_pos_order_paid()", order.name)
                            order.action_pos_order_paid()
                            _logger.info("  [Recovery Finalization] Order %s state successfully changed to PAID", order.name)
                        except Exception as e:
                            _logger.error("  [Recovery Finalization] Failed to transition order %s to PAID: %s", order.name, e)
                            
        return orders


    def action_pos_order_refund(self):
        """
        Safety check for Woodforest payments before opening the refund wizard.
        """
        self.refund()
        return super(PosOrder, self).action_pos_order_refund()

    def _refund(self):
        """
        Better error message and chatter logging when no session is open.
        """
        for order in self:
            current_session = order.session_id.config_id.current_session_id
            if not current_session:
                pos_name = order.session_id.config_id.name or _("specified PoS")
                msg = _("Please open '%s' first, then try the refund again.") % pos_name
                
                # Post to chatter of the original order
                order.message_post(body=msg)
                
                raise UserError(msg)
        return super(PosOrder, self)._refund()

    def refund(self):
        """
        Fail-closed return guard for Woodforest payments.

        Rules:
        - Cybersource unreachable → BLOCK + chatter
        - Payment not found in Cybersource → BLOCK + chatter
        - Payment status not confirmed → BLOCK + chatter
        - Only AUTHORIZED / CAPTURED / COMPLETED → allow return + chatter
        """
        CONFIRMED_STATUSES = {'DONE', 'CAPTURED', 'COMPLETED'}

        woodforest_payments = self.payment_ids.filtered(
            lambda p: p.payment_method_id.use_payment_terminal == 'woodforest'
        )

        if woodforest_payments:
            # Use native pos.payment.transaction_id (Cybersource provider_reference)
            provider_refs = [r for r in woodforest_payments.mapped('transaction_id') if r]
            search_domain = [
                ('provider_id.code', '=', 'woodforest'),
                '|',
                ('pos_order_id', '=', self.id),
                ('provider_reference', 'in', provider_refs),
            ]
            txs = self.env['payment.transaction'].search(search_domain)

            if not txs:
                msg = _("Return not possible. No payment record found for this order.")
                self.message_post(body=msg)
                raise UserError(msg)

            for tx in txs:
                token = tx.provider_reference or tx.reference
                if not token:
                    msg = _("Return not possible. Payment reference missing.")
                    self.message_post(body=msg)
                    raise UserError(msg)

                # ── Live check: call API directly, never trust cached field ──────
                try:
                    res = get_payment_status(tx, token, env=self.env)
                except Exception as e:
                    _logger.error("Could not reach payment gateway for tx %s: %s", tx.reference, e)
                    msg = _("Could not verify payment status. Please try again.")
                    self.message_post(body=msg)
                    raise UserError(msg)

                if not res.get('success'):
                    _logger.warning("Payment verification failed for tx %s: %s", tx.reference, res.get('message', ''))
                    msg = _("Could not confirm payment. Please try again later.")
                    self.message_post(body=msg)
                    raise UserError(msg)

                # ── Use Decision Engine to evaluate the transaction ──────
                status_data = res.get('data') or {}
                decision = self.env['payment.transaction']._cybersource_decide_action(status_data)
                derived_status = decision.get('derived_status', 'UNKNOWN')

                if derived_status not in CONFIRMED_STATUSES:
                    _logger.warning("Return blocked for tx %s: status=%s", tx.reference, derived_status)
                    msg = _("Return not available. Payment has not been completed yet.")
                    self.message_post(body=msg)
                    raise UserError(msg)

                self.message_post(body=_(
                    "Payment verified (%(status)s). Return authorized."
                ) % {'status': derived_status})


        res = super(PosOrder, self).refund()
        
        # Check if any payment was made via Woodforest
        woodforest_payments = self.payment_ids.filtered(
            lambda p: p.payment_method_id.use_payment_terminal == 'woodforest'
        )
        
        if woodforest_payments:
            pos_name = self.session_id.config_id.name or _("its original PoS")
            config = self.session_id.config_id
            original_order_name = self.name
            refund_order_name = _("the new refund order")
            tracking_number = ""
            
            if res.get('res_id'):
                refund_order = self.browse(res['res_id'])
                refund_order_name = refund_order.name
                # Calculate tracking number (904 style) as used in Odoo 18 POS UI
                tracking_val = (refund_order.session_id.id % 10) * 100 + refund_order.sequence_number % 100
                tracking_number = str(tracking_val).zfill(3)
            
            msg = _(
                "Refund created: '%s'.\n"
                "Open '%s' in the register and process Order #%s."
            ) % (refund_order_name, pos_name, tracking_number)
            
            # Post instructions to the chatter of the new refund order
            if res.get('res_id'):
                refund_order.message_post(body=msg)

            # Redirect to the POS session if open
            open_session = self.env['pos.session'].search([
                ('config_id', '=', config.id),
                ('state', 'in', ['opened', 'opening_control']),
            ], limit=1)

            if open_session:
                return {
                    'type': 'ir.actions.act_url',
                    'url': '/pos/ui?config_id=%d' % config.id,
                    'target': 'self',
                }
            else:
                # No open session — show notification with instructions
                if isinstance(res, dict):
                    res.setdefault('params', {})
                    res['params']['next'] = {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _("Woodforest Refund Process"),
                            'message': msg,
                            'sticky': True,
                            'type': 'info',
                        }
                    }
        
        return res

    def action_check_mirillium_status(self):
        """
        Diagnostic action to check the status of Woodforest transactions in Mirillium.
        This iterates over all linked payment.transaction records and calls their status check.
        """
        self.ensure_one()
        
        # 1. Find directly linked account.payment.transaction
        # In Odoo 18, pos.payment records usually have a payment_transaction_id if via terminal
        pos_payments = self.payment_ids.filtered(lambda p: p.payment_method_id.use_payment_terminal == 'woodforest')
        
        # Robust Search: Look for transactions linked to this order or with the same POS reference or payment reference
        txs = self.env['payment.transaction'].search([
            ('provider_id.code', '=', 'woodforest'),
            '|',
            ('pos_order_id', '=', self.id),
            '|',
            ('reference', 'ilike', self.pos_reference),
            ('provider_reference', 'in', pos_payments.mapped('transaction_id'))
        ])

        if not txs:
            raise UserError(_("No Woodforest payment transactions found for this order. "
                            "If the payment was interrupted before reaching Odoo, "
                            "please use the 'Manual Transaction Check' in Woodforest Settings."))
            
        # Execute diagnostic on each found transaction
        results = []
        for tx in txs:
            res = tx.action_woodforest_check_status()
            # If the action returns a notification dict, we extract the message for a summary
            if isinstance(res, dict) and 'params' in res:
                results.append(res['params'].get('message', ''))
        
        if len(results) > 1:
            msg = "\n".join(results)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Multi-Payment Status Check"),
                    'message': msg,
                    'sticky': True,
                    'type': 'info',
                }
            }
        elif txs:
            # If only one, just return the result of that one transaction check
            return txs[0].action_woodforest_check_status()
        
        return True
