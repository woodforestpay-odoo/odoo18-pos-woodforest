# Used to store and manage Woodforest configuration settings including API token and global parameters
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
import base64
import hashlib
from ..services.mirillium import get_terminals_from_token
from ..config import version, PAYMENT_METHOD_NAME, APPLY_FOR_ACTIVATION_URL

_logger = logging.getLogger(__name__)

# Prefixes to identify storage format
_PLAIN_TEXT_PREFIX = 'PLAIN:'  # Plain text (unencrypted)
_ENCRYPTED_PREFIX = 'ENC:'     # Encrypted with XOR + base64


class PayrilliumConfig(models.Model):
    _name = "payrillium.config"
    _description = "Woodforest Configuration"

    name = fields.Char(string="Name", default="Woodforest Settings")
    ACTIVATION_STATES = [
        ("not_configured", "Not activated"),
        ("pending", "Pending approval"),
        ("active", "Active"),
        ("rejected", "Rejected"),
    ]

    token = fields.Char(string="API Token")
    installed = fields.Boolean(string="Installed", default=False)
    activation_state = fields.Selection(
        ACTIVATION_STATES,
        string="Activation Status",
        default="not_configured",
        required=True,
        help="Not configured: no token yet. Pending: applied for access. Active: token validated.",
    )
    version = fields.Char(
        string="Version", compute='_compute_version', store=False)
    auto_sync_enabled = fields.Boolean(
        string="Enable Automatic Payment Sync",
        compute="_compute_auto_sync_enabled",
        inverse="_inverse_auto_sync_enabled",
        store=False
    )

    @api.depends()
    def _compute_version(self):
        from ..config import ENVIRONMENT, BASE_VERSION
        for record in self:
            if ENVIRONMENT == 'dev':
                record.version = version
            else:
                record.version = f"odoo 18 pos_woodforest {BASE_VERSION}"

    def has_token_or_not(self):
        record = self.search([('token', '!=', False)], limit=1)
        return bool(record)

    @api.model
    def _get_payment_method_name(self):
        """Parametrized name for UI (from config.py)."""
        return PAYMENT_METHOD_NAME or "Woodforest"

    payment_method_name = fields.Char(
        string="Payment method name",
        compute="_compute_payment_method_name",
        store=False,
    )
    activate_button_string = fields.Char(
        compute="_compute_button_strings",
        store=False,
    )
    update_button_string = fields.Char(
        compute="_compute_button_strings",
        store=False,
    )
    not_activated_message = fields.Char(
        compute="_compute_not_activated_message",
        store=False,
    )
    click_activate_message = fields.Char(
        compute="_compute_click_activate_message",
        store=False,
    )
    config_form_title = fields.Char(
        compute="_compute_config_form_title",
        store=False,
    )

    @api.depends()
    def _compute_payment_method_name(self):
        name = self._get_payment_method_name()
        for record in self:
            record.payment_method_name = name

    @api.depends()
    def _compute_button_strings(self):
        name = self._get_payment_method_name()
        for record in self:
            record.activate_button_string = f"Activate {name}"
            record.update_button_string = f"Update {name} Configuration"

    @api.depends()
    def _compute_not_activated_message(self):
        name = self._get_payment_method_name()
        for record in self:
            record.not_activated_message = f"{name} is not activated."

    @api.depends()
    def _compute_click_activate_message(self):
        for record in self:
            record.click_activate_message = (
                "Click the \"Activate\" button above to configure your API token "
                "and enable payment methods."
            )

    @api.depends()
    def _compute_config_form_title(self):
        name = self._get_payment_method_name()
        for record in self:
            record.config_form_title = f"{name} Configuration"

    wizard_button_text = fields.Char(
        string="Configuration Button Text",
        compute="_compute_wizard_button_text",
        store=False
    )

    @api.depends('installed', 'token')
    def _compute_wizard_button_text(self):
        name = self._get_payment_method_name()
        for record in self:
            if record.installed and record.token:
                record.wizard_button_text = f"Update {name} Configuration"
            else:
                record.wizard_button_text = f"Activate {name}"

    merchant_id = fields.Char("Merchant ID")

    # Encrypted field stored in DB
    _secret_key_encrypted = fields.Char("Secret Key (Encrypted)", store=True)

    # Computed field for access (automatically decrypted when read)
    secret_key = fields.Char(
        "Secret Key",
        compute='_compute_secret_key',
        inverse='_inverse_secret_key',
        store=False,
        help="Secret key used for HMAC signing. Automatically encrypted at rest."
    )

    pbl_developer_id = fields.Char("Developer ID")
    pbl_solution_id = fields.Char("Solution ID")
    pbl_request_phone = fields.Boolean("Request Phone", default=False)
    pbl_request_shipping = fields.Boolean("Request Shipping", default=False)
    receivable_account_id = fields.Many2one(
        "account.account", "Settlement Bank",
        help="Bank account where Woodforest will deposit the funds (Settlement)."
    )
    outstanding_account_id = fields.Many2one(
        "account.account", "Intermediate Account",
        help="Intermediate account used for payments waiting for settlement."
    )

    # Approved/Decline Terminal Messages
    approved_title = fields.Char(string="Approved Title", default="Approved", help="Title shown on the terminal when a payment is successful")
    approved_message = fields.Char(string="Approved Message", default="{amount} Successfully Charged", help="Message shown on the terminal when a payment is successful. Use {amount} as placeholder.")
    approved_timeout = fields.Integer(string="Approved Timeout", default=5, help="Time in seconds the approved message stays on screen")
    
    decline_title = fields.Char(string="Decline Title", default="Declined", help="Title shown on the terminal when a payment fails")
    decline_message = fields.Char(string="Decline Message", default="Transaction failed", help="Message shown on the terminal when a payment fails")
    decline_timeout = fields.Integer(string="Decline Timeout", default=5, help="Time in seconds the decline message stays on screen")

    @api.model
    def get_singleton_id(self):
        record = self.search([], limit=1)
        if not record:
            record = self.create({})
        return record.id

    @api.model
    def migrate_existing_secret_keys(self):
        """
        Migrate existing unencrypted secret_keys to encrypted format.
        Automatically executed when accessing secret_key if old format is detected.
        """
        configs = self.search([('_secret_key_encrypted', '!=', False)])

        for config in configs:
            if not config._secret_key_encrypted or not config._secret_key_encrypted.startswith(_PLAIN_TEXT_PREFIX):
                continue

            try:
                salt = config._get_encryption_salt()
                plain_value = config._secret_key_encrypted[len(
                    _PLAIN_TEXT_PREFIX):]
                encrypted_value = config._simple_encrypt(plain_value, salt)

                if encrypted_value:
                    # Update encrypted field directly
                    config.write(
                        {'_secret_key_encrypted': f"{_ENCRYPTED_PREFIX}{encrypted_value}"})
                    _logger.info(
                        "Migrated secret_key for config ID %s to encrypted format", config.id)
            except Exception as e:
                _logger.error(
                    "Error migrating secret_key for config ID %s: %s", config.id, e)

    def get_apply_for_activation_url(self):
        """URL for 'Apply for activation' (from config.py)."""
        return APPLY_FOR_ACTIVATION_URL or ""

    def action_apply_for_activation(self):
        """Set state to Pending and open external form URL."""
        self.ensure_one()
        self.write({"activation_state": "pending"})
        url = self.get_apply_for_activation_url()
        if not url:
            return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                "title": "Apply for activation",
                "message": "Apply URL is not configured. Contact your administrator.",
                "type": "warning",
                "sticky": True,
            }}
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    def open_activate_with_code_wizard(self):
        """Open wizard to enter token only (Activate with code / I already have a code)."""
        self.ensure_one()
        wizard_values = {"activate_only": True}
        if self.token:
            wizard_values["token"] = self.token
        wizard = self.env["payrillium.wizard"].create(wizard_values)
        return {
            "name": "Activate with code",
            "type": "ir.actions.act_window",
            "res_model": "payrillium.wizard",
            "view_mode": "form",
            "target": "new",
            "res_id": wizard.id,
        }

    def open_payrillium_wizard(self):
        """Open full wizard (token + accounts) for Update Configuration."""
        self.ensure_one()
        is_installed = bool(self.installed and self.token)

        wizard_values = {}
        if self.token:
            wizard_values["token"] = self.token
        if self.receivable_account_id:
            wizard_values["receivable_account_id"] = self.receivable_account_id.id
        if self.outstanding_account_id:
            wizard_values["account_id"] = self.outstanding_account_id.id

        wizard = self.env["payrillium.wizard"].create(wizard_values)

        name = self._get_payment_method_name()
        wizard_name = (
            f"Update {name} Configuration" if is_installed else f"Activate {name}"
        )
        return {
            "name": wizard_name,
            "type": "ir.actions.act_window",
            "res_model": "payrillium.wizard",
            "view_mode": "form",
            "target": "new",
            "res_id": wizard.id,
        }

    def action_refresh_status(self):
        """Placeholder for Refresh status (e.g. re-sync or re-validate)."""
        self.ensure_one()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": "Status",
            "message": "Activation status refreshed.",
            "type": "info",
            "sticky": False,
        }}

    def action_deactivate(self):
        """Clear activation (keeps token in DB but marks not configured)."""
        self.ensure_one()
        raise UserError("Deactivation is currently disabled. Please contact support if you need to deactivate this terminal.")
        # self.write({"installed": False, "activation_state": "not_configured"})
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": "Deactivated",
            "message": "Module has been deactivated. You can activate again with a code.",
            "type": "warning",
            "sticky": False,
        }}

    def _compute_auto_sync_enabled(self):
        # Temporarily disabled for optimizations
        for rec in self:
            rec.auto_sync_enabled = False

    def _inverse_auto_sync_enabled(self):
        # Ensure cron is turned off
        cron = self.env.ref(
            "pos_woodforest.ir_cron_payrillium_check_payment_status",
            raise_if_not_found=False
        )
        if cron and cron.active:
            cron.sudo().write({"active": False})

    # ─────────────────────────────────────────────
    #  Secret Key Encryption (without external libraries)
    # ─────────────────────────────────────────────

    def _get_encryption_salt(self):
        """
        Get or generate an encryption salt from ir.config_parameter.
        This salt is used together with system data to encrypt/decrypt secret_key.
        """
        salt_param = self.env['ir.config_parameter'].sudo().get_param(
            'payrillium.encryption_salt')

        if not salt_param:
            # Generate new salt if it doesn't exist (using system secrets)
            import secrets
            # Use database UUID + timestamp to create unique salt
            db_uuid = self.env['ir.config_parameter'].sudo(
            ).get_param('database.uuid') or ''
            salt_value = hashlib.sha256(
                f"{db_uuid}{secrets.token_hex(16)}".encode('utf-8')
            ).hexdigest()[:32]  # 32 characters for XOR

            self.env['ir.config_parameter'].sudo().set_param(
                'payrillium.encryption_salt', salt_value)
            _logger.info(
                "Generated new encryption salt for Payrillium secret keys")
            return salt_value

        return salt_param

    def _simple_encrypt(self, plaintext, salt):
        """
        Simple XOR encryption using derived salt.
        NOTE: This is basic obfuscation, not cryptographically secure encryption.
        For real production, using cryptography.fernet is recommended.
        """
        if not plaintext:
            return ''

        # Generate key derived from salt
        key = hashlib.sha256(salt.encode('utf-8')).digest()

        # Simple XOR cipher (obfuscation, not real encryption)
        encrypted_bytes = bytearray()
        plaintext_bytes = plaintext.encode('utf-8')

        for i, byte in enumerate(plaintext_bytes):
            encrypted_bytes.append(byte ^ key[i % len(key)])

        # Encode in base64 for safe storage in DB
        return base64.b64encode(bytes(encrypted_bytes)).decode('utf-8')

    def _simple_decrypt(self, ciphertext, salt):
        """
        Decrypt text encrypted with _simple_encrypt.
        """
        if not ciphertext:
            return ''

        try:
            # Decode from base64
            encrypted_bytes = base64.b64decode(ciphertext.encode('utf-8'))

            # Generate the same derived key
            key = hashlib.sha256(salt.encode('utf-8')).digest()

            # XOR cipher (reversible)
            decrypted_bytes = bytearray()
            for i, byte in enumerate(encrypted_bytes):
                decrypted_bytes.append(byte ^ key[i % len(key)])

            return bytes(decrypted_bytes).decode('utf-8')
        except Exception as e:
            _logger.error("Error decrypting secret_key: %s", e)
            return ''

    @api.depends('_secret_key_encrypted')
    def _compute_secret_key(self):
        """
        Decrypt the secret_key stored in _secret_key_encrypted.
        Automatically executed when accessing secret_key.
        Uses simple XOR encryption (without external libraries).
        """
        for record in self:
            if not record._secret_key_encrypted:
                record.secret_key = False
                continue

            # Detect format: PLAIN:xxx or ENC:xxx or old value without prefix
            if record._secret_key_encrypted.startswith(_PLAIN_TEXT_PREFIX):
                # Plain text (migration or unencrypted)
                record.secret_key = record._secret_key_encrypted[len(
                    _PLAIN_TEXT_PREFIX):]
                # Auto-migrate to encrypted format on next save
                try:
                    salt = record._get_encryption_salt()
                    encrypted = record._simple_encrypt(record.secret_key, salt)
                    if encrypted:
                        record._secret_key_encrypted = f"{_ENCRYPTED_PREFIX}{encrypted}"
                except Exception:
                    pass  # Keep plain text if encryption fails
            elif record._secret_key_encrypted.startswith(_ENCRYPTED_PREFIX):
                # Encrypted value
                try:
                    salt = record._get_encryption_salt()
                    ciphertext = record._secret_key_encrypted[len(
                        _ENCRYPTED_PREFIX):]
                    record.secret_key = record._simple_decrypt(
                        ciphertext, salt)
                    if not record.secret_key:
                        _logger.error(
                            "Failed to decrypt secret_key for record %s", record.id)
                        record.secret_key = False
                except Exception as e:
                    _logger.error(
                        "Error decrypting secret_key for record %s: %s", record.id, e)
                    record.secret_key = False
            else:
                # Old value without prefix (assume plain text and migrate)
                record.secret_key = record._secret_key_encrypted
                try:
                    salt = record._get_encryption_salt()
                    encrypted = record._simple_encrypt(record.secret_key, salt)
                    if encrypted:
                        record._secret_key_encrypted = f"{_ENCRYPTED_PREFIX}{encrypted}"
                except Exception:
                    pass  # Keep original value if encryption fails

    def _inverse_secret_key(self):
        """
        Encrypt the secret_key before saving it to _secret_key_encrypted.
        Automatically executed when assigning a value to secret_key.
        Uses simple XOR encryption (without external libraries).
        """
        for record in self:
            if not record.secret_key:
                record._secret_key_encrypted = False
                continue

            try:
                salt = record._get_encryption_salt()
                encrypted = record._simple_encrypt(record.secret_key, salt)
                if encrypted:
                    record._secret_key_encrypted = f"{_ENCRYPTED_PREFIX}{encrypted}"
                else:
                    # Fallback to plain text if encryption fails
                    record._secret_key_encrypted = f"{_PLAIN_TEXT_PREFIX}{record.secret_key}"
                    _logger.warning(
                        "Failed to encrypt secret_key, storing in plain text")
            except Exception as e:
                _logger.error(
                    "Error encrypting secret_key for record %s: %s", record.id, e)
                # Fallback to plain text with prefix
                record._secret_key_encrypted = f"{_PLAIN_TEXT_PREFIX}{record.secret_key}"

