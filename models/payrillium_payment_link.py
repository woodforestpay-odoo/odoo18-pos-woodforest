from odoo import models, fields, api
import logging
from odoo.exceptions import ValidationError
from ..services.mirillium import sync_existing_payment_links, patch_payment_link
from odoo.exceptions import UserError
from ..services.mirillium.utils import get_payrillium_credentials


class PayrilliumPaymentLink(models.Model):
    _name = "payrillium.payment.link"
    _description = "Payrillium Payment Link"

    payment_link_id = fields.Char("Payment Link ID")
    link_url = fields.Char("Payment Link")
    status = fields.Selection([
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("paid", "Paid"),
        ("expired", "Expired"),
    ], string="Status", default="active", required=True)
    amount = fields.Monetary("Amount")
    currency_id = fields.Many2one(
        "res.currency", string="Currency", required=True)
    created_at = fields.Datetime("Created At", default=fields.Datetime.now)
    expiration_date = fields.Datetime("Expiration Date")
    last_check_date = fields.Datetime(
        "Last Status Check", help="Last time the payment status was checked via API")
    invoice_id = fields.Many2one("account.move", string="Invoice")
    business_id = fields.Char("Business ID")
    external_id = fields.Char("External ID")
    update_href = fields.Char("Update URL")
    self_href = fields.Char("Self URL")
    product_description = fields.Char("Product Description")
    link_type = fields.Char("Link Type")

    @api.model
    def action_fix_orphan_links(self):
        """
        Fix payment links that don't have invoice_id assigned.
        Extracts invoice_id from payment_link_id (format: INV<id>A<seq>).
        """
        _logger = logging.getLogger(__name__)
        import re

        orphan_links = self.search([('invoice_id', '=', False)])
        fixed_count = 0
        skipped_count = 0

        for link in orphan_links:
            # Extract invoice_id from payment_link_id (e.g., INV105A1 → 105)
            match = re.match(r"^INV(\d+)A\d+$", link.payment_link_id or "")
            if match:
                invoice_id = int(match.group(1))
                invoice = self.env['account.move'].search(
                    [('id', '=', invoice_id)], limit=1)
                if invoice:
                    link.write({'invoice_id': invoice_id})
                    _logger.info("Fixed link %s → Invoice %s",
                                 link.payment_link_id, invoice_id)
                    fixed_count += 1
                else:
                    _logger.warning("Invoice %s not found for link %s",
                                    invoice_id, link.payment_link_id)
                    skipped_count += 1
            else:
                _logger.warning(
                    "Could not extract invoice_id from %s", link.payment_link_id)
                skipped_count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Fix Orphan Links",
                'message': f"Fixed {fixed_count} links, skipped {skipped_count}.",
                'type': 'success' if fixed_count > 0 else 'warning',
                'sticky': False,
            }
        }

    @api.model
    def action_sync_paybylink(self):
        """
        Synchronize Pay by Link records from Mirillium and store only new ones.
        Returns a client notification with the result.
        """
        result = sync_existing_payment_links(
            self.env
        )

        if result is False:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': "Error",
                    'message': "  Failed to sync Pay by Links.",
                    'type': 'danger',
                    'sticky': True,
                }
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Woodforest",
                'message': f"  {result} new links synchronized.",
                'type': 'success',
                'sticky': False,
            }
        }

    def write(self, vals):
        _logger = logging.getLogger(__name__)
        _logger.info("  Write called with vals: %s", vals)
        if 'status' in vals:
            old_status = self.status
            _logger.info("  Current status: %s", old_status)

            if old_status != vals['status'] and not self.env.context.get('syncing_from_mirillium'):
                _logger.info(
                    "    Status change detected - Old: %s, New: %s", old_status, vals['status'])
                try:
                    if not patch_payment_link(self, vals['status']):
                        raise ValidationError(
                            "Failed to update status in external service")
                except Exception as e:
                    _logger.error("  Error in Mirillium API call: %s", str(e))
                    raise ValidationError(str(e))
            elif self.env.context.get('syncing_from_mirillium'):
                _logger.info(
                    "  Skipping Mirillium PATCH - syncing from Mirillium")

        return super().write(vals)

    def web_save(self, values, specification=None):
        """Override web_save to ensure our write method is called"""
        _logger = logging.getLogger(__name__)
        _logger.info("  Web_save called with values: %s", values)

        try:
            if values.get('id') and 'status' in values:
                record = self.browse(values['id'])
                if not record.exists():
                    raise ValidationError(
                        'Record not found. It might have been deleted.')

                current_status = record.status
                new_status = values['status']

                if current_status != new_status:
                    _logger.info(
                        "    Calling write with new status: %s", new_status)
                    record.write({'status': new_status})
                    values.pop('status')
                else:
                    _logger.info("  Status unchanged, skipping write")

            return super().web_save(values, specification=specification)

        except ValidationError as e:
            raise  # Re-raise ValidationError to let Odoo handle it
        except Exception as e:
            _logger.error("  Unexpected error in web_save: %s", str(e))
            raise ValidationError(
                'An unexpected error occurred. Please try again.')

    def open_link_url(self):
        if not self.link_url:
            raise UserError("No link available.")
        return {
            'type': 'ir.actions.act_url',
            'url': self.link_url,
            'target': 'new',
        }

    link_url = fields.Char("Link URL")
    link_as_html = fields.Html(string="Link", compute="_compute_link_as_html")

    def _compute_link_as_html(self):
        for rec in self:
            if rec.link_url:
                rec.link_as_html = (
                    f'<a href="{rec.link_url}" target="_blank" '
                    f'style="color:#3F51B5;text-decoration:none;font-weight:500;">'
                    f' Open Payment Link</a>'
                    f'</div>'
                )
            else:
                rec.link_as_html = "<span style='color:#888;'>No link available</span>"
        for rec in self:
            if rec.link_url:
                rec.link_as_html = (
                    f'<a href="{rec.link_url}" target="_blank" '
                    f'style="display:inline-block;color:#3F51B5;font-weight:500;text-decoration:none;">'
                    f'Open Payment Link</a></div>'
                )
            else:
                rec.link_as_html = "<span style='color:#888;'>No link available</span>"

