/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";

export class FaqPage extends Component {}

FaqPage.template = "pos_woodforest.FaqPage";

registry.category("actions").add("pos_woodforest.FaqPage", FaqPage);
