# Used to define module metadata, dependencies and asset loading
{
    'name': 'Woodforest Payment',
    'version': '18.0.1.5.5',
    'depends': ['point_of_sale', 'payment', 'account', 'web', 'pos_restaurant', 'hr'],
    'author': 'Woodforest',
    'category': 'Point of Sale',
    'license': 'OPL-1',
    'summary': 'Woodforest Payment Integration',
    'description': """
Integrates Odoo POS and Invoicing with Woodforest payment services to support:
- Physical payment terminals
- Tokenized payments
- Pay-by-link
- Refunds
- Session-scoped terminal selection

This module communicates with Woodforest external APIs. A valid account and API token are required for full functionality.
If the external service is unavailable, the module will display errors and will not process payments until connectivity is restored.

What's New in 1.4.9 (Stability & Compatibility Fixes):
- Fix: Removed _isOrderValid override that blocked payment validation when pos_loyalty or other modules call it before our validateOrder runs. Protection is now exclusively inside validateOrder.
- Fix: hasTerminal check in validateOrder now accepts 'payrillium' and name-based fallback in addition to 'woodforest', preventing 'Payment method not available' on some installations.
- Fix: Uninstall hook no longer renames the Woodforest journal with '(Archived - Woodforest Uninstalled)' suffix, preventing duplicate pos.payment.method records on reinstall.
- Fix: Wizard now cleans up old '(Archived...)' suffix from journal name on reinstall from older module versions.

What's New in 1.4.2 (Terminal UX Improvements):
- Faster Checkout: The shopping cart is now sent to the terminal instantly upon opening the Payment Screen, saving 2-3 seconds during payment validation.
- Fire-and-Forget Messages: Terminal success and decline messages no longer block the POS, allowing the cashier to immediately see the result and proceed.
- Custom Loading States: Added clear, descriptive step messages to the POS loading spinner ("Sending order...", "Awaiting tip...", "Processing payment...").
- Auto-Recovery: Clicking "Recover" in the navbar now instantly verifies the first interrupted transaction without requiring an extra click in a popup menu.
- Automatic Terminal Cleanup: Returning to the Product Screen automatically sends an abort command to the terminal, ensuring it's always ready for the next customer.
- Receipt Cleanups: Fixed an issue where the printed receipt could duplicate payment blocks from previous failed or cancelled transaction attempts.
- Performance: Eliminated redundant database queries and async bottlenecks in the payment validation loop, making the entire flow significantly faster and more reliable.
""",
    'post_init_hook': 'show_woodforest_wizard_once',
    'uninstall_hook': 'uninstall_cleanup_woodforest',
    'data': [
        'views/accounting_invoicing_action_sync_history.xml',
        'views/accounting_invoicing_payment_create_token_wizard.xml',
        'views/accounting_invoicing_payment_token_action.xml',
        'views/accounting_invoicing_payment_list_token.xml',
        'views/accounting_invoicing_list_actions_buttons.xml',
        'views/accounting_invoicing_buttons_payment.xml',
        'views/accounting_invoicing_payment_link_views.xml',
        'views/accounting_invoicing_configuration_set_paybylink_menu.xml',
        'views/accounting_invoicing_payment_link_wizard_patch.xml',
        'data/ir_cron_data.xml',

        'views/patch_payment_transaction_form.xml',
        'security/ir.model.access.csv',
        'security/woodforest_transaction_rules.xml',
        'views/pos_payment_method_views.xml',
        "data/data.xml",
        'views/res_config_settings.xml',
        'views/woodforest_terminal_views.xml',
        'views/woodforest_support_views.xml',
        'views/woodforest_assign_wizard_views.xml',
        'views/payment_provider_views_patch.xml',
        'views/patch_payment_transaction_list.xml',
        'views/pos_config_form_terminal.xml',
        'views/res_partner_payment_tokens.xml',
        'views/terminal_log_wizard_views.xml',
        'views/pos_make_payment_views.xml',
        'views/pos_order_views.xml',
        'views/woodforest_transaction_views.xml',
        'wizard/woodforest_refund_wizard_views.xml',
        'wizard/payment_action_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # MUST BE FIRST - Intercepts console.log/warn for production
            'pos_woodforest/static/src/js/console_interceptor.js',

            'pos_woodforest/static/src/js/utils.js',
            'pos_woodforest/static/src/css/status_colors.scss',
            'pos_woodforest/static/src/js/payment_link_wizard.js',
            'pos_woodforest/static/src/js/notification_handler.js',
            'pos_woodforest/static/src/css/loader.css',
            'pos_woodforest/static/src/css/payment_link_chatter.css',
            'pos_woodforest/static/src/js/sync_button.js',
            'pos_woodforest/static/src/xml/sync_button.xml',
            'pos_woodforest/static/src/js/loading_indicator_patch.js',
            'pos_woodforest/static/src/js/chatter_payment_link.js',
            'pos_woodforest/static/src/css/token_wizard.css',
            'pos_woodforest/static/src/css/pay_by_token_form_fix.css',
            'pos_woodforest/static/src/css/activation_panel.css',
            'pos_woodforest/static/src/css/terminal_list_fix.css',
            'pos_woodforest/static/src/css/support_page.css',
            'pos_woodforest/static/src/js/support_page.js',
            'pos_woodforest/static/src/xml/support_page.xml',
            'pos_woodforest/static/src/css/faq_page.css',
            'pos_woodforest/static/src/js/faq_page.js',
            'pos_woodforest/static/src/xml/faq_page.xml',
            'pos_woodforest/static/src/js/terminal_list_banner.js',
            'pos_woodforest/static/src/js/terminal_pos_selector.js',
            'pos_woodforest/static/src/xml/terminal_pos_selector.xml',
            'pos_woodforest/static/src/js/store_tabs.js',
        ],
        'point_of_sale._assets_pos': [
            'pos_woodforest/static/src/xml/chrome_patch.xml',
            'pos_woodforest/static/src/xml/pos_terminal_status.xml',
            'pos_woodforest/static/src/js/api_service.js',
            'pos_woodforest/static/src/js/config_loader.js',
            'pos_woodforest/static/src/js/order_models.js',
            'pos_woodforest/static/src/js/order_patch.js',
            'pos_woodforest/static/src/js/order_receipt_patch.js',
            'pos_woodforest/static/src/js/payment_handler.js',
            'pos_woodforest/static/src/js/payment_screen.js',
            'pos_woodforest/static/src/xml/payment_screen_patch.xml',
            'pos_woodforest/static/src/js/payment_lines_patch.js',
            'pos_woodforest/static/src/js/patch_pos_store.js',
            'pos_woodforest/static/src/js/product_screen.js',
            'pos_woodforest/static/src/js/order_summary.js',
            'pos_woodforest/static/src/js/setup_config.js',
            'pos_woodforest/static/src/js/terminal_service.js',
            'pos_woodforest/static/src/js/ticket_screen.js',
            'pos_woodforest/static/src/js/utils.js',
            'pos_woodforest/static/src/css/woodforest.css',
            'pos_woodforest/static/src/js/navbar_patch.js',
            'pos_woodforest/static/src/xml/order_receipt_template.xml',
            'pos_woodforest/static/src/js/saved_card_selection_popup.js',
            'pos_woodforest/static/src/xml/saved_card_selection_popup.xml',
            'pos_woodforest/static/src/js/recovery_popup.js',
            'pos_woodforest/static/src/xml/control_buttons_patch.xml',
            'pos_woodforest/static/src/js/receipt_screen_patch.js',
            'pos_woodforest/static/src/js/close_pos_patch.js',
            'pos_woodforest/static/src/js/transaction_detail_popup.js',
            'pos_woodforest/static/src/xml/transaction_detail_popup.xml',
            'pos_woodforest/static/src/js/transaction_list_screen.js',
            'pos_woodforest/static/src/xml/transaction_list_screen.xml',
            'pos_woodforest/static/src/xml/navbar_transactions.xml',

        ],
    },
    'icon': '/pos_woodforest/static/description/icon.png',
    'images': [
        'static/description/Woodofrest-thumbnail.mp4',
    ],
    'installable': True,

    'application': True,
    'auto_install': False,
}
