from odoo import models, fields, api
from odoo.exceptions import UserError
from ..services.mirillium.api import authorize_payment
import random
from odoo.http import Controller, route
import logging
from ..services.webhook_service import process_webhook_payload

logger = logging.getLogger(__name__)


class PaymentTokenSelector(models.TransientModel):
    _name = 'payment.token.selector'
    _description = 'Select Existing Token'
    amount = fields.Monetary(string='Amount to Charge')
    max_amount = fields.Monetary(
        string="Max Amount", currency_field='currency_id', readonly=True)

    partner_id = fields.Many2one(
        'res.partner', string="Customer", required=True)
    payment_token_id = fields.Many2one(
        'payment.token',
        string="Payment Method"
    )
    active_id = fields.Integer()
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id.id
    )

    TOKEN_TYPE_SELECTION = [
        ('bank_checking', 'Bank - Checking'),
        ('bank_savings', 'Bank - Savings'),
        ('tokenized_credit', 'Saved Credit Card'),
        ('tokenized_debit', 'Saved Debit Card'),
    ]

    token_type = fields.Selection(
        TOKEN_TYPE_SELECTION,
        string="Token Type",
        help="Filter tokens by type (Checking, Savings, or Card).",
        default='bank_checking'
    )
    payment_token_domain = fields.Binary(compute='_compute_token_domain')

    @api.depends('partner_id', 'token_type')
    def _compute_token_domain(self):
        for rec in self:
            domain = [('partner_id', '=', rec.partner_id.id),
                      ('active', '=', True)]
            if rec.token_type:
                domain.append(('token_type', '=', rec.token_type))
            logger.info(f"Returning domain: {domain}")
            rec.payment_token_domain = domain

    @api.onchange('token_type', 'partner_id')
    def _onchange_token_type_clear_selection(self):
        self.payment_token_id = False

    @staticmethod
    def generate_client_reference_code(provider_id, provider_token, invoice_id=None):
        from ..services.mirillium.utils import generate_client_reference_code as gen_ref
        return gen_ref(
            source_type='INVOICE',
            provider_id=provider_id,
            token_ref=provider_token,
            invoice_id=invoice_id
        )

    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        active_id = self.env.context.get('active_id')
        if active_id:
            invoice = self.env['account.move'].browse(active_id)
            res.update({
                'amount': invoice.amount_residual,
                'max_amount': invoice.amount_residual,
                'currency_id': invoice.currency_id.id,
                'partner_id': invoice.partner_id.id,
            })
        else:
            amount = self.env.context.get('default_amount')
            currency_id = self.env.context.get('default_currency_id')
            partner_id = self.env.context.get('default_partner_id')

            if amount is not None:
                res['amount'] = amount
                res['max_amount'] = amount
            if currency_id:
                res['currency_id'] = currency_id
            if partner_id:
                res['partner_id'] = partner_id

        return res

    @api.depends('partner_id')
    def _compute_token_ids(self):
        for record in self:
            if record.partner_id:
                record.token_ids = self.env['payment.token'].search([
                    ('partner_id', '=', record.partner_id.id),
                    ('active', '=', True)
                ])
            else:
                record.token_ids = False

    def action_create_token(self):
        return {'type': 'ir.actions.act_window_close'}

    def action_confirm(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError("There is nothing to be paid.")

        instrument_token = self.payment_token_id.provider_ref
        if not instrument_token:
            raise UserError(
                "The selected token does not have a valid instrument ID.")

        # Get invoice ID from context if available
        invoice_id = None
        ctx = self.env.context or {}
        candidate = ctx.get('active_id') or (ctx.get('params') or {}).get('id')
        if candidate:
            try:
                cid = int(candidate)
                if self.env['account.move'].sudo().browse(cid).exists():
                    invoice_id = cid
            except Exception:
                pass

        clientReferenceCode = self.generate_client_reference_code(
            provider_id=self.payment_token_id.provider_id.id,
            provider_token=instrument_token,
            invoice_id=invoice_id
        )
        result = authorize_payment(
            record=self,
            payment_instrument=instrument_token,
            amount=self.amount,
            currency=self.currency_id.name,
            type='CARD' if self.payment_token_id.token_type in ['tokenized_credit', 'tokenized_debit'] else 'CHECK',
            clientReferenceCode=clientReferenceCode
        )

        if not result["success"]:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Authorization Failed',
                    'message': result['message'],
                    'type': 'danger',
                    'sticky': False,

                }
            }

        auth_data = result["authorization_data"]

        logger.info(f"auth_data: {auth_data}")
        logger.info(f"self.env.context: {self.env.context}")

        ctx = self.env.context or {}
        candidate = ctx.get('active_id') or (ctx.get('params') or {}).get(
            'id') or (ctx.get('active_ids') or [None])[0]

        invoice_id = None
        if candidate is not None:
            try:
                cid = int(candidate)
                if self.env['account.move'].sudo().browse(cid).exists():
                    invoice_id = cid
            except Exception:
                pass
        if not invoice_id:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Payment",
                    "message": "Invoice id not found in context. Open the wizard from the invoice form.",
                    "type": "warning",
                    "sticky": False,
                },
            }

        success = process_webhook_payload(self.env, "manual.token.payment", {
            'invoice_id': invoice_id,
            'amount': auth_data["data"]['amount'],
            'currency': auth_data["data"]['currency'],
            'status': auth_data["data"]['status'],
            'cybersource_tx_id': auth_data["data"]['token'],
            'partner_id': self.partner_id.id,
            'type_payment': self.payment_token_id.token_type,
        })

        status = auth_data["data"]['status'].upper()
        if not success:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Transaction Warning',
                    'message': 'The payment was authorized, but we could not register the transaction in Odoo.',
                    'type': 'warning',
                    'sticky': False,
                }
            }
        if status == "PENDING":
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Payment Pending',
                    'message': 'The payment is currently pending. We will update the invoice automatically once confirmation is received.',
                    'type': 'warning',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Payment Authorized',
                'message': 'The payment has been successfully authorized.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }

