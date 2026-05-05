# wizard/woodforest_refund_wizard.py
from odoo import models, fields, api, _
from odoo.exceptions import UserError

class WoodforestRefundWizard(models.TransientModel):
    _name = 'woodforest.refund.wizard'
    _description = 'Woodforest Refund Confirmation Wizard'

    transaction_id = fields.Many2one('payment.transaction', string="Transaction", required=True, readonly=True)
    payment_status = fields.Char(string="Payment Status", readonly=True)
    card_type = fields.Char(string="Card Type", readonly=True)
    
    can_refund = fields.Boolean(string="Can Revert", readonly=True)
    action_type = fields.Selection([
        ('VOID', 'Void Authorization'),
        ('REFUND_CREDIT', 'Credit Refund'),
        ('REFUND_DEBIT', 'Debit Refund'),
        ('NONE', 'None')
    ], string="Internal Action", readonly=True)
    
    rejection_reason = fields.Char(string="Reason", readonly=True)
    friendly_message = fields.Text(string="Notification", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if not active_id:
            return res
            
        tx = self.env['payment.transaction'].browse(active_id)
        # 1. Sync status with Cybersource
        tx.action_woodforest_check_status()
        
        # 2. Apply decision logic
        status = tx.transaction_status.upper() if tx.transaction_status else 'NONE'
        card_type = (tx.card_type or 'CREDIT').upper()
        
        allowed, action, reason = self._decide_reversal(status, card_type)
        
        msg = ""
        if allowed:
            msg = _("The payment will be cancelled/reverted on the customer's card.")
        elif status in ["VOIDED", "REVERSED", "CANCELLED", "REFUNDED", "CREDIT_REFUNDED", "DEBIT_REFUNDED"]:
            msg = _("No action required. This transaction has already been reversed or refunded.")
        elif status in ["PENDING", "PENDING_AUTHENTICATION", "TRANSMITTED", "AUTHORIZED_PENDING_REVIEW"]:
            msg = _("The payment is still being processed by the bank. Please wait 5-10 minutes and try again.")
        else:
            msg = _("This transaction cannot be cancelled at this moment.")

        res.update({
            'transaction_id': tx.id,
            'payment_status': status,
            'card_type': card_type,
            'can_refund': allowed,
            'action_type': action,
            'rejection_reason': reason,
            'friendly_message': msg
        })
        return res

    def _decide_reversal(self, status, card_type):
        """Logic provided by user to determine the reversal path"""
        if status == "AUTHORIZED":
            return True, "VOID", ""
        elif status == "COMPLETED":
            return True, "REFUND_DEBIT" if card_type == "DEBIT" else "REFUND_CREDIT", ""
        elif status in ["VOIDED", "REVERSED", "CANCELLED", "REFUNDED", "CREDIT_REFUNDED", "DEBIT_REFUNDED"]:
            return False, "NONE", _("Already reversed or refunded.")
        elif status in ["PENDING", "PENDING_AUTHENTICATION", "TRANSMITTED", "AUTHORIZED_PENDING_REVIEW"]:
            return False, "NONE", _("Transaction is still being processed.")
        elif status in ["DECLINED", "FAILED", "INVALID_REQUEST"]:
            return False, "NONE", _("This transaction was not successful (Declined/Failed).")
        else:
            return False, "NONE", f"Status: {status}. Manual review required."

    def action_confirm_refund(self):
        self.ensure_one()
        
        # Idempotency / Double-click protection: Re-check status before executing
        self.transaction_id.action_woodforest_check_status()
        current_status = (self.transaction_id.transaction_status or '').upper()
        
        if current_status in ["VOIDED", "REVERSED", "CANCELLED", "REFUNDED", "CREDIT_REFUNDED", "DEBIT_REFUNDED"]:
            raise UserError(_("This transaction has already been processed (Status: %s). No further action is needed.") % current_status)

        if not self.can_refund:
            raise UserError(self.rejection_reason or _("Refund not allowed."))
            
        if self.action_type == 'VOID':
            return self.transaction_id.action_woodforest_void()
        elif self.action_type in ('REFUND_CREDIT', 'REFUND_DEBIT'):
            return self.transaction_id.action_woodforest_refund()
        
        return {'type': 'ir.actions.act_window_close'}
