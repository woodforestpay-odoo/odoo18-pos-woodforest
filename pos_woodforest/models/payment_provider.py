from odoo import models, fields, _


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('woodforest', 'Woodforest')],
        ondelete={'woodforest': 'set default'},
        help="Technical provider identifier"
    )

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        # enable refund full_only
        self.support_refund = 'full_only'

    def _get_default_payment_method_line_id(self):
        if self.code == 'woodforest':
            lines = self.env['account.payment.method.line'].search([
                ('journal_id.type', '=', 'bank'),
            ])
            return lines.filtered(lambda l: l.payment_type == 'inbound')[:1]
        return super()._get_default_payment_method_line_id()

    def _get_supported_currencies(self):
        """Override to support USD and EUR for Woodforest."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'woodforest':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in ['USD', 'EUR']
            )
        return supported_currencies

    def _get_supported_countries(self):
        """Override to support US and EU countries for Woodforest."""
        supported_countries = super()._get_supported_countries()
        if self.code == 'woodforest':
            # Add US and major EU countries
            supported_countries = supported_countries.filtered(
                lambda c: c.code in ['US', 'CA', 'GB', 'DE',
                                     'FR', 'ES', 'IT', 'NL', 'BE', 'AT', 'CH']
            )
        return supported_countries

