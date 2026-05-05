from odoo import fields
from odoo.http import request
import json
import logging
import odoo
_logger = logging.getLogger(__name__)


def log_payrillium_event(execution_id, step_name, kind, payload=None, success=True, error_message=None, env=None, dbname=None):
    # Import masking function
    from ..services.mirillium.utils import _mask_sensitive_data

    # Mask sensitive data in payload before logging
    masked_payload = _mask_sensitive_data(payload) if payload else None
    _logger.info("  Logging event: %s | %s | %s | %s", step_name, execution_id,
                 " " if success else " ", masked_payload or error_message)
    try:
        log_values = {
            'timestamp': fields.Datetime.now(),
            'execution_id': execution_id or 'system',
            'endpoint': step_name,
            'log_type': kind,
            'success': success,
            'error_message': error_message or "",
        }

        if kind == "request":
            # Mask sensitive data before storing
            masked_for_storage = _mask_sensitive_data(
                payload) if payload else {}
            log_values['request_payload'] = json.dumps(masked_for_storage)
        else:
            # Mask sensitive data before storing
            masked_for_storage = _mask_sensitive_data(
                payload) if payload else {}
            log_values['response_payload'] = json.dumps(masked_for_storage)

        # Resolve the database name for the isolated cursor
        if dbname:
            # Called from a background thread — use dbname directly
            _dbname = dbname
        elif env:
            _dbname = env.cr.dbname
        elif getattr(request, "env", None):
            _dbname = request.env.cr.dbname
        else:
            _logger.warning("  No dbname, env, or request available — cannot save log to DB")
            return

        # Use an isolated cursor so the log is committed even if the main transaction rolls back
        with odoo.registry(_dbname).cursor() as new_cr:
            new_env = odoo.api.Environment(new_cr, odoo.SUPERUSER_ID, {})
            record = new_env['payrillium.log'].create(log_values)
            record_id = record.id
            # The context manager automatically commits upon exiting the block

        _logger.info("  Log saved: ID=%s, type=%s, endpoint=%s",
                     record_id, kind, step_name)
    except Exception as e:
        _logger.error("   Error logging Payrillium event: %s", e)

