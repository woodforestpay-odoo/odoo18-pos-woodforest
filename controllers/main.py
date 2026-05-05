import json
import base64
import hashlib
import requests
import threading
import time
import uuid as uuid_lib
from datetime import datetime, timezone
import random
import logging

from odoo import _
from ..config import PAYMENT_METHOD_NAME
from ..config import PAYMENT_METHOD_COLOR
from ..config import PAYMENT_METHOD_ICON
from odoo import http, fields
from odoo.http import request
from ..services.mirillium.utils import get_payrillium_credentials
from ..services.logging_service import log_payrillium_event
from ..services.mirillium import create_payment_link, authorize_payment
from ..config import CLOUD_MIRILLIUM_API_URL, ENVIRONMENT
from ..services.mirillium.api import refund_payment_by_token
_logger = logging.getLogger(__name__)


API_BASE_URL = f"{CLOUD_MIRILLIUM_API_URL}"

# ─────────────────────────────────────────────
#  Async Polling Infrastructure
#  Avoids proxy timeout (504) on long Mirillium calls
# ─────────────────────────────────────────────
_pending_jobs = {}   # { job_id: { "status": "pending"|"done"|"error", "data": ..., "ts": float } }
_jobs_lock = threading.Lock()
_JOBS_MAX_AGE = 300  # seconds before a job is cleaned up


def _cleanup_old_jobs():
    """Remove jobs older than _JOBS_MAX_AGE seconds."""
    now = time.time()
    with _jobs_lock:
        expired = [k for k, v in _pending_jobs.items() if now - v.get("ts", 0) > _JOBS_MAX_AGE]
        for k in expired:
            del _pending_jobs[k]


def _run_mirillium_call(job_id, url, headers, body, execution_id, endpoint, dbname, post_process=None):
    """
    Execute the Mirillium HTTP call in a background thread and store the result.
    This function does NOT use request.env — it only does:
      1. Pure HTTP via requests.post()
      2. DB logging via log_payrillium_event(dbname=...)
    """
    try:
        response = requests.post(url, headers=headers, json={"data": body})

        # Capture response body BEFORE raise_for_status — Mirillium sends
        # useful error details in the body even on 4xx/5xx responses
        if not response.ok:
            http_status = response.status_code
            try:
                error_body = response.json()
            except Exception:
                error_body = {"raw": response.text[:2000] if response.text else "empty response"}
            _logger.error("  [async] Mirillium %s returned HTTP %s: %s", endpoint, http_status, error_body)
            log_payrillium_event(execution_id, endpoint, "response", error_body,
                                success=False, error_message=f"HTTP {http_status}", dbname=dbname)
            with _jobs_lock:
                _pending_jobs[job_id] = {
                    "status": "error",
                    "data": {"status": "error", "http_status": http_status, "api_response": error_body},
                    "ts": time.time()
                }
            return

        data = response.json()
        if post_process:
            data = post_process(data)
        log_payrillium_event(execution_id, endpoint, "response", data, success=True, dbname=dbname)
        with _jobs_lock:
            _pending_jobs[job_id] = {"status": "done", "data": data, "ts": time.time()}
    except Exception as e:
        error_msg = str(e)
        _logger.error("  [async] Error in %s: %s", endpoint, error_msg)
        log_payrillium_event(execution_id, endpoint, "response", None,
                            success=False, error_message=error_msg, dbname=dbname)
        with _jobs_lock:
            _pending_jobs[job_id] = {
                "status": "error",
                "data": {"status": "error", "message": error_msg},
                "ts": time.time()
            }


def _start_async_job(url, headers, body, execution_id, endpoint, dbname, post_process=None):
    """Create a pending job, launch a thread, and return the job_id."""
    job_id = str(uuid_lib.uuid4())
    with _jobs_lock:
        _pending_jobs[job_id] = {"status": "pending", "ts": time.time()}
    thread = threading.Thread(
        target=_run_mirillium_call,
        args=(job_id, url, headers, body, execution_id, endpoint, dbname, post_process),
        daemon=True
    )
    thread.start()
    _logger.info("  [async] Started job %s for %s", job_id, endpoint)
    return job_id

# ─────────────────────────────────────────────
#  Signature generation
# ─────────────────────────────────────────────


def get_token_from_config(env):
    config = env['payrillium.config'].sudo().search([], limit=1)
    if not config or not config.token:
        return None  # Return None instead of raising to allow mode without token
    return config.token


def build_header_hash(env, data, timestamp):
    key = get_token_from_config(env)  # token example: A210-1234567890
    if not key:
        raise ValueError(
            "Woodforest token not configured. Please configure in Settings > Woodforest Configuration.")
    data["key"] = base64.b64encode(
        f"{key}{timestamp}".encode("utf-8")).decode("utf-8")
    json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    json_bytes = json_str.encode("utf-8")
    base64_json = base64.b64encode(json_bytes).decode("utf-8")
    return hashlib.sha512(base64_json.encode("utf-8")).hexdigest()


def build_url(terminal_id, path_type, action):
    return f"{API_BASE_URL}{terminal_id}/{path_type}/{action}"


def _get_current_terminal(session_id=None):
    """
    Get the current terminal for the session, validating access permissions.

    Args:
        session_id: POS session ID (optional)

    Returns:
        Woodforest terminal or None if not found or no permissions
    """
    user = request.env.user

    if session_id:
        # Validate that session_id is a valid integer
        try:
            session_id_int = int(session_id)
            if session_id_int <= 0:
                _logger.warning(f"Invalid session_id: {session_id}")
                return None
        except (ValueError, TypeError):
            _logger.warning(f"Invalid session_id format: {session_id}")
            return None

        # Search session with permission validation
        session = request.env['pos.session'].sudo().browse(session_id_int)
        if not session.exists():
            _logger.warning(f"Session {session_id_int} not found")
            return None

        # Verify that the user has access to this session
        # The user must be the owner or have POS permissions
        if session.user_id.id != user.id:
            # Verify if the user has POS manager permissions
            if not user.has_group('point_of_sale.group_pos_manager'):
                _logger.warning(
                    f"User {user.id} attempted to access session {session_id_int} owned by {session.user_id.id}")
                return None

        # Verify that the session is opened
        if session.state not in ['opened', 'opening_control']:
            _logger.warning(
                f"Session {session_id_int} is not in opened state (state: {session.state})")
            return None

        # Get terminal only if it exists
        if session.config_id.payrillium_terminal_id:
            return session.config_id.payrillium_terminal_id
    else:
        # Search for current user's session
        session = request.env['pos.session'].sudo().search([
            ('user_id', '=', user.id),
            ('state', '=', 'opened')
        ], limit=1)
        _logger.debug("Current session: %s", session.id if session else "None")

        if session and session.config_id.payrillium_terminal_id:
            return session.config_id.payrillium_terminal_id

    return None


def deep_clean_payload(payload):
    if isinstance(payload, dict):
        cleaned = {}
        for key, value in payload.items():
            if key != "executionId" and value is not None:
                cleaned[key] = deep_clean_payload(value)
        return cleaned
    elif isinstance(payload, list):
        return [deep_clean_payload(item) for item in payload if item is not None]
    else:
        return payload


class PayrilliumWizardController(http.Controller):

    # ─────────────────────────────────────────────
    #  Async Polling Endpoint
    # ─────────────────────────────────────────────
    @http.route('/woodforest/poll', type='json', auth='user')
    def poll_job(self, **kwargs):
        """Poll for the result of an async Mirillium job."""
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]
        job_id = kwargs.get("job_id")
        if not job_id:
            return {"status": "error", "message": "Missing job_id"}

        _cleanup_old_jobs()

        with _jobs_lock:
            job = _pending_jobs.get(job_id)
            if job and job["status"] != "pending":
                # Job is done or errored — return and clean up
                _pending_jobs.pop(job_id, None)

        if not job:
            return {"status": "not_found"}

        return {"status": job["status"], "data": job.get("data")}

    # ─────────────────────────────────────────────
    #  Configuration Endpoints
    # ─────────────────────────────────────────────
    @http.route('/woodforest/config/environment', type='json', auth='public')
    def get_environment_config(self):
        """Return the current environment (dev/prod) for frontend logging control"""
        return {'environment': ENVIRONMENT}

    # ─────────────────────────────────────────────
    #  Local request --> DB-ODOO
    # ─────────────────────────────────────────────
    @http.route('/woodforest/payment_method_name', type='json', auth='user')
    def get_payment_method_name(self):
        _logger.debug("  Getting payment method name...")
        try:
            result = {"payment_method_name": PAYMENT_METHOD_NAME}
            _logger.debug("  Payment method name retrieved: %s",
                          PAYMENT_METHOD_NAME)

            return result
        except Exception as e:
            error_msg = str(e)
            _logger.error(f"  Error getting payment method name: {error_msg}")
            return {"status": "error", "message": error_msg}

    @http.route('/woodforest/payment_method_color', type='json', auth='user')
    def payment_method_color(self):
        _logger.debug(" Getting payment method color...")
        try:
            result = {"color": PAYMENT_METHOD_COLOR}
            _logger.debug("  Payment method color retrieved: %s",
                          PAYMENT_METHOD_COLOR)
            return result
        except Exception as e:
            error_msg = str(e)
            _logger.error(f"  Error getting payment method color: {error_msg}")
            return {"status": "error", "message": error_msg}

    @http.route('/woodforest/payment_method_icon', type='json', auth='user')
    def payment_method_icon(self):
        _logger.debug(" Getting payment method icon...")
        try:
            result = {"icon": PAYMENT_METHOD_ICON}
            _logger.debug("  Payment method icon retrieved: %s",
                          PAYMENT_METHOD_ICON)
            return result
        except Exception as e:
            error_msg = str(e)
            _logger.error(f"  Error getting payment method icon: {error_msg}")
            return {"status": "error", "message": error_msg}

    @http.route('/woodforest/payment_method_data', type='json', auth='user')
    def get_payment_method_data(self):
        payment_method = request.env['pos.payment.method'].search([
            ('use_payment_terminal', '=', 'woodforest')
        ], limit=1)

        return {
            'id': payment_method.id,
            'name': payment_method.name,
            'payment_provider_id': payment_method.payment_provider_id.id if payment_method.payment_provider_id else None,
            'receivable_account_id': payment_method.receivable_account_id.id if payment_method.receivable_account_id else None,
            'outstanding_account_id': payment_method.outstanding_account_id.id if payment_method.outstanding_account_id else None,
        }

    @http.route('/woodforest/log', type='json', auth='user')
    def log_from_js(self, execution_id, step, kind, success=True, error_message=None, payload=None):
        # Validate input to prevent injection or malicious data
        # Limit text field length
        MAX_STRING_LENGTH = 500

        if execution_id and len(str(execution_id)) > MAX_STRING_LENGTH:
            _logger.warning(f"Execution ID too long: {len(str(execution_id))}")
            return {"status": "error", "message": "Execution ID too long"}

        if step and len(str(step)) > MAX_STRING_LENGTH:
            _logger.warning(f"Step name too long: {len(str(step))}")
            return {"status": "error", "message": "Step name too long"}

        if kind and len(str(kind)) > MAX_STRING_LENGTH:
            _logger.warning(f"Kind too long: {len(str(kind))}")
            return {"status": "error", "message": "Kind too long"}

        if error_message and len(str(error_message)) > MAX_STRING_LENGTH * 2:
            error_message = str(error_message)[:MAX_STRING_LENGTH * 2]

        # Limit payload size (JSON)
        if payload and isinstance(payload, dict):
            import json
            try:
                payload_str = json.dumps(payload)
                if len(payload_str) > 10000:  # 10KB maximum
                    _logger.warning("Payload too large, truncating")
                    # Do not process very large payloads
                    payload = {"truncated": True, "size": len(payload_str)}
            except Exception:
                payload = None

        try:
            log_payrillium_event(
                execution_id=execution_id,
                step_name=step,
                kind=kind,
                payload=payload,
                success=success,
                error_message=error_message
            )
            return {"status": "success"}
        except Exception as e:
            _logger.error(f"Error logging event: {e}")
            return {"status": "error", "message": "Failed to log event"}

    @http.route('/woodforest/image_base_url', type='json', auth='user')
    def get_image_base_url(self):
        base_url = http.request.env['ir.config_parameter'].sudo(
        ).get_param('web.base.url')
        return {"image_base_url": f"{base_url}/woodforest" or "http://localhost:8069"}

    @http.route('/woodforest/environment', type='json', auth='user')
    def get_environment(self):
        return {"environment": ENVIRONMENT}

    @http.route('/woodforest/config/messages', type='json', auth='user')
    def get_terminal_messages(self):
        config = request.env['payrillium.config'].sudo().search([], limit=1)
        return {
            "approved": {
                "title": config.approved_title or "Approved",
                "message": config.approved_message or "{amount} Successfully Charged",
                "timeout": str(config.approved_timeout or 5)
            },
            "decline": {
                "title": config.decline_title or "Declined",
                "message": config.decline_message or "Transaction failed",
                "timeout": str(config.decline_timeout or 5)
            }
        }

    @http.route('/woodforest/session/terminal', type='json', auth='user')
    def get_terminal_from_session(self, sessionId, **kwargs):
        # Validate input
        try:
            session_id_int = int(sessionId)
            if session_id_int <= 0:
                raise ValueError("Invalid session ID")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid sessionId provided: {sessionId}")
            return {"success": False, "message": "Invalid session ID"}

        user = request.env.user

        # Search session with permission verification
        session = request.env['pos.session'].sudo().browse(session_id_int)
        if not session.exists():
            return {"success": False, "message": "No session found"}

        # Verify that the user has access to this session
        if session.user_id.id != user.id:
            # Verify if the user has POS manager permissions
            if not user.has_group('point_of_sale.group_pos_manager'):
                _logger.warning(
                    f"User {user.id} attempted to access session {session_id_int} owned by {session.user_id.id}")
                return {"success": False, "message": "Access denied"}

        # Verify that the session is opened
        if session.state != 'opened':
            _logger.warning(
                f"Session {session_id_int} is not in opened state (state: {session.state})")
            return {"success": False, "message": "Session is not opened"}

        _logger.info(f"session: {session}")
        terminal = session.config_id.payrillium_terminal_id
        _logger.info(f"terminal: {terminal}")
        if not terminal:
            return {"success": False, "message": "No terminal configured"}
        return {
            "success": True,
            "terminal": {
                "id": terminal.id,
                "name": terminal.name,
                "serial": terminal.serial,
                "iface_tipproduct": terminal.iface_tipproduct,
                "tip_mode": terminal.tip_mode or "amount",
                "tip_options": terminal.get_tip_option_values(),
            }
        }

    @http.route('/woodforest/image/<int:product_id>', type='http', auth='public', website=True, methods=['GET'])
    def get_image(self, product_id):
        # Validate that product_id is valid
        if product_id <= 0:
            return http.Response(status=400)

        # Only allow access to publicly visible products or with active POS session
        # Use sudo only for reading, not for writing
        product = request.env['product.product'].sudo().browse(product_id)

        if not product.exists():
            return http.Response(status=404)

        # Verify that the product is active (for additional security)
        if not product.active:
            return http.Response(status=404)

        if product.image_128:
            try:
                image_data = base64.b64decode(product.image_128)
                return request.make_response(
                    image_data,
                    headers=[
                        ('Content-Type', 'image/png'),
                        # Add security headers
                        ('Cache-Control', 'private, max-age=3600'),
                        ('X-Content-Type-Options', 'nosniff')
                    ]
                )
            except Exception as e:
                _logger.error(
                    f"Error decoding product image {product_id}: {e}")
                return http.Response(status=500)

        return http.Response(status=404)

    # ── Terminal Abort (18.3) ──────────────────
    @http.route('/woodforest/terminal/abort', type='json', auth='user')
    def abort_terminal(self, sessionId=None, **kwargs):
        """Send /payment/abort to the terminal assigned to this session.

        Called by the POS frontend when opening a session or navigating to the
        product screen, so the terminal is clean before a new order starts.
        """
        execution_id = kwargs.get("executionId", "missing")
        terminal = _get_current_terminal(sessionId)
        if not terminal:
            _logger.info("[abort_terminal] No terminal for session %s — skipping", sessionId)
            return {"success": False, "message": "No terminal for this session"}
        result = request.env['payrillium.terminal'].sudo()._reset_terminal_core(
            terminal.serial, execution_id=execution_id
        )
        ok = result.get("status") == "success"
        _logger.info("[abort_terminal] serial=%s execution_id=%s ok=%s", terminal.serial, execution_id, ok)
        return {"success": ok, "message": result.get("message", "")}

    # ── Terminal Ping (Live health check via local/terminal_info) ───
    @http.route('/woodforest/terminal/ping', type='json', auth='user')
    def ping_terminal(self, **kwargs):
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]
        session_id = kwargs.get("sessionId")
        execution_id = kwargs.get("executionId")
        terminal = _get_current_terminal(session_id)
        if not terminal:
            return {"status": "offline", "message": "No terminal assigned"}
            
        result = request.env['payrillium.terminal'].sudo()._ping_terminal_core(
            terminal.serial, execution_id=execution_id)
        if result.get("status") == "success":
            terminal_status = result.get("data", {}).get("data", {}).get("status", "").lower()
            if terminal_status in ("busy", "processing"):
                return {"status": "busy", "message": "Terminal is busy"}
            return {"status": "online", "message": "Terminal is ready"}
            
        return {"status": "offline", "message": result.get("message", "Network error")}

    # ── Terminal Status (lightweight, no PAX call) ──────────────────────
    @http.route('/woodforest/terminal/status', type='json', auth='user')
    def get_terminal_status(self, **kwargs):
        """Lightweight connectivity indicator: checks Odoo state only, does NOT call the PAX.
        Returns whether a terminal is configured and its session is open.
        Used by the navbar badge when tapped manually.
        """
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]
        session_id = kwargs.get("sessionId")
        terminal = _get_current_terminal(session_id)
        if not terminal:
            return {"configured": False, "session_open": False}
        return {
            "configured": True,
            "session_open": True,
            "terminal_name": terminal.name,
            "terminal_serial": terminal.serial,
        }

    # ── Duplicate Sale Detection ─────────────────────────────────────────
    @http.route('/woodforest/check_duplicate', type='json', auth='user')
    def check_duplicate(self, **kwargs):
        """Check if a completed transaction with the same amount exists in the last 60 minutes.
        Only matches state='done' — retries after error/cancel are never blocked.
        """
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]
        try:
            amount = float(kwargs.get("amount", 0))
            session_id = kwargs.get("session_id")
            if not amount or not session_id:
                return {"is_duplicate": False}

            from datetime import timedelta
            now = fields.Datetime.now()
            cutoff = now - timedelta(minutes=60)

            tx = request.env["payment.transaction"].sudo().search([
                ("state", "=", "done"),
                ("amount", ">=", amount - 0.01),
                ("amount", "<=", amount + 0.01),
                ("pos_session_id", "=", int(session_id)),
                ("create_date", ">=", cutoff),
            ], order="create_date desc", limit=1)

            if not tx:
                return {"is_duplicate": False}

            delta = now - tx.create_date
            minutes_ago = max(1, int(delta.total_seconds() / 60))
            return {
                "is_duplicate": True,
                "minutes_ago": minutes_ago,
                "reference": tx.reference or "",
            }
        except Exception as e:
            _logger.warning("[check_duplicate] Error: %s", e)
            return {"is_duplicate": False}

    # ── Payment router ─────────────────────────
    # actions: basket , card, tip
    @http.route('/woodforest/proxy/<string:action>', type='json', auth='user')
    def proxy_to_terminal(self, action, **kwargs):
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]
        # Use logger instead of print, and mask sensitive data
        from ..services.mirillium.utils import _mask_sensitive_data
        masked_kwargs = _mask_sensitive_data(kwargs)
        _logger.info(f" Incoming proxy request to endpoint: {action}")
        _logger.info(
            f" Payload (masked): {json.dumps(masked_kwargs, indent=2)}")

        execution_id = kwargs.get("executionId", "missing")
        if isinstance(execution_id, dict):
            execution_id = execution_id.get("execution_id", "missing")
        _logger.debug("execution_id: %s", execution_id)

        session_id = kwargs.get('sessionId')
        terminal = _get_current_terminal(session_id)
        if not terminal:
            return {"status": "error", "message": "No terminal configured for this session"}
        terminal_id = terminal.serial

        if action == "card":
            payload = {
                "data": "",
            }
        elif action in ["approved", "decline"]:
            # These endpoints use single nesting
            data = kwargs.copy()
            data.pop('executionId', None)
            data.pop('sessionId', None)
            payload = {"data": data}
        else:
            # basket, tip, view, test, etc. use double nesting
            payload_data = kwargs.copy()
            payload_data.pop('executionId', None)
            payload_data.pop('sessionId', None)
            payload = {
                "data": {
                    "data": payload_data,
                }
            }

        timestamp = int(datetime.utcnow().timestamp()) * 1000
        payload = deep_clean_payload(payload)
        request_body = json.dumps(payload, separators=(",", ":"))

        # Use logger instead of print, and mask sensitive data
        masked_payload = _mask_sensitive_data(payload)
        _logger.info(
            f" Final payload (masked): {json.dumps(masked_payload, indent=2)}")
        auth_hash = build_header_hash(request.env, payload, timestamp)

        log_payrillium_event(execution_id, action, "request", request_body)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth_hash}",
            "timestamp": str(timestamp)
        }

        try:
            url = build_url(terminal_id, "local", action)
            _logger.debug(f"  Calling endpoint: {action}")

            # Launch Mirillium call in background thread to avoid proxy timeout
            dbname = request.env.cr.dbname
            job_id = _start_async_job(
                url, headers, request_body, execution_id, action, dbname
            )
            return {"status": "polling", "job_id": job_id}

        except Exception as e:
            error_msg = str(e)
            _logger.error(f"  Error in call: {error_msg}")
            log_payrillium_event(
                execution_id, action, "response", None, success=False, error_message=error_msg)
            return {"status": "error", "message": error_msg}

    @http.route('/woodforest/payment/<string:action>', type='json', auth='user')
    def payrillium_payment_router(self, action, **kwargs):
        _logger.info(f" Incoming dynamic payment request to: {action}")
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]
        execution_id = kwargs.get("executionId", "missing")
        _logger.info("execution_id: %s", execution_id)

        session_id = kwargs.get('sessionId')
        terminal = _get_current_terminal(session_id)
        if not terminal:
            return {"status": "error", "message": "No terminal configured for this session"}
        terminal_id = terminal.serial

        payload_data = kwargs.copy()
        payload_data.pop('executionId', None)
        payload_data.pop('sessionId', None)
        # Remove order_ref and partner_id if present - these are not needed for terminal communication
        payload_data.pop('order_ref', None)
        payload_data.pop('partner_id', None)
        payload = {
            "data": payload_data,
        }

        payload = deep_clean_payload(payload)
        request_body = json.dumps(payload, separators=(",", ":"))
        timestamp = int(datetime.utcnow().timestamp()) * 1000
        auth_hash = build_header_hash(request.env, payload, timestamp)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth_hash}",
            "timestamp": str(timestamp)
        }

        log_payrillium_event(
            execution_id, f"payment/{action}", "request", request_body)

        # Ensure long numeric IDs are strings to avoid JS precision loss
        def _stringify_ids(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ('transactionId', 'id') and isinstance(v, (int, float)):
                        obj[k] = str(v)
                    else:
                        _stringify_ids(v)
            elif isinstance(obj, list):
                for item in obj:
                    _stringify_ids(item)

        def _post_process(data):
            """Called inside the thread after receiving the Mirillium response."""
            _stringify_ids(data)
            if isinstance(data, dict) and 'status' not in data:
                data['status'] = 'ok'
            return data

        try:
            url = build_url(terminal_id, "payment", action)
            _logger.debug(f"  Calling {url}")

            # Launch Mirillium call in background thread to avoid proxy timeout
            dbname = request.env.cr.dbname
            job_id = _start_async_job(
                url, headers, request_body, execution_id,
                f"payment/{action}", dbname, post_process=_post_process
            )
            return {"status": "polling", "job_id": job_id}

        except Exception as e:
            error_msg = str(e)
            _logger.error(f"  Error: {error_msg}")
            log_payrillium_event(
                execution_id, f"payment/{action}", "response", None, success=False, error_message=error_msg)
            return {"status": "error", "message": error_msg}


# only for tokenize card

    @http.route('/woodforest/refund_tokenize', type='json', auth='user')
    def payrillium_payment_refund_tokenize(self, **kwargs):
        """
        POS calls this endpoint to trigger a refund via token.
        kwargs expected: token, amount, currency (optional), record_id (optional)
        """
        if "kwargs" in kwargs:
            kwargs = kwargs["kwargs"]

        execution_id = kwargs.get("executionId", "missing")
        token = kwargs.get("token_card_id")
        amount = kwargs.get("amount")
        currency = kwargs.get("currency", "USD")
        record_id = kwargs.get("record_id")
        transaction_id = kwargs.get("transaction_id")

        # Validate input
        if not token:
            return {"status": "error", "message": "Missing token"}

        if not amount:
            return {"status": "error", "message": "Missing amount"}

        try:
            amount_float = float(amount)
            if amount_float <= 0:
                raise ValueError("Invalid amount")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid amount provided: {amount}")
            return {"status": "error", "message": "Invalid amount"}

        # Validate currency
        if not isinstance(currency, str) or len(currency) != 3:
            _logger.warning(f"Invalid currency code provided: {currency}")
            return {"status": "error", "message": "Invalid currency"}

        # Validate record_id if provided
        if record_id:
            try:
                record_id_int = int(record_id)
                if record_id_int <= 0:
                    raise ValueError("Invalid record ID")
                # Verify that the company exists and the user has access
                company = request.env["res.company"].browse(record_id_int)
                if not company.exists():
                    _logger.warning(f"Company {record_id_int} not found")
                    return {"status": "error", "message": "Company not found"}
                try:
                    company.check_access_rights('read')
                    company.check_access_rule('read')
                except Exception as e:
                    _logger.warning(
                        f"Access denied to company {record_id_int}: {e}")
                    return {"status": "error", "message": "Access denied"}
                record = company
            except (ValueError, TypeError):
                _logger.warning(f"Invalid record_id provided: {record_id}")
                return {"status": "error", "message": "Invalid record ID"}
        else:
            record = request.env.company

        try:
            result = refund_payment_by_token(
                record, transaction_id, amount, currency)

            if result.get("success"):
                return {"status": "ok", "data": result}
            else:
                return {
                    "status": "error",
                    "message": result.get("message", "Refund failed"),
                    "data": result,
                }
        except Exception as e:
            _logger.error("Refund error in controller: %s", str(e))
            return {"status": "error", "message": str(e)}
# ─────────────────────────────────────────────
#  Pay by Link – Routes
# ─────────────────────────────────────────────

    @http.route('/woodforest/generate_link', type='json', auth='user')
    def generate_link(self, model, id, amount):
        # Validate that the model is allowed (only invoice models)
        allowed_models = ['account.move']
        if model not in allowed_models:
            _logger.warning(f"Unauthorized model access attempt: {model}")
            return {"success": False, "error": "Unauthorized model"}

        # Validate that id is a valid integer
        try:
            record_id = int(id)
            if record_id <= 0:
                raise ValueError("Invalid ID")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid ID provided: {id}")
            return {"success": False, "error": "Invalid record ID"}

        # Validate that amount is a valid number
        try:
            amount_float = float(amount)
            if amount_float <= 0:
                raise ValueError("Invalid amount")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid amount provided: {amount}")
            return {"success": False, "error": "Invalid amount"}

        # Get the record with access verification
        record = request.env[model].browse(record_id)
        if not record.exists():
            return {"success": False, "error": "Record not found"}

        # Verify access permissions to the record
        try:
            record.check_access_rights('read')
            record.check_access_rule('read')
        except Exception as e:
            _logger.warning(
                f"Access denied to {model} record {record_id}: {e}")
            return {"success": False, "error": "Access denied"}

        # Early guard: if there is already an active/pending link for this invoice, reuse it
        try:
            # First check by invoice_id
            existing_links = request.env['payrillium.payment.link'].search([
                ('invoice_id', '=', record.id),
                ('status', 'in', ['active', 'pending'])
            ], order='create_date desc')

            _logger.info(
                f"🔍 Checking for existing links for invoice {record.id}: found {len(existing_links)} links by invoice_id")

            # If no links found by invoice_id, try by payment_link_id pattern (INV<id>A<seq>)
            if not existing_links:
                all_links = request.env['payrillium.payment.link'].search([
                    ('status', 'in', ['active', 'pending'])
                ], order='create_date desc')
                _logger.info(
                    f"🔍 Checking all active/pending links ({len(all_links)} total) for pattern matching")

                for link in all_links:
                    if link.payment_link_id and link.payment_link_id.startswith(f"INV{record.id}A"):
                        _logger.info(
                            f"  - Found link {link.id} with pattern INV{record.id}A*: status={link.status}")
                        existing_links = link
                        break

            for link in existing_links:
                _logger.info(
                    f"  - Link {link.id}: status={link.status}, invoice_id={link.invoice_id}, url={link.link_url[:50] if link.link_url else 'None'}...")

            if existing_links and existing_links[0].link_url:
                _logger.info(
                    f"✅ Reusing existing link {existing_links[0].id} for invoice {record.id}")
                return {
                    "success": True,
                    "link": existing_links[0].link_url,
                    "warning": "A payment link already exists for this invoice. Copying existing link. If you need a new one, deactivate/delete the current link first."
                }
            else:
                _logger.info(
                    f"❌ No valid existing link found for invoice {record.id}, proceeding to create new one")
        except Exception as e:
            _logger.error(
                f"Error checking existing links for invoice {record.id}: {e}")
            # If guard lookup fails, proceed to creation path (fallback)
            pass

        link = create_payment_link(record, amount=amount)

        # Check if link creation returned an error dict
        if isinstance(link, dict) and link.get("error"):
            # Handle DUPLICATE_RECORD specifically
            if link.get("reason") == "DUPLICATE_RECORD":
                # Try to find existing payment link for this invoice
                existing_link = request.env['payrillium.payment.link'].search([
                    ('invoice_id', '=', record.id),
                    ('status', 'in', ['active', 'pending'])
                ], limit=1, order='create_date desc')

                if existing_link and existing_link.link_url:
                    return {
                        "success": True,
                        "link": existing_link.link_url,
                        "warning": "A payment link already exists for this invoice. Showing existing link."
                    }
                else:
                    return {
                        "success": False,
                        "error": "A payment link already exists for this invoice, but could not be retrieved from the database."
                    }
            else:
                # Other errors
                return {
                    "success": False,
                    "error": link.get("message", "Failed to create payment link")
                }

        if not link:
            return {"success": False, "error": "Failed to create payment link"}

            # Add message to chatter when payment link is created
        if hasattr(record, 'message_post'):
            # Create a message with truncated URL and hidden full URL
            from markupsafe import Markup, escape
            # Sanitize the URL before using it in HTML to prevent XSS
            # Show only the first 30 characters + "..."
            escaped_link = escape(link)
            short_url = escaped_link[:30] + \
                "..." if len(escaped_link) > 30 else escaped_link
            # Use escape to prevent XSS in the hidden URL as well
            hidden_url = Markup(
                f'<span style="display:none;">{escaped_link}</span>')
            message_body = Markup(
                f"Payment link created: {short_url} {hidden_url}")

            record.sudo().message_post(
                body=message_body,
                message_type='notification',
                subtype_xmlid='mail.mt_comment'
            )

        return {"success": True, "link": link}

    @http.route('/woodforest/token/authorize', type='json', auth='user')
    def payrillium_token_authorize(self, token_id, amount, currency, provider_id):
        # Validate input
        try:
            token_id_int = int(token_id)
            if token_id_int <= 0:
                raise ValueError("Invalid token ID")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid token_id provided: {token_id}")
            return {"success": False, "message": "Invalid token ID"}

        try:
            amount_float = float(amount)
            if amount_float <= 0:
                raise ValueError("Invalid amount")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid amount provided: {amount}")
            return {"success": False, "message": "Invalid amount"}

        # Validate currency (only valid ISO codes)
        if not isinstance(currency, str) or len(currency) != 3:
            _logger.warning(f"Invalid currency code provided: {currency}")
            return {"success": False, "message": "Invalid currency"}

        # Get token with permission verification
        token = request.env['payment.token'].browse(token_id_int)
        if not token.exists():
            return {"success": False, "message": "Token not found"}

        try:
            token.check_access_rights('read')
            token.check_access_rule('read')
        except Exception as e:
            _logger.warning(f"Access denied to payment token {token_id}: {e}")
            return {"success": False, "message": "Access denied"}

        if not token.provider_ref:
            return {"success": False, "message": "Invalid token"}

        # Use centralized function to generate clientReferenceCode
        from ..services.mirillium.utils import generate_client_reference_code
        client_ref = generate_client_reference_code(
            source_type='TOKEN',
            provider_id=provider_id,
            token_ref=token.provider_ref,
            record_id=token_id_int
        )
        result = authorize_payment(
            record=token,
            payment_instrument=token.provider_ref,
            amount=amount,
            currency=currency,
            type='CARD',  # function called  from POS (only card type enabled)
            clientReferenceCode=client_ref,
        )
        return result

    def _build_bill_to_from_partner(self, partner):
        """
        Build billTo object from res.partner for Cybersource API
        Similar to payment_token_wizard format
        """
        # Split name into first_name and last_name
        name_parts = (partner.name or "").strip().split(None, 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        # Get country code
        country_code = "US"  # default
        if partner.country_id:
            country_code = partner.country_id.code or "US"

        # Get state code (administrativeArea needs 2-letter code for US/CA)
        state_name = ""
        if partner.state_id:
            state_name = partner.state_id.code or partner.state_id.name or ""
        elif partner.state:
            state_name = partner.state

        # Get phone (prefer phone, fallback to mobile)
        phone = partner.phone or partner.mobile or ""

        bill_to = {
            "firstName": first_name,
            "lastName": last_name,
            "address1": partner.street or "",
            "locality": partner.city or "",
            "administrativeArea": state_name,
            "postalCode": partner.zip or partner.zipcode or "",
            "country": country_code,
            "email": partner.email or "",
            "phoneNumber": phone,
        }

        return bill_to

    @http.route('/woodforest/token/create_from_auth', type='json', auth='user')
    def payrillium_token_create_from_auth(self, instrument_identifier_id, payment_details, partner_id, provider_id, token_type='tokenized_credit', cardVendor=None):
        """
        Create a payment.token from the auth response when tokenization was requested.
        This creates a Payment Instrument in Cybersource using instrumentIdentifier + billTo.
        """
        try:
            partner = request.env['res.partner'].browse(partner_id)
            if not partner.exists():
                return {"success": False, "message": "Invalid partner"}

            payment_method = request.env['payment.method'].search([
                ('code', '=', 'woodforest')
            ], limit=1)

            if not payment_method:
                return {"success": False, "message": "Payment method 'woodforest' not found"}

            payment_provider = request.env['payment.provider'].search([
                ('code', '=', 'woodforest')
            ], limit=1)

            if not payment_provider:
                return {"success": False, "message": "Payment provider 'woodforest' not found"}

            # Build billTo from partner
            bill_to = self._build_bill_to_from_partner(partner)

            # Validate that required billing address fields are present
            missing_fields = []
            if not bill_to.get("firstName"):
                missing_fields.append("First Name")
            if not bill_to.get("lastName"):
                missing_fields.append("Last Name")
            if not bill_to.get("email"):
                missing_fields.append("Email")
            if not bill_to.get("phoneNumber"):
                missing_fields.append("Phone")
            if not bill_to.get("country"):
                missing_fields.append("Country")
            if not bill_to.get("address1"):
                missing_fields.append("Address Line 1")
            if not bill_to.get("locality"):
                missing_fields.append("City")
            if not bill_to.get("administrativeArea"):
                missing_fields.append("State/Province")
            if not bill_to.get("postalCode"):
                missing_fields.append("ZIP")

            if missing_fields:
                return {
                    "success": False,
                    "message": f"Billing address incomplete. Missing fields: {', '.join(missing_fields)}",
                    "missing_fields": missing_fields
                }

            # Extract last 4 digits from payment_details (format: "VISA ****5228")
            last_four_digits = None
            if payment_details:
                parts = payment_details.split('****')
                if len(parts) > 1:
                    last_four_digits = parts[-1].strip()
                else:
                    import re
                    match = re.search(r'(\d{4})$', payment_details)
                    if match:
                        last_four_digits = match.group(1)

            # Check if token already exists for this instrument_identifier_id and partner (exact match)
            existing_token_by_ref = request.env['payment.token'].search([
                ('provider_ref', '=', instrument_identifier_id),
                ('partner_id', '=', partner_id),
                ('provider_id', '=', payment_provider.id),
            ], limit=1)

            if existing_token_by_ref:
                _logger.info(
                    f"Token already exists with same instrument_identifier_id: {existing_token_by_ref.id} for instrument_identifier_id: {instrument_identifier_id}")
                return {
                    "success": True,
                    "token_id": existing_token_by_ref.id,
                    "message": "Token already exists",
                    "already_exists": True
                }

            # Check if token with same last 4 digits already exists for this partner
            if last_four_digits:
                existing_tokens = request.env['payment.token'].search([
                    ('partner_id', '=', partner_id),
                    ('provider_id', '=', payment_provider.id),
                    ('token_type', 'in', ['TOKENIZED_CARD', 'tokenized_credit', 'tokenized_debit']),
                    ('active', '=', True),
                ])

                for token in existing_tokens:
                    token_last_four = None
                    if token.payment_details:
                        parts = token.payment_details.split('****')
                        if len(parts) > 1:
                            token_last_four = parts[-1].strip()
                        else:
                            import re
                            match = re.search(
                                r'(\d{4})$', token.payment_details)
                            if match:
                                token_last_four = match.group(1)

                    if token_last_four == last_four_digits:
                        _logger.info(
                            f"Token already exists with same last 4 digits ({last_four_digits}): {token.id} for partner {partner_id}")
                        return {
                            "success": True,
                            "token_id": token.id,
                            "message": f"Token already exists for card ending in {last_four_digits}",
                            "already_exists": True
                        }

            # Create Payment Instrument in Cybersource using instrumentIdentifier + billTo
            from ..services.mirillium.api import create_payment_token

            # Use lowercase card vendor as type (e.g. "visa", "mastercard") as requested
            card_type_code = "visa"  # Default
            if cardVendor:
                card_type_code = cardVendor.lower().strip()

            # Format payload for Cybersource API
            # The endpoint expects instrumentIdentifier as an object with id field
            payload_data = {
                "instrument": instrument_identifier_id,
                "billTo": bill_to,
                "card": {
                    "type": card_type_code
                }
            }

            _logger.info(
                f"bill_to data: {bill_to}")

            _logger.info(
                f"Payload data: {payload_data}")

            _logger.info(
                f"Creating Payment Instrument in Cybersource with instrumentIdentifier: {instrument_identifier_id[:10]}...")

            # Pass env explicitly to ensure logging works in RPC context
            token_result = create_payment_token(
                record=partner,
                payload_data=payload_data
            )

            # Log full response as requested
            _logger.info(f"Token creation API response: {token_result}")

            if not token_result.get("success"):
                _logger.error(
                    f"Failed to create Payment Instrument in Cybersource: {token_result.get('message')}")
                return {
                    "success": False,
                    "message": f"Failed to create payment instrument: {token_result.get('message')}"
                }

            # The token_id returned is the Payment Instrument ID (not the instrumentIdentifier)
            payment_instrument_id = token_result.get("token_id")

            if not payment_instrument_id:
                return {
                    "success": False,
                    "message": "No payment instrument ID returned from Cybersource"
                }

            # Create new payment.token with the Payment Instrument ID
            new_token = request.env['payment.token'].create({
                # Store Payment Instrument ID, not instrumentIdentifier
                "provider_ref": payment_instrument_id,
                "payment_details": payment_details,
                "partner_id": partner_id,
                "company_id": request.env.company.id,
                "payment_method_id": payment_method.id,
                "provider_id": payment_provider.id,
                "token_type": token_type,
                "active": True,
            })

            _logger.info(
                f"Payment token created from POS auth: {new_token.id} for partner {partner_id}, card ending in {last_four_digits or 'N/A'}, Payment Instrument ID: {payment_instrument_id[:10]}...")

            return {
                "success": True,
                "token_id": new_token.id,
                "payment_instrument_id": payment_instrument_id,
                "message": "Token created successfully",
                "already_exists": False,
                "notification": {
                    "title": "Token Created",
                    "message": f"Card added for {partner.name}",
                    "type": "success"
                }
            }
        except Exception as e:
            _logger.error(
                f"Error creating payment token from auth: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Error creating token: {str(e)}"
            }

    # ─────────────────────────────────────────────
    #  Terminal CRUD
    # ─────────────────────────────────────────────
    @http.route('/woodforest/check_terminal_backend', type='json', auth='user')
    def check_terminal_backend(self, terminal_id=None, **kw):
        if not terminal_id:
            return {"status": "error", "message": "No terminal ID or serial provided"}

        # Try to find terminal by ID (integer) or by serial (string)
        terminal = None
        try:
            # First, try as integer (terminal ID)
            terminal_id_int = int(terminal_id)
            if terminal_id_int > 0:
                terminal = request.env['payrillium.terminal'].browse(
                    terminal_id_int)
                if not terminal.exists():
                    terminal = None
        except (ValueError, TypeError):
            # Not an integer, try as serial (string)
            pass

        # If not found by ID, search by serial
        if not terminal or not terminal.exists():
            terminal = request.env['payrillium.terminal'].search([
                ('serial', '=', str(terminal_id))
            ], limit=1)

        # If still not found, return error
        if not terminal or not terminal.exists():
            _logger.warning(
                f"Terminal not found: {terminal_id} (tried as ID and serial)")
            return {"status": "error", "message": "Terminal not found"}

        # Verify that the user has access to this terminal
        try:
            terminal.check_access_rights('read')
            terminal.check_access_rule('read')
        except Exception as e:
            _logger.warning(
                f"Access denied to terminal {terminal.id} ({terminal_id}): {e}")
            return {"status": "error", "message": "Access denied"}

        # Check terminal using the terminal serial (not ID) - build_url requires serial
        if not terminal.serial:
            return {"status": "error", "message": "Terminal has no serial number configured"}

        return request.env['payrillium.terminal'].sudo()._check_terminal_core(terminal.serial)

    @http.route('/woodforest/check_config', type='json', auth='user')
    def check_config(self, **kw):
        """Check if Payrillium is configured"""
        config = request.env['payrillium.config'].sudo().search([], limit=1)
        is_configured = bool(config and config.installed and config.token)
        return {"configured": is_configured}

    @http.route('/woodforest/reset_terminal_backend', type='json', auth='user')
    def reset_terminal_backend(self, terminal_id=None, **kw):
        if not terminal_id:
            return {"status": "error", "message": "No terminal ID provided"}

        # Validate that terminal_id is a valid integer
        try:
            terminal_id_int = int(terminal_id)
            if terminal_id_int <= 0:
                raise ValueError("Invalid terminal ID")
        except (ValueError, TypeError):
            _logger.warning(f"Invalid terminal_id provided: {terminal_id}")
            return {"status": "error", "message": "Invalid terminal ID"}

        # Verify that the terminal exists and the user has write permissions
        terminal = request.env['payrillium.terminal'].browse(terminal_id_int)
        if not terminal.exists():
            return {"status": "error", "message": "Terminal not found"}

        try:
            terminal.check_access_rights('write')
            terminal.check_access_rule('write')
        except Exception as e:
            _logger.warning(
                f"Access denied to reset terminal {terminal_id}: {e}")
            return {"status": "error", "message": "Access denied"}

        return request.env['payrillium.terminal'].sudo()._reset_terminal_core(terminal_id_int)
