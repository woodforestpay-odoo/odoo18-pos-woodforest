# -*- coding: utf-8 -*-
from odoo import models, fields


class PosConfig(models.Model):
    _inherit = "pos.config"

    payrillium_terminal_id = fields.Many2one(
        'payrillium.terminal',
        string='Woodforest Terminal',
        ondelete='set null',
        store=True
    )

    payrillium_terminal_name = fields.Char(
        string="Terminal Name",
        related='payrillium_terminal_id.name',
        store=True,
        readonly=True
    )
    payrillium_terminal_serial = fields.Char(
        string="Terminal Serial",
        related='payrillium_terminal_id.serial',
        store=True,
        readonly=True
    )

    def write(self, vals):
        if 'payrillium_terminal_id' in vals and not self.env.context.get('terminal_sync'):
            for config in self:
                new_terminal_id = vals.get('payrillium_terminal_id')
                
                # Safety check: Cannot change terminal if session is OPEN
                active_session = self.env['pos.session'].search([
                    ('config_id', '=', config.id),
                    ('state', 'in', ['opened', 'opening_control']),
                ], limit=1)
                if active_session:
                    from odoo.exceptions import UserError
                    raise UserError(
                        f"Cannot change terminal assignment for POS '{config.name}' "
                        f"because the session '{active_session.name}' is currently OPEN."
                    )

                # Bidirectional sync: Update the Terminal side
                # 1. Clear old terminal link
                if config.payrillium_terminal_id:
                    config.payrillium_terminal_id.with_context(terminal_sync=True).write({
                        'pos_config_id': False
                    })
                
                # 2. Assign new terminal link
                if new_terminal_id:
                    terminal = self.env['payrillium.terminal'].browse(new_terminal_id)
                    terminal.with_context(terminal_sync=True).write({
                        'pos_config_id': config.id
                    })

        return super().write(vals)

