from odoo import models, fields, api
import json
from datetime import datetime

from odoo.tools import format_datetime


class PayrilliumLog(models.Model):
    _name = "payrillium.log"
    _description = "Woodforest Log"

    timestamp = fields.Datetime(string="Timestamp", required=True, index=True)

    execution_id = fields.Char(string="Execution ID", index=True)
    log_type = fields.Selection(
        [("request", "Request"), ("response", "Response"), ('start', 'Start'),
         ('finish', 'Finish'), ], string="Log Type")
    endpoint = fields.Char(string="Endpoint")
    request_payload = fields.Text(string="Request")
    response_payload = fields.Text(string="Response")
    success = fields.Boolean(string="Success")
    error_message = fields.Text(string="Error")

    # Summary fields parsed from response_payload
    invoices = fields.Integer(
        string="Invoices", compute="_compute_summary_from_payload", store=False)
    processed_invoices = fields.Integer(
        string="Processed Invoices", compute="_compute_summary_from_payload", store=False)
    links_processed = fields.Integer(
        string="Links Processed", compute="_compute_summary_from_payload", store=False)
    ach_processed = fields.Integer(
        string="ACH Processed", compute="_compute_summary_from_payload", store=False)
    links_total = fields.Integer(
        string="Links Total", compute="_compute_summary_from_payload", store=False)
    ach_total = fields.Integer(
        string="ACH Total", compute="_compute_summary_from_payload", store=False)
    errors = fields.Integer(
        string="Errors", compute="_compute_summary_from_payload", store=False)

    # HTML detail table built from per_invoice entries in response_payload
    detail_html = fields.Html(
        string="Details", compute="_compute_summary_from_payload", sanitize=False, store=False)

    @api.depends("response_payload")
    def _compute_summary_from_payload(self):
        for record in self:
            invoices = 0
            processed_invoices = 0
            links_processed = 0
            ach_processed = 0
            links_total = 0
            ach_total = 0
            errors = 0
            detail_html = ""

            try:
                payload = json.loads(record.response_payload or "{}")
                invoices = int(payload.get("invoices") or 0)
                processed_invoices = int(
                    payload.get("processed_invoices") or 0)
                links_processed = int(payload.get("links_processed") or 0)
                ach_processed = int(payload.get("ach_processed") or 0)
                links_total = int(payload.get("links_total") or 0)
                ach_total = int(payload.get("ach_total") or 0)
                errors = int(payload.get("errors") or 0)

                def _parse_dt(value):
                    if not value:
                        return None
                    # Try Odoo helper first
                    try:
                        return fields.Datetime.to_datetime(value)
                    except Exception:
                        pass
                    # Try ISO format (with or without 'Z')
                    try:
                        v = value.rstrip("Z")
                        return datetime.fromisoformat(v)
                    except Exception:
                        return None

                def _to_local_string(dt_value):
                    if not dt_value:
                        return ""
                    try:
                        return format_datetime(self.env, dt_value, dt_format="short", tz="local")
                    except Exception:
                        return fields.Datetime.to_string(dt_value)

                if record.execution_id and record.endpoint:
                    if record.log_type == 'finish':
                        # Find the earliest start log for the same execution
                        start_log = self.env['payrillium.log'].sudo().search([
                            ("execution_id", "=", record.execution_id),
                            ("endpoint", "=", record.endpoint),
                            ("log_type", "=", "start"),
                        ], limit=1, order="timestamp asc")

                per_invoice = payload.get("per_invoice") or []
                if isinstance(per_invoice, list) and per_invoice:
                    rows = []
                    # Build header
                    header = (
                        "<thead><tr>"
                        "<th>Invoice</th>"
                        "<th>Customer</th>"
                        "<th>Links (processed/total)</th>"
                        "<th>ACH (processed/total)</th>"
                        "</tr></thead>"
                    )
                    for item in per_invoice:
                        invoice_number = (item or {}).get(
                            "invoice_number") or ""
                        customer = (item or {}).get("customer") or ""
                        links = (item or {}).get("links") or {}
                        ach = (item or {}).get("ach") or {}
                        l_proc = links.get("processed") or 0
                        l_total = links.get("total") or 0
                        l_ok = links.get("ok")
                        a_proc = ach.get("processed") or 0
                        a_total = ach.get("total") or 0
                        a_ok = ach.get("ok")

                        def ok_badge(ok_value):
                            if ok_value is True:
                                return "<span style='color:#1f8f2e;font-weight:600'>&#10004;</span>"
                            if ok_value is False:
                                return "<span style='color:#b91c1c;font-weight:600'>&#10006;</span>"
                            return ""

                        rows.append(
                            "<tr>"
                            f"<td>{invoice_number}</td>"
                            f"<td>{customer}</td>"
                            f"<td>{l_proc}/{l_total} {ok_badge(l_ok)}</td>"
                            f"<td>{a_proc}/{a_total} {ok_badge(a_ok)}</td>"
                            "</tr>"
                        )

                    detail_html = (
                        "<div>"
                        "<table class='o_list_view table table-sm table-striped'>"
                        f"{header}<tbody>" + "".join(rows) + "</tbody></table>"
                        "</div>"
                    )
            except Exception:
                # Leave defaults if payload is not JSON
                pass

            record.invoices = invoices
            record.processed_invoices = processed_invoices
            record.links_processed = links_processed
            record.ach_processed = ach_processed
            record.links_total = links_total
            record.ach_total = ach_total
            record.errors = errors
            record.detail_html = detail_html

