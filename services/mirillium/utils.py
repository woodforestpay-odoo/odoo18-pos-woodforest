# utils.py
# ─────────────────────────────────────────────
#  Utilities for Mirillium API Integration
# ─────────────────────────────────────────────
import re
from odoo import fields
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hmac
import hashlib
import base64
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


def _mask_sensitive_data(data, sensitive_keys=None):
    """
    Mask sensitive data in dictionaries/lists for safe logging.
    Keys: 'secret_key', 'token', 'card', 'bankAccount', 'securityCode', 'number'
    """
    if sensitive_keys is None:
        sensitive_keys = ['secret_key', 'token', 'card', 'bankAccount', 'securityCode', 'number',
                          'routingNumber', 'account_number', 'card_number', 'x-auth-signature']

    if isinstance(data, dict):
        masked = {}
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                if isinstance(value, str) and len(value) > 4:
                    masked[key] = f"{value[:4]}****"
                elif isinstance(value, (dict, list)):
                    masked[key] = _mask_sensitive_data(value, sensitive_keys)
                else:
                    masked[key] = "****"
            elif isinstance(value, (dict, list)):
                masked[key] = _mask_sensitive_data(value, sensitive_keys)
            else:
                masked[key] = value
        return masked
    elif isinstance(data, list):
        return [_mask_sensitive_data(item, sensitive_keys) for item in data]
    return data


def generate_signature(date_str, request_body='', method='post', resource_path='/', secret_key='', host='', merchant_id=''):
    digest = ''
    if request_body:
        utf8_encoded = request_body.encode('utf-8')
        sha256_digest = hashlib.sha256(utf8_encoded).digest()
        digest = base64.b64encode(sha256_digest).decode()

    signature_string = f"host: {host} date: {date_str} request-target: {method.lower()} {resource_path} digest: {digest} x-merchant-id: {merchant_id} - {secret_key}"

    signature_bytes = signature_string.encode('utf-8')
    hmac_signature = hmac.new(secret_key.encode(
        'utf-8'), signature_bytes, hashlib.sha256).digest()
    signature_b64 = base64.b64encode(hmac_signature).decode()

    # NOTE: signature_string contains secret_key - NEVER log this directly
    # Return masked version for logging if needed
    masked_signature_string = f"host: {host} date: {date_str} request-target: {method.lower()} {resource_path} digest: {digest} x-merchant-id: {merchant_id} - ****"
    return signature_b64, masked_signature_string


def build_headers(date_str, signature, merchant_id, host):
    """
    Construct standard headers for Mirillium API requests.
    """
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-merchant-id": merchant_id,
        "x-request-date-time": date_str,
        "x-auth-signature": signature,
        "x-mirillium-host": host,
    }


_logger = logging.getLogger(__name__)


def parse_iso_datetime(dt_str):
    """
    Parse provider datetime strings with tolerance for fractional seconds.
    Works only with Python stdlib (no dateutil).
    Returns an Odoo-formatted datetime string or None.
    """
    if not dt_str:
        return None

    s = str(dt_str).strip()

    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return fields.Datetime.to_string(dt)
    except Exception:
        pass

    m = re.match(
        r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?$", s)
    if m:
        date_part, time_part, frac = m.groups()
        frac = (frac or "0")[:6].ljust(6, "0")
        normalized = f"{date_part} {time_part}.{frac}"
        try:
            dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S.%f")
            return fields.Datetime.to_string(dt)
        except Exception as e:
            _logger.warning(
                "   Invalid datetime after normalization: %s (%s)", s, e)
            return None

    _logger.warning("   Invalid datetime format: %s", s)
    return None


def get_unique_purchase_number(invoice):
    """
    Returns a unique alphanumeric purchase number for payment attempts.
    Format: INV<invoice_id>A<timestamp>
    """
    from datetime import datetime

    # Use timestamp to ensure uniqueness and avoid duplicates
    timestamp = int(datetime.now().timestamp())
    return f"INV{invoice.id}A{timestamp}"


def generate_client_reference_code(source_type, record_id=None, provider_id=None,
                                   token_ref=None, order_ref=None, invoice_id=None):
    """
    Generate a standardized clientReferenceCode for Cybersource API calls.

    Format: {PREFIX}-{TYPE}-{ID_INFO}-{TIMESTAMP}-{RANDOM}

    Args:
        source_type: Type of source ('POS', 'INVOICE', 'TOKEN')
        record_id: Optional record ID (order_id, invoice_id, etc.)
        provider_id: Optional provider ID
        token_ref: Optional token reference (for token payments)
        order_ref: Optional order reference (POS order number)
        invoice_id: Optional invoice ID

    Returns:
        str: Formatted clientReferenceCode
    """
    import random
    from datetime import datetime

    timestamp = int(datetime.now().timestamp())
    random_suffix = random.randint(1000, 9999)

    # Build ID parts
    id_parts = []
    if record_id:
        id_parts.append(f"R{record_id}")
    if provider_id:
        id_parts.append(f"P{provider_id}")
    if token_ref:
        # Use first 8 chars of token ref to keep it short
        token_short = str(token_ref)[:8] if token_ref else ""
        if token_short:
            id_parts.append(f"T{token_short}")
    if order_ref:
        # Use last 6 chars of order ref
        order_short = str(order_ref)[-6:] if order_ref else ""
        if order_short:
            id_parts.append(f"O{order_short}")
    if invoice_id:
        id_parts.append(f"I{invoice_id}")

    id_str = "-".join(id_parts) if id_parts else "GEN"

    # Format: PAYRILLIUM-{TYPE}-{ID_INFO}-{TIMESTAMP}-{RANDOM}
    return f"PAYRILLIUM-{source_type}-{id_str}-{timestamp}-{random_suffix}"


def get_payrillium_credentials(env):
    config = env['payrillium.config'].sudo().search([], limit=1)
    if not config or not config.merchant_id or not config.secret_key:
        return None, None, None  # Retornar None en lugar de raise para permitir modo sin token
    return config.merchant_id, config.secret_key, config.token


def prepare_signed_request(method, path, body, record, host):
    env = record.env if hasattr(record, "env") else record
    merchant_id, secret_key, _ = get_payrillium_credentials(env)

    # Validate that credentials are configured
    if not merchant_id or not secret_key:
        raise UserError(
            "Payrillium not configured. Please configure merchant ID and secret key in Settings > Payrillium Configuration.")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    signature, _ = generate_signature(
        date_str=date_str,
        request_body=body,
        method=method,
        resource_path=path,
        secret_key=secret_key,
        host=host,
        merchant_id=merchant_id
    )
    headers = build_headers(date_str, signature, merchant_id, host)
    return headers, host


def build_payment_payload(purchase_number, amount, record=None):
    """
    Build the JSON payload for creating a payment link.

    :param purchase_number: Unique ID for the purchase (e.g. INV2A1)
    :param amount: Decimal or string amount (e.g. "47812.50")
    :return: dict payload
    """
    env = record.env if hasattr(record, "env") else record
    config = env['payrillium.config'].search([], limit=1)
    # Ensure amount is properly formatted with two decimal places
    amount_str = str(Decimal(amount).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "clientReferenceInformation": {
            # Developer ID and Solution ID are used by Cybersource to identify the integrator or partner
            "partner": {
                "developerId": config.pbl_developer_id,     # ID for  integrator
                "solutionId": config.pbl_solution_id       # ID registered with Cybersource
            }
        },
        "processingInformation": {
            # Use "PURCHASE" for products/services. Use "DONATION" for voluntary payments
            "linkType": "PURCHASE",
            # Should we ask the customer for their phone number?
            "requestPhone": config.pbl_request_phone,
            # Should we ask for a shipping address?
            "requestShipping": config.pbl_request_shipping
        },
        "purchaseInformation": {
            # Custom alphanumeric code. Format: INV<id>A<attempt> (e.g. INV2A1)
            "purchaseNumber": purchase_number
        },
        "orderInformation": {
            "amountDetails": {
                "totalAmount": amount_str,
                "currency": "USD",
                # "maxAmount": "",  # Optional: Only for DONATION
                # "minAmount": "1"  # Optional: Only for DONATION
            },
            "lineItems": [
                {
                    "productSku": "",  # Are we going to include a product SKU? Empty for now cause not working
                    # Static title (can be customized)
                    "productName": "Payment for invoice",
                    "quantity": "1",  # Always 1 per link
                    "unitPrice": amount_str,
                    # Dynamic description
                    "productDescription": f"Payment link for invoice {purchase_number}"
                }
            ]
        }
    }


def parse_error_body_mirillium(exc, response=None):
    """Return (body_dict_or_text, friendly_message, status_code)."""
    import json
    resp = getattr(exc, "response", None) or response
    status_code = getattr(resp, "status_code",
                          None) if resp is not None else None

    # parse body (prefer JSON)
    if resp is not None:
        try:
            body = resp.json()
        except Exception:
            txt = getattr(resp, "text", None) or str(exc)
            try:
                body = json.loads(txt)
            except Exception:
                body = txt
    else:
        try:
            body = json.loads(str(exc))
        except Exception:
            body = {"error": str(exc)}

    # build friendly message for Mirillium API errors
    if isinstance(body, dict):
        parts = []

        # Main message from top level
        main_msg = body.get("message")
        if main_msg:
            parts.append(str(main_msg))

        # Check for Mirillium-specific error structure
        if "data" in body and isinstance(body["data"], dict):
            data = body["data"]

            # Check for errors array (Mirillium format)
            if "errors" in data and isinstance(data["errors"], list):
                error_details = []
                for error in data["errors"]:
                    if isinstance(error, dict):
                        error_msg = error.get("message", "")
                        error_type = error.get("type", "")

                        # Handle details array for field-specific errors
                        if "details" in error and isinstance(error["details"], list):
                            for detail in error["details"]:
                                if isinstance(detail, dict):
                                    field_name = detail.get("name", "")
                                    field_location = detail.get("location", "")
                                    field_msg = detail.get("message", "")

                                    if field_location:
                                        field_desc = f"{field_location}.{field_name}" if field_name else field_location
                                    else:
                                        field_desc = field_name if field_name else "unknown field"

                                    error_details.append(
                                        f"{field_desc}: {field_msg or 'invalid value'}")

                        # Add error message and type if no details
                        if not error.get("details"):
                            if error_type and error_msg:
                                error_details.append(
                                    f"{error_type}: {error_msg}")
                            elif error_msg:
                                error_details.append(error_msg)
                            elif error_type:
                                error_details.append(error_type)

                if error_details:
                    parts.extend(error_details)

            # Fallback to regular data structure (DUPLICATE_RECORD, etc)
            elif "reason" in data or "status" in data:
                status = data.get("status")
                reason = data.get("reason")
                data_msg = data.get("message")
                if status:
                    parts.append(str(status))
                if reason:
                    parts.append(str(reason))
                if data_msg:
                    parts.append(str(data_msg))

        # If no specific structure found, try generic approach
        if not parts:
            data = body.get("data") if "data" in body else body
            status = (data.get("status") or body.get("status")
                      ) if isinstance(data, dict) else None
            main_msg = (body.get("message") or (
                data.get("message") if isinstance(data, dict) else None))
            if status:
                parts.append(str(status))
            if main_msg:
                parts.append(str(main_msg))
            # details -> list of {field, reason}
            details = (data.get("details") if isinstance(
                data, dict) else None) or body.get("details")
            if isinstance(details, list) and details:
                detail_texts = []
                for d in details:
                    f = d.get("field") or d.get("property") or "field?"
                    r = d.get("reason") or d.get(
                        "message") or d.get("detail") or ""
                    detail_texts.append(f"{f}: {r}")
                parts.append("; ".join(detail_texts))

        friendly = " - ".join(parts) if parts else str(body)
    else:
        friendly = str(body)

    return body, friendly, status_code

