from odoo import models
import logging

_logger = logging.getLogger(__name__)
_logger.warning("  payment_link_wizard.py LOADED")

class PaymentLinkWizard(models.TransientModel):
    _inherit = 'payment.link.wizard'

    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'amount' in res and 'amount_max' in res:
            res['amount'] = res['amount_max']
        return res
