from odoo import models, fields

class PayrilliumAsyncJob(models.Model):
    _name = "payrillium.async.job"
    _description = "Payrillium Async Job"
    _order = "create_date desc"

    job_id = fields.Char(required=True, index=True)
    execution_id = fields.Char(index=True)
    endpoint = fields.Char(index=True)

    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("running", "Running"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        required=True,
        default="pending",
        index=True,
    )

    result_data = fields.Json(string="Result Data")
    error_message = fields.Text()

    _sql_constraints = [
        ("job_id_unique", "unique(job_id)", "The async job id must be unique."),
    ]
