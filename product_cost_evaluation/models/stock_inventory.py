# -*- coding: utf-8 -*-

from odoo import fields, models, api, _


class StockInventoryLine(models.Model):
    _inherit = 'stock.inventory.line'

    purchase_evaluation_id = fields.Many2one(
        'product.cost.evaluation.history',
    )
