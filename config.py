from odoo import tools

PAYMENT_METHOD_NAME = "Woodforest"
PAYMENT_METHOD_COLOR = "#B3D87F"
PAYMENT_METHOD_ICON = "/pos_woodforest/static/description/icon.png"

ENVIRONMENT = "dev"

# prod

SHOPNET_API_URLS = {
    "dev": "http://sn.mirillium.io",
    "prod": "https://sn.mirillium.net",
}


SHOPNET_API_URL = SHOPNET_API_URLS[ENVIRONMENT]


WF_MIRILLIUM_API_URLS = {
    "dev": "http://wf.mirillium.io",
    "prod": "https://wf.mirillium.net",
}


WF_MIRILLIUM_API_URL = WF_MIRILLIUM_API_URLS[ENVIRONMENT]

# API_BASE_URL = "https://cloud.mirillium.io/cloud-payment-vpc//"
CLOUD_MIRILLIUM_API_URLS = {
    "dev": "https://mqtt-p100.mirillium.io/cloud-payment-vpc/",
    "prod": "https://cloud.mirillium.net/cloud-payment-vpc/",
}

CLOUD_MIRILLIUM_API_URL = CLOUD_MIRILLIUM_API_URLS[ENVIRONMENT]

MIRILLIUM_PRIVATE_KEYS = {
    "dev": "mirillium_test_private_key",
    "prod": "mirillium_prod_private_key",
}

MIRILLIUM_PRIVATE_KEY = MIRILLIUM_PRIVATE_KEYS[ENVIRONMENT]

BASE_VERSION = "1.5.5"
version = f"odoo 18 pos_woodforest {BASE_VERSION} environment: {ENVIRONMENT}"

# Apply for activation: external form URL (e.g. Google Form for testing)
APPLY_FOR_ACTIVATION_URL = "https://www.woodforest.com/about-us/contact-us/"
