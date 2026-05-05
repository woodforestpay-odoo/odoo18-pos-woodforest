from odoo import models, api, _
from odoo.exceptions import UserError

class PosMakePayment(models.TransientModel):
    _inherit = "pos.make.payment"

    def check(self):
        """
        Block backend payments for Woodforest methods.
        Terminal payments MUST be processed through the POS UI.
        """
        self.ensure_one()
        if self.payment_method_id.use_payment_terminal == 'woodforest':
            order = self.env['pos.order'].browse(self.env.context.get('active_id'))
            pos_name = order.session_id.config_id.name or _("its assigned POS")
            order_ref = order.name
            
            # Calculate tracking number (904 style)
            tracking_val = (order.session_id.id % 10) * 100 + order.sequence_number % 100
            tracking_number = str(tracking_val).zfill(3)
            
            msg = _(
                "This payment must be completed at the register.\n"
                "Open '%s' and look for Order #%s."
            ) % (pos_name, tracking_number)
            
            # Post instructions to chatter for ease of use
            order.message_post(body=msg)
            
            raise UserError(msg)
            
        return super(PosMakePayment, self).check()
