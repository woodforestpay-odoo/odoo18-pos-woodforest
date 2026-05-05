from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    payrillium_payment_token_ids = fields.One2many(
        string="Woodforest Payment Tokens",
        comodel_name='payment.token',
        inverse_name='partner_id',
        domain=[('active', '=', True)],
        compute='_compute_payrillium_payment_token_ids',
        store=False,
        help="Active payment tokens from Woodforest provider"
    )

    @api.depends('payment_token_ids', 'payment_token_ids.provider_code', 'payment_token_ids.active')
    def _compute_payrillium_payment_token_ids(self):
        """Compute only Woodforest tokens that are active."""
        for partner in self:
            partner.payrillium_payment_token_ids = partner.payment_token_ids.filtered(
                lambda t: t.active and t.provider_code == 'woodforest'
            )
