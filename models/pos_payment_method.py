from odoo import models, fields, api

class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    is_payrillium = fields.Boolean(
        compute='_compute_is_woodforest',
        store=True,
    )
    payrillium_color = fields.Char(string="Woodforest Color")
    payrillium_icon  = fields.Char(string="Woodforest Icon")
    payment_provider_id = fields.Many2one('payment.provider', string="Payment Provider")
    @api.depends('use_payment_terminal')
    def _compute_is_woodforest(self):
        for record in self:
            record.is_payrillium = record.use_payment_terminal == 'woodforest'

    @api.model
    def _get_payment_terminal_selection(self):
        selection = super(PosPaymentMethod, self)._get_payment_terminal_selection()
        if not any(code == 'woodforest' for code, _ in selection):
            selection.append(('woodforest', 'Woodforest'))
        return selection
