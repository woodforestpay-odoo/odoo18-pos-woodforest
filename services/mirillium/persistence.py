# persistence.py
# ─────────────────────────────────────────────
#  Persistence logic for payment link data
# ─────────────────────────────────────────────

import logging
import re
from .utils import parse_iso_datetime
from decimal import Decimal

_logger = logging.getLogger(__name__)


def save_payment_link_to_odoo(data, record_env):
    try:
        """
        Save or update a payment link in Odoo if not already present.
        Accepts either a record or env directly.
        """
        top = data.get("data", {})
        metadata = data.get(
            "metadata", {}) if "metadata" in data else top.get("metadata", {})
        payment_link_id = str(top.get("payment_link_id")
                              or top.get("id") or metadata.get("id") or "")
        created_date = parse_iso_datetime(
            str(top.get("createdDate") or top.get("id") or metadata.get("createdDate") or ""))

        if not payment_link_id or not payment_link_id.startswith("INV"):
            _logger.debug(
                "  Skipping invalid or unrecognized link ID: %s", payment_link_id)
            return None

        env = record_env.env if hasattr(record_env, "env") else record_env

        order_info = metadata.get("orderInformation", {}) or top.get(
            "orderInformation", {})
        amount_details = order_info.get("amountDetails", {})
        currency_code = amount_details.get("currency")

        if not currency_code:
            _logger.warning("   No currency code found in link data.")
            return None

        currency = env["res.currency"].search(
            [("name", "=", currency_code)], limit=1)
        if not currency:
            _logger.warning("  Currency %s not found in Odoo", currency_code)
            return None

        currency_id = currency.id
        STATUS_PRIORITY = {
            "active": 1,
            "inactive": 2,
            "expired": 3,
            "paid": 4,
        }

        existing = env["payrillium.payment.link"].search(
            [("payment_link_id", "=", payment_link_id)], limit=1)
        if existing:
            current_status = existing.status or "active"
            new_status = (metadata.get("status") or top.get(
                "status") or "active").lower()
            if STATUS_PRIORITY.get(new_status, 0) > STATUS_PRIORITY.get(current_status, 0):
                existing.with_context(syncing_from_mirillium=True).write(
                    {"status": new_status})
                _logger.info(" Updated payment link %s status to %s",
                             payment_link_id, new_status)
                return existing.link_url
            return None

        purchase_info = metadata.get("purchaseInformation", {}) or top.get(
            "purchaseInformation", {})
        purchase_number = purchase_info.get("purchaseNumber", "")
        invoice_id = None

        # Try to get invoice_id directly from record_env if it's an invoice
        if hasattr(record_env, "_name") and record_env._name == "account.move":
            invoice_id = record_env.id
            _logger.info(
                "  Using invoice_id directly from record: %s", invoice_id)
        else:
            # Fallback to extracting from purchase_number
            match = re.match(r"^INV(\d+)A\d+$", purchase_number)
            if not match:
                _logger.debug(
                    "  Skipping link due to invalid purchase number format: %s", purchase_number)
                return None
            invoice_id = int(match.group(1))

            invoice = env["account.move"].search(
                [("id", "=", invoice_id)], limit=1)
            if not invoice:
                _logger.warning(
                    " Invoice %s not found, skipping relation for link %s", invoice_id, payment_link_id)
                invoice_id = None

        link_url = purchase_info.get("paymentLink")
        if not link_url:
            _logger.debug(
                "  Skipping link %s due to missing paymentLink", payment_link_id)
            return None

        # created_date = parse_iso_datetime(purchase_info.get("createdDate") or top.get("createdDate"))
        expiration_date = parse_iso_datetime(purchase_info.get(
            "expirationDate") or top.get("expirationDate"))

        vals = {
            "link_url": link_url,
            "status": (metadata.get("status") or top.get("status") or "active").lower(),
            "payment_link_id": payment_link_id,
            "amount": Decimal(str(amount_details.get("totalAmount", "0"))),
            "currency_id": currency_id,
            "created_at": created_date,
            "expiration_date": expiration_date,
            "external_id": str(metadata.get("id") or top.get("id") or ""),
            "update_href": metadata.get("_links", {}).get("update", {}).get("href")
            or top.get("_links", {}).get("update", {}).get("href"),
            "self_href": metadata.get("_links", {}).get("self", {}).get("href")
            or top.get("_links", {}).get("self", {}).get("href"),
            "business_id": top.get("business_id"),
            "link_type": metadata.get("processingInformation", {}).get("linkType")
            or top.get("processingInformation", {}).get("linkType"),
            "product_description": order_info.get("lineItems", [{}])[0].get("productDescription"),
        }

        if invoice_id:
            vals["invoice_id"] = invoice_id

        clean_vals = {k: v for k, v in vals.items() if v not in [
            None, "", [], {}]}
        env["payrillium.payment.link"].create(clean_vals)
        _logger.info("  Created new payment link in Odoo: %s", payment_link_id)
        return clean_vals["link_url"]
    except Exception as e:
        _logger.exception("Error saving payment link to Odoo: %s", e)
        return None

