# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PayrilliumTipOption(models.Model):
    _name = "payrillium.tip.option"
    _description = "Woodforest Terminal Tip Option (amount or percent preset)"
    _order = "sequence, id"

    terminal_id = fields.Many2one(
        "payrillium.terminal",
        string="Terminal",
        required=True,
        ondelete="cascade",
    )
    option_type = fields.Selection(
        [
            ("amount", "Fixed amount ($)"),
            ("percent", "Percentage (%)"),
        ],
        string="Type",
        required=True,
        default="amount",
    )
    sequence = fields.Integer(string="Sequence", default=10)
    value = fields.Float(
        string="Value",
        required=True,
        digits=(6, 2),
        help=(
            "For Amount mode: enter the dollar value (e.g. 5 for $5.00).\n"
            "For Percentage mode: enter a number between 1 and 100 (e.g. 15 for 15%, 1.4 for 1.4%). "
            "Do NOT include the % symbol."
        ),
    )

    @api.onchange("value", "option_type")
    def _onchange_value(self):
        if self.option_type == "percent":
            if self.value and not (1.0 <= self.value <= 100.0):
                msg = (
                    f"Percentage must be between 1 and 100 (e.g. 15 for 15%). "
                    f"Got: {self.value}. Value has been cleared."
                )
                self.value = 0.0
                return {"warning": {"title": "Invalid Percentage", "message": msg}}
        elif self.option_type == "amount":
            if self.value and self.value < 0:
                msg = f"Amount must be greater than 0. Got: {self.value}. Value has been cleared."
                self.value = 0.0
                return {"warning": {"title": "Invalid Amount", "message": msg}}

    @api.constrains("option_type", "value")
    def _check_value(self):
        for rec in self:
            if rec.option_type == "percent":
                if not (1.0 <= rec.value <= 100.0):
                    raise ValidationError(
                        f"Percentage tip value must be between 1 and 100 "
                        f"(e.g. 15 for 15%). Got: {rec.value}. "
                        f"Do not use decimal fractions like 0.15 — enter 15 instead."
                    )
            elif rec.option_type == "amount":
                if rec.value <= 0:
                    raise ValidationError(
                        f"Amount tip value must be greater than 0. Got: {rec.value}."
                    )

