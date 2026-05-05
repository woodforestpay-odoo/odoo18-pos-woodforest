from odoo import models, fields, api, _
from odoo.exceptions import UserError

class PayrilliumPaymentActionWizard(models.TransientModel):
    _name = 'payrillium.payment.action.wizard'
    _description = 'Woodforest Payment Action Wizard'

    transaction_id = fields.Many2one('payment.transaction', required=True, readonly=True)
    
    action_type = fields.Selection([
        ('void', 'Authorization Reversal (Void)'),
        ('refund', 'Refund'),
        ('none', 'No Action Available')
    ], string="Recommended Action", readonly=True)
    
    explanation = fields.Html(string="Cybersource Logic Explanation", readonly=True)
    amount = fields.Monetary(string="Amount", related="transaction_id.amount", readonly=True)
    currency_id = fields.Many2one(related="transaction_id.currency_id")
    
    # Internal state carriers (from the sync status call)
    derived_transaction_status = fields.Char(string="Derived Status")
    derived_state = fields.Char(string="Derived State")
    available_actions_json = fields.Char(string="Available Actions JSON")
    
    is_pos_recovery_available = fields.Boolean(string="Is POS Recovery Available", compute="_compute_is_pos_recovery")
    pos_session_id = fields.Many2one(related="transaction_id.pos_session_id", readonly=True)

    @api.depends('transaction_id')
    def _compute_is_pos_recovery(self):
        for rec in self:
            rec.is_pos_recovery_available = bool(rec.transaction_id.pos_order_snapshot_json and not rec.transaction_id.pos_order_id)

    def action_open_pos_session(self):
        """Open the POS config kanban or the specific session so the user can finish the sale."""
        self.ensure_one()
        # Find the POS config to open
        config_id = self.transaction_id.pos_config_id or self.transaction_id.pos_session_id.config_id
        if not config_id:
            raise UserError(_("Could not determine POS Configuration for this transaction."))
            
        action = self.env["ir.actions.actions"]._for_xml_id("point_of_sale.action_pos_config_kanban")
        action['domain'] = [('id', '=', config_id.id)]
        return action

    def action_confirm_void(self):
        """Execute the Void action directly on the transaction."""
        self.ensure_one()
        if self.action_type != 'void':
            raise UserError(_("This transaction is not eligible for a Void."))
        self.transaction_id.action_woodforest_void()
        return {'type': 'ir.actions.act_window_close'}
        
    def action_confirm_refund(self):
        """Execute the Refund action directly on the transaction."""
        self.ensure_one()
        if self.action_type != 'refund':
            raise UserError(_("This transaction is not eligible for a Refund."))
        
        # We can either directly call refund or pop the actual refund wizard if more inputs are needed.
        # But for Woodforest, action_woodforest_refund exists. Let's call it.
        self.transaction_id.action_woodforest_refund()
        return {'type': 'ir.actions.act_window_close'}
