# Used to handle Woodforest configuration wizard functionality
from ..config import PAYMENT_METHOD_NAME, PAYMENT_METHOD_COLOR, PAYMENT_METHOD_ICON
from odoo import Command, models, fields, api
from odoo.exceptions import UserError
import logging
from ..services.mirillium import get_terminals_from_token, sync_existing_payment_links

_logger = logging.getLogger(__name__)


class PayrilliumWizard(models.TransientModel):
    _name = 'payrillium.wizard'
    _description = 'Wizard to configure Woodforest'

    activate_only = fields.Boolean(
        default=lambda self: self.env.context.get('activate_only', False),
        string="Activate with code only (no accounting fields)",
    )
    token = fields.Char(string="Token", required=False)
    account_id = fields.Many2one(
        'account.account',
        string="Payment Account",
        required=False,
        domain=[
            ('account_type', 'in', ['asset_cash', 'bank']),
            ('deprecated', '=', False)
        ],
        help="This account will be used as the 'Outstanding Payments/Receipts' account. It must be of type 'Cash' or 'Bank' and be reconciliable."
    )
    receivable_account_id = fields.Many2one(
        'account.account',
        string="Receivable Account",
        required=False,
        domain=[
            ('account_type', '=', 'asset_receivable'),
            ('reconcile', '=', True),
            ('deprecated', '=', False)
        ],
        help="Account used for POS counterpart. Must be type 'Receivable'."
    )

    @api.model
    def check_and_open_wizard(self, *args, **kwargs):
        _logger.info(
            " Executing check_and_open_wizard with args: %s, kwargs: %s", args, kwargs)
        config = self.env['payrillium.config'].search([], limit=1)
        if not config or not config.token:
            _logger.info(" No configuration found, opening wizard")
            action = self.env.ref(
                'pos_woodforest.action_payrillium_wizard').read()[0]
            return action
        _logger.info(" Configuration already exists with token")
        return False

    def _clean_duplicate_providers(self):
        _logger.info("  Searching for duplicate Woodforest providers for cleanup...")
        
        # 1. Search for existing 'woodforest' providers (active or inactive)
        providers = self.env['payment.provider'].search([('code', '=', 'woodforest')], order='id desc')
        if not providers:
            return None

        # 1. Identify the 'best' provider to keep (one with transactions, or the most recent)
        best_provider = None
        for provider in providers:
            tx_count = self.env['payment.transaction'].search_count([
                ('provider_id', '=', provider.id)
            ])
            if tx_count > 0:
                best_provider = provider
                break
        
        if not best_provider:
            best_provider = providers[0]

        _logger.info("  Reusing primary provider: %s (ID: %s)", best_provider.name, best_provider.id)

        # 2. Quietly disable other duplicates (to avoid UI clutter) 
        # but NO UNLINK per user request.
        duplicates_to_disable = providers.filtered(lambda p: p.id != best_provider.id)
        for dup in duplicates_to_disable:
            if dup.state != 'disabled':
                _logger.info("  Disabling redundant provider: %s (ID: %s)", dup.name, dup.id)
                dup.write({'state': 'disabled'})

        return best_provider

    def submit_token(self):
        # --- VALIDATE TERMINAL STATE BEFORE STARTING ---
        for terminal in self.env['payrillium.terminal'].search([]):
            if terminal.pos_config_id:
                active_session = self.env['pos.session'].search([
                    ('config_id', '=', terminal.pos_config_id.id),
                    ('state', 'in', ['opened', 'opening_control']),
                ], limit=1)
                if active_session:
                    raise UserError(
                        f"Cannot update configuration because terminal '{terminal.name}' is assigned to POS config '{terminal.pos_config_id.name}' "
                        f"which has an active session '{active_session.name}'. Please close the session first."
                    )
        try:
            activate_only = self.env.context.get(
                'activate_only', False) or self.activate_only
            if not self.token:
                raise UserError(
                    "Token is required. Please enter your activation token.")
            if not activate_only:
                if not self.account_id:
                    raise UserError(
                        "Payment Account is required. Please select an account.")
                if not self.receivable_account_id:
                    raise UserError(
                        "Receivable Account is required. Please select an account.")
            else:
                # Resolve default accounts for activate-only flow
                config = self.env['payrillium.config'].search([], limit=1)
                if config and config.receivable_account_id:
                    self.receivable_account_id = config.receivable_account_id
                if config and config.outstanding_account_id:
                    self.account_id = config.outstanding_account_id
                if not self.receivable_account_id:
                    recv = self.env['account.account'].search([
                        ('account_type', '=', 'asset_receivable'),
                        ('reconcile', '=', True),
                        ('deprecated', '=', False),
                        ('company_ids', 'in', [self.env.company.id]),
                    ], limit=1)
                    self.receivable_account_id = recv
                if not self.account_id:
                    outstanding = self.env['account.account'].search([
                        ('account_type', 'in', ['asset_cash', 'bank']),
                        ('deprecated', '=', False),
                        ('company_ids', 'in', [self.env.company.id]),
                    ], limit=1)
                    self.account_id = outstanding
                if not self.receivable_account_id or not self.account_id:
                    raise UserError(
                        "Default accounting accounts could not be found. "
                        "Please configure Receivable and Payment accounts in Settings first, "
                        "or use 'Update Configuration' to set them manually."
                    )

            token = self.token.strip()

            if not token or len(token) < 4:
                raise UserError(
                    "Token is too short. Please enter a valid activation token.")

            if not all(c.isalnum() or c in '-_' for c in token):
                raise UserError(
                    "Token contains invalid characters. Please enter a valid activation token.")

            _logger.info("Received token in wizard: %s****",
                         token[:4] if token and len(token) > 4 else "****")

            if token == "INVALID":
                raise UserError("Invalid Token")

            response = get_terminals_from_token(token)
            _logger.info("Response: %s", response)

            if not response.get("success", False):
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Token Validation Failed",
                        "message": response.get("message", "Unknown Error"),
                        "type": "danger",
                        "sticky": False,
                    }
                }

            terminals = response.get("terminals", [])
            merchant_id = response.get("merchant_id")
            secret_key = response.get("secret_key")
            pbl_developer_id = response.get("pbl_developer_id")
            pbl_solution_id = response.get("pbl_solution_id")
            pbl_request_phone = response.get("pbl_request_phone")
            pbl_request_shipping = response.get("pbl_request_shipping")
            config = self.env['payrillium.config'].search([], limit=1)
            is_update = bool(config and config.installed)

            provider = self.env['payment.provider'].search([('code', '=', 'woodforest')], limit=1)
            if not provider:
                _logger.info("   No provider found with code 'woodforest', creating fresh...")
                provider = self.env['payment.provider'].create({
                    'name': 'Woodforest',
                    'code': 'woodforest',
                    'state': 'test',
                    'is_published': True,
                    'company_id': self.env.company.id,
                })
            
            if is_update and not provider:
                raise UserError("No Woodforest provider found for update.")

            deleted_terminals, skipped_terminals = self._synchronize_terminals(
                terminals)

            account = self.env['account.account'].search([
                ('code', '=', '101401'),
                ('company_ids', 'in', [self.env.company.id]),
            ], limit=1)
            if not account:
                account = self.env['account.account'].create({
                    'name': 'Woodforest Bridge Account',
                    'code': '101401',
                    'account_type': 'asset_cash',
                    'reconcile': True,
                    'company_ids': [Command.set([self.env.company.id])],
                })

            # Search for journal using new code 'woodforest' or legacy 'PAYR'
            journal = self.env['account.journal'].with_context(active_test=False).search([
                ('code', 'in', ['woodforest', 'PAYR']),
                ('company_id', '=', self.env.company.id)
            ], limit=1)

            if not journal:
                journal = self.env['account.journal'].create({
                    'name': 'Woodforest',
                    'type': 'bank',
                    'code': 'woodforest',
                    'company_id': self.env.company.id,
                    'default_account_id': account.id,
                })
            else:
                updates = {}
                # Migrate name and code if they were legacy
                if journal.code == 'PAYR':
                    updates['code'] = 'woodforest'
                if "Payrillium" in (journal.name or "") or "(Archived" in (journal.name or ""):
                    updates['name'] = 'Woodforest'
                
                if not journal.active:
                    updates['active'] = True
                if journal.default_account_id != account:
                    updates['default_account_id'] = account.id
                
                if updates:
                    journal.write(updates)

            if not provider:
                provider = self.env['payment.provider'].create({
                    'name': 'Woodforest Terminal',
                    'code': 'woodforest',
                    'state': 'disabled',
                    'company_id': self.env.company.id,
                })

            payment_method = self.env['account.payment.method'].search([
                ('payment_type', '=', 'inbound'), ('name', 'ilike', 'Manual')
            ], limit=1)
            if not payment_method:
                raise UserError("No inbound payment method found.")

            existing_lines = self.env['account.payment.method.line'].search([
                ('journal_id', '=', journal.id),
                ('payment_method_id', '=', payment_method.id)
            ])
            if existing_lines:
                existing_lines.write({
                    'name': 'Woodforest Manual In',
                    'payment_provider_id': provider.id,
                    'payment_account_id': self.account_id.id,
                    'sequence': 10,
                })
            else:
                self.env['account.payment.method.line'].create({
                    'name': 'Woodforest Manual In',
                    'journal_id': journal.id,
                    'payment_method_id': payment_method.id,
                    'payment_provider_id': provider.id,
                    'payment_account_id': self.account_id.id,
                    'sequence': 10,
                })

            if provider.state != 'enabled':
                provider.write({'state': 'enabled'})

            if is_update and provider:
                tokens = self.env['payment.token'].with_context(active_test=False).search([
                    ('provider_id', '=', provider.id),
                    ('active', '=', True)
                ])
                _logger.info("Found %s active tokens for provider %s",
                             len(tokens), provider.id)

            payment_method_code = "woodforest"
            existing_method = self.env['payment.method'].with_context(active_test=False).search([
                ('code', '=', payment_method_code)
            ], limit=1)

            if not existing_method:
                self.env['payment.method'].create({
                    'name': PAYMENT_METHOD_NAME,
                    'code': payment_method_code,
                    'sequence': 1000,
                    'active': True,
                })
            else:
                updates = {}
                if not existing_method.active:
                    updates["active"] = True
                # if existing_method.primary_payment_method_id.id != provider.id:
                #     updates["primary_payment_method_id"] = provider.id
                if updates:
                    existing_method.write(updates)

            existing = self.env['pos.payment.method'].with_context(active_test=False).search([
                ('use_payment_terminal', 'in', ['woodforest', 'payrillium'])
            ], limit=1)
            
            _logger.info("  Wizard Search Result: %s", existing)
            if existing:
                _logger.info("  Found existing method: ID=%s, Current use_payment_terminal=%s", 
                            existing.id, existing.use_payment_terminal)

            if not existing:
                _logger.info("  No existing method found, creating new one...")
                self.env['pos.payment.method'].create({
                    'name': PAYMENT_METHOD_NAME,
                    'journal_id': journal.id,
                    'receivable_account_id': self.receivable_account_id.id,
                    'outstanding_account_id': self.account_id.id,
                    'use_payment_terminal': 'woodforest',
                    'payrillium_color': PAYMENT_METHOD_COLOR,
                    'payrillium_icon': PAYMENT_METHOD_ICON,
                    'payment_provider_id': provider.id,
                })
            else:
                updates = {}
                if not existing.active:
                    updates['active'] = True
                if existing.receivable_account_id.id != self.receivable_account_id.id:
                    updates['receivable_account_id'] = self.receivable_account_id.id
                if not existing.outstanding_account_id or existing.outstanding_account_id.id != self.account_id.id:
                    updates['outstanding_account_id'] = self.account_id.id
                if not existing.payment_provider_id or existing.payment_provider_id.id != provider.id:
                    updates['payment_provider_id'] = provider.id
                
                if existing.use_payment_terminal != 'woodforest':
                    _logger.info("  Queuing use_payment_terminal update to 'woodforest'")
                    updates['use_payment_terminal'] = 'woodforest'
                
                if updates:
                    _logger.info("  Applying updates to record %s: %s", existing.id, updates)
                    existing.write(updates)
                else:
                    _logger.info("  No updates needed for record %s", existing.id)

            config = self.env['payrillium.config'].search([], limit=1)
            values = {
                'token': token,
                'installed': True,
                'activation_state': 'active',
                'merchant_id': merchant_id,
                'secret_key': secret_key,
                'pbl_developer_id': pbl_developer_id,
                'pbl_solution_id': pbl_solution_id,
                'pbl_request_phone': pbl_request_phone,
                'pbl_request_shipping': pbl_request_shipping,
                'receivable_account_id': self.receivable_account_id.id,
                'outstanding_account_id': self.account_id.id
            }
            if config:
                config.write(values)
            else:
                self.env['payrillium.config'].create(values)
            # Build terminal names for user notification
            terminal_names = ", ".join([t["name"] for t in terminals])

            # Build message with synchronized and deleted terminals
            message_parts = [f"Terminals synchronized: {terminal_names}"]

            if deleted_terminals:
                deleted_names = ", ".join(deleted_terminals)
                message_parts.append(f"Terminals removed: {deleted_names}")

            if skipped_terminals:
                skipped_names = ", ".join(skipped_terminals)
                message_parts.append(
                    f"Terminals skipped (active session): {skipped_names}")

            message = " | ".join(message_parts)

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Woodforest Synchronization Complete",
                    "message": message,
                    "sticky": False,
                    "type": "success",
                    "next": {"type": "ir.actions.act_window_close"},
                },
            }
        except Exception as e:
            _logger.exception("Error submitting token")
            raise UserError(f"An error occurred while submitting token: {str(e)}")

    def _synchronize_terminals(self, terminals):
        TerminalModel = self.env['payrillium.terminal'].sudo()
        existing_terminals = {t.serial: t for t in TerminalModel.search([])}
        incoming_serials = {t["serial"] for t in terminals}
        deleted_terminals = []
        skipped_terminals = []

        for serial, terminal_obj in existing_terminals.items():
            if serial not in incoming_serials:
                if terminal_obj.pos_config_id:
                    active_session = self.env['pos.session'].search([
                        ('config_id', '=', terminal_obj.pos_config_id.id),
                        ('state', 'in', ['opened', 'opening_control']),
                    ], limit=1)
                    if active_session:
                        _logger.warning(
                            f"Terminal {terminal_obj.name} (serial: {serial}) no longer exists in shopnet, "
                            f"but cannot be deleted because POS config '{terminal_obj.pos_config_id.name}' "
                            f"has an active session '{active_session.name}'. Skipping deletion.")
                        skipped_terminals.append(terminal_obj.name)
                        continue

                    _logger.info(
                        f"Terminal {terminal_obj.name} (serial: {serial}) no longer exists in shopnet. "
                        f"Removing from POS Config '{terminal_obj.pos_config_id.name}' before deletion.")
                    pos_config = terminal_obj.pos_config_id
                    pos_config.payrillium_terminal_id = False
                    terminal_obj.write({
                        'pos_config_id': False,
                        'pos_config_name': False,
                        'last_session_id': False,
                    })

                _logger.info(
                    f"Deleting terminal {terminal_obj.name} (serial: {serial}) because it no longer exists in shopnet.")
                deleted_terminals.append(terminal_obj.name)
                terminal_obj.unlink()

        for terminal in terminals:
            serial = terminal["serial"]
            name = terminal["name"]
            last4 = serial[-4:] if serial and len(serial) >= 4 else ""
            display_name = f"{name} - {last4}" if last4 else name

            if serial in existing_terminals:
                existing = existing_terminals[serial]
                updates = {}
                if existing.name != display_name:
                    updates['name'] = display_name
                if updates:
                    existing.write(updates)
            else:
                TerminalModel.create({
                    "name": display_name,
                    "serial": serial
                })

        return deleted_terminals, skipped_terminals

