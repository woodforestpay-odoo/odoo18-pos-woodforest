# models/payment_transaction.py
import logging
from odoo import models, fields, api, _
from ..services.mirillium.api import refund_payment_by_token, void_payment_by_token
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentTransactionPayrillium(models.Model):
    _inherit = 'payment.transaction'

    payrillium_terminal_id = fields.Many2one(
        'payrillium.terminal',
        string="Woodforest Terminal Used",
        help="Terminal used to process this Woodforest transaction."
    )
    terminal_short = fields.Char(
        string="Terminal",
        compute="_compute_terminal_short",
        store=True,
        help="Last 4 digits of the terminal serial."
    )

    @api.depends('payrillium_terminal_id', 'payrillium_terminal_id.serial')
    def _compute_terminal_short(self):
        for rec in self:
            serial = rec.payrillium_terminal_id.serial if rec.payrillium_terminal_id else ''
            rec.terminal_short = f"...{serial[-4:]}" if serial and len(serial) >= 4 else serial or ''
    card_type = fields.Char(
        string="Card Type",
        help="The card type used in this transaction (CREDIT or DEBIT)"
    )
    pos_order_id = fields.Many2one(
        'pos.order',
        string="PoS Order",
        help="The Point of Sale order linked to this transaction."
    )
    pos_order_uid = fields.Char(
        string="PoS Order Reference",
        index=True,
        help="POS pos_reference stored to allow backfilling pos_order_id after order is created."
    )
    pos_session_id = fields.Many2one(
        'pos.session',
        string="PoS Session",
        help="The Point of Sale session linked to this transaction, even if failed."
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string="PoS Configuration",
        help="The Point of Sale configuration linked to this transaction."
    )
    is_duplicate = fields.Boolean(
        string="Duplicate Payment",

        help="This transaction was made using a payment link that has already been used. It should be reviewed manually."
    )
    payrillium_card_token = fields.Char(
        string="Woodforest Token",
        copy=False,
        help="Token returned by terminal or provider"
    )
    payrillium_available_actions = fields.Char(
        string="Available Actions", 
        help="JSON list of valid next actions based on Cybersource state (e.g., ['void'] or ['refund'])",
        default="[]"
    )
    execution_id = fields.Char(
        string="Execution ID",
        index=True,
        help="Traces this transaction to its execution logs in payrillium.log."
    )
    transaction_status = fields.Selection([
        ('none', 'Not Initiated'),
        ('in_progress', 'Processing'),
        ('authorized', 'Approved'),
        ('auth_failed', 'Declined'),
        ('cancelled', 'Cancelled'),
        ('declined', 'Declined'),
        ('device_error', 'Terminal Error'),
        ('terminal_busy', 'Terminal Busy'),
        ('captured', 'Approved'),
        ('capture_failed', 'Needs Review'),
        ('voided', 'Voided'),
        ('void_failed', 'Needs Review'),
        ('reversed', 'Reversed'),
        ('reversal_failed', 'Needs Review'),
        ('credit_refund_pending', 'Refund Pending'),
        ('credit_refunded', 'Refunded'),
        ('debit_refund_pending', 'Refund Pending'),
        ('debit_refunded', 'Refunded'),
        ('refunded', 'Refunded'),
        ('error', 'Error')
    ], string="Payment Status", default='none', help="Current status of the transaction.")

    terminal_message = fields.Text(
        string="Terminal Response",
        readonly=True,
        help="Original response from the terminal. Never overwritten after first set."
    )

    pos_order_snapshot_json = fields.Text(string="POS Order Snapshot JSON", help="Stores the serialized cart before actual terminal execution.")
    pos_receipt_data = fields.Text(string="POS Receipt Data JSON", help="Cached receipt metadata from TSS for offline recovery.")
    is_recovery_order = fields.Boolean(string="Is Recovery Order", help="Technical flag for pos.order")
    status_summary = fields.Char(string="Details", readonly=True, help="Simple user-facing status summary.")
    tip_amount = fields.Monetary(
        string="Tip Amount",
        default=0,
        currency_field='currency_id',
        help="Terminal tip charged on top of the base amount. Only set when auth is approved."
    )
    pos_employee_id = fields.Many2one(
        'hr.employee', string="Cashier", readonly=True,
        help="POS cashier (employee) who processed this transaction."
    )

    def _append_status_log(self, msg):
        """Append a timestamped entry to state_message instead of overwriting."""
        ts = fields.Datetime.now().strftime('%m/%d %H:%M')
        entry = f"[{ts}] {msg}"
        for rec in self:
            current = rec.state_message or ""
            rec.state_message = f"{current}\n{entry}".strip()

    # ── Default friendly message by transaction_status ──
    # Error-specific messages (T01, C202, etc.) are handled ONLY in the frontend
    # (utils.js TCODE_RULES / CCODE_MAP) and sent as status_summary.
    # This map is used as a fallback when the frontend doesn't provide a summary
    # (e.g., server-side status changes via Cybersource Check Payment Status).
    STATUS_SUMMARY_MAP = {
        'none':                  "Not initiated.",
        'in_progress':           "Processing payment...",
        'authorized':            "Payment approved.",
        'auth_failed':           "Payment declined.",
        'cancelled':             "Transaction cancelled.",
        'declined':              "Payment declined.",
        'device_error':          "Terminal error. Please try again.",
        'terminal_busy':         "Terminal was busy. Please try again.",
        'captured':              "Payment completed.",
        'capture_failed':        "Capture failed. Needs review.",
        'voided':                "Payment Voided \u2013 Please Try Again.",
        'void_failed':           "Void failed. Needs review.",
        'reversed':              "Payment Reversed \u2013 Please Try Again.",
        'reversal_failed':       "Reversal failed. Needs review.",
        'credit_refund_pending': "Refund pending.",
        'credit_refunded':       "Refund completed.",
        'debit_refund_pending':  "Refund pending.",
        'debit_refunded':        "Refund completed.",
        'refunded':              "Refund completed.",
        'error':                 "Payment error.",
    }

    def _compute_status_summary(self, terminal_message=None, frontend_summary=None):
        """Compute status_summary from:
           1. Frontend-provided summary (from utils.js resolveErrorCode)
           2. Default by transaction_status
        """
        for rec in self:
            # 1. Frontend already resolved a specific message
            if frontend_summary:
                rec.status_summary = frontend_summary
                continue
            # 2. Default by transaction_status
            rec.status_summary = self.STATUS_SUMMARY_MAP.get(
                rec.transaction_status, "Payment error."
            )

    @api.model
    def get_session_transactions(self, session_id, employee_id=False):
        """Return Woodforest transactions for a POS session (read-only, for POS UI).
        
        employee_id values:
          - False/None: no employee filter (show all in session)
          - positive int: filter to that specific employee
        """
        domain = [
            ('provider_id.code', '=', 'woodforest'),
            ('pos_session_id', '=', session_id),
        ]
        if employee_id:
            # Match by pos_employee_id (new), or by pos_order.employee_id (legacy fallback)
            domain.extend([
                '|',
                ('pos_employee_id', '=', employee_id),
                '&',
                ('pos_employee_id', '=', False),
                ('pos_order_id.employee_id', '=', employee_id),
            ])
        return self._read_transaction_list(domain)

    @api.model
    def get_my_transactions(self, config_id, employee_id=False):
        """Return Woodforest transactions across all sessions of a POS config.
        
        employee_id values:
          - False/None: fallback to create_uid (legacy/no pos_hr)
          - positive int: filter to that specific employee
        """
        domain = [
            ('provider_id.code', '=', 'woodforest'),
            ('pos_session_id.config_id', '=', config_id),
        ]
        if employee_id:
            # Match by pos_employee_id (new), or by pos_order.employee_id (legacy fallback)
            domain.extend([
                '|',
                ('pos_employee_id', '=', employee_id),
                '&',
                ('pos_employee_id', '=', False),
                ('pos_order_id.employee_id', '=', employee_id),
            ])
        else:
            # Fallback: filter by the user who created the transaction
            domain.append(('create_uid', '=', self.env.uid))

        return self._read_transaction_list(domain, limit=200)

    def _read_transaction_list(self, domain, limit=None):
        """Shared helper for transaction list queries."""
        txs = self.search(domain, order='create_date desc', limit=limit)
        result = []
        for tx in txs:
            vals = tx.read([
                'id', 'reference', 'amount', 'create_date',
                'transaction_status', 'state', 'card_type',
                'terminal_message', 'state_message',
                'status_summary', 'provider_reference',
                'execution_id', 'tip_amount', 'terminal_short',
            ])[0]
            vals['pos_order_name'] = tx.pos_order_id.pos_reference or tx.pos_order_id.name or ''
            vals['session_name'] = tx.pos_session_id.name or ''
            vals['employee_name'] = tx.pos_employee_id.name or ''
            result.append(vals)
        return result


    def _resolve_sync_status(self, internal_status, is_pos_orphan):
        """Decide the final transaction_status after a Sync Status check.

        Guards:
        - POS orphans: keep current status if sync says 'captured' (for recovery)
        - Refunds: never overwrite 'credit_refunded'/'debit_refunded' with
          'captured' because Cybersource reports pending refunds as
          PENDING/TRANSMITTED which our mapping converts to 'captured'.
        """
        REFUND_STATUSES = ('credit_refunded', 'debit_refunded', 'refunded',
                          'credit_refund_pending', 'debit_refund_pending')

        # Guard 1: POS orphan protection (existing logic, centralized)
        if is_pos_orphan and internal_status == 'captured':
            return self.transaction_status

        # Guard 2: Don't overwrite refund status with 'captured'
        if self.transaction_status in REFUND_STATUSES and internal_status == 'captured':
            return self.transaction_status

        return internal_status


    @api.model
    def get_stuck_transactions_for_config(self, config_id):
        """
        Returns all pending Woodforest transactions for a given POS configuration.
        This handles transactions from previous (crashed) sessions too.
        """
        domain = [
            ('provider_id.code', '=', 'woodforest'),
            # Snapshot required for auth recovery, but refunds don't need one
            '|',
            ('pos_order_snapshot_json', '!=', False),
            '|',
            ('reference', '=like', '%r'),   # credit refund suffix
            ('reference', '=like', '%rd'),  # debit refund suffix
            ('state', '=', 'pending'),
            ('transaction_status', '=', 'in_progress'),
            '|',
            ('pos_order_id', '=', False),
            '&',
            ('pos_order_id.state', 'not in', ['paid', 'done', 'invoiced']),
            ('pos_order_id.is_superseded', '=', False),
            '|',
            ('pos_config_id', '=', config_id),
            ('pos_session_id.config_id', '=', config_id)
        ]
        txs = self.search(domain)
        _logger.info("Found %d stuck transactions for config %s using criteria: state=pending, status=in_progress, order_not_finalized", len(txs), config_id)
        return txs.read(['id', 'amount', 'reference', 'provider_reference', 'pos_order_snapshot_json', 'pos_receipt_data', 'transaction_status', 'pos_order_uid'])


    def _map_tss_to_receipt(self, tss_data):
        """Python mirror of mapTSSDataToReceipt from utils.js"""
        import json
        info = tss_data or {}
        card = info.get('paymentInformation', {}).get('card', {})
        proc = info.get('processorInformation', {})
        pos = info.get('pointOfSaleInformation', {})
        order = info.get('orderInformation', {}).get('amountDetails', {})
        
        card_method = info.get('paymentInformation', {}).get('paymentType', {}).get('method', "")
        
        vendor_map = {
            "001": "VISA", "002": "MASTERCARD", "003": "AMERICAN EXPRESS",
            "004": "DISCOVER", "005": "DINERS CLUB", "006": "CARTE BLANCHE",
            "007": "JCB", "033": "VISA ELECTRON",
            "VI": "VISA", "MC": "MASTERCARD", "AX": "AMERICAN EXPRESS",
            "DI": "DISCOVER", "DC": "DINERS CLUB", "CB": "CARTE BLANCHE", "JC": "JCB"
        }
        card_vendor = vendor_map.get(card.get('type'), vendor_map.get(card_method, "N/A"))
        
        receipt_data = {
            '_amount': float(order.get('totalAmount') or 0),
            'cardType': self._normalize_card_type_py(info.get('paymentInformation', {}).get('paymentType', {}).get('type', "N/A")),
            'approvalCode': proc.get('approvalCode', "N/A"),
            'cardVendor': card_vendor,
            'cardNumber': f"****{card.get('suffix')}" if card.get('suffix') else "N/A",
            'transactionId': info.get('id', "N/A"),
            'terminalId': pos.get('terminalId', "N/A"),
            'entryMode': pos.get('entryMode', "N/A").upper(),
            'date': info.get('submitTimeUTC', "N/A").replace("T", " ").replace("Z", ""),
            'status': info.get('applicationInformation', {}).get('status', 'SUCCESS'),
            'responseCode': proc.get('responseCode', "00"),
        }
        
        # Merge EMV tags if any (basic string format as in JS)
        tags = pos.get('emv', {}).get('tags')
        if tags:
            # We don't implement full TLV parsing here yet, but we store the raw tags
            # so the POS can parse them if it strictly needs them.
            receipt_data['emv_tags'] = tags
            
        return json.dumps(receipt_data)

    def _normalize_card_type_py(self, raw_type):
        """Helper to normalize card type to DEBIT or CREDIT (no extra words)"""
        if not raw_type or raw_type == "N/A":
            return "N/A"
        raw_upper = raw_type.upper()
        if 'DEBIT' in raw_upper:
            return 'DEBIT'
        if 'CREDIT' in raw_upper:
            return 'CREDIT'
        return raw_upper


    @api.model
    def action_save_snapshot_and_progress(self, tx_id, snapshot_json):
        """Called from frontend IMMEDIATELY before sending auth to terminal to save cart state."""
        tx = self.browse(tx_id)
        if tx.exists():
            tx.write({
                'pos_order_snapshot_json': snapshot_json,
                'transaction_status': 'in_progress',
                'state': 'pending', 
            })
            return True
        return False

    invoice_ids = fields.Many2many(
        'account.move',
        help='Invoices related to this Woodforest transaction.',
        string='Invoices Related',
    )
    duplicate_status = fields.Char(
        compute='_compute_duplicate_status',
        string='',
        store=True
    )
    duplicate_icon = fields.Char(
        string='',
        compute='_compute_duplicate_icon',
        store=False
    )
    has_pos_order = fields.Boolean(
        string="Has POS Order Link",
        compute='_compute_has_pos_order'
    )
    has_invoice = fields.Boolean(
        string="Has Invoice Link",
        compute='_compute_has_invoice'
    )
    is_fully_synced = fields.Boolean(
        string="Is Fully Synced",
        compute='_compute_is_fully_synced'
    )

    def _compute_is_fully_synced(self):
        for tx in self:
            if not tx.provider_reference:
                tx.is_fully_synced = False
                continue
            
            synced = getattr(tx, 'is_post_processed', False) or bool(getattr(tx, 'payment_id', False))
            if not synced and hasattr(self.env, 'pos.payment'):
                synced = bool(self.env['pos.payment'].sudo().search_count([('transaction_id', '=', tx.id)]))
            if not synced and tx.has_invoice:
                synced = True # Assuming invoice means it is synced via pay link
            tx.is_fully_synced = synced

    def _compute_has_pos_order(self):
        for tx in self:
            if tx.pos_order_id:
                tx.has_pos_order = True
                continue
            # Use the native Odoo link: pos.payment.transaction_id stores the provider_reference
            if tx.provider_reference:
                pos_payment = self.env['pos.payment'].search([
                    ('transaction_id', '=', tx.provider_reference)
                ], limit=1)
                tx.has_pos_order = bool(pos_payment)
            else:
                tx.has_pos_order = False

    def _compute_has_invoice(self):
        for tx in self:
            if tx.invoice_ids:
                tx.has_invoice = True
            else:
                # Search by reference in account.move
                move = self.env['account.move'].search([('name', '=', tx.reference)], limit=1)
                tx.has_invoice = bool(move)

    def _compute_duplicate_status(self):
        for record in self:
            record.duplicate_status = "   Duplicate" if record.is_duplicate else ""

    def _compute_duplicate_icon(self):
        for rec in self:
            rec.duplicate_icon = "  " if rec.is_duplicate else ""

    def _payrillium_form_get_tx_from_data(self, data):
        """Find a transaction based on the 'reference' from Woodforest data."""
        reference = data.get('reference')
        return self.search([('reference', '=', reference)], limit=1)

    def _payrillium_form_validate(self, data):
        """Mark the transaction as completed successfully."""
        self.ensure_one()
        self.write({
            'acquirer_reference': data.get('transaction_id'),
            'state': 'done',  # Or 'pending' if you use a delayed capture flow
        })
        return True

    @api.model
    def create_from_pos_woodforest(self, values):
        """Create or Update a Payment Transaction record for a Woodforest payment."""
        _logger.info("  [create_from_pos_woodforest] Values received: %s", values)

        reference = values.get('reference')
        if not reference:
            _logger.error("  No reference provided in create_from_pos_woodforest.")
            return False

        provider = self.env['payment.provider'].search([('code', '=', 'woodforest')], limit=1)
        if not provider:
            _logger.error("  No Woodforest payment provider found.")
            raise ValueError("No Woodforest payment provider configured.")

        # Resolve the correct payment.method_id dynamically.
        payment_method = self.env['payment.method'].search([('code', '=', 'woodforest')], limit=1)
        payment_method_id = payment_method.id if payment_method else values.get('payment_method_id')

        terminal_id = values.get('terminal_id') or False
        # Fallback: resolve by serial if ID is missing (POS proxy serialization issue)
        if not terminal_id and values.get('terminal_serial'):
            terminal = self.env['payrillium.terminal'].search(
                [('serial', '=', str(values['terminal_serial']))], limit=1)
            terminal_id = terminal.id if terminal else False
        
        # Link to POS order explicitly if UID is provided
        order_uid = values.get('order_uid') or values.get('pos_order_uid')

        # Upsert Logic: Check if transaction already exists
        transaction = self.search([('reference', '=', reference), ('provider_id', '=', provider.id)], limit=1)
        
        vals = {
            'reference': reference,
            'provider_reference': values.get('acquirer_reference') or (transaction.provider_reference if transaction else None),
            'payment_method_id': payment_method_id,
            'amount': values.get('amount'),
            'currency_id': self.env.company.currency_id.id,
            'partner_id': self.env.user.partner_id.id,
            'provider_id': provider.id,
            'state': values.get('state', transaction.state if transaction else 'draft'),
            'payrillium_terminal_id': terminal_id,
            'card_type': values.get('card_type'),
            'payrillium_card_token': values.get('payrillium_card_token'),
            'transaction_status': values.get('transaction_status', transaction.transaction_status if transaction else 'none'),
            'pos_order_uid': values.get('order_pos_reference') or values.get('order_uid'),
            'pos_session_id': values.get('pos_session_id'),
            'pos_config_id': values.get('pos_config_id'),
            'tip_amount': values.get('tip_amount', transaction.tip_amount if transaction else 0),
            'execution_id': values.get('execution_id', transaction.execution_id if transaction else None),
        }

        # pos_employee_id is write-once — the cashier who started the transaction
        employee_id = values.get('pos_employee_id')
        if employee_id and not (transaction and transaction.pos_employee_id):
            vals['pos_employee_id'] = employee_id

        # terminal_message is write-once — never overwrite the original terminal error
        terminal_msg = values.get('terminal_message')
        if terminal_msg and not (transaction and transaction.terminal_message):
            vals['terminal_message'] = terminal_msg

        # CRITICAL: When the WF transaction is done/authorized, mark it as post-processed so that
        # pos_online_payment's _post_process() cron does NOT call pos_order.add_payment() on it.
        # That cron would create a duplicate "Online Payment" pos.payment entry (with Online Payment
        # method), inflating the order's total paid amount and creating phantom payment lines.
        # We handle the pos.payment linkage ourselves via POS syncAllOrders flow.
        if vals.get('state') in ('done', 'authorized'):
            vals['is_post_processed'] = True

        # Auto-resolve config from session if missing
        if not vals.get('pos_config_id') and vals.get('pos_session_id'):
            session = self.env['pos.session'].browse(vals.get('pos_session_id'))
            if session.exists():
                vals['pos_config_id'] = session.config_id.id

        if order_uid and not transaction.pos_order_id:
            order = self.env['pos.order'].search([('pos_reference', '=', order_uid)], limit=1)
            if order:
                vals['pos_order_id'] = order.id

        if transaction:
            # Prevent state regressions during updates unless explicitly overriding
            if 'state' in vals and transaction.state in ['done', 'authorized'] and vals['state'] not in ['cancel', 'error']:
               del vals['state']
            _logger.info("  Updating existing transaction: ID %s", transaction.id)
            old_state = transaction.state
            transaction.write(vals)
            # Append timestamped log entry for state transition
            new_state = vals.get('state', old_state)
            terminal_code = values.get('terminal_message') or ''
            error_msg = values.get('error_message') or values.get('status_summary') or ''
            if new_state != old_state:
                log_parts = [f"{old_state} → {new_state}"]
                if terminal_code:
                    log_parts.append(terminal_code)
                if error_msg and error_msg != terminal_code:
                    log_parts.append(error_msg)
                transaction._append_status_log(" — ".join(log_parts))
            elif error_msg or terminal_code:
                log_parts = []
                if terminal_code:
                    log_parts.append(terminal_code)
                if error_msg and error_msg != terminal_code:
                    log_parts.append(error_msg)
                if log_parts:
                    transaction._append_status_log(" — ".join(log_parts))
        else:
            _logger.info("  Creating new transaction for reference: %s", reference)
            transaction = self.create(vals)
            # Append initial log entry
            state = vals.get('state', 'draft')
            amount = vals.get('amount', 0)
            terminal_code = values.get('terminal_message') or ''
            error_msg = values.get('error_message') or values.get('status_summary') or ''
            log_parts = [f"Created as {state} (${amount})"]
            if terminal_code:
                log_parts.append(terminal_code)
            if error_msg and error_msg != terminal_code:
                log_parts.append(error_msg)
            transaction._append_status_log(" — ".join(log_parts))

        # Auto-compute status_summary — single source of truth for friendly messages
        transaction._compute_status_summary(
            terminal_message=values.get('terminal_message'),
            frontend_summary=values.get('status_summary'),
        )

        pos_ref = values.get('order_pos_reference') or values.get('order_uid')
        pos_order = self.env['pos.order'].search(
            [('pos_reference', '=', pos_ref)], limit=1)
        
        if not pos_order and values.get('order_id'):
            # Try by database ID if provided
            pos_order = self.env['pos.order'].browse(values.get('order_id')).exists()

        if pos_order:
            transaction.pos_order_id = pos_order.id
            # Ensure the session link is also explicit if we have it
            if pos_order.session_id:
                transaction.pos_session_id = pos_order.session_id.id
                
            pos_payment = self.env['pos.payment'].search([
                ('pos_order_id', '=', pos_order.id),
                ('transaction_id', '=', False),
                ('amount', '=', values.get('amount')),
            ], limit=1)
            if pos_payment:
                pos_payment.write(
                    {'transaction_id': values.get('acquirer_reference') or transaction.provider_reference})
                _logger.info(" pos.payment updated with transaction_id")

            _logger.info(
                "  Payment transaction linked to Order %s (ID %s)", pos_order.name, transaction.id)

        return transaction.id

    @api.model
    def get_pos_return_action(self, transaction_id):
        """
        Called from the POS frontend (payment_screen.js) to determine whether to
        process a void or refund for a return. Uses the Decision Engine.
        Returns: { action: 'void' | 'refund', gate: '...', derived_status: '...' }
        """
        from ..services.mirillium.api import get_payment_status
        
        tx = self.env['payment.transaction'].search([
            ('provider_reference', '=', str(transaction_id)),
            ('provider_id.code', '=', 'woodforest'),
        ], limit=1)
        
        if not tx:
            _logger.warning("get_pos_return_action: No transaction found for ID %s, defaulting to refund", transaction_id)
            return {'action': 'refund', 'gate': 'NOT_FOUND', 'derived_status': 'UNKNOWN'}
        
        token = tx.provider_reference or tx.payrillium_card_token
        if not token:
            _logger.warning("get_pos_return_action: No token for tx %s, defaulting to refund", tx.reference)
            return {'action': 'refund', 'gate': 'NO_TOKEN', 'derived_status': 'UNKNOWN'}
        
        try:
            res = get_payment_status(tx, token, env=self.env)
            if res.get('success'):
                status_data = res.get('data', {}) or {}
                decision = self._cybersource_decide_action(status_data)
                action = decision.get('recommended_action', 'none')
                return {
                    'action': action,
                    'gate': decision.get('gate_triggered', 'UNKNOWN'),
                    'derived_status': decision.get('derived_status', 'UNKNOWN'),
                    'explanation_title': decision.get('explanation_title', ''),
                    'explanation_lines': decision.get('explanation_lines', []),
                }
        except Exception as e:
            _logger.warning("get_pos_return_action: Error checking transaction %s: %s", transaction_id, e)
        
        return {'action': 'refund', 'gate': 'ERROR_FALLBACK', 'derived_status': 'UNKNOWN'}

    @api.model
    def _cybersource_decide_action(self, details_json):
        """
        Pure function to determine the transaction status and available actions based on Cybersource details.
        No database writes occur inside this function.
        Every gate logs its evaluation for QA traceability.
        """
        import json
        app_info = details_json.get('applicationInformation', {})
        proc_info = details_json.get('processorInformation', {})
        
        apps = app_info.get('applications', [])
        app_names = [app.get('name') for app in apps if app.get('name')]
        
        global_status = str(app_info.get('status') or "").upper()
        processor_event_status = str(proc_info.get('eventStatus') or "").upper()
        processor_response_code = str(proc_info.get('responseCode') or "")
        
        _logger.info(
            "🔍 Decision Engine START — global_status=%s, eventStatus=%s, responseCode=%s, apps=%s",
            global_status or '(empty)', processor_event_status or '(empty)',
            processor_response_code or '(empty)', app_names
        )
        
        # Default result structure
        result_default = {
            "derived_status": "ERROR",
            "recommended_action": "none",
            "available_actions": [],
            "ui_badge": "danger",
            "explanation_title": "Evaluation Fallback",
            "explanation_lines": ["Transaction could not be fully evaluated."],
            "evidence": {}
        }

        def ok(app):
            if not isinstance(app, dict): return False
            return bool(str(app.get('reasonCode')) == "100" and str(app.get('rCode')) == "1" and str(app.get('rFlag')).upper() == "SOK")
            
        def status_of(app):
            if not isinstance(app, dict): return ""
            return str(app.get('status') or "").upper()

        auth_app = next((app for app in apps if app.get('name') in ["ics_auth", "ics_ap_auth", "ics_incremental_auth", "ics_service_fee_auth"]), None)
        bill_app = next((app for app in apps if app.get('name') in ["ics_bill", "ics_ap_sale", "ics_service_fee_bill"]), None)
        pin_debit_app = next((app for app in apps if app.get('name') in ["ics_pin_debit_purchase", "ics_ecp_debit", "ics_pin_debit_sale"]), None)
        refund_app = next((app for app in apps if app.get('name') in ["ics_credit", "ics_ap_refund"]), None)
        void_app = next((app for app in apps if app.get('name') in ["ics_auth_reversal", "ics_void", "ics_auto_auth_reversal", "ics_auto_full_auth_reversal", "ics_ap_auth_reversal", "ics_ap_cancel", "ics_pin_debit_reversal"]), None)

        def _app_evidence(app):
            if not app:
                return {"present": False}
            return {"present": True, "status": status_of(app), "reasonCode": app.get('reasonCode'), "rCode": app.get('rCode'), "rFlag": app.get('rFlag'), "rMessage": app.get('rMessage')}

        evidence = {
            "global_status": global_status,
            "processor_event_status": processor_event_status,
            "processor_response_code": processor_response_code,
            "detected_app_names": app_names,
            "key_apps": {
                "auth": _app_evidence(auth_app),
                "bill": _app_evidence(bill_app),
                "pin_debit_purchase": _app_evidence(pin_debit_app),
                "refund": _app_evidence(refund_app),
                "void": _app_evidence(void_app),
            }
        }

        def _log_and_return(gate, result):
            """Log the decision and return it."""
            # Merge with defaults to ensure all keys exist
            final_res = result_default.copy()
            final_res.update(result)
            
            _logger.info(
                "✅ Decision Engine RESULT — gate=%s, derived_status=%s, recommended_action=%s, available_actions=%s, title=%s",
                gate, final_res.get('derived_status'), final_res.get('recommended_action'),
                final_res.get('available_actions'), final_res.get('explanation_title')
            )
            final_res['gate_triggered'] = gate
            return final_res

        # Gate #1 - Final global states (only trust if NO successful auth/bill/pin_debit apps exist)
        # Cybersource's global status can be misleading (e.g. REFUNDED on a partially refunded sale).
        # We only short-circuit here if the global status is a terminal state AND there are no
        # successful payment apps that would indicate the original sale went through.
        _logger.info("  Gate #1 checking: global_status=%s in FINAL_STATES? eventStatus=%s == CANCELLED?", global_status, processor_event_status)
        has_successful_payment_app = ok(auth_app) or ok(bill_app) or ok(pin_debit_app)
        if (global_status in ["CANCELLED", "REVERSED"] or processor_event_status == "CANCELLED") and not has_successful_payment_app:
            return _log_and_return("GATE_1_FINAL_STATE", {
                "derived_status": global_status if global_status else "CANCELLED",
                "recommended_action": "none",
                "available_actions": [],
                "ui_badge": "danger",
                "explanation_title": "No action available",
                "explanation_lines": [
                    f"Cybersource reports a final state: applicationInformation.status={global_status} or eventStatus={processor_event_status}.",
                    "No successful payment applications were found. The transaction was cancelled or reversed before completion."
                ],
                "evidence": evidence
            })

        # Gate #2 - Detect "refund/void already occurred" by app names
        # CRITICAL FIX: We now validate that the refund/void app actually SUCCEEDED (ok()) before
        # declaring the transaction as REFUNDED/VOIDED. A failed refund attempt should NOT be
        # reported as "refunded" — the money was never returned.
        _logger.info("  Gate #2 checking: refund_app=%s ok=%s, void_app=%s ok=%s",
                     refund_app.get('name') if refund_app else None, ok(refund_app) if refund_app else 'N/A',
                     void_app.get('name') if void_app else None, ok(void_app) if void_app else 'N/A')
        if refund_app:
            if ok(refund_app):
                return _log_and_return("GATE_2_REFUND_CONFIRMED", {
                    "derived_status": "REFUNDED",
                    "recommended_action": "none",
                    "available_actions": [],
                    "ui_badge": "info",
                    "explanation_title": "Refund Confirmed",
                    "explanation_lines": [
                        f"A successful refund was found: {refund_app.get('name')} with reasonCode={refund_app.get('reasonCode')}, rFlag={refund_app.get('rFlag')}.",
                        "The funds have been returned. No further action is allowed."
                    ],
                    "evidence": evidence
                })
            else:
                _logger.warning("  Gate #2: Refund app %s exists but FAILED (reasonCode=%s, rFlag=%s). Ignoring and continuing evaluation.",
                               refund_app.get('name'), refund_app.get('reasonCode'), refund_app.get('rFlag'))
                # Don't return — let the engine continue to evaluate the original payment status

        if void_app:
            if ok(void_app):
                return _log_and_return("GATE_2_VOID_CONFIRMED", {
                    "derived_status": "VOIDED",
                    "recommended_action": "none",
                    "available_actions": [],
                    "ui_badge": "info",
                    "explanation_title": "Void Confirmed",
                    "explanation_lines": [
                        f"A successful void/reversal was found: {void_app.get('name')} with reasonCode={void_app.get('reasonCode')}, rFlag={void_app.get('rFlag')}.",
                        "The authorization hold has been released. No further action is allowed."
                    ],
                    "evidence": evidence
                })
            else:
                _logger.warning("  Gate #2: Void app %s exists but FAILED (reasonCode=%s, rFlag=%s). Ignoring and continuing evaluation.",
                               void_app.get('name'), void_app.get('reasonCode'), void_app.get('rFlag'))

        # Gate #3 - PIN Debit Purchase flow
        _logger.info("  Gate #3 checking: pin_debit_app present=%s, ok=%s, processor_response_code=%s",
                      bool(pin_debit_app), ok(pin_debit_app) if pin_debit_app else 'N/A', processor_response_code)
        if pin_debit_app:
            if ok(pin_debit_app) and processor_response_code == "00":
                return _log_and_return("GATE_3_PIN_DEBIT_OK", {
                    "derived_status": "DONE",
                    "recommended_action": "refund",
                    "available_actions": ["refund"],
                    "ui_badge": "success",
                    "explanation_title": "Refund available",
                    "explanation_lines": [
                        "PIN debit purchase was processed successfully (ics_pin_debit_purchase).",
                        f"Evidence: reasonCode={pin_debit_app.get('reasonCode')}, rFlag={pin_debit_app.get('rFlag')}, rCode={pin_debit_app.get('rCode')}, processor responseCode={processor_response_code}.",
                        "Because it is a completed purchase, the only supported return action is Refund."
                    ],
                    "evidence": evidence
                })
            else:
                return _log_and_return("GATE_3_PIN_DEBIT_FAIL", {
                    "derived_status": "ERROR",
                    "recommended_action": "none",
                    "available_actions": [],
                    "ui_badge": "danger",
                    "explanation_title": "PIN Debit Failed",
                "explanation_lines": [
                    "PIN debit purchase did not complete successfully.",
                    f"ReasonCode: {pin_debit_app.get('reasonCode') or app_info.get('reasonCode')}, rMessage: {pin_debit_app.get('rMessage') or app_info.get('rFlag')}"
                ],
                "evidence": evidence
            })

        # Gate #3.5 - Explicit check for Invalid Data (102) or other failures
        global_reason = str(app_info.get('reasonCode') or "")
        if global_reason and global_reason != "100":
             return _log_and_return("GATE_3_5_GLOBAL_FAILURE", {
                "derived_status": "ERROR",
                "recommended_action": "none",
                "available_actions": [],
                "ui_badge": "danger",
                "explanation_title": "Cybersource Validation Error",
                "explanation_lines": [
                    f"Cybersource rejected the transaction with Reason Code {global_reason} ({app_info.get('rFlag', 'UNKNOWN')}).",
                    "This usually means the data sent to the bank was invalid or incomplete."
                ],
                "evidence": evidence
            })

        # Gate #4 - Credit flow auth/bill logic
        bill_present = bool(bill_app)
        auth_present = bool(auth_app)
        bill_ok = ok(bill_app)
        auth_ok = ok(auth_app)
        bill_status = status_of(bill_app)
        
        _logger.info(
            "  Gate #4 checking: auth_present=%s auth_ok=%s, bill_present=%s bill_ok=%s bill_status=%s",
            auth_present, auth_ok, bill_present, bill_ok, bill_status or '(empty)'
        )
        
        if bill_present and bill_ok:
            # bill_ok = True means reasonCode=100 + rCode=1 + rFlag=SOK — processor confirmed the capture.
            # PENDING/TRANSMITTED just means the nightly settlement batch hasn't closed yet.
            # Cybersource allows refunds against pending settlements, so we allow the return.
            return _log_and_return("GATE_4_BILL_COMPLETED", {
                "derived_status": "DONE",
                "recommended_action": "refund",
                "available_actions": ["refund"],
                "ui_badge": "success",
                "explanation_title": "Refund required",
                "explanation_lines": [
                    "A confirmed settlement step was found (ics_bill).",
                    f"Evidence: bill reasonCode={bill_app.get('reasonCode')}, rFlag={bill_app.get('rFlag')}, bill_status={bill_status or 'N/A'}, global_status={global_status or 'N/A'}.",
                    "Merchant has funds (or funds are in transit); refund is the only valid action."
                ],
                "evidence": evidence
            })
        elif bill_present and not bill_ok:
            # Bill exists but did NOT pass validation — could be pending confirmation
            return _log_and_return("GATE_4_BILL_UNCONFIRMED", {
                "derived_status": bill_status if bill_status else "PENDING",
                "recommended_action": "none",
                "available_actions": [],
                "ui_badge": "info",
                "explanation_title": "Capture Not Confirmed",
                "explanation_lines": [
                    "A settlement step exists but it has NOT been confirmed by the processor.",
                    f"Bill reasonCode={bill_app.get('reasonCode')}, rFlag={bill_app.get('rFlag')}, status={bill_status or 'N/A'}.",
                    "Wait for the capture to finalize and try again."
                ],
                "evidence": evidence
            })
        elif auth_present and auth_ok and not bill_ok:
            # AUTH_HOLD: The bank authorized the payment but capture did not complete.
            # In a sale-with-capture flow this is rare but possible (e.g. capture timeout).
            # The customer's funds are on HOLD — the money is NOT taken yet.
            # The only safe action is VOID (release the hold).
            return _log_and_return("GATE_4_AUTH_ONLY", {
                "derived_status": "AUTH_HOLD",
                "recommended_action": "void",
                "available_actions": ["void"],
                "ui_badge": "warning",
                "explanation_title": "Void required",
                "explanation_lines": [
                    "Authorization exists but no completed capture was found.",
                    f"Evidence: auth reasonCode={auth_app.get('reasonCode')}, rFlag={auth_app.get('rFlag')}; bill missing or not successful.",
                    "Funds are on hold only — the payment was NOT captured. Void releases the hold."
                ],
                "evidence": evidence
            })
        else:
            return _log_and_return("GATE_4_FALLBACK_REVIEW", {
                "derived_status": "REVIEW",
                "recommended_action": "none",
                "available_actions": [],
                "ui_badge": "danger",
                "explanation_title": "Manual Review Required",
                "explanation_lines": [
                    "Unable to determine a safe action from Cybersource applications; manual review required.",
                    f"Detected apps: {', '.join(app_names)}"
                ],
                "evidence": evidence
            })


    def action_evaluate_woodforest_action(self):
        self.ensure_one()
        from ..services.mirillium.api import get_payment_status

        token = self.provider_reference or self.payrillium_card_token
        decision = None

        if token:
            res = get_payment_status(self, token, env=self.env)
            if res.get('success'):
                status_data = res.get('data', {}) or {}
                decision = self._cybersource_decide_action(status_data)
        
        if not decision:
            import json
            actions = []
            if self.payrillium_available_actions:
                try:
                    actions = json.loads(self.payrillium_available_actions)
                except Exception:
                    pass
            decision = {
                "recommended_action": "none",
                "explanation_title": "No details available",
                "explanation_lines": ["Could not evaluate transaction from Cybersource."]
            }
            if 'void' in actions:
                decision['recommended_action'] = 'void'
                decision['explanation_title'] = "Authorization Reversal (Void)"
                decision['explanation_lines'] = ["This transaction has NOT been settled. The customer's funds are only on hold."]
            elif 'refund' in actions:
                decision['recommended_action'] = 'refund'
                decision['explanation_title'] = "Refund"
                decision['explanation_lines'] = ["This transaction HAS been settled and the merchant has the funds."]

        import json as json_lib
        action_type = decision.get('recommended_action', 'none')
        title = decision.get('explanation_title', 'Action Needed')
        gate = decision.get('gate_triggered', 'N/A')
        lines = "<br/>".join(decision.get('explanation_lines', []))
        evidence_data = decision.get('evidence', {})
        evidence_json = json_lib.dumps(evidence_data, indent=2, default=str) if evidence_data else "N/A"
        
        explanation_text = (
            f"<strong>{title}</strong><br/><br/>"
            f"{lines}<br/><br/>"
            f"<small style='color:#6c757d;'>Gate: <code>{gate}</code></small><br/>"
            f"<pre style='background:#f1f3f5;padding:8px;border-radius:4px;font-size:0.8em;overflow-x:auto;margin-top:4px;'>{evidence_json}</pre>"
        )

        wizard = self.env['payrillium.payment.action.wizard'].create({
            'transaction_id': self.id,
            'action_type': action_type,
            'explanation': explanation_text,
        })

        return {
            'name': _('Payment Status'),
            'type': 'ir.actions.act_window',
            'res_model': 'payrillium.payment.action.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_woodforest_check_status(self):
        """
        Diagnostic action to check the status of a Woodforest transaction in Mirillium.
        Can be called from any transaction record.
        """
        from ..services.mirillium.api import get_payment_status, search_cybersource_transactions
        self.ensure_one()
        silent = self._context.get('silent')
        
        try:
            token = self.provider_reference or self.payrillium_card_token
            
            # Step 1: If we don't have a Cybersource ID but we have an Odoo reference,
            # perform a Transaction Search Service (TSS) call to retrieve the ID.
            if not token and self.reference:
                _logger.info("Sync Status: Missing Cybersource ID. Searching by Odoo Ref: %s", self.reference)
                search_res = search_cybersource_transactions({"odoo_ref": self.reference, "limit": 1}, env=self.env)
                if search_res.get('success') and search_res.get('data'):
                    summaries = search_res.get('data')
                    for summary in summaries:
                        cs_id = summary.get('id')
                        if cs_id:
                            token = cs_id
                            self.provider_reference = cs_id
                            _logger.info("TSS Search successful. Found Cybersource ID %s for Odoo Ref %s", cs_id, self.reference)
                            break
                
                if not token:
                    msg = _("Payment Processing Error.Please try again!")
                    _logger.warning(msg)
                    
                    # Only overwrite state if it's not already in a definitive terminal state.
                    # "Not found at bank" CONFIRMS cancellations/declines — don't lose that context.
                    terminal_states = ('cancelled', 'declined', 'voided', 'reversed', 'refunded',
                                       'credit_refunded', 'debit_refunded', 'auth_failed')
                    if self.transaction_status not in terminal_states:
                        self.write({
                            'state': 'error',
                            'transaction_status': 'error',
                        })
                    self._append_status_log(msg)

                    if silent:
                        return {
                            'success': True,
                            'engine_internal_status': 'error', # 'error' is a definitive end-state
                            'should_remove_from_recovery': True,
                            'status_data': {},
                            'message': msg
                        }
                    
                    wizard = self.env['payrillium.payment.action.wizard'].create({
                        'transaction_id': self.id,
                        'action_type': 'none',
                        'explanation': msg,
                    })
                    return {
                        'name': _('Payment Status'),
                        'type': 'ir.actions.act_window',
                        'res_model': 'payrillium.payment.action.wizard',
                        'res_id': wizard.id,
                        'view_mode': 'form',
                        'target': 'new',
                    }
            if not token:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _("Missing Reference"),
                        'message': _("No payment reference available to check."),
                        'type': 'warning',
                    }
                }
                
            # Step 2: Use the true Cybersource ID to get the detailed status
            res = get_payment_status(self, token, env=self.env)
            
            if res.get('success'):
                status_data = res.get('data', {}) or {}
                
                # --- DECISION ENGINE INTEGRATION ---
                import json
                decision = self._cybersource_decide_action(status_data)
                
                # Map engine's derived_status to Odoo's internal transaction_status
                # AUTH_HOLD = auth went through but capture failed (rare). Funds on hold, NOT taken.
                # PENDING/TRANSMITTED with bill OK = capture submitted, awaiting settlement batch = effectively captured.
                state_mapping = {
                    'AUTH_HOLD': 'auth_hold',  # Separate from captured — money NOT taken
                    'DONE': 'captured',        # Bill confirmed, money taken or in transit
                    'VOIDED': 'voided',
                    'REFUNDED': 'refunded',
                    'CANCELLED': 'voided',
                    'REVERSED': 'voided',
                    'ERROR': 'error',
                    'REVIEW': 'error',
                    'PENDING': 'captured',     # Bill OK + pending settlement = effectively captured
                    'TRANSMITTED': 'captured', # Bill OK + submitted to batch = effectively captured
                }
                derived = decision.get('derived_status', 'ERROR')
                internal_status = state_mapping.get(derived, 'error')

                # External References
                cs_tx_id = (
                    status_data.get('id')
                    or status_data.get('transactionId')
                    or status_data.get('transaction_id')
                )
                
                payment_type_info = status_data.get('paymentInformation', {}).get('paymentType', {})
                card_type_raw = self._normalize_card_type_py(payment_type_info.get('type', 'N/A'))
                
                # --- State Transitions ---
                # CRITICAL (V3.2.6): If this is a POS transaction without an order yet, 
                # we MUST keep it in 'pending' in the backend, otherwise the POS 'Recover Payments'
                # search will not find it.
                is_pos_orphan = bool(not self.pos_order_id and self.pos_order_snapshot_json)
                
                derived_state = 'pending'
                if internal_status in ['voided', 'refunded', 'error']:
                    derived_state = 'cancel' if internal_status in ['voided', 'refunded'] else 'error'
                elif internal_status == 'captured':
                    # Only mark as 'done' if the order is already linked or it's not a POS recovery case
                    derived_state = 'done' if not is_pos_orphan else 'pending'
                elif internal_status == 'authorized':
                    derived_state = 'authorized'
                elif internal_status == 'auth_hold':
                    # Auth went through but capture failed — treat as authorized (money on hold)
                    derived_state = 'authorized'

                # Cache receipt data for POS (V3.2.7)
                receipt_data_json = self._map_tss_to_receipt(status_data)

                if not self._context.get('silent'):
                    # MANUAL UI CALL: Return the wizard with the findings
                    # We commit the internal status and provider reference first so the wizard sees them
                    # but we keep the state pending for the POS.
                    update_vals = {
                        # V3.2.8: Keep status 'in_progress' for the DB if it's an orphan POS tx
                        'transaction_status': self._resolve_sync_status(internal_status, is_pos_orphan),
                        'payrillium_available_actions': json.dumps(decision.get('available_actions', [])),
                        'pos_receipt_data': receipt_data_json,
                        'card_type': card_type_raw if card_type_raw != 'N/A' else self.card_type,
                    }
                    if cs_tx_id and not self.provider_reference:
                        update_vals['provider_reference'] = cs_tx_id
                    
                    # We update everything EXCEPT state potentially if orphan
                    update_vals['state'] = derived_state
                    self.write(update_vals)

                    # Prepare data for the wizard
                    # The wizard DOES show the LIVE status from bank
                    explanation_html = (
                        f"<strong>{decision.get('explanation_title')}</strong><br/><br/>"
                        + "<br/>".join(decision.get('explanation_lines', []))
                        + f"<br/><br/><small style='color:#6c757d;'>Gate: <code>{decision.get('gate_triggered')}</code></small>"
                        + f"<pre style='background:#f1f3f5;padding:8px;border-radius:4px;font-size:0.8em;overflow-x:auto;margin-top:8px;'>{json.dumps(decision.get('evidence', {}), indent=2)}</pre>"
                    )
                    wizard = self.env['payrillium.payment.action.wizard'].create({
                        'transaction_id': self.id,
                        'action_type': decision.get('recommended_action', 'none'),
                        'explanation': explanation_html,
                        'derived_transaction_status': internal_status,
                        'derived_state': derived_state,
                        'available_actions_json': json.dumps(decision.get('available_actions', []))
                    })
                    
                    return {
                        'name': _('Payment Status'),
                        'type': 'ir.actions.act_window',
                        'res_model': 'payrillium.payment.action.wizard',
                        'view_mode': 'form',
                        'res_id': wizard.id,
                        'target': 'new',
                        'context': self._context,
                    }

                # SILENT CALL (POS Recovery or Batch sync): Commit changes immediately
                vals = {
                    'transaction_status': self._resolve_sync_status(internal_status, is_pos_orphan),
                    'payrillium_available_actions': json.dumps(decision.get('available_actions', [])),
                    'pos_receipt_data': receipt_data_json,
                    'card_type': card_type_raw if card_type_raw != 'N/A' else self.card_type,
                }
                if cs_tx_id and not self.provider_reference:
                    vals['provider_reference'] = cs_tx_id
                
                vals['state'] = derived_state
                self.write(vals)

                extra = (_(' — provider_reference saved: %s') % cs_tx_id) if (cs_tx_id and not self.provider_reference) else ''
                explanation_str = " ".join(decision.get('explanation_lines', []))
                msg = _("Sync Status for %(ref)s: Cybersource returned %(status)s (%(action)s). %(expl)s%(extra)s") % {
                    'ref': self.reference, 'status': derived, 'action': decision.get('recommended_action'), 'expl': explanation_str, 'extra': extra,
                }
                _logger.info("Sync [%s]: %s", self.reference, msg)

                # User-friendly summary — use the centralized STATUS_SUMMARY_MAP
                self._compute_status_summary()
                self._append_status_log(msg)
                
                notification_title = _("Payment Status")
                notification_type = decision.get('ui_badge', 'info')
                notification_msg = msg

                if internal_status == 'captured' and not self.pos_order_id and self.pos_order_snapshot_json:
                    notification_title = _("Payment found, order missing")
                    notification_type = 'warning'
                    notification_msg = _("This payment was charged but the order was not created. Open the register to recover it.")

                if silent:
                    return {
                        'success': True,
                        'engine_internal_status': internal_status,
                        'status_data': status_data,
                        'message': notification_msg 
                    }

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': notification_title,
                        'message': notification_msg,
                        'sticky': True if notification_type == 'warning' else False,
                        'type': notification_type,
                        'status_data': status_data,
                        'engine_internal_status': internal_status,
                        'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                    }
                }
            else:
                api_msg = res.get('message', 'No details returned.')
                api_data = res.get('data', {}) or {}
                api_data_msg = api_data.get('message', '') if isinstance(api_data, dict) else ''
                
                # Check if this is a "resource not found" response (not a real error)
                is_not_found = (
                    'does not exist' in api_msg.lower()
                    or 'does not exist' in api_data_msg.lower()
                    or 'not found' in api_msg.lower()
                )
                
                if is_not_found:
                    msg = _("Payment Processing Error.Please try again!")
                    _logger.warning("Payment Processing Error [%s]: %s", self.reference, api_msg)
                    
                    # Only overwrite state if it's not already in a definitive terminal state.
                    terminal_states = ('cancelled', 'declined', 'voided', 'reversed', 'refunded',
                                       'credit_refunded', 'debit_refunded', 'auth_failed')
                    if self.transaction_status not in terminal_states:
                        self.write({
                            'state': 'error',
                            'transaction_status': 'error',
                        })
                    self._append_status_log(msg)
                    
                    if silent:
                        return {
                            'success': True,
                            'engine_internal_status': 'error',
                            'should_remove_from_recovery': True,
                            'status_data': {},
                            'message': msg
                        }
                    
                    wizard = self.env['payrillium.payment.action.wizard'].create({
                        'transaction_id': self.id,
                        'action_type': 'none',
                        'explanation': msg,
                    })
                    return {
                        'name': _('Payment Status'),
                        'type': 'ir.actions.act_window',
                        'res_model': 'payrillium.payment.action.wizard',
                        'res_id': wizard.id,
                        'view_mode': 'form',
                        'target': 'new',
                    }
                
                _logger.warning("Sync error [%s]: %s", self.reference, api_msg)
                msg = _("Could not connect to the bank. Try again later.")
                self._append_status_log(msg)
                if silent:
                    return {
                        'success': False,
                        'engine_internal_status': 'error',
                        'message': msg
                    }
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _("Connection Error"),
                        'message': msg,
                        'sticky': True,
                        'type': 'warning',
                    }
                }
        except Exception as e:
            _logger.exception("CRITICAL ERROR in action_woodforest_check_status: %s", e)
            if silent:
                return {
                    'success': False,
                    'engine_internal_status': 'error',
                    'message': str(e)
                }
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("System Error"),
                    'message': _("An unexpected error occurred while checking transaction status. Contact support."),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def action_woodforest_void(self):
        """Manually void a Woodforest transaction."""
        self.ensure_one()
        token = self.provider_reference or self.payrillium_card_token
        if not token:
            raise UserError(_("No Woodforest token found for transaction %s.") % self.reference)

        res = void_payment_by_token(self, token)
        if res.get('success'):
            self.write({
                'state': 'cancel',
                'transaction_status': 'voided'
            })
            _logger.info("Void successful for transaction %s", self.reference)
            self._append_status_log(_("Transaction voided successfully in Woodforest."))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Void Successful"),
                    'message': _("Transaction %s has been voided.") % self.reference,
                    'type': 'success',
                }
            }
        else:
            raise UserError(_("Void failed: %s") % res.get('message'))

    def action_woodforest_refund(self):
        """Manually trigger a refund for this transaction."""
        self.ensure_one()
        # If it's already done and it's a Woodforest transaction, we use the standard Odoo refund flow
        # which eventually calls _send_refund_request.
        if self.state != 'done':
            raise UserError(_("Only completed transactions can be refunded."))

        # This will trigger the standard refund wizard if called from form
        return self._send_refund_request(amount_to_refund=self.amount)

    def action_open_refund_wizard(self):
        """Open the defensive refund wizard for Orphan Transactions."""
        self.ensure_one()
        return {
            'name': _('Cancel/Revert Purchase'),
            'type': 'ir.actions.act_window',
            'res_model': 'woodforest.refund.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_id': self.id,
                'default_transaction_id': self.id,
            }
        }

    def action_view_pos_order(self):
        """Redirect to the linked PoS Order via pos.payment or explicit pos_order_id."""
        self.ensure_one()
        order = self.pos_order_id
        if not order and self.provider_reference:
            # Find via native pos.payment link
            pos_payment = self.env['pos.payment'].search([
                ('transaction_id', '=', self.provider_reference)
            ], limit=1)
            if pos_payment:
                order = pos_payment.pos_order_id

        if not order:
            return False

        return {
            'name': _('PoS Order'),
            'view_mode': 'form',
            'res_model': 'pos.order',
            'res_id': order.id,
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def action_check_status_from_list(self):
        """
        Called from the Server Action on the Woodforest Transactions list view.
        Works on a single selected record only.
        - If status changed: posts to chatter and navigates to the transaction detail.
        - If connection error / timeout: posts the error to chatter and navigates to detail.
        - If status unchanged: shows a brief notification without navigation.
        """
        self.ensure_one()
        previous_status = (self.transaction_status or '').upper()

        try:
            self.action_woodforest_check_status()
        except Exception as e:
            error_msg = _(
                "⚠️ Cybersource Sync Error\n"
                "Could not reach Cybersource to verify the payment status.\n"
                "Error: %s"
            ) % str(e)
            _logger.warning("Sync Status error in list action [%s]: %s", self.reference, error_msg)
            self._append_status_log(error_msg)
            return {
                'name': _('Transaction Detail'),
                'view_mode': 'form',
                'res_model': 'payment.transaction',
                'res_id': self.id,
                'type': 'ir.actions.act_window',
                'target': 'current',
            }

        new_status = (self.transaction_status or '').upper()

        if new_status != previous_status:
            # Status changed — post to chatter and open detail
            status_change_msg = _("Payment Status Updated: %s -> %s") % (
                previous_status or 'UNKNOWN', new_status or 'UNKNOWN'
            )
            _logger.info("Status change [%s]: %s", self.reference, status_change_msg)
            self._append_status_log(status_change_msg)
            return {
                'name': _('Transaction Detail'),
                'view_mode': 'form',
                'res_model': 'payment.transaction',
                'res_id': self.id,
                'type': 'ir.actions.act_window',
                'target': 'current',
            }

        # Status unchanged — just show a notification
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Woodforest Status"),
                'message': _("Status is unchanged: %s") % (new_status or 'UNKNOWN'),
                'type': 'info',
                'sticky': False,
            }
        }


    def action_view_invoices(self):
        """Redirect to linked invoices (explicit or by reference)."""
        self.ensure_one()
        invoices = self.invoice_ids
        if not invoices:
            invoices = self.env['account.move'].search([('name', '=', self.reference)], limit=1)

        if not invoices:
            return False

        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_out_invoice_type")
        action['domain'] = [('id', 'in', invoices.ids)]
        return action

    def _send_payment_request_to_terminal(self):
        self.ensure_one()
        self.write({
            'state': 'done',
            'provider_reference': self.reference or 'Manual',
        })

    def _log_message_on_linked_documents(self, message):
        """Log a message on the invoices linked to the transaction."""
        super()._log_message_on_linked_documents(message)

        # For Woodforest transactions, also log on the invoice
        for tx in self:
            if tx.provider_code == 'woodforest' and tx.invoice_ids:
                for invoice in tx.invoice_ids:
                    invoice.state_message = message

    def _update_source_transaction_state(self):
        """Update the source transaction state and create payment if needed."""
        for tx in self:
            if tx.provider_code == 'woodforest' and tx.state == 'done':
                # Create payment if it doesn't exist
                if not tx.payment_id and tx.invoice_ids:
                    tx._create_payment_from_transaction()

    def _create_payment_from_transaction(self):
        """Create an account.payment from this transaction."""
        self.ensure_one()

        _logger.info(
            f"🔧 _create_payment_from_transaction called for transaction {self.id}")
        _logger.info(f"  - payment_id: {self.payment_id}")
        _logger.info(f"  - invoice_ids: {self.invoice_ids}")
        _logger.info(f"  - partner_id: {self.partner_id}")
        _logger.info(f"  - amount: {self.amount}")
        _logger.info(f"  - currency_id: {self.currency_id}")

        if self.payment_id:
            _logger.info(f"  - Payment already exists: {self.payment_id}")
            return self.payment_id

        if not self.invoice_ids:
            _logger.warning("  - No invoice_ids found")
            return False

        invoice = self.invoice_ids[0]
        _logger.info(f"  - Using invoice: {invoice.name} (ID: {invoice.id})")

        # Get payment method line
        payment_method_line = self.env['account.payment.method.line'].search([
            ('payment_type', '=', 'inbound'),
            ('journal_id.type', '=', 'bank')
        ], limit=1)

        _logger.info(f"  - payment_method_line: {payment_method_line}")

        if not payment_method_line:
            _logger.warning(
                "No payment method line found for Woodforest payment")
            return False

        # Create payment
        payment_vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.partner_id.id,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'date': fields.Date.context_today(self),
            'journal_id': payment_method_line.journal_id.id,
            'payment_method_line_id': payment_method_line.id,
            'payment_transaction_id': self.id,
            'memo': f"Payment for {invoice.name} via Woodforest",
        }

        _logger.info(f"  - Creating payment with vals: {payment_vals}")

        try:
            payment = self.env['account.payment'].sudo().create(payment_vals)
            _logger.info(f"  - Payment created: {payment.id}")

            payment.action_post()
            _logger.info(f"  - Payment posted: {payment.id}")

            # Reconcile with invoice
            (payment.move_id.line_ids + invoice.line_ids).filtered(
                lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
            ).reconcile()
            _logger.info(f"  - Payment reconciled with invoice")

            # Update invoice UI to show "Paid" status immediately
            invoice.invalidate_recordset(
                ['invoice_outstanding_credits_debits_widget'])
            _logger.info(f"  - Invoice UI updated to show Paid status")

            # Force UI refresh by invalidating related fields
            invoice.invalidate_recordset(
                ['payment_state', 'amount_residual', 'invoice_payments_widget'])
            _logger.info(
                f"  - Invoice payment fields invalidated for UI refresh")

            # Log payment posted message
            _logger.info("The payment related to the transaction with reference %s has been posted: %s", self.reference, payment.name)
            _logger.info(f"  - Message posted to payment")

            _logger.info(
                f"  Payment {payment.id} created and reconciled for transaction {self.id}")
            return payment

        except Exception as e:
            _logger.error(f"  Error creating payment: {e}")
            raise

    def _send_refund_request(self, amount_to_refund=None):
        self.ensure_one()

        if self.provider_code != "woodforest":
            return super()._send_refund_request(amount_to_refund=amount_to_refund)

        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)
        _logger.info("refund_tx: %s", refund_tx)

        token = self.provider_reference
        if not token:
            raise UserError("No provider_reference found to request refund.")

        result = refund_payment_by_token(
            self.provider_id, token, -refund_tx.amount)
        _logger.info("resultrefund_payment_by_token: %s", result)

        if not result.get("success"):
            refund_tx.state = "cancel"
            self._append_status_log(f"Refund failed: {result.get('message')}")
            raise UserError(f"Refund failed: {result.get('message')}")

        refund_data = result["refund_data"]
        refund_tx.provider_reference = refund_data.get("external_ref")
        refund_tx.card_type = refund_data.get("card_type")

        refund_status = refund_data.get("status", "").upper()
        if refund_status in ("AUTHORIZED", "DONE"):
            refund_tx.sudo()._set_done("Refund completed via Woodforest")

            if self.invoice_ids:
                invoice = self.invoice_ids[0].sudo()
                reversal = invoice._reverse_moves(default_values_list=[{
                    'ref': f"Refund of {invoice.name}",
                    'date': fields.Date.context_today(self),
                }], cancel=False)

                _logger.info("reversal: %s", reversal)

                refund_invoice = reversal
                refund_invoice.action_post()

                journal = self.env['account.journal'].search([
                    ('type', '=', 'bank'),
                    ('company_id', '=', self.company_id.id),
                ], limit=1)

                if not journal:
                    journal = self.env['account.journal'].search([
                        ('type', '=', 'bank'),
                        ('company_id', '=', self.company_id.id),
                    ], limit=1)

                if not journal:
                    _logger.warning(
                        "Refund journal not found. Skipping payment registration.")
                    return refund_tx

                refund_payment = self.env['account.payment'].create({
                    'partner_id': self.partner_id.id,
                    'journal_id': journal.id,
                    'payment_type': 'outbound',
                    'amount': abs(refund_tx.amount),
                    'payment_method_line_id': journal.inbound_payment_method_line_ids[:1].id,
                    'partner_type': 'customer',
                    'date': fields.Date.context_today(self),
                    'ref': f"Refund for {invoice.name}",
                })

                refund_payment.action_post()

                lines = (refund_payment.line_ids + invoice.line_ids).filtered(
                    lambda l: l.account_id == invoice.line_ids[0].account_id and l.account_id.reconcile
                )
                lines.reconcile()

        else:
            refund_tx.state = "cancel"
            refund_tx.sudo().write({
                "state": "cancel",
                "state_message": f"Refund failed: {result.get('message') or 'Unknown error'}",
            })

        return refund_tx

        # # def _send_refund_request(self, amount_to_refund=None):
        # self.ensure_one()

        # if self.provider_code != "payrillium":
        #     return super()._send_refund_request(amount_to_refund=amount_to_refund)

        # refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)

        # _logger.info("refund_tx: %s", refund_tx)

        # token = self.provider_reference
        # if not token:
        #     raise UserError("No provider_reference found to request refund.")

        # result = refund_payment_by_token(self.provider_id, token, -refund_tx.amount)

        # _logger.info("resultrefund_payment_by_token: %s", result)

        # if not result.get("success"):
        #     refund_tx.state = "cancel"
        #     self.message_post(body=f"Refund failed: {result.get('message')}")
        #     raise UserError(f"Refund failed: {result.get('message')}")

        # refund_data = result["refund_data"]

        # refund_tx.provider_reference = refund_data.get("external_ref")
        # refund_tx.card_type = refund_data.get("card_type")

        # if refund_data.get("status").upper() == "AUTHORIZED" or refund_data.get("status").upper() == "DONE":
        #     if refund_tx and refund_tx.exists():
        #         refund_tx.sudo()._set_done("Refund completed via Payrillium")
        #         if self.payment_id:
        #             payment = self.payment_id.sudo()
        #             if payment.state not in ("cancelled", "reconciled"):
        #                 payment.cancel()
        # else:
        #     refund_tx.state = "cancel"
        #     refund_tx.sudo().write({
        #         "state": "cancel",
        #         "state_message": f"Refund failed: {result.get('message') or 'Unknown error'}",
        #     })
        # return refund_tx

