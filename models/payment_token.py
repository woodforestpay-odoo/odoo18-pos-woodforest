from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class PaymentToken(models.Model):
    _inherit = 'payment.token'

    token_type = fields.Selection([
        ('bank_checking', 'Bank - Checking'),
        ('bank_savings', 'Bank - Savings'),
        ('tokenized_credit', 'Saved Credit Card'),
        ('tokenized_debit', 'Saved Debit Card'),
    ], string="Token Type", readonly=True)

    def action_disable_token(self):
        """Disable a payment token (soft delete) and notify Mirillium.
        
        Sets active=False and calls Mirillium API to delete the token remotely.
        This is a safe operation that doesn't hard delete the record.
        """
        self.ensure_one()
        
        if not self.active:
            raise UserError("This token is already disabled.")
        
        provider_ref = self.provider_ref
        provider_name = (self.provider_id.name or "").lower() if self.provider_id else ""
        
        # Call Mirillium API to delete token remotely (best effort)
        if provider_ref and "woodforest" in provider_name.lower():
            try:
                from ..services.mirillium import api as mirillium_api
                result = mirillium_api.delete_payment_token(self, provider_ref)
                _logger.info(
                    "Mirillium delete_payment_token for %s (%s): %s",
                    self.id,
                    provider_ref,
                    result,
                )
            except Exception as e:
                # Log error but continue with local disable
                _logger.error(
                    "Error calling Mirillium delete_payment_token for token %s (%s): %s",
                    self.id,
                    provider_ref,
                    e,
                )
        
        # Disable token locally (soft delete)
        self.write({'active': False})
        
        # Log message on partner chatter
        if self.partner_id:
            self.partner_id.message_post(
                body=f"Payment token {self.payment_details or 'N/A'} has been disabled.",
                subject="Payment Token Disabled"
            )
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Token Disabled',
                'message': f'Payment token {self.payment_details or "N/A"} has been disabled successfully.',
                'type': 'success',
                'sticky': False,
            }
        }

    def unlink(self):
        """Override delete to also notify Mirillium before removing the token.

        We keep the UX exactly the same: user clicks the normal 'Delete' action
        on payment tokens. For tokens that belong to Payrillium and have a
        provider_ref, we call the Mirillium helper first, then always delete
        the local record.
        """
        for token in self:
            provider_ref = token.provider_ref
            provider_name = (token.provider_id.name or "").lower() if token.provider_id else ""

            # Only touch tokens that belong to Woodforest
            if provider_ref and "woodforest" in provider_name.lower():
                try:
                    from ..services.mirillium import api as mirillium_api
                    result = mirillium_api.delete_payment_token(token, provider_ref)
                    _logger.info(
                        "Mirillium delete_payment_token for %s (%s): %s",
                        token.id,
                        provider_ref,
                        result,
                    )
                except Exception as e:
                    # Do not block local deletion if external delete fails
                    _logger.error(
                        "Error calling Mirillium delete_payment_token for token %s (%s): %s",
                        token.id,
                        provider_ref,
                        e,
                    )

        return super().unlink()

