import logging
from odoo import models, fields, api
from odoo.exceptions import UserError
from ..controllers.main import build_url, build_header_hash, deep_clean_payload, log_payrillium_event
from datetime import datetime
import json
import requests


_logger = logging.getLogger(__name__)


class PayrilliumTerminal(models.Model):
    _name = 'payrillium.terminal'
    _description = 'Woodforest Terminal'

    name = fields.Char(string="Name", required=True)
    serial = fields.Char(string="Serial Number")
    iface_tipproduct = fields.Boolean(
        string="Enable Tips on Terminal",
        default=True,
        help="If enabled, the terminal will prompt for tips during payment. "
             "This setting works in conjunction with the POS session tip configuration."
    )
    tip_mode = fields.Selection(
        [
            ("amount", "Amount"),
            ("percent", "Percentage"),
        ],
        string="Tip Mode",
        default="amount",
        help="How the terminal tip is calculated: fixed dollar amounts or percentage of the order total.",
    )

    tip_mode_ui = fields.Selection(
        selection="_selection_tip_mode_ui",
        string="Tips",
        compute="_compute_tip_mode_ui",
        inverse="_inverse_tip_mode_ui",
        store=True,
        default="off",
        required=True,
        help="Tips: Off or Amount ($). Percentage (%) is visually hidden.",
    )

    def _selection_tip_mode_ui(self):
        return [
            ("off", "Off"),
            ("amount", "Amount"),
            ("percent", "Percentage"),
        ]

    tips_enabled = fields.Boolean(
        string="Tips Enabled",
        compute="_compute_tips_enabled",
        inverse="_inverse_tips_enabled",
        store=True,
        help="Simple toggle for tips (Off vs Amount mode)",
    )

    DEFAULT_TIP_AMOUNTS = [5.0, 10.0, 20.0]
    DEFAULT_TIP_PERCENTS = [15, 20, 25]

    @api.depends("tip_mode_ui")
    def _compute_tips_enabled(self):
        for terminal in self:
            terminal.tips_enabled = terminal.tip_mode_ui not in (False, "off")

    def _inverse_tips_enabled(self):
        for terminal in self:
            if terminal.tips_enabled:
                if terminal.tip_mode_ui in (False, "off"):
                    terminal.tip_mode_ui = "amount"
            else:
                terminal.tip_mode_ui = "off"

    @api.depends("iface_tipproduct", "tip_mode")
    def _compute_tip_mode_ui(self):
        for terminal in self:
            if not terminal.iface_tipproduct:
                terminal.tip_mode_ui = "off"
            else:
                terminal.tip_mode_ui = terminal.tip_mode or "amount"

    def _inverse_tip_mode_ui(self):
        for terminal in self:
            mode = terminal.tip_mode_ui or "off"
            if mode == "off":
                terminal.iface_tipproduct = False
                terminal.tip_mode = "amount"
            else:
                terminal.iface_tipproduct = True
                terminal.tip_mode = mode
            terminal._ensure_default_tip_options()

    def _ensure_default_tip_options(self):
        """Create default lines: Amount 5,10,20 and Percentage 15,20,25 when none exist. Editable and reorderable in the view."""
        self.ensure_one()
        # Use tip_mode_ui if set; else when tips are on use tip_mode (important on create when computed may not be flushed yet)
        effective_mode = self.tip_mode_ui or (
            self.iface_tipproduct and self.tip_mode) or None
        _logger.info(
            "  payrillium.terminal(%s): _ensure_default_tip_options tip_mode_ui=%s iface_tipproduct=%s tip_mode=%s effective_mode=%s",
            self.name, self.tip_mode_ui, self.iface_tipproduct, self.tip_mode, effective_mode,
        )
        # If no mode detected but we have tips enabled by default, fill both amount and percent so lists are never empty
        if effective_mode not in ("amount", "percent"):
            if not self.iface_tipproduct:
                _logger.info(
                    "  payrillium.terminal(%s): skip default tip options (tips off)",
                    self.name,
                )
                return
            # Tips on but mode not set yet (e.g. right after create): fill both amount and percent
            effective_mode = "amount"
        Option = self.env["payrillium.tip.option"]
        created_any = False
        for opt_type, defaults in (
            ("amount", self.DEFAULT_TIP_AMOUNTS),
            ("percent", self.DEFAULT_TIP_PERCENTS),
        ):
            existing = self.tip_option_ids.filtered(
                lambda o, t=opt_type: o.option_type == t
            )
            if existing:
                continue
            for seq, value in enumerate(defaults, start=1):
                Option.create({
                    "terminal_id": self.id,
                    "option_type": opt_type,
                    "sequence": seq * 10,
                    "value": value,
                })
                created_any = True
        if created_any:
            _logger.info(
                "  payrillium.terminal(%s): default tip options created (amount 5,10,20 and/or percent 15,20,25)",
                self.name,
            )

    @api.model_create_multi
    def create(self, vals_list):
        terminals = super().create(vals_list)
        for terminal in terminals:
            terminal._ensure_default_tip_options()
        return terminals

    def write(self, vals):
        if 'pos_config_id' in vals and not self.env.context.get('terminal_sync'):
            for terminal in self:
                new_pos_id = vals.get('pos_config_id')
                
                # 1. Safety check for the CURRENT assignment
                if terminal.pos_config_id:
                    active_session = self.env['pos.session'].search([
                        ('config_id', '=', terminal.pos_config_id.id),
                        ('state', 'in', ['opened', 'opening_control']),
                    ], limit=1)
                    if active_session:
                        raise UserError(
                            f"Cannot unassign terminal '{terminal.name}' because "
                            f"the session '{active_session.name}' is currently OPEN."
                        )

                # 2. Safety check for the NEW assignment
                if new_pos_id:
                    new_active_session = self.env['pos.session'].search([
                        ('config_id', '=', new_pos_id),
                        ('state', 'in', ['opened', 'opening_control']),
                    ], limit=1)
                    if new_active_session:
                        raise UserError(
                            f"Cannot assign to this POS because its current "
                            f"session '{new_active_session.name}' is OPEN."
                        )

                # 3. Synchronize with pos.config (Cleanup old, Assign new)
                if terminal.pos_config_id:
                    terminal.pos_config_id.payrillium_terminal_id = False
                
                if new_pos_id:
                    new_pos = self.env['pos.config'].browse(new_pos_id)
                    new_pos.payrillium_terminal_id = terminal.id

        res = super().write(vals)
        for terminal in self:
            terminal._ensure_default_tip_options()
        return res

    tip_option_ids = fields.One2many(
        "payrillium.tip.option",
        "terminal_id",
        string="Tip options",
        help="Preset values for tip selection (amount or percent depending on tip mode).",
    )
    
    # Filtered fields for form view - direct One2many with domain only
    tip_option_ids_amount = fields.One2many(
        "payrillium.tip.option",
        "terminal_id",
        string="Amount Options",
        domain=[('option_type', '=', 'amount')],
        help="Tip options in fixed dollar amounts.",
    )
    tip_option_ids_percent = fields.One2many(
        "payrillium.tip.option",
        "terminal_id",
        string="Percentage Options",
        domain=[('option_type', '=', 'percent')],
        help="Tip options in percentage of order total.",
    )
    
    tip_options_summary = fields.Char(
        string="Tip presets",
        compute="_compute_tip_options_summary",
        help="Summary of configured tip values for list view.",
    )



    @api.depends(
        "tip_mode_ui",
        "tip_option_ids",
        "tip_option_ids.value",
        "tip_option_ids.option_type",
        "tip_option_ids.sequence",
    )
    def _compute_tip_options_summary(self):
        for terminal in self:
            if terminal.tip_mode_ui in ("off", False):
                terminal.tip_options_summary = ""
                continue
            options = terminal.tip_option_ids.filtered(
                lambda o, t=terminal: o.option_type == t.tip_mode
            ).sorted("sequence")
            if not options:
                terminal.tip_options_summary = ""
                continue
            if terminal.tip_mode == "amount":
                terminal.tip_options_summary = ", ".join(
                    f"${o.value:.2f}" for o in options
                )
            else:
                terminal.tip_options_summary = ", ".join(
                    f"{int(o.value)}%" for o in options
                )

    def get_tip_option_values(self):
        """Return list of numeric values for current tip mode (for API/POS)."""
        self.ensure_one()
        if self.tip_mode_ui in ("off", False):
            return []
        options = self.tip_option_ids.filtered(
            lambda o, t=self: o.option_type == t.tip_mode
        ).sorted("sequence")
        return [o.value for o in options]

    last_session_id = fields.Many2one(
        'pos.session', string="Last Session", compute='_compute_last_session', store=True)

    pos_config_name = fields.Char(
        string="POS Config", related='pos_config_id.name', store=True, readonly=True)
    pos_config_id = fields.Many2one(
        'pos.config',
        string='Assigned POS'
    )

    session_status = fields.Selection([
        ('none', 'Unassigned'),
        ('available', 'Available'),
        ('busy', 'Busy'),
    ], string="Session Status", compute='_compute_session_status')

    @api.depends('pos_config_id')
    def _compute_session_status(self):
        for terminal in self:
            if not terminal.pos_config_id:
                terminal.session_status = 'none'
                continue
            
            # Check for ANY open session in the linked POS
            open_session = self.env['pos.session'].search([
                ('config_id', '=', terminal.pos_config_id.id),
                ('state', 'in', ['opened', 'opening_control'])
            ], limit=1)
            
            terminal.session_status = 'busy' if open_session else 'available'

    selectable_pos_ids = fields.Many2many(
        'pos.config',
        'payrillium_terminal_selectable_pos_rel',
        'terminal_id',
        'pos_config_id',
        string='Selectable POS',
        compute='_compute_selectable_pos_ids',
    )

    @api.depends('pos_config_id')
    def _compute_selectable_pos_ids(self):
        # POS configs that currently have an OPEN session are excluded
        open_sessions = self.env['pos.session'].search([
            ('state', 'in', ['opened', 'opening_control']),
        ])
        blocked_ids = open_sessions.mapped('config_id').ids
        available = self.env['pos.config'].search([('id', 'not in', blocked_ids)])
        for terminal in self:
            terminal.selectable_pos_ids = available

    session_state_label = fields.Char(
        string="Session",
        compute='_compute_session_state_label',
        help="Real-time state of the linked POS session."
    )

    SESSION_STATE_LABELS = {
        'opening_control': 'Opening',
        'opened': 'Open',
        'closing_control': 'Closing',
        'closed': 'Closed',
        'new_session': 'New',
    }

    @api.depends('pos_config_id', 'last_session_id')
    def _compute_session_state_label(self):
        for terminal in self:
            if not terminal.pos_config_id:
                terminal.session_state_label = ""
                continue

            session = self.env['pos.session'].search([
                ('config_id', '=', terminal.pos_config_id.id),
            ], order='id desc', limit=1)

            if not session:
                terminal.session_state_label = ""
            else:
                terminal.session_state_label = self.SESSION_STATE_LABELS.get(
                    session.state, session.state.capitalize()
                )

    def action_open_assign_wizard(self):
        """Open the 'Assign Terminal to POS' dialog."""
        self.ensure_one()
        wizard = self.env['payrillium.assign.wizard'].create({
            'terminal_id': self.id,
            'new_pos_config_id': self.pos_config_id.id if self.pos_config_id else False,
        })
        return {
            'name': 'Assign to POS Session',
            'type': 'ir.actions.act_window',
            'res_model': 'payrillium.assign.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.depends("pos_config_id")
    def _compute_last_session(self):
        _logger.info(" Starting _compute_last_session for all terminals...")
        for terminal in self:
            _logger.info(f"  Terminal: {terminal.name} (ID: {terminal.id})")

            terminal.last_session_id = False
            terminal.pos_config_name = "Not assigned"

            pos_config = self.env['pos.config'].search([
                ('payrillium_terminal_id', '=', terminal.id)
            ], limit=1)

            if pos_config:
                _logger.info(
                    f"  POS Config found: {pos_config.name} (ID: {pos_config.id})")

                session = self.env['pos.session'].search([
                    ('config_id', '=', pos_config.id),
                    ('state', 'in', ['opened', 'opening_control', 'closed'])
                ], order='id desc', limit=1)

                if session:
                    _logger.info(
                        f"  Session found: {session.name} (State: {session.state})")
                    terminal.last_session_id = session.id
                terminal.pos_config_name = pos_config.name
            else:
                terminal.pos_config_name = "Not assigned"
                _logger.warning(
                    f"   No POS Config found for terminal {terminal.name}")

        _logger.info(" Finished _compute_last_session.")

    @api.model
    def _check_terminal_core(self, terminal_serial):
        try:
            if not terminal_serial:
                return {"status": "error", "message": "No terminal serial provided"}

            payload = {"data": {}}
            url = build_url(terminal_serial, "local", "test")
            log_payrillium_event(
                "missing", "check_terminal", "request", payload)

            timestamp = int(datetime.utcnow().timestamp()) * 1000
            payload = deep_clean_payload(payload)
            auth_hash = build_header_hash(self.env, payload, timestamp)
            request_body = json.dumps(payload, separators=(",", ":"))

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth_hash}",
                "timestamp": str(timestamp),
            }

            _logger.info("Check terminal payload=%s, ts=%s",
                         request_body, timestamp)
            resp = requests.post(url, headers=headers, json={
                                 "data": request_body})
            resp.raise_for_status()
            data = resp.json()

            log_payrillium_event("missing", "check_terminal",
                                 "response", data, success=True)
            return {"status": "success", "data": data}
        except Exception as e:
            log_payrillium_event("missing", "check_terminal",
                                 "response", None, success=False, error_message=str(e))
            return {"status": "error", "message": str(e)}

    @api.model
    def _ping_terminal_core(self, terminal_serial, execution_id=None):
        if not execution_id:
            execution_id = f"check_conn_{terminal_serial}_{int(datetime.utcnow().timestamp())}"
        try:
            if not terminal_serial:
                return {"status": "error", "message": "No terminal serial provided"}

            payload = {"data": {}}
            url = build_url(terminal_serial, "local", "terminal_info")
            timestamp = int(datetime.utcnow().timestamp()) * 1000
            payload_clean = deep_clean_payload(payload)
            auth_hash = build_header_hash(self.env, payload_clean, timestamp)
            request_body = json.dumps(payload_clean, separators=(",", ":"))

            log_payrillium_event(
                execution_id, "terminal_ping", "request", request_body)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth_hash}",
                "timestamp": str(timestamp),
            }

            resp = requests.post(url, headers=headers, json={"data": request_body}, timeout=3.0)
            resp.raise_for_status()
            data = resp.json()

            log_payrillium_event(
                execution_id, "terminal_ping", "response", data, success=True)
            return {"status": "success", "data": data}
        except Exception as e:
            log_payrillium_event(
                execution_id, "terminal_ping", "response", None,
                success=False, error_message=str(e))
            return {"status": "error", "message": str(e)}

    def action_check_terminal(self):
        self.ensure_one()
        if not self.serial:
            raise UserError("Terminal has no serial number configured.")
        _logger.info("Checking terminal (backend direct): %s (%s)",
                     self.name, self.serial)

        result = self.env['payrillium.terminal']._check_terminal_core(
            self.serial)
        ok = result.get("status") == "success" and (result.get(
            "data", {}).get("data", {}).get("success") is True)

        if ok:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'title': 'Terminal Connected Successfully',
                           'message': f"Terminal {self.name} ({self.serial}) is online.",
                           'type': 'success', 'sticky': False}
            }
        else:
            msg = result.get('message') or 'Unknown error'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'title': 'Connection Error',
                           'message': f"Unable to connect to terminal: {msg}",
                           'type': 'danger', 'sticky': True}
            }

    def action_test_tips(self):
        """Test tip selection menu on device with $100 test amount."""
        self.ensure_one()
        if not self.serial:
            raise UserError("Terminal has no serial number configured.")
        
        if not self.iface_tipproduct:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Tips Disabled',
                    'message': 'Tips are not enabled on this terminal. Please enable tips first.',
                    'type': 'warning',
                    'sticky': False
                }
            }
        
        _logger.info("Testing tips menu on terminal: %s (%s)", self.name, self.serial)
        
        # Build tip options for display
        tip_values = self.get_tip_option_values()
        if not tip_values:
            # Use defaults if no values configured
            tip_values = self.DEFAULT_TIP_AMOUNTS if self.tip_mode == 'amount' else self.DEFAULT_TIP_PERCENTS
        
        test_amount = 100  # Test amount of $100 (integer)
        is_percent = self.tip_mode == 'percent'
        
        # Format options for display
        tip_options_show = []
        if is_percent:
            tip_options_show = [f"{int(v)}%" for v in tip_values]
        else:
            tip_options_show = [f"${float(v):.2f}" for v in tip_values]
        tip_options_show.extend(["Custom", "No Tip"])
        
        # Build payload as if it came from POS controller
        # amount: 100 (integer) like POS often sends
        kwargs = {
            "title": "Select Tip",
            "menu": tip_options_show,
            "amount": int(test_amount)
        }
        
        # Now use the SAME logic as proxy_to_terminal in main.py
        payload_data = kwargs.copy()
        payload = {
            "data": {
                "data": payload_data,
            }
        }
        
        execution_id = f"TEST-TIPS-{self.serial}-{int(datetime.utcnow().timestamp())}"
        
        try:
            url = build_url(self.serial, "local", "tip")
            timestamp = int(datetime.utcnow().timestamp()) * 1000
            payload_clean = deep_clean_payload(payload)
            
            # Match main.py exactly: stringify BEFORE hashing
            request_body = json.dumps(payload_clean, separators=(",", ":"))
            auth_hash = build_header_hash(self.env, payload_clean, timestamp)
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth_hash}",
                "timestamp": str(timestamp),
            }
            
            _logger.info("Test Tips DEBUG - URL: %s", url)
            _logger.info("Test Tips DEBUG - Auth Hash: %s", auth_hash)
            _logger.info("Test Tips DEBUG - Request Body: %s", request_body)
            
            log_payrillium_event(execution_id, "tip", "request", request_body)
            
            resp = requests.post(url, headers=headers, json={"data": request_body})
            
            if resp.status_code != 200:
                error_msg = f"HTTP {resp.status_code}: {resp.text}"
                log_payrillium_event(execution_id, "tip", "response", None, success=False, error_message=error_msg)
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Test Tips Error',
                        'message': f"Terminal returned HTTP {resp.status_code}. Check terminal connection.",
                        'type': 'danger',
                        'sticky': False
                    }
                }

            data = resp.json()
            log_payrillium_event(execution_id, "tip", "response", data, success=True)
            
            _logger.info("Test tips response: %s", data)
            
            # Extract selected tip info from response
            result_type = data.get('data', {}).get('type', '')
            result_data = data.get('data', {}).get('data', {})
            
            tip_message = "No tip selected"
            if 'TipResultCustom' in result_type:
                custom_value = result_data.get('value', 0)
                if is_percent:
                    tip_amount = (test_amount * float(custom_value)) / 100
                    tip_message = f"Custom: {custom_value}% = ${tip_amount:.2f}"
                else:
                    tip_message = f"Custom: ${float(custom_value):.2f}"
            elif 'TipResultOption' in result_type:
                selection = result_data.get('selection', -1)
                if 0 <= selection < len(tip_values):
                    value = tip_values[selection]
                    if is_percent:
                        tip_amount = (test_amount * float(value)) / 100
                        tip_message = f"Selected: {int(value)}% = ${tip_amount:.2f}"
                    else:
                        tip_message = f"Selected: ${float(value):.2f}"
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Tip Test Result',
                    'message': f"Test amount: ${test_amount:.2f}\n{tip_message}",
                    'type': 'info',
                    'sticky': False
                }
            }
            
        except Exception as e:
            error_msg = str(e)
            _logger.error("Error testing tips: %s", error_msg)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Test Tips Error',
                    'message': "Could not communicate with terminal. Check connection and try again.",
                    'type': 'danger',
                    'sticky': False
                }
            }

    def action_test_approved_view(self):
        """Test the 'Approved' custom view on the terminal."""
        self.ensure_one()
        config = self.env['payrillium.config'].sudo().search([], limit=1)
        message = (config.approved_message or "{amount} Successfully Charged").replace("{amount}", "$100.00")
        payload = {
            "title": config.approved_title or "Approved",
            "message": message,
            "timeout": str(config.approved_timeout or 5)
        }
        return self._send_view_to_terminal(payload, "approved", "Approved")

    def action_test_decline_view(self):
        """Test the 'Decline' custom view on the terminal."""
        self.ensure_one()
        config = self.env['payrillium.config'].sudo().search([], limit=1)
        payload = {
            "title": config.decline_title or "Declined",
            "message": config.decline_message or "Transaction failed",
            "timeout": str(config.decline_timeout or 5)
        }
        return self._send_view_to_terminal(payload, "decline", "Decline")

    def _send_view_to_terminal(self, view_payload, action, state_name):
        """Send a view payload to the terminal using the specified action endpoint.
        
        action: 'approved' or 'decline' → calls local/approved or local/decline
        """
        if not self.serial:
            raise UserError("Terminal has no serial number configured.")
            
        _logger.info("Sending %s to terminal %s: %s", state_name, self.serial, view_payload)
        
        payload = {
            "data": view_payload
        }
        
        try:
            url = build_url(self.serial, "local", action)
            timestamp = int(datetime.utcnow().timestamp()) * 1000
            payload_clean = deep_clean_payload(payload)
            request_body = json.dumps(payload_clean, separators=(",", ":"))
            auth_hash = build_header_hash(self.env, payload_clean, timestamp)
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth_hash}",
                "timestamp": str(timestamp),
            }
            
            resp = requests.post(url, headers=headers, json={"data": request_body})
            resp.raise_for_status()
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': f'Terminal {state_name} Test',
                    'message': f"Payload sent successfully to terminal {self.name}.",
                    'type': 'success',
                    'sticky': False
                }
            }
        except Exception as e:
            error_msg = str(e)
            _logger.error("Error sending %s view: %s", state_name, error_msg)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': f'Terminal {state_name} Error',
                    'message': f"Failed to send view: {error_msg}",
                    'type': 'danger',
                    'sticky': True
                }
            }

    @api.model
    def _reset_terminal_core(self, terminal_serial, execution_id="missing"):
        try:
            if not terminal_serial:
                return {"status": "error", "message": "No terminal ID provided"}

            payload = {"data": {}}
            url = build_url(terminal_serial, "payment", "abort")
            timestamp = int(datetime.utcnow().timestamp()) * 1000
            payload = deep_clean_payload(payload)
            auth_hash = build_header_hash(self.env, payload, timestamp)
            request_body = json.dumps(payload, separators=(",", ":"))
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth_hash}",
                "timestamp": str(timestamp),
            }

            log_payrillium_event(
                execution_id, "reset_terminal", "request", request_body)
            resp = requests.post(url, headers=headers, json={
                                 "data": request_body})
            resp.raise_for_status()
            data = resp.json()

            log_payrillium_event(execution_id, "reset_terminal",
                                 "response", data, success=True)
            return {"status": "success", "data": data}
        except Exception as e:
            log_payrillium_event(execution_id, "reset_terminal",
                                 "response", None, success=False, error_message=str(e))
            return {"status": "error", "message": str(e)}

    def action_abort_terminal(self):
        self.ensure_one()
        if not self.serial:
            raise UserError("Terminal has no serial number configured.")

        _logger.info("Reset terminal (backend direct): %s (%s)",
                     self.name, self.serial)

        result = self.env['payrillium.terminal']._reset_terminal_core(
            self.serial)
        top_success = result.get("status") == "success"
        data = result.get("data") or {}
        data_success = (data.get("data") or {}).get("success")

        if top_success and data_success:
            message = f"Terminal {self.name} ({self.serial}) was reset successfully"
            msg_type = "success"
        elif top_success and not data_success:
            reason = (data.get("data") or {}).get(
                "reason", "No reason provided")
            message = (f"No operation to abort on terminal {self.name} ({self.serial})\n"
                       f"Reason: {reason}")
            msg_type = "warning"
        else:
            message = result.get("message", "Unknown error")
            msg_type = "danger"

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Terminal Reset Result',
                'message': message,
                'type': msg_type,
                'sticky': False,
            }
        }

    def action_unlink_terminal(self):
        if len(self) != 1:
            raise UserError(
                "Please select exactly one terminal to perform this action.")
        for terminal in self:
            _logger.info(
                f" Unlinking terminal: {terminal.name} (ID: {terminal.id})")
            if terminal.pos_config_id:
                active_session = self.env['pos.session'].search([
                    ('config_id', '=', terminal.pos_config_id.id),
                    ('state', 'in', ['opened', 'opening_control']),
                ], limit=1)

                if active_session:
                    _logger.warning(
                        f"  Cannot unlink terminal; session {active_session.name} is still active.")
                    raise UserError(
                        f"Cannot unlink terminal '{terminal.name}' because session '{active_session.name}' is still open or in opening control."
                    )

            if terminal.pos_config_id:
                _logger.info(
                    f"  Removing terminal {terminal.name} from POS Config {terminal.pos_config_id.name}")

                pos_config = terminal.pos_config_id
                pos_config.payrillium_terminal_id = False

                terminal.write({
                    'pos_config_id': False,
                    'pos_config_name': False,
                    'last_session_id': False,
                })

                _logger.info(
                    f"  Terminal {terminal.name} unlinked successfully.")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Terminal Unlinked',
                        'message': 'The terminal was successfully unlinked.',
                        'type': 'success',
                        'sticky': False,
                        'next': {
                            'type': 'ir.actions.client',
                            'tag': 'reload',
                        }
                    }
                }
            else:
                _logger.warning(
                    f"   Terminal {terminal.name} is not linked to any POS Config.")
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Not Linked',
                        'message': 'The terminal is not linked to any POS Config.',
                        'type': 'warning',
                        'sticky': True,
                    }
                }

    def action_delete_terminal(self):
        if len(self) != 1:
            raise UserError(
                "Please select exactly one terminal to perform this action.")
        for terminal in self:
            _logger.info(
                f" DELETE Terminal requested: {terminal.name} (Serial: {terminal.serial})")

    def name_get(self):
        result = []
        for terminal in self:
            serial_suffix = terminal.serial[-4:] if terminal.serial and len(
                terminal.serial) >= 4 else ""
            label = f"{terminal.name} - {serial_suffix}" if serial_suffix else terminal.name
            _logger.info(f" name_get called for: {label}")
            result.append((terminal.id, label))
        return result

    def action_download_terminal_logs(self):
        """Open the date-range wizard for terminal log download."""
        self.ensure_one()
        _logger.info("=== action_download_terminal_logs called for terminal %s (serial=%s) ===", self.name, self.serial)

        if not self.serial or self.serial == "NONE":
            raise UserError("Terminal has no serial number configured.")

        try:
            wizard = self.env["payrillium.terminal.log.wizard"].create({
                "terminal_id": self.id,
            })
            _logger.info("=== Wizard created id=%s, opening form ===", wizard.id)
            return {
                "type": "ir.actions.act_window",
                "name": "Download Terminal Logs",
                "res_model": "payrillium.terminal.log.wizard",
                "res_id": wizard.id,
                "view_mode": "form",
                "views": [[False, "form"]],
                "target": "new",
            }
        except Exception as e:
            _logger.exception("=== ERROR creating terminal log wizard: %s ===", e)
            raise UserError(f"Error opening log download wizard: {e}")
