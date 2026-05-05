# Used for post-install hook to show configuration wizard
from odoo import api, SUPERUSER_ID
from odoo.api import Environment
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


def _ensure_default_tip_options_for_existing_terminals(env):
    """Fill default tip options (5,10,20 and 15,20,25) for terminals that have none. So when you open a terminal detail you see the list filled."""
    Terminal = env["payrillium.terminal"]
    terminals = Terminal.search([])
    _logger.info(
        "  pos_woodforest: ensuring default tip options for %s terminal(s)", len(terminals))
    for terminal in terminals:
        try:
            terminal._ensure_default_tip_options()
        except Exception as e:
            _logger.warning(
                "  Terminal %s: could not ensure default tip options: %s", terminal.name, e)


def show_woodforest_wizard_once(env):
    """
    Show the configuration wizard once after installation (Odoo 18+)
    """
    _logger.info("  Running show_woodforest_wizard_once (post-init hook)")
    # Ensure existing terminals have default tip options (5,10,20 / 15,20,25) so detail view is not empty
    _ensure_default_tip_options_for_existing_terminals(env)
    
    # Sync activation_state for existing configs (installed + token => active)
    for cfg in env["payrillium.config"].search([]):
        if cfg.installed and cfg.token and cfg.activation_state != "active":
            cfg.write({"activation_state": "active"})
    config = env['payrillium.config'].search([], limit=1)
    if not config or not config.token or not config.installed:
        _logger.info(
            "  No Woodforest config found, creating server action to open config form")
        env['ir.actions.server'].create({
            'name': 'Show Woodforest Wizard',
            'model_id': env.ref('base.model_res_config_settings').id,
            'binding_model_id': env.ref('base.model_res_config_settings').id,
            'state': 'code',
            'code': """config_id = env['payrillium.config'].get_singleton_id()
title = env['payrillium.config']._get_payment_method_name() + ' Configuration'
action = {
    'type': 'ir.actions.act_window',
    'name': title,
    'res_model': 'payrillium.config',
    'view_mode': 'form',
    'views': [(env.ref('pos_woodforest.view_payrillium_config_form').id, 'form')],
    'res_id': config_id,
    'target': 'current',
}""",
        })
    else:
        _logger.info(
            "  Woodforest config with token already exists, wizard will not be shown")


def uninstall_cleanup_woodforest(env):
    """
    Cleanup hook that runs during module uninstallation
    """
    _logger.info("  Running uninstallation cleanup for pos_woodforest")

    try:
        #   Check for open POS sessions using Payrillium payment methods
        open_sessions = env['pos.session'].search([
            ('state', 'not in', ['closed', 'archived']),
            ('state', 'in', ['opening_control', 'opened', 'closing_control']),
            ('config_id.payment_method_ids.use_payment_terminal', '=', 'woodforest')
        ])

        if open_sessions:
            session_names = ', '.join(open_sessions.mapped('name'))
            _logger.error(
                f"  Cannot uninstall: open POS sessions using Woodforest exist: {session_names}")
            raise UserError(
                f"  Cannot uninstall module: there are open POS sessions using Woodforest.\nPlease close these sessions first:\n{session_names}")
        # 1. FIRST Find and disable ALL payment providers that might be Payrillium
        _logger.info("  Searching for all possible Woodforest providers...")
        providers = env['payment.provider'].search([('code', '=', 'woodforest')])

        if not providers:
            _logger.warning(
                "   No providers found with code 'woodforest', searching by name...")
            providers = env['payment.provider'].search([
                ('name', 'ilike', 'Terminal'),
            ])

        if providers:
            _logger.info(
                f"    Found {len(providers)} payment providers to process")
            for provider in providers:
                _logger.info(
                    f"    Processing provider: {provider.name} (state: {provider.state})")

                # Just disable the provider
                if provider.state in ['enabled', 'test']:
                    provider.write({'state': 'disabled'})
                    _logger.info(
                        f"  Provider {provider.name} disabled successfully")

            env.cr.commit()  # Force commit to ensure state is updated

        # 2. Then deactivate POS payment methods
        pos_methods = env['pos.payment.method'].search([
            ('use_payment_terminal', '=', 'woodforest')
        ])
        if pos_methods:
            _logger.info(
                f"    Deactivating {len(pos_methods)} POS payment methods")
            for method in pos_methods:
                method.write({'active': False})
                _logger.info(
                    f"  POS payment method deactivated: {method.name}")

        # 3. Remove payment method lines
        journal = env['account.journal'].search([
            ('code', 'in', ['woodforest', 'PAYR']),
            ('company_id', '=', env.company.id)
        ], limit=1)
        if journal:
            method_lines = env['account.payment.method.line'].search(
                [('journal_id', '=', journal.id)])
            if method_lines:
                _logger.info(
                    f"  Removing {len(method_lines)} payment method lines")
                method_lines.unlink()
                _logger.info("  Payment method lines removed")

        # 4. Remove terminals
        terminals = env['payrillium.terminal'].search([])
        if terminals:
            _logger.info(f"  Removing {len(terminals)} terminals")
            terminals.unlink()
            _logger.info("  Terminals removed")

        # 5. Archive the Woodforest journal (preserve accounting entries)
        if journal:
            # Check for draft entries that would prevent archiving
            draft_moves = env['account.move'].search([
                ('journal_id', '=', journal.id),
                ('state', '=', 'draft')
            ])

            if draft_moves:
                _logger.warning(
                    f"  Found {len(draft_moves)} draft entries in journal '{journal.name}'. "
                    f"These entries should be reviewed and handled manually before archiving the journal. "
                    f"Skipping automatic deletion to preserve data integrity."
                )
                # DO NOT delete automatically - let the user do it manually

            moves_count = env['account.move'].search_count(
                [('journal_id', '=', journal.id)])
            if moves_count > 0:
                _logger.info(
                    f"  Journal has {moves_count} posted accounting entries, archiving journal instead of deleting")

            _logger.info("    Archiving Woodforest journal (name preserved for reinstall)")
            journal.write({'active': False})
            _logger.info("  Journal archived successfully")

        # 6. Remove configurations
        config = env['payrillium.config'].search([])
        if config:
            _logger.info("  Removing configurations")
            config.unlink()
            _logger.info("  Configurations removed")

        _logger.info("  Uninstallation cleanup completed successfully")
        env.cr.commit()

    except Exception as e:
        _logger.error(f"  Error during uninstallation cleanup: {str(e)}")
        raise e
