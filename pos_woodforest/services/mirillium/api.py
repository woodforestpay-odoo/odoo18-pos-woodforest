# ─────────────────────────────────────────────
#  Mirillium API Client – Terminals & Pay by Link
# ─────────────────────────────────────────────

from ...config import SHOPNET_API_URL, MIRILLIUM_PRIVATE_KEY, WF_MIRILLIUM_API_URL
import requests
import logging
import json
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from .utils import prepare_signed_request, parse_error_body_mirillium
from ..logging_service import log_payrillium_event
from .utils import get_unique_purchase_number
from .persistence import save_payment_link_to_odoo
from .utils import build_payment_payload

_logger = logging.getLogger(__name__)


environment_key = MIRILLIUM_PRIVATE_KEY

# ─────────────────────────────────────────────
#  Terminal Management (GET)
# ─────────────────────────────────────────────


def get_terminals_from_token(full_token):
    """
    Fetch terminal list using token and code from the Mirillium API.
    """
    # Mask token in logs - only show first 4 chars
    masked_token = f"{full_token[:4]}****" if full_token and len(
        full_token) > 4 else "****"
    _logger.info(
        "  Preparing Mirillium API call with token: %s", masked_token)

    if not full_token or len(full_token) < 5:
        return {"success": False, "message": "Token format is invalid. Minimum 5 characters required.", "terminals": []}

    code, token = full_token[:4], full_token[4:]
    try:
        response = requests.get(f"{SHOPNET_API_URL}/api/v1/get_terminals_by_customer",
                                params={"code": code, "token": token}, timeout=10)

        data = response.json()

        if response.status_code != 200:
            message = data.get(
                "message", f"API returned HTTP {response.status_code}")
            return {"success": False, "message": message, "terminals": []}

        # Mask sensitive data in response before logging
        from .utils import _mask_sensitive_data
        masked_data = _mask_sensitive_data(data)
        _logger.info("  Mirillium API response: %s", masked_data)

        if data.get("success") and isinstance(data.get("data"), list):
            terminals = [
                {
                    "name": t.get("model", {}).get("name", "Unknown"),
                    "serial": t.get("serial", "N/A"),
                    "gateway": t.get("gateway", "Unknown")
                }
                for t in data["data"]
            ]

            mirillium_config = {
                "merchant_id": data["mirillium_config"].get("code") if data["mirillium_config"].get("code") else None,
                "secret_key": data["mirillium_config"].get(environment_key) if data["mirillium_config"].get(environment_key) else None,
                "pbl_developer_id": data["mirillium_config"].get("pbl_developer_id") if data["mirillium_config"].get("pbl_developer_id") else None,
                "pbl_solution_id": data["mirillium_config"].get("pbl_solution_id") if data["mirillium_config"].get("pbl_solution_id") else None,
                "pbl_request_phone": data["mirillium_config"].get("pbl_request_phone") if data["mirillium_config"].get("pbl_request_phone") else None,
                "pbl_request_shipping": data["mirillium_config"].get("pbl_request_shipping") if data["mirillium_config"].get("pbl_request_shipping") else None
            }

            # SECURITY NOTE: secret_key is returned but should NEVER be logged
            # This is only returned to store in Odoo config (encrypted at rest)
            # Do not log this return value directly
            return {
                "success": True,
                "message": "Terminals fetched successfully",
                "terminals": terminals,
                "merchant_id": mirillium_config["merchant_id"],
                "secret_key": mirillium_config["secret_key"],
                "pbl_developer_id": mirillium_config["pbl_developer_id"],
                "pbl_solution_id": mirillium_config["pbl_solution_id"],
                "pbl_request_phone": mirillium_config["pbl_request_phone"],
                "pbl_request_shipping": mirillium_config["pbl_request_shipping"]
            }

        return {"success": False, "message": data.get("message", "Unexpected response from Mirillium"), "terminals": []}

    except requests.RequestException as e:
        _logger.error("  Terminal API error: %s", str(e))
        return {"success": False, "message": "Failed to connect to Mirillium API", "terminals": []}


# ─────────────────────────────────────────────
#  Pay by Link – CRUD Endpoints
# ─────────────────────────────────────────────

def create_payment_link(record, amount=None):
    """
    Create a new payment link (POST /api/v1/payment/links).
    """
    execution_id = str(uuid4())
    path = "/api/v1/payment/links"

    purchase_number = get_unique_purchase_number(record)
    amount_str = str(Decimal(amount or record.amount_total).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))

    payload = build_payment_payload(purchase_number, amount_str, record)
    request_body = json.dumps(payload, separators=(",", ":"))
    headers, host = prepare_signed_request(
        "POST", path, request_body, record, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"
    _logger.info(f"  url_payByLink: {url}")
    response = None
    try:
        log_payrillium_event(
            execution_id, "payment/createLink", "request", payload)
        response = requests.post(url, headers=headers,
                                 data=request_body.encode("utf-8"))
        _logger.info(f"  response.status_code : {response.status_code}")

        response.raise_for_status()
        data = response.json()

        # Mask sensitive data before logging
        from .utils import _mask_sensitive_data
        masked_data = _mask_sensitive_data(data)
        _logger.info(f"  response_payByLink (masked): %s", masked_data)
        _logger.info(f"  data.get success: %s", data.get('success'))

        if (response.status_code == 200 and data.get("success") == True):
            log_payrillium_event(
                execution_id, "payment/createLink", "response", data, success=True)
            link = save_payment_link_to_odoo(data, record)
            _logger.info("  Payment link saved in Odoo: %s", link)
            # Chatter message is posted only in the controller (generate_link) to avoid duplicates
            return link
        else:
            log_payrillium_event(
                execution_id, "payment/createLink-error", "response", data,
                success=False,
                error_message=data.get(
                    "message", f"HTTP {response.status_code}")
            )
            _logger.error("  HTTP Error %s: %s",
                          response.status_code, data.get("message"))
            return None

    except Exception as e:
        body, message, status = parse_error_body_mirillium(e, response)

        log_payrillium_event(
            execution_id,
            "payment/createLink-error",
            "response",
            {"status_code": status, "body": body},
            success=False,
            error_message=str(e)
        )
        _logger.error(
            "  Error creating payment link: %s - body: %s", str(e), body)

        # Return error details for better handling in controller
        return {
            "error": True,
            "status_code": status,
            "reason": body.get("data", {}).get("reason") if isinstance(body, dict) else None,
            "message": message
        }


def fetch_payment_links(offset, limit, record):
    """
    Read a list of payment links (GET /api/v1/payment/links).
    """
    path = f"/api/v1/payment/links?offset={offset}&limit={limit}"

    headers, host = prepare_signed_request(
        "GET", path, "", record, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def patch_payment_link(self, new_status):
    """
    Update payment link status (PATCH /api/v1/payment/links/<id>).
    """
    if not self.payment_link_id:
        _logger.warning("   No payment_link_id set, skipping external update")
        return

    execution_id = str(uuid4())
    path = f"/api/v1/payment/links/{self.payment_link_id}"
    payload = {"status": new_status.upper()}
    request_body = json.dumps(payload, separators=(",", ":"))

    headers, host = prepare_signed_request(
        "PATCH", path, request_body, self, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"
    response = None

    try:
        log_payrillium_event(
            execution_id, "payment/patchStatus", "request", payload)
        response = requests.patch(
            url, headers=headers, data=request_body.encode("utf-8"))
        response.raise_for_status()
        result = response.json()
        log_payrillium_event(execution_id, "payment/patchStatus",
                             "response", result, success=True)
        return result
    except Exception as e:
        body, message, status = parse_error_body_mirillium(e, response)
        log_payrillium_event(execution_id, "payment/patchStatus",
                             "response", {"status_code": status, "body": body}, success=False, error_message=message)
        _logger.error("  PATCH error: %s - body: %s", message, body)
        raise


# ─────────────────────────────────────────────
#   Authorize Payment (Using token)
# ─────────────────────────────────────────────


def create_payment_token(record, payload_data):
    """
    Call tokens/payment_instrument endpoint with full logging and signed request,
    but skips logging sensitive card data for PCI compliance.
    """
    execution_id = str(uuid4())
    path = "/api/v1/tokens/payment_instrument"
    request_body = json.dumps(payload_data, separators=(",", ":"))

    headers, host = prepare_signed_request(
        "POST", path, request_body, record, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"
    response = None

    try:
        # Determine if payload contains sensitive card data
        contains_card = "card" in payload_data

        # Get env from record to ensure logging works in RPC context
        odoo_env = record.env if hasattr(record, 'env') else None

        # Log only non-sensitive data
        # if not contains_card:
        #     log_payrillium_event(
        #         execution_id, "tokenization/createToken", "request", payload_data, env=odoo_env)
        # else:
        #     _logger.info(
        #         " Skipping request log for card payload due to sensitive data.")

        response = requests.post(url, headers=headers,
                                 data=request_body.encode("utf-8"), timeout=10)
        response.raise_for_status()
        data = response.json()

        # Log response only if it's not sensitive (usually token only)
        log_payrillium_event(
            execution_id, "tokenization/createToken", "response", data, success=True, env=odoo_env)

        _logger.info(
            " Payment token created successfully (execution_id=%s)", execution_id)

        if not data.get("success"):
            message = data.get(
                "message", "Unexpected response without success flag")
            return {"success": False, "message": message, "data": None}

        token_id = data["data"].get("token")
        if not token_id:
            return {"success": False, "message": "No instrument token returned", "data": None}

        return {"success": True, "token_id": token_id, "raw_data": data}

    except Exception as e:
        body, message, status = parse_error_body_mirillium(e, response)
        odoo_env = record.env if hasattr(record, 'env') else None
        log_payrillium_event(execution_id, "tokenization/createToken",
                             "response", body, success=False, error_message=message, env=odoo_env)
        _logger.error(
            " Exception during token creation: %s - body: %s", message, body)
        return {"success": False, "message": message, "data": body}


def delete_payment_token(record, provider_ref):
    """Delete a payment token / payment instrument in Mirillium.

    This helper is called from the `payment.token` model when an operator
    deletes one or more tokens from Odoo. The exact HTTP endpoint and
    payload are not yet defined, so for now this function just logs the
    intent and returns a dummy response structure.

    :param record: Odoo record used for env/credentials (usually payment.token)
    :param provider_ref: External reference for the token (payment instrument id)
    :return: dict with at least {"success": bool, "message": str}
    """
    _logger.info(
        "Requested deletion of payment token in Mirillium: provider_ref=%s (endpoint TBD)",
        provider_ref,
    )
    execution_id = str(uuid4())
    path = f"/api/v1/tokens/payment_instrument/{provider_ref}"
    
    try:
        headers, host = prepare_signed_request("DELETE", path, "", record, WF_MIRILLIUM_API_URL)
        url = f"{host}{path}"
        
        # Log request
        log_payrillium_event(execution_id, "tokenization/deleteToken", "request", {"path": path, "provider_ref": provider_ref})
        _logger.info(f"  Sending DELETE request to: {url}")

        response = requests.delete(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Log response
        log_payrillium_event(execution_id, "tokenization/deleteToken", "response", data, success=True)
        _logger.info("  Token deletion response: %s", data)
        
        return {"success": True, "message": data.get("message", "Deleted successfully")}
        
    except Exception as e:
        body, message, status = parse_error_body_mirillium(e, response if 'response' in locals() else None)
        log_payrillium_event(execution_id, "tokenization/deleteToken", 
                             "response", {"status_code": status, "body": body}, success=False, error_message=message)
        _logger.error("  Error deletion token: %s - body: %s", message, body)
        return {
            "success": False,
            "message": message,
            "data": body
        }


def authorize_payment(record, payment_instrument, amount, currency="USD", type=None, clientReferenceCode=None):
    """
    Call payment/authorize endpoint with full logging and signed request.
    """
    execution_id = str(uuid4())
    path = "/api/v1/payment/authorize"

    # Ojo
    # type card_type o check o saving set diferent endpoint mirillium

    payload = {
        "paymentInstrument": payment_instrument,
        "capture": True,
        "paymentType": type,
        "totalAmount": str(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "currency": currency,
        "clientReferenceCode": clientReferenceCode
    }

    request_body = json.dumps(payload, separators=(",", ":"))
    headers, host = prepare_signed_request(
        "POST", path, request_body, record, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"
    response = None

    try:
        log_payrillium_event(
            execution_id, "payment/authorize", "request", payload)

        response = requests.post(url, headers=headers,
                                 data=request_body.encode("utf-8"), timeout=10)

        response.raise_for_status()
        data = response.json()
        log_payrillium_event(execution_id, "payment/authorize",
                             "response", data, success=True)
        # Mask sensitive data before logging
        from .utils import _mask_sensitive_data
        masked_data = _mask_sensitive_data(data)
        _logger.info("  Payment authorized (masked): %s", masked_data)

        if not data.get("success"):
            message = data.get(
                "message", "Unexpected response without success flag")
            return {"success": False, "message": message, "data": None}

        return {"success": True, "authorization_data": data}
    except Exception as e:
        body, message, status = parse_error_body_mirillium(e, response)
        log_payrillium_event(
            execution_id,
            "payment/authorize",
            "response",
            {"status_code": status, "body": body},
            success=False,
            error_message=message
        )
        _logger.error(
            "  Exception during authorization: %s - body: %s", message, body)
        return {"success": False, "message": message, "data": body}


def tokenize_and_authorize(record, payload_data, amount, currency="USD"):
    """
    Orchestrates the full tokenization and authorization process.
    """
    token_result = create_payment_token(record, payload_data)
    if not token_result["success"]:
        return {"success": False, "step": "tokenization", "message": token_result["message"]}
    type = payload_data.get("type")
    token_id = token_result["token_id"]

    auth_result = authorize_payment(record, token_id, amount, currency, type)
    if not auth_result["success"]:
        return {"success": False, "step": "authorization", "message": auth_result["message"]}

    return {
        "success": True,
        "token_data": token_result["raw_data"],
        "authorization_data": auth_result["authorization_data"]
    }


def get_payment_status(record, token, env=None):
    """
    Fetch payment status by token (GET /api/v1/transactions/{token}).
    """
    execution_id = str(uuid4())
    path = f"/api/v1/transactions/{token}"

    try:
        headers, host = prepare_signed_request("GET", path, "", record, WF_MIRILLIUM_API_URL)
        url = f"{host}{path}"

        # GET request has no body payload
        log_payrillium_event(execution_id, f"transactions/{token}", "request", "", env=env)

        response = requests.get(url, headers=headers, timeout=30)
        # Try to parse JSON always to get API-level error messages
        try:
            data = response.json()
        except Exception:
            data = {"message": response.text or f"HTTP {response.status_code}"}

        if response.status_code == 200 and data.get("success"):
            log_payrillium_event(execution_id, f"transactions/{token}", "response", data, success=True, env=env)
            return {"success": True, "data": data.get("data"), "raw": data}
        else:
            log_payrillium_event(execution_id, f"transactions/{token}", "response", data, success=False, env=env)
            return {"success": False, "message": data.get("message", f"HTTP {response.status_code}"), "data": data}

    except Exception as e:
        # Fallback if request failed before getting a response
        resp = response if 'response' in locals() else None
        body, message, status = parse_error_body_mirillium(e, resp)
        log_payrillium_event(execution_id, f"transactions/{token}", "response", {"status_code": status, "body": body}, success=False, error_message=message, env=env)
        return {"success": False, "message": message, "data": body}


def refund_payment_by_token(record, token, amount, currency="USD"):
    """
    Call /api/v1/payment/{token}/refund to execute a refund via Mirillium.
    Returns relevant refund data including status, external_ref, receipt URL, etc.
    """
    from uuid import uuid4
    from decimal import Decimal, ROUND_HALF_UP
    import json
    import logging
    import requests

    _logger = logging.getLogger(__name__)

    execution_id = str(uuid4())
    path = f"/api/v1/payment/{token}/refund"

    payload = {
        "totalAmount": str(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "currency": currency
    }

    request_body = json.dumps(payload, separators=(",", ":"))
    headers, host = prepare_signed_request(
        "POST", path, request_body, record, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"

    _logger.info("urltest: %s", url)
    response = None

    try:
        log_payrillium_event(
            execution_id, "payment/refund", "request", payload)

        response = requests.post(url, headers=headers,
                                 data=request_body.encode("utf-8"), timeout=10)
        response.raise_for_status()

        data = response.json()

        log_payrillium_event(execution_id, "payment/refund",
                             "response", data, success=True)
        # Mask sensitive data before logging
        from .utils import _mask_sensitive_data
        masked_data = _mask_sensitive_data(data)
        _logger.info("Refund response (masked): %s", masked_data)

        if not data.get("success"):
            return {
                "success": False,
                "message": data.get("message", "Refund failed"),
                "data": data
            }

        refund_info = data.get("data", {})
        receipt_data = refund_info.get("metadata", {}).get(
            "payload", {}).get("data", {})

        return {
            "success": True,
            "refund_data": {
                "external_ref": refund_info.get("token"),
                "amount": refund_info.get("amount"),
                "status": refund_info.get("status"),
                "receipt_url": receipt_data.get("receiptUrl"),
                "card_type": receipt_data.get("transactionType"),
                "approval_code": receipt_data.get("orderAuthorizationCode"),
                "raw": data
            }
        }

    except Exception as e:
        body, message, status = parse_error_body_mirillium(e, response)
        log_payrillium_event(execution_id, "payment/refund",
                             "response", {"status_code": status, "body": body}, success=False, error_message=message)
        _logger.error("Refund error: %s - body: %s", message, body)
        return {
            "success": False,
            "message": message,
            "data": body
        }


def void_payment_by_token(record, token):
    """
    Call /api/v1/payment/{token}/void to execute a void via Mirillium.
    Usually for transactions that are AUTHORIZED but not yet settled.
    """
    from uuid import uuid4
    import logging
    import requests

    _logger = logging.getLogger(__name__)

    execution_id = str(uuid4())
    path = f"/api/v1/payment/{token}/void"

    headers, host = prepare_signed_request(
        "POST", path, "{}", record, WF_MIRILLIUM_API_URL)
    url = f"{host}{path}"

    try:
        log_payrillium_event(execution_id, "payment/void", "request", {"token": token})

        response = requests.post(url, headers=headers, data="{}", timeout=10)
        response.raise_for_status()

        data = response.json()
        log_payrillium_event(execution_id, "payment/void", "response", data, success=True)

        return {
            "success": data.get("success", False),
            "message": data.get("message", ""),
            "data": data.get("data", {})
        }

    except Exception as e:
        resp = response if 'response' in locals() else None
        body, message, status = parse_error_body_mirillium(e, resp)
        log_payrillium_event(execution_id, "payment/void",
                             "response", {"status_code": status, "body": body}, success=False, error_message=message)
        return {
            "success": False,
            "message": message,
            "data": body
        }


# ─────────────────────────────────────────────
#  Fetch payment  notifications
# ─────────────────────────────────────────────


def fetch_payment_link_notifications(record, link_id, env):
    """
    Fetch payment-link notifications for a given link_id.
    Endpoint: GET {WF_MIRILLIUM_API_URL}/payment/links/{link_id}/notifications
    Returns a dict: {"success": bool, "message": str, "payments": [...], "webhook_notifications": [...], "raw": <resp>}
    """
    execution_id = str(uuid4())
    path = f"/api/v1/payment/links/{link_id}/notifications"
    try:
        headers, host = prepare_signed_request(
            "GET", path, "", record, WF_MIRILLIUM_API_URL)
        url = f"{host}{path}"
    except Exception as e:
        _logger.exception(
            "prepare_signed_request error for link %s: %s", link_id, e)
        return {"success": False, "message": str(e), "payments": [], "webhook_notifications": [], "raw": None}

    try:
        log_payrillium_event(execution_id, "payment/link_notifications",
                             "request", {"path": path, "link_id": link_id}, env=env)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        log_payrillium_event(execution_id, "payment/link_notifications",
                             "response", data, success=True, env=env)
    except requests.HTTPError as e:
        _logger.exception(
            "HTTP error fetching link notifications for %s: %s", link_id, e)
        log_payrillium_event(execution_id, "payment/link_notifications",
                             "response", None, success=False, error_message=str(e), env=env)
        return {"success": False, "message": f"HTTP error: {str(e)}", "payments": [], "webhook_notifications": [], "raw": None}
    except Exception as e:
        _logger.exception(
            "Error fetching link notifications for %s: %s", link_id, e)
        log_payrillium_event(execution_id, "payment/link_notifications",
                             "response", None, success=False, error_message=str(e), env=env)
        return {"success": False, "message": str(e), "payments": [], "webhook_notifications": [], "raw": None}

    if not isinstance(data, dict):
        return {"success": False, "message": "Unexpected non-dict response", "payments": [], "webhook_notifications": [], "raw": data}

    if data.get("success") is False:
        return {"success": False, "message": data.get("message", "API returned success=false"), "payments": [], "webhook_notifications": [], "raw": data}

    payload = data.get("data") or {}
    payments = payload.get("payments") or []
    webhook_notifications = payload.get("webhook_notifications") or []

    if not isinstance(payments, list):
        payments = [payments]
    if not isinstance(webhook_notifications, list):
        webhook_notifications = [webhook_notifications]

    return {"success": True, "message": data.get("message", ""), "payments": payments, "webhook_notifications": webhook_notifications, "raw": data}


def fetch_ach_transaction_status(record, payment_id, env):
    """
    Fetch ACH/transaction status by paymentId.
    Endpoint: GET {WF_MIRILLIUM_API_URL}/payment/{paymentId}/transactionStatus
    Returns {"success": bool, "message": str, "data": {...}, "raw": <resp>}
    """
    execution_id = str(uuid4())
    path = f"/api/v1/payment/{payment_id}/transactionStatus"
    try:
        headers, host = prepare_signed_request(
            "GET", path, "", record, WF_MIRILLIUM_API_URL)
        url = f"{host}{path}"
    except Exception as e:
        _logger.exception(
            "prepare_signed_request error for payment %s: %s", payment_id, e)
        return {"success": False, "message": str(e), "data": None, "raw": None}

    try:
        log_payrillium_event(execution_id, "payment/transaction_status",
                             "request", {"path": path, "payment_id": payment_id}, env=env)
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        log_payrillium_event(execution_id, "payment/transaction_status",
                             "response", data, success=True, env=env)
    except requests.HTTPError as e:
        _logger.exception(
            "HTTP error fetching transaction status for %s: %s", payment_id, e)
        log_payrillium_event(execution_id, "payment/transaction_status",
                             "response", None, success=False, error_message=str(e), env=env)
        return {"success": False, "message": f"HTTP error: {str(e)}", "data": None, "raw": None}
    except Exception as e:
        _logger.exception(
            "Error fetching transaction status for %s: %s", payment_id, e)
        log_payrillium_event(execution_id, "payment/transaction_status",
                             "response", None, success=False, error_message=str(e), env=env)
        return {"success": False, "message": str(e), "data": None, "raw": None}

    if not isinstance(data, dict):
        return {"success": False, "message": "Unexpected non-dict response", "data": None, "raw": data}

    if data.get("success") is False:
        return {"success": False, "message": data.get("message", "API returned success=false"), "data": None, "raw": data}

    return {"success": True, "message": data.get("message", ""), "data": data.get("data"), "raw": data}


# ─────────────────────────────────────────────────────────────────────────────
#  Cybersource Transaction Search (TSS)
# ─────────────────────────────────────────────────────────────────────────────

def search_cybersource_transactions(filters: dict, env=None) -> dict:
    """
    POST {WF_MIRILLIUM_API_URL}/api/v1/transactions/searches

    Supported filter keys:
        date_from    str  "YYYY-MM-DD"  (required if no odoo_ref / cs_id)
        date_to      str  "YYYY-MM-DD"
        odoo_ref     str  earlyPaymentRef (clientReferenceInformation.code)
        cs_id        str  Cybersource transaction ID (searches by id field)
        amount       str  e.g. "4.60"
        card_suffix  str  last 4 digits of card

    Returns:
        { success, data: [list of transactionSummary dicts], message }
    """
    from ...config import WF_MIRILLIUM_API_URL
    from uuid import uuid4
    import json
    execution_id = f"cs_search_{uuid4().hex[:8]}"
    import logging
    _logger = logging.getLogger(__name__)
    _logger.info(f"TSS Search called with filters: {filters}")

    # Build Lucene query clauses
    clauses = []
    
    cs_id = filters.get("cs_id")
    if cs_id and str(cs_id).strip():
        clauses.append(f"id:{str(cs_id).strip()}")
        
    odoo_ref = filters.get("odoo_ref")
    if odoo_ref and str(odoo_ref).strip():
        clauses.append(f"clientReferenceInformation.code:{str(odoo_ref).strip()}")
        
    date_from = str(filters.get("date_from", "")).strip()
    date_to = str(filters.get("date_to", "")).strip()
    
    # In Cybersource TSS, if we search specifically by an ID (clientReferenceInformation.code or id), 
    # we do NOT need a date range. It actually hinders the search if the transaction is older or mistyped.
    # We should ONLY append a date query if the user EXPLICTLY requested a date filter.
    if date_from or date_to:
        if not date_from:
            date_from = "2024-01-01"
        if not date_to:
            date_to = "2099-12-31"

        try:
            # Note: The older format used [ms TO ms] which some TSS versions reject.
            # Cybersource strictly wants epoch MS when using the REST API for dates.
            from datetime import datetime, timezone
            dt_from_ts = datetime.strptime(f"{date_from} 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            ms_from = int(dt_from_ts.timestamp() * 1000)
            
            dt_to_ts = datetime.strptime(f"{date_to} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            ms_to = int(dt_to_ts.timestamp() * 1000)
            
            clauses.append(f"submitTimeUtc:[{ms_from} TO {ms_to}]")
        except ValueError as e:
            _logger.warning("Invalid date format for TSS search: %s", e)
            return {"success": False, "message": "Invalid date format. Expected YYYY-MM-DD", "data": []}
    
    amount = filters.get("amount")
    if amount and str(amount).strip():
        clauses.append(f"orderInformation.amountDetails.totalAmount:{str(amount).strip()}")
        
    card_suffix = filters.get("card_suffix")
    if card_suffix and str(card_suffix).strip():
        clauses.append(f"paymentInformation.card.suffix:{str(card_suffix).strip()}")

    # We want to ensure the user explicitly provided AT LEAST ONE diagnostic filter
    # Skip pagination/sorting params for this check
    ignore_keys = ["limit", "offset", "sort"]
    explicit_filters_count = sum(1 for k, v in filters.items() if k not in ignore_keys and v and str(v).strip())
    
    if explicit_filters_count == 0:
        _logger.warning("TSS Search blocked: No explicit filters provided")
        return {"success": False, "message": "At least one search filter is required (Date, Ref, ID, etc).", "data": []}

    query = " AND ".join(clauses)

    # Default sort if not provided
    sort_param = filters.get("sort", "submitTimeUtc:desc")

    payload = {
        "name": "MRN",
        "save": "false",
        "timezone": "America/Chicago",
        "query": query,
        "offset": int(filters.get("offset", 0)),
        "limit": int(filters.get("limit", 100)),
        "sort": sort_param,
    }

    path = "/api/v1/transactions/searches"
    request_body = json.dumps(payload, separators=(",", ":"))

    try:
        headers, host = prepare_signed_request("POST", path, request_body, env, WF_MIRILLIUM_API_URL)
        url = f"{host}{path}"
        
        log_payrillium_event(
            execution_id, "transactions/searches", "request",
            payload, env=env
        )
        resp = requests.post(url, headers=headers, data=request_body.encode("utf-8"), timeout=30)
        
        # Safely parse JSON - handle empty or whitespace-only responses from Mirillium
        try:
            data = resp.json() if resp.text and resp.text.strip() else {}
        except Exception as json_err:
            _logger.warning("TSS search: failed to parse response JSON (body=%r): %s", resp.text[:200] if resp.text else "", json_err)
            data = {"message": resp.text or f"HTTP {resp.status_code}"}
        resp.raise_for_status()

        log_payrillium_event(
            execution_id, "transactions/searches", "response",
            data, success=True, env=env
        )
    except requests.HTTPError as e:
        log_payrillium_event(
            execution_id, "transactions/searches", "response",
            None, success=False, error_message=str(e), env=env
        )
        return {"success": False, "message": f"HTTP error: {e}", "data": []}
    except Exception as e:
        log_payrillium_event(
            execution_id, "transactions/searches", "response",
            None, success=False, error_message=str(e), env=env
        )
        return {"success": False, "message": str(e), "data": []}

    # The Mirillium proxy should return { success, data: { message: { _embedded: { transactionSummaries: [...] } } } }
    # Or possibly directly { success: true, data: { _embedded: { transactionSummaries: [...] } } }
    # We must handle both structures just in case Mirillium flattened the 'message' layer.
    if not data.get("success"):
        upstream_msg = data.get("message")
        return {"success": False, "message": upstream_msg if upstream_msg else "TSS search failed", "data": []}

    nested = (data.get("data") or {})
    
    # It seems the previous CyberSource API returned `message` as an object:
    msg = nested.get("message")
    if isinstance(msg, dict) and "_embedded" in msg:
        embedded = msg.get("_embedded") or {}
        total = msg.get("totalCount", 0)
    else:
        # Fallback in case "message" is just a string and data has _embedded
        embedded = nested.get("_embedded") or {}
        total = nested.get("totalCount", 0)

    summaries = embedded.get("transactionSummaries") or []
    if not total:
        total = len(summaries)

    return {"success": True, "data": summaries, "total": total, "message": ""}


def get_transaction_status_by_cs_id(cs_id: str, env=None) -> dict:
    """
    GET {WF_MIRILLIUM_API_URL}/api/v1/transactions/{cs_id}

    Returns:
        { success, data: { status, amount, currency, ... }, message }
    """
    from ...config import WF_MIRILLIUM_API_URL
    from uuid import uuid4
    execution_id = f"cs_status_{uuid4().hex[:8]}"

    if not cs_id:
        return {"success": False, "message": "Cybersource ID is required.", "data": None}

    path = f"/api/v1/transactions/{cs_id}"

    try:
        headers, host = prepare_signed_request("GET", path, "", env, WF_MIRILLIUM_API_URL)
        url = f"{host}{path}"

        log_payrillium_event(
            execution_id, f"transactions/{cs_id}", "request",
            {"cs_id": cs_id}, env=env
        )
        resp = requests.get(url, headers=headers, timeout=30)
        
        # Safely parse JSON - handle empty or whitespace-only responses from Mirillium
        try:
            data = resp.json() if resp.text and resp.text.strip() else {}
        except Exception as json_err:
            _logger.warning("CS status: failed to parse response JSON (body=%r): %s", resp.text[:200] if resp.text else "", json_err)
            data = {"message": resp.text or f"HTTP {resp.status_code}"}
        resp.raise_for_status()

        log_payrillium_event(
            execution_id, f"transactions/{cs_id}", "response",
            data, success=True, env=env
        )
    except requests.HTTPError as e:
        log_payrillium_event(
            execution_id, f"transactions/{cs_id}", "response",
            None, success=False, error_message=str(e), env=env
        )
        return {"success": False, "message": f"HTTP error: {e}", "data": None}
    except Exception as e:
        log_payrillium_event(
            execution_id, f"transactions/{cs_id}", "response",
            None, success=False, error_message=str(e), env=env
        )
        return {"success": False, "message": str(e), "data": None}

    if not data.get("success"):
        return {"success": False, "message": data.get("message", "Status call failed"), "data": None}

    return {"success": True, "data": data.get("data"), "message": ""}
