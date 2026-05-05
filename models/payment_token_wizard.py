from odoo import models, fields, api
from odoo.exceptions import ValidationError
from ..services.mirillium import create_payment_token
import logging
from datetime import datetime
logger = logging.getLogger(__name__)


def get_exp_months(self):
    return [(f"{i:02}", f"{i:02}") for i in range(1, 13)]


def get_exp_years(self):
    current = datetime.now().year
    return [(str(y), str(y)[-2:]) for y in range(current, current + 25)]


class PaymentTokenWizard(models.TransientModel):
    _name = 'payment.token.wizard'
    _description = 'Payment Token Generation Wizard'
    partner_id = fields.Many2one(
        'res.partner', string='Customer', readonly=True, required=True)
    amount = fields.Monetary(string="Amount", currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.ref('base.USD').id
    )
    # Savings and Checking
    account_number = fields.Char(string='Account Number')
    routing_number = fields.Char(string='Routing Number')
    # Card
    card_number = fields.Char(string="Card Number", size=19)
    expiration_month = fields.Selection(
        selection=get_exp_months, string="Expiration Month")
    expiration_year = fields.Selection(
        selection=get_exp_years, string="Expiration Year")

    account_type = fields.Selection([
        ('checking', 'Checking'),
        ('savings', 'Savings'),
        ('card', 'Card'),
    ], string='Account Type', default='checking')

    first_name = fields.Char(string='First Name')
    last_name = fields.Char(string='Last Name')
    email = fields.Char(string='Email')
    phone = fields.Char(string='Phone')

    billing_address_required = fields.Boolean(
        string='Billing Address Required', default=True)
    country = fields.Many2one(
        'res.country',
        string='Country',
        default=lambda self: self.env.ref('base.us').id
    )
    address_line_1 = fields.Char(string='Address Line 1')
    address_line_2 = fields.Char(string='Address Line 2')
    city = fields.Char(string='City')
    state = fields.Char(string='State/Province')
    zip_code = fields.Char(string='ZIP')

    accept_terms = fields.Boolean(string='Accept Terms')

    security_code = fields.Char(string="Security Code", size=3)

    @api.constrains('security_code')
    def _check_security_code(self):
        for rec in self:
            code = (rec.security_code or '').strip()
            if code:
                if not code.isdigit():
                    raise ValidationError(
                        "Security Code must contain only digits.")
                if len(code) != 3:
                    raise ValidationError(
                        "Security Code must be exactly 3 digits.")

    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        partner_id = self.env.context.get("default_partner_id")
        if partner_id:
            res["partner_id"] = partner_id
        elif self.env.context.get("active_id"):
            try:
                invoice = self.env["account.move"].browse(
                    self.env.context["active_id"]).exists()
                if invoice:
                    res["partner_id"] = invoice.partner_id.id
            except Exception:
                pass
        return res

    def _validate_required_fields(self):
        self.ensure_one()
        error_messages = []

        if self.account_type == 'card':
            if not self.card_number:
                error_messages.append("Card Number")
            if not self.expiration_month:
                error_messages.append("Expiration Month")
            if not self.expiration_year:
                error_messages.append("Expiration Year")
            if not self.security_code:
                error_messages.append("Security Code")
        else:
            if not self.account_number:
                error_messages.append("Account Number")
            if not self.routing_number:
                error_messages.append("Routing Number")

        if not self.account_type:
            error_messages.append("Account Type")

        if self.billing_address_required:
            for field_name, label in [
                ('first_name', 'First Name'),
                ('last_name', 'Last Name'),
                ('email', 'Email'),
                ('phone', 'Phone'),
                ('country', 'Country'),
                ('address_line_1', 'Address Line 1'),
                ('city', 'City'),
                ('state', 'State/Province'),
                ('zip_code', 'ZIP'),
            ]:
                if not getattr(self, field_name):
                    error_messages.append(label)

        if error_messages:
            raise ValidationError(
                "Please complete the following required fields:\n• " +
                "\n• ".join(error_messages)
            )

    def action_generate_token(self):
        self.ensure_one()

        # Validate that Payrillium is configured before creating tokens
        config = self.env['payrillium.config'].sudo().search([], limit=1)
        if not config or not config.token or not config.installed:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Payrillium Not Configured',
                    'message': 'Payrillium must be activated before creating payment tokens. Please go to Settings > Payrillium > Settings and click "Activate Payrillium" to configure your API token.',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        self._validate_required_fields()
        payload = {}
        if self.account_type == 'card':
            payload["card"] = {
                "number": self.card_number,
                "expirationMonth": self.expiration_month,
                "expirationYear": self.expiration_year,
                "securityCode": str(self.security_code),
            }
        else:

            payload = {
                "bankAccount": {
                    "type": self.account_type[0].upper(),
                    "number": self.account_number,
                    "routingNumber": self.routing_number,
                }
            }

        if self.billing_address_required:
            payload["billTo"] = {
                "firstName": self.first_name,
                "lastName": self.last_name,
                "address1": self.address_line_1,
                "locality": self.city,
                "administrativeArea": self.state,
                "postalCode": self.zip_code,
                "country": self.country.code if self.country else "US",
                "email": self.email,
                "phoneNumber": self.phone,
            }

        result = create_payment_token(
            record=self,
            payload_data=payload,
        )

        if not result["success"]:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': f"  Error during {result.get('step', 'processing')}: {result['message']}",
                    'type': 'danger',
                    'sticky': False,
                }
            }

        token_id = result.get("token_id")
        last_digits = self.card_number[-4:] if self.account_type == 'card' else self.account_number[-4:]
        account_type_label = dict(self._fields['account_type'].selection).get(
            self.account_type, '').upper()
        payment_details = f"{account_type_label} ****{last_digits}"
        invoice_id = self.env.context.get("params", {}).get("id")
        invoice = self.env['account.move'].browse(invoice_id).exists()
        # amount = self.amount or (invoice.amount_residual if invoice else 0.0)
        logger.info(f"context: {self.env.context}")

        type_map = {
            'checking': 'bank_checking',
            'savings': 'bank_savings',
            'card': 'tokenized_credit',  # Default
        }
        token_type = type_map.get(self.account_type)

        if self.account_type == 'card' and result.get('raw_data'):
            # Attempt to detect card type from processor response
            raw_res = result.get('raw_data', {})
            # Cybersource usually returns card info metadata in the tokenization response
            card_meta = raw_res.get('data', {}).get('card', {})
            card_type_str = (card_meta.get('type') or '').lower()
            
            if 'debit' in card_type_str:
                token_type = 'tokenized_debit'
            elif 'credit' in card_type_str:
                token_type = 'tokenized_credit'

        new_token = self.env['payment.token'].create({
            "provider_ref": token_id,
            "payment_details": payment_details,
            "partner_id": self.partner_id.id,
            "company_id": self.env.company.id,
            "payment_method_id": self.env['payment.method'].search([
                ('code', '=', 'woodforest')
            ], limit=1).id,
            "provider_id": self.env['payment.provider'].search([('code', '=', 'woodforest')], limit=1).id,
            "token_type": token_type,
            "active": True,
        })

        amount_ctx = self.env.context.get('default_amount')
        currency_ctx = self.env.context.get('default_currency_id')

        # return {
        #     "type": "ir.actions.act_window",
        #     "res_model": "payment.token.selector",
        #     "view_mode": "form",
        #     "view_id": self.env.ref("pos_woodforest.list_payment_token_selector_form_view").id,
        #     "target": "new",
        #     "context": {
        #         "default_partner_id": self.partner_id.id,
        #         "default_payment_token_id": new_token.id,
        #         "default_amount": amount_ctx,
        #         "default_max_amount": amount_ctx,
        #         "default_currency_id": self.currency_id.id,

        #         # "active_id": invoice.id if invoice else False,
        #         # "active_model": "account.move" if invoice else False,
        #         "active_model": "account.move",
        #         "active_id": invoice.id if invoice else False,
        #         "active_ids": [invoice.id] if invoice else [],
        #         "default_active_model": "account.move",
        #         "default_active_id": invoice.id if invoice else False,

        #         # "default_active_id": self.env.context.get('active_id'),
        #         # "default_active_model": self.env.context.get('active_model'),

        #     },
        # }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Token created',
                'message': 'Token created successfully.',
                'type': 'success',
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'}
            }
        }

    def action_reset_form(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'target': 'new',
        }

