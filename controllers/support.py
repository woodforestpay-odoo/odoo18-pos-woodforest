import csv
import io
import json
import uuid
from datetime import datetime

from odoo import http
from odoo.http import request, Response
from ..services.logging_service import log_payrillium_event


class PayrilliumSupportController(http.Controller):

    @http.route(
        "/woodforest/support/download_server_logs",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def download_server_logs(self, date_from=None, date_to=None, **kwargs):
        """
        Stream payrillium.log records as a CSV file download.
        Query params:
            date_from  YYYY-MM-DD  (inclusive, defaults to last 7 days)
            date_to    YYYY-MM-DD  (inclusive, defaults to today)
        """
        domain = []
        if date_from:
            domain.append(("timestamp", ">=", date_from + " 00:00:00"))
        if date_to:
            domain.append(("timestamp", "<=", date_to + " 23:59:59"))

        logs = request.env["payrillium.log"].sudo().search(
            domain, order="timestamp desc", limit=10000
        )

        # Build CSV in-memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "Timestamp",
            "Execution ID",
            "Type",
            "Endpoint",
            "Success",
            "Error",
            "Request",
            "Response",
        ])

        for log in logs:
            writer.writerow([
                log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "",
                log.execution_id or "",
                log.log_type or "",
                log.endpoint or "",
                "Yes" if log.success else "No",
                log.error_message or "",
                log.request_payload or "",
                log.response_payload or "",
            ])

        csv_bytes = output.getvalue().encode("utf-8-sig")  # utf-8-sig = BOM for Excel

        # Build filename with date range
        suffix = ""
        if date_from or date_to:
            suffix = f"_{date_from or 'start'}_to_{date_to or 'today'}"
        filename = f"woodforest_server_logs{suffix}.csv"

        # Log the download to payrillium.log
        exec_id = f"slog_{uuid.uuid4().hex[:8]}"
        user = request.env.user
        log_payrillium_event(
            exec_id, "server_logs_download", "request",
            payload={
                "date_from": date_from or "all",
                "date_to": date_to or "today",
                "records_exported": len(logs),
                "user": user.name,
                "filename": filename,
            },
            success=True,
            env=request.env,
        )

        return Response(
            csv_bytes,
            status=200,
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(csv_bytes)),
            },
        )

    @http.route("/woodforest/terminal_list_banner", type="http", auth="user")
    def get_terminal_list_banner(self, **kwargs):
        """Returns the HTML for the gradient banner shown in the Terminals list view."""
        return request.render("pos_woodforest.terminal_list_banner_template", {})

    @http.route(
        "/woodforest/support/cs_search_csv",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def cs_search_csv(self, **kw):
        """
        Download Cybersource Transaction Search results as CSV.
        Forces offset=0 and limit=2000 to get all results.
        """
        # Re-use the existing logic but override pagination
        kw["limit"] = 2000
        kw["offset"] = 0
        
        # Call the json route logic directly
        res = self.cs_search(**kw)
        
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Date",
            "External Ref",
            "Odoo Ref",
            "Currency",
            "Amount",
            "Card Prefix",
            "Card Last 4",
            "Card Type",
            "Status",
            "Approval Code",
            "Steps (Apps)",
            "App Name",
            "App Status",
            "Reason Code",
            "rCode",
            "rMessage",
            "Reconciliation ID",
            "Odoo Transaction ID",
        ])

        if res.get("success"):
            for row in res.get("results", []):
                apps = row.get("apps_status") or []
                base_cols = [
                    row.get("submit_time", ""),
                    row.get("cs_id", ""),
                    row.get("odoo_ref", ""),
                    row.get("currency", ""),
                    row.get("amount", ""),
                    row.get("card_prefix", ""),
                    row.get("card_suffix", ""),
                    row.get("card_type", ""),
                    row.get("status", ""),
                    row.get("approval_code", ""),
                    ", ".join(a.get("name", "") for a in apps) if apps else "",
                ]
                if apps:
                    for app in apps:
                        writer.writerow(base_cols + [
                            app.get("name", ""),
                            app.get("status", ""),
                            app.get("reasonCode", ""),
                            app.get("rCode", ""),
                            app.get("rMessage", ""),
                            app.get("reconciliationId", ""),
                            row.get("odoo_tx_id", ""),
                        ])
                else:
                    writer.writerow(base_cols + ["", "", "", "", "", "", row.get("odoo_tx_id", "")])

        csv_bytes = output.getvalue().encode("utf-8-sig")
        filename = f"cybersource_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        return Response(
            csv_bytes,
            status=200,
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(csv_bytes)),
            },
        )

    def _friendly_error(self, raw_msg):
        """Convert ugly HTTP error strings to human-readable messages."""
        import re
        raw = str(raw_msg or "")
        # Detect HTTP status code at the start of the message
        m = re.match(r"HTTP error:\s*(\d{3})", raw)
        if m:
            code = int(m.group(1))
            if code == 530:
                return "⚠️ The Cybersource network service is temporarily unavailable (Cloudflare 530). Please try again in a moment."
            if code == 503:
                return "⚠️ The Cybersource network is currently unavailable (503). Please try again shortly."
            if code == 504:
                return "⚠️ The request to Cybersource timed out (504). Please try again."
            if code == 401:
                return "🔒 Authentication failed (401). Check your Payrillium API credentials in Settings."
            if code == 403:
                return "🔒 Access denied (403). Your credentials may not have permission for this operation."
            if code == 404:
                return "❌ Transaction not found in Cybersource (404)."
            if code >= 500:
                return f"⚠️ Cybersource network error ({code}). Please try again in a moment."
        # Clean up URL from message to avoid leaking internal endpoints
        raw = re.sub(r" for url: https?://\S+", "", raw)
        return raw

    @http.route(
        "/woodforest/support/cs_search",
        type="json",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def cs_search(self, **kw):
        """
        Cybersource Transaction Search.

        Expected JSON body (all optional except at least one filter):
            date_from   str  "YYYY-MM-DD"
            date_to     str  "YYYY-MM-DD"
            odoo_ref    str  earlyPaymentRef
            cs_id       str  Cybersource transaction ID
            amount      str  e.g. "4.60"
            card_suffix str  last 4 digits
            limit       int  default 50

        Response:
            { success, results: [...], total, message }
        Each result:
            { cs_id, odoo_ref, amount, currency, card_suffix, card_type,
              status, submit_time, approval_code, odoo_tx_id }
        """
        from ..services.mirillium.api import (
            search_cybersource_transactions,
            get_transaction_status_by_cs_id,
        )

        try:
            limit = int(kw.get("limit", 50))
            if limit <= 0 or limit > 100:
                limit = 50
        except (ValueError, TypeError):
            limit = 50

        try:
            offset = int(kw.get("offset", 0))
            if offset < 0:
                offset = 0
        except (ValueError, TypeError):
            offset = 0

        filters = {
            "date_from":   kw.get("date_from", ""),
            "date_to":     kw.get("date_to", ""),
            "odoo_ref":    kw.get("odoo_ref", ""),
            "cs_id":       kw.get("cs_id", ""),
            "amount":      kw.get("amount", ""),
            "card_suffix": kw.get("card_suffix", ""),
            "limit":       limit,
            "offset":      offset,
            "sort":        kw.get("sort", "submitTimeUtc:desc"),
        }

        results = []

        # ── Always use TSS search with appropriate query filters ────────────
        # The cs_id filter is handled inside search_cybersource_transactions
        # as `id:{cs_id}` in the Lucene query, so no separate endpoint needed.
        search_res = search_cybersource_transactions(filters, env=request.env)
        if not search_res.get("success"):
            return {"success": False, "message": self._friendly_error(search_res.get("message")), "results": [], "total": 0}

        summaries = search_res.get("data") or []
        total = search_res.get("total", len(summaries))

        for summary in summaries:
            cs_id = summary.get("id", "")
            if not cs_id:
                continue

            entry = self._build_result_entry(cs_id, {}, request.env, summary=summary)
            results.append(entry)

        return {"success": True, "results": results, "total": total, "message": ""}

    def _build_result_entry(self, cs_id, status_data, env, summary=None):
        """Build a normalized result dict from CS data + Odoo lookup."""
        summary = summary or {}

        # Extract fields from search summary if status_data is empty
        order_info = (status_data.get("orderInformation") or summary.get("orderInformation") or {})
        amount_details = order_info.get("amountDetails") or {}
        payment_info = (status_data.get("paymentInformation") or summary.get("paymentInformation") or {})
        card = payment_info.get("card") or {}
        proc_info = (status_data.get("processorInformation") or summary.get("processorInformation") or {})
        client_ref = (status_data.get("clientReferenceInformation") or summary.get("clientReferenceInformation") or {})

        # Mirror raw Cybersource status
        app_info_status = (status_data.get("applicationInformation") or {}).get("status")
        app_info_summary = (summary.get("applicationInformation") or {}).get("status")
        rflag_status = (status_data.get("applicationInformation") or {}).get("rFlag")
        rflag_summary = (summary.get("applicationInformation") or {}).get("rFlag")

        live_status = (
            status_data.get("status")
            or app_info_status
            or app_info_summary
            or rflag_status
            or rflag_summary
            or ""
        ).upper()

        # Find matching Odoo payment.transaction
        odoo_tx_id = None
        has_payment = False
        odoo_tx = env["payment.transaction"].sudo().search(
            [("provider_reference", "=", cs_id)], limit=1
        )
        if odoo_tx:
            odoo_tx_id = odoo_tx.id
            has_payment = getattr(odoo_tx, 'is_post_processed', False) or bool(getattr(odoo_tx, 'payment_id', False))
            if not has_payment and hasattr(env, 'pos.payment'):
                has_payment = bool(env['pos.payment'].sudo().search_count([('transaction_id', '=', odoo_tx.id)]))

        # Handle submit_time with case-insensitive check across summary and status_data
        submit_time = (
            summary.get("submitTimeUtc") 
            or summary.get("submitTimeUTC") 
            or status_data.get("submitTimeUtc") 
            or status_data.get("submitTimeUTC") 
            or ""
        )

        # Extract detailed application statuses for UI badges & tooltips
        apps_status = []
        apps_list = (status_data.get("applicationInformation") or {}).get("applications") or (summary.get("applicationInformation") or {}).get("applications") or []
        for app in apps_list:
            if not isinstance(app, dict):
                continue
            raw_name = app.get("name", "")
            name = raw_name.replace("ics_", "").upper()
            flag = (app.get("rFlag") or app.get("status") or "").upper()
            if name and flag:
                apps_status.append({
                    "name": name, 
                    "status": flag,
                    "reasonCode": app.get("reasonCode"),
                    "rCode": app.get("rCode"),
                    "rMessage": app.get("rMessage"),
                    "reconciliationId": app.get("reconciliationId")
                })

        return {
            "cs_id":         cs_id,
            "odoo_ref":      client_ref.get("code", ""),
            "amount":        amount_details.get("totalAmount", ""),
            "currency":      amount_details.get("currency", "USD"),
            "card_suffix":   card.get("suffix", ""),
            "card_prefix":   card.get("prefix", ""),
            "card_type":     (payment_info.get("paymentType") or {}).get("type", ""),
            "status":        live_status,
            "apps_status":   apps_status,
            "submit_time":   submit_time,
            "approval_code": (proc_info.get("authorizationCode") or proc_info.get("approvalCode") or ""),
            "odoo_tx_id":    odoo_tx_id,
            "has_payment":   has_payment,
        }
