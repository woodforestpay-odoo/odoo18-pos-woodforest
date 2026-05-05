import logging
from odoo import models, fields, api
from ..config import PAYMENT_METHOD_NAME

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dummy_terminal_label = fields.Char(
        default="Select Terminal", readonly=True)

    enable_payrillium_terminal = fields.Boolean(string=PAYMENT_METHOD_NAME)
    module_pos_woodforest = fields.Boolean(
        string="Install Woodforest Module", default=False)
    pos_config_id = fields.Many2one(
        'pos.config',
        default=lambda self: self.env['pos.config'].search(
            [('company_id', '=', self.env.company.id)], limit=1),
        string="POS Config"
    )
    terminal_id = fields.Many2one(
        'payrillium.terminal',
        string='Available Woodforest Terminals',
        readonly=False,
        store=True,
    )

    has_custom_payment = fields.Boolean(string='Has Payment Method')

    terminal_dirty = fields.Boolean(default=False)

    @api.depends('pos_module_pos_restaurant', 'pos_config_id')
    def _compute_pos_module_pos_restaurant(self):
        super()._compute_pos_module_pos_restaurant()
        for res_config in self:
            # Overwrite the restaurant logic that forces splitbill to False
            if not res_config.pos_module_pos_restaurant:
                current_val = res_config.pos_config_id.iface_splitbill
                _logger.info(
                    f"[OVERRIDE] Retail Mode detected. Preserving Split Bill setting: {current_val}")
                res_config.pos_iface_splitbill = current_val

    def _get_no_terminal_record(self):
        Terminal = self.env['payrillium.terminal']
        no_terminal = Terminal.search([('serial', '=', 'NONE')], limit=1)
        if not no_terminal:
            no_terminal = Terminal.create({
                'name': 'No Terminal',
                'serial': 'NONE',
            })
        return no_terminal

    @api.model
    def get_values(self):
        res = super().get_values()
        _logger.info("  get_values() called in ResConfigSettings")

        pos_config_id = self.env.context.get("pos_config_id")
        if pos_config_id:
            pos_config = self.env['pos.config'].browse(pos_config_id)
        else:
            pos_config = self.env['pos.config'].search(
                [('company_id', '=', self.env.company.id)], limit=1)

        has_payment = False
        if pos_config:
            _logger.info(f"  Found POS Config: {pos_config.display_name}")
            payment_method = self.env['pos.payment.method'].search([
                ('name', '=', PAYMENT_METHOD_NAME),
                ('id', 'in', pos_config.payment_method_ids.ids)
            ], limit=1)
            has_payment = bool(payment_method)
        else:
            _logger.warning("   No POS Config found for current company")
            has_payment = False

        active_sessions = self.env['pos.session'].search(
            [('state', '=', 'opened')])
        terminal_ids_in_use = []
        for session in active_sessions:
            for pm in session.payment_method_ids:
                if hasattr(pm, 'terminal_id') and pm.terminal_id:
                    terminal_ids_in_use.append(pm.terminal_id.id)
        _logger.info(f"  Terminals currently in use: {terminal_ids_in_use}")

        _logger.info(f" Setting has_custom_payment to: {has_payment}")

        no_terminal = self._get_no_terminal_record()
        res.update({
            'has_custom_payment': has_payment,
            'terminal_id': pos_config.payrillium_terminal_id.id
            if pos_config and pos_config.payrillium_terminal_id
            else no_terminal.id,
            'enable_payrillium_terminal': has_payment,
            'terminal_dirty': False,
        })
        _logger.info(" get_values() completed with update")
        return res

    def set_values(self):
        super().set_values()
        _logger.info("set_values() called in ResConfigSettings")

        pos_config = self.pos_config_id
        if not pos_config:
            _logger.warning("No POS Config selected.")
            return True

        has_payment = bool(self.env['pos.payment.method'].search([
            ('name', '=', PAYMENT_METHOD_NAME),
            ('id', 'in', pos_config.payment_method_ids.ids)
        ], limit=1))

        no_terminal = self._get_no_terminal_record()
        selected = self.terminal_id or no_terminal
        current = pos_config.payrillium_terminal_id or False

        if not has_payment:
            _logger.info(
                f"{PAYMENT_METHOD_NAME} not active → clearing assignment on POS")
            if current and current.serial != 'NONE':
                current.write({'pos_config_id': False,
                               'pos_config_name': False,
                               'last_session_id': False})
            pos_config.payrillium_terminal_id = False
            self.terminal_id = no_terminal
            return True

        if selected.serial == 'NONE':
            _logger.info(
                "User selected 'No Terminal' → clearing assignment on POS")
            if current and current.serial != 'NONE':
                current.write({'pos_config_id': False,
                               'pos_config_name': False,
                               'last_session_id': False})
            pos_config.payrillium_terminal_id = False
            return True

        if selected.pos_config_id and selected.pos_config_id != pos_config:
            pos_name = selected.pos_config_id.name or "Unknown POS Config"
            raise models.ValidationError(
                f"The terminal '{selected.name}' is already assigned to the POS '{pos_name}'."
            )

        if current and current.id == selected.id:
            _logger.info("Same terminal already assigned → nothing to do")
            return True

        if current and current.serial != 'NONE':
            current.write({'pos_config_id': False,
                           'pos_config_name': False,
                           'last_session_id': False})

        _logger.info(
            f"Assigning terminal {selected.name} to POS Config {pos_config.name}")
        session = self.env['pos.session'].search([
            ('config_id', '=', pos_config.id),
            ('state', 'in', ['opened', 'opening_control', 'closed'])
        ], order='id desc', limit=1)

        selected.write({
            'pos_config_id': pos_config.id,
            'pos_config_name': pos_config.name,
            'last_session_id': session.id if session else False,
        })
        pos_config.payrillium_terminal_id = selected
        _logger.info(
            f"Terminal {selected.name} updated with POS Config {pos_config.name}")

    @api.onchange('pos_config_id')
    def _onchange_pos_config_id(self):
        if not self.pos_config_id:
            self.terminal_id = False
            self.has_custom_payment = False
            self.terminal_dirty = False
            return

        payment_method = self.env['pos.payment.method'].search([
            ('name', '=', PAYMENT_METHOD_NAME),
            ('id', 'in', self.pos_config_id.payment_method_ids.ids)
        ], limit=1)
        self.has_custom_payment = bool(payment_method)

        current = self.pos_config_id.payrillium_terminal_id
        self.terminal_id = current if current else self._get_no_terminal_record()
        self.terminal_dirty = False

        _logger.info(
            f"[onchange] POS '{self.pos_config_id.display_name}' "
            f"→ has_custom_payment={self.has_custom_payment}, terminal={self.terminal_id.display_name}"
        )
