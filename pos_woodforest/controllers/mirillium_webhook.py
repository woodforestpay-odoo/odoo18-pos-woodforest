from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied
import logging
import json
import hmac
import hashlib
from ..services.logging_service import log_payrillium_event
from ..services.webhook_service import process_webhook_payload  

_logger = logging.getLogger(__name__)


class MirilliumWebhookController(http.Controller):

    def _verify_webhook_signature(self, payload, signature_header, secret_key):
        """
        Verify the webhook signature to ensure it comes from Mirillium.
        Uses HMAC SHA-256 to validate the signature.
        
        Args:
            payload: The request body (string or bytes)
            signature_header: The header with the received signature
            secret_key: The configured secret key
        
        Returns:
            bool: True if the signature is valid, False otherwise
        """
        if not secret_key or not signature_header:
            return False
        
        try:
            # Convert payload to bytes if it's a string
            if isinstance(payload, str):
                payload_bytes = payload.encode('utf-8')
            else:
                payload_bytes = payload
            
            # Calculate the HMAC hash
            expected_signature = hmac.new(
                secret_key.encode('utf-8'),
                payload_bytes,
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures securely (prevents timing attacks)
            return hmac.compare_digest(expected_signature, signature_header)
        except Exception as e:
            _logger.error(f"Error verifying webhook signature: {e}")
            return False

    # NOTE: This endpoint must be public and csrf=False to receive webhooks from external Mirillium service.
    # Webhook signature is validated to ensure requests come from Mirillium.
    @http.route('/payment/mirillium/webhook', type='http', auth='public', csrf=False, methods=['POST'])
    def mirillium_webhook(self, **kw):
        # Get the request body as string for validation
        raw_payload = request.httprequest.get_data(as_text=True)
        post = request.httprequest.get_json(silent=True) or {}
        
        # Validate webhook signature if configured
        config = request.env['payrillium.config'].sudo().search([], limit=1)
        if config and config.secret_key:
            signature_header = request.httprequest.headers.get('X-Webhook-Signature') or \
                             request.httprequest.headers.get('X-Signature') or \
                             request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')
            
            if not self._verify_webhook_signature(raw_payload, signature_header, config.secret_key):
                _logger.warning("Invalid webhook signature received. Request rejected.")
                return request.make_response("Invalid signature", status=403)

        # Validate that the payload has the expected structure
        if not isinstance(post, dict):
            _logger.warning("Invalid webhook payload format. Expected dict.")
            return request.make_response("Invalid payload format", status=400)

        if "data" in post and "eventType" in post.get("data", {}):
            event_type = post.get("data", {}).get("eventType")
            data = post.get("data", {}).get("payload", {}).get("data", {})
        elif "type" in post:
            event_type = post.get("type")
            data = post.get("data", {})
        else:
            _logger.warning("Missing eventType/type in webhook payload")
            return request.make_response("Missing eventType/type", status=400)
        
        # Validate that event_type is a valid string
        if not isinstance(event_type, str) or not event_type.strip():
            _logger.warning("Invalid event_type in webhook payload")
            return request.make_response("Invalid event_type", status=400)

        try:
            success = process_webhook_payload(request.env, event_type, data)
        except Exception as e:
            _logger.error(f"Error processing webhook payload: {e}", exc_info=True)
            return request.make_response("Processing failed", status=500)

        if success is None:
            return request.make_response("", status=204)
        if success is False:
            return request.make_response("Processing failed", status=500)

        return request.make_response("OK", status=200)



