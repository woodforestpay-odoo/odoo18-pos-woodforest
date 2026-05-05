import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PayrilliumAssignWizard(models.TransientModel):
    _name = 'payrillium.assign.wizard'
    _description = 'Assign Terminal to POS Session'

    terminal_id = fields.Many2one(
        'payrillium.terminal',
        string='Terminal',
        required=True,
        readonly=True,
    )
    terminal_name = fields.Char(
        related='terminal_id.name',
        string='Terminal Name',
        readonly=True,
    )
    current_pos_config_id = fields.Many2one(
        'pos.config',
        related='terminal_id.pos_config_id',
        string='Currently Assigned To',
        readonly=True,
    )
    current_session_status = fields.Selection(
        related='terminal_id.session_status',
        string='Current Session State',
        readonly=True,
    )

    new_pos_config_id = fields.Many2one(
        'pos.config',
        string='Assign to POS',
        domain="[('id', 'in', available_pos_ids)]",
    )

    # Computed list of POS configs WITHOUT an open session
    available_pos_ids = fields.Many2many(
        'pos.config',
        string='Available POS Configs',
        compute='_compute_available_pos_ids',
    )

    @api.depends('terminal_id')
    def _compute_available_pos_ids(self):
        for wizard in self:
            # Find all POS configs that have an open/opening session — exclude them
            open_sessions = self.env['pos.session'].search([
                ('state', 'in', ['opened', 'opening_control']),
            ])
            blocked_pos_ids = open_sessions.mapped('config_id').ids

            # Available = all POS configs NOT currently busy
            available = self.env['pos.config'].search([
                ('id', 'not in', blocked_pos_ids),
            ])
            wizard.available_pos_ids = available

    def action_confirm(self):
        self.ensure_one()
        terminal = self.terminal_id

        # If session is open — safety guard
        if terminal.session_status == 'busy':
            raise UserError(
                f"Cannot reassign '{terminal.name}': its current session is OPEN. "
                "Close the session first."
            )

        # Save old POS config before the write
        old_pos = terminal.pos_config_id

        # Perform the assignment (our write override handles sync & validation)
        terminal.write({'pos_config_id': self.new_pos_config_id.id if self.new_pos_config_id else False})

        if self.new_pos_config_id:
            msg = (
                f"Terminal '{terminal.name}' assigned to "
                f"POS '{self.new_pos_config_id.name}'."
            )
        else:
            msg = f"Terminal '{terminal.name}' unassigned (no session)."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Assignment Updated',
                'message': msg,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
