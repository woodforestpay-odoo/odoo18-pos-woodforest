# sync.py
# ─────────────────────────────────────────────
#  Sync: Import Mirillium payment links into Odoo
# ─────────────────────────────────────────────

import logging
from uuid import uuid4
from .api import fetch_payment_links
from .persistence import save_payment_link_to_odoo
from ..logging_service import log_payrillium_event

_logger = logging.getLogger(__name__)


def sync_existing_payment_links(env):
    """
    Fetch all payment links from Mirillium and store/update them in Odoo.

    Args:
        env (Environment): Odoo environment (request.env or self.env).
        merchant_id (str): Merchant ID provided by Mirillium.
        secret_key (str): Secret key to sign requests.
        host (str): Hostname of Mirillium API (default: wf.mirillium.io).

    Returns:
        int: Number of new links successfully saved or updated.
    """
    execution_id = str(uuid4())
    offset = 0
    limit = 20
    total_synced = 0

    while True:
        try:
            _logger.info(f"    Fetching links from offset={offset}")
            log_payrillium_event(
                execution_id, "payment/getListLinks", "request", {"offset": offset, "limit": limit}, env=env)

            result = fetch_payment_links(offset, limit, env)
            log_payrillium_event(
                execution_id, "payment/getListLinks", "response", result, success=True, env=env)

            links = result.get("data", {}).get("data", {}).get("links", [])
            if not links:
                _logger.info("  No more links to sync.")
                break

            for item in links:
                if save_payment_link_to_odoo({"data": item}, env):
                    total_synced += 1

            next_exists = result.get("data", {}).get(
                "data", {}).get("_links", {}).get("next")
            if next_exists:
                offset += limit
            else:
                break

        except Exception as e:
            error_msg = str(e)
            _logger.error(f"  Error syncing payment links: {error_msg}")
            log_payrillium_event(
                execution_id,
                "payment/getListLinks",
                "response",
                None,
                success=False,
                error_message=error_msg
            )
            break

    _logger.info(f" Sync complete: {total_synced} payment links saved.")
    return total_synced

