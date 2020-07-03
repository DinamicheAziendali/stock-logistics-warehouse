# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.addons import decimal_precision as dp


class StockInventoryEvaluation(models.Model):
    _name = 'stock.inventory.evaluation'

    def action_cancel_draft(self):
        self.write({
            'line_ids': [(5,)],
            'state': 'draft'
        })

    @api.model
    def _default_location_id(self):
        company_user = self.env.user.company_id
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', company_user.id)], limit=1)
        if warehouse:
            return warehouse.lot_stock_id.id
        else:
            raise UserError(
                _('You must define a warehouse for the company: %s.') % (
                    company_user.name,))

    name = fields.Char()
    date = fields.Datetime(string='Date', required=True)
    line_history_ids = fields.One2many(
        'product.cost.evaluation.history', 'inventory_evaluation_id',
        string='Inventories',
        copy=True, readonly=False,
        states={'done': [('readonly', True)]})
    line_ids = fields.One2many(
        'stock.inventory.evaluation.line', 'inventory_id', string='Inventories',
        copy=True, readonly=False,
        states={'done': [('readonly', True)]})
    company_id = fields.Many2one(
        'res.company', 'Company',
        readonly=True, index=True, required=True,
        states={'draft': [('readonly', False)]},
        default=lambda self: self.env['res.company']._company_default_get(
            'stock.inventory'))
    state = fields.Selection(string='Status', selection=[
        ('draft', 'Draft'),
        ('cancel', 'Cancelled'),
        ('done', 'Done')],
                             copy=False, index=True, readonly=True,
                             default='draft')
    location_id = fields.Many2one(
        'stock.location', 'Inventoried Location',
        readonly=True, required=True,
        states={'draft': [('readonly', False)]},
        default=_default_location_id)

    @api.multi
    def action_cancel(self):
        for stock in self:
            stock.state = 'cancel'

    @api.multi
    def action_done(self):
        for stock in self:
            stock.state = 'done'

    def _get_inventory_lines_values_new(self):
        product_model = self.env['product.product']
        product_ids = product_model.search([('type', '=', 'product')])
        res = product_ids._compute_quantities_at_date(self.location_id,
                                                      self.date)
        self.create_inventory_line(res)
        self.get_last_purchase_cost()
        self.get_average_cost()

    def create_inventory_line(self, res):
        product_model = self.env['product.product']
        product_cost_eval_history_model = self.env[
            'product.cost.evaluation.history']
        for element in res:
            product = product_model.browse(element)
            vals = {'name': self.name,
                    'product_id': element,
                    'product_qty': res[element]['qty_available'],
                    'location_id': self.location_id.id,
                    'date_evaluation': self.date,
                    'standard_cost': product.standard_price,
                    'list_price': product.list_price,
                    'inventory_evaluation_id': self.id}
            product_cost_eval_history_model.create(vals)

    def get_last_purchase_cost(self):
        for line in self.line_history_ids:
            domain = [
                ('company_id', '=', self.env.user.company_id.id),
                ('date', '<=', self.date),
                ('state', '=', 'done'),
                ('purchase_line_id', '!=', False),
                ('product_id', '=', line.product_id.id),
                '|',
                ('location_id.usage', '=', 'internal'),
                ('location_dest_id.usage', '=', 'internal'),
            ]
            move_obj = self.env['stock.move']
            move_ids = move_obj.search(domain)
            for move in move_ids:
                if move.purchase_line_id.invoice_lines:
                    inv_lines = move.purchase_line_id.invoice_lines.filtered(
                        lambda x: x.invoice_id.state in ['open', 'paid'])
                    if inv_lines:
                        if move.purchase_line_id.qty_invoiced > 0:
                            line_sorted = inv_lines.sorted(
                                key=lambda l: l.invoice_id.date_invoice,
                                reverse=True)
                            last_purchase_cost = line_sorted[0].price_unit
                            if last_purchase_cost:
                                line.last_purchase_cost = last_purchase_cost
                                continue

    def get_average_cost(self):
        count_product = {}
        for line in self.line_history_ids:
            if line.product_id.id not in count_product.keys():
                total_qty, total_cost = self.compute_average_cost(
                    line.product_id)
                if total_qty > 0:
                    average_purchase_cost = total_cost / total_qty
                    count_product[line.product_id.id] = average_purchase_cost
                else:
                    count_product[line.product_id.id] = 0
            line.average_purchase_cost = count_product[line.product_id.id]

    def compute_average_cost(self, product):
        total_qty = 0
        total_cost = 0
        domain = [
            ('company_id', '=', self.env.user.company_id.id),
            ('date', '<=', self.date),
            ('state', '=', 'done'),
            ('purchase_line_id', '!=', False),
            ('product_id', '=', product.id),
            '|',
            ('location_id.usage', '=', 'internal'),
            ('location_dest_id.usage', '=', 'internal'),
        ]
        move_obj = self.env['stock.move']
        move_ids = move_obj.search(domain)
        for move in move_ids:
            if move.purchase_line_id.invoice_lines:
                inv_lines = move.purchase_line_id.invoice_lines.filtered(
                    lambda x: x.invoice_id.state in ['open', 'paid'])
                if inv_lines:
                    for line in inv_lines:
                        total_cost += line.price_subtotal
                        total_qty += line.quantity
        return total_qty, total_cost

    def action_start(self):
        for inventory in self.filtered(lambda x: x.state not in ('done', 'cancel')):
            if not inventory.line_ids:
                inventory._get_inventory_lines_values_new()
        return True

    @api.multi
    def product_cost_history_evaluation(self):
        incomplete_invoice_msg = _('Incomplete invoice Data')
        for inventory in self:
            product_list = []
            for line in inventory.line_ids:
                product_id = line.product_id
                product_list.append(product_id)
                vals = dict(
                    date_evaluation=inventory.date,  # todo date_inventory
                    product_id=product_id.id,
                    standard_cost=product_id.standard_price,
                    product_qty=line.product_qty,
                    list_price=product_id.list_price,
                    name=inventory.name,
                )
                incoming_domain = [
                    ('company_id', '=', self.env.user.company_id.id),
                    ('date', '<=', self.date),  # todo self.date_inventory or
                    ('state', '=', 'done'),
                    ('product_id', '=', line.product_id.id),
                    ('location_id.usage', '!=', 'internal'),
                    ('location_dest_id.usage', '=', 'internal'),
                ]
                all_domain = [
                    ('company_id', '=', self.env.user.company_id.id),
                    ('date', '<=', self.date),  # todo self.date_inventory or
                    ('state', '=', 'done'),
                    ('product_id', '=', line.product_id.id),
                    '|',
                    ('location_id.usage', '=', 'internal'),
                    ('location_dest_id.usage', '=', 'internal'),
                ]
                res_incoming = self.price_calculation(line, 'average',
                                                      incoming_domain)
                price_amount = product_qty = purchase_amount = 0
                for match in res_incoming:
                    price_amount += match[1] * match[2]
                    product_qty += match[1]
                    purchase_amount += match[1] * match[4]
                if product_qty != 0.0:
                    if not match[5]:
                        vals['average_cost'] = price_amount / product_qty
                    else:
                        vals['name'] += incomplete_invoice_msg
                    vals['average_purchase_cost'] = (
                        purchase_amount / product_qty)
                # fifo
                res_incoming = self.price_calculation(
                    line, 'fifo', incoming_domain)
                price_amount = product_qty = purchase_amount = 0
                for match in res_incoming:
                    price_amount += match[1] * match[2]
                    product_qty += match[1]
                    purchase_amount += match[1] * match[4]
                if product_qty != 0.0:
                    if not match[5]:
                        vals['fifo_cost'] = price_amount / product_qty
                    else:
                        if incomplete_invoice_msg not in vals['name']:
                            vals['name'] += incomplete_invoice_msg
                    vals['fifo_purchase_cost'] = purchase_amount / product_qty
                # lifo needs incoming and outgoing moves
                res_all = self.price_calculation(line, 'lifo', all_domain)
                price_amount = product_qty = purchase_amount = 0
                for match in res_all:
                    price_amount += match[1] * match[2]
                    product_qty += match[1]
                    purchase_amount += match[1] * match[4]
                if product_qty != 0.0:
                    if not match[5]:
                        vals['lifo_cost'] = price_amount / product_qty
                    else:
                        if incomplete_invoice_msg not in vals['name']:
                            vals['name'] += incomplete_invoice_msg
                    vals['lifo_purchase_cost'] = purchase_amount / product_qty
                vals['inventory_evaluation_id'] = inventory.id
                if res_all:
                    if len(res_all[0]) == 7:
                        vals['last_purchase_cost'] = res_all[0][6]
                    else:
                        vals['last_purchase_cost'] = res_all[0][5]
                res = self.env["product.cost.evaluation.history"].create(vals)
                line.purchase_evaluation_id = res.id

    @api.multi
    def price_calculation(self, line, evaluation_type, domain):
        order = 'date desc, id desc'
        move_obj = self.env['stock.move']
        move_ids = move_obj.search(domain, order=order)
        tuples = []
        qty_to_go = line.product_qty
        older_qty = line.product_qty
        invoice_data_incomplete = False
        flag = False
        for move in move_ids:
            for move_line in move.move_line_ids:
                # Convert to UoM of product each time
                uom_from = move.product_uom
                qty_from = move_line.qty_done
                product_qty = uom_from._compute_quantity(
                    qty_from, move.product_id.uom_id)
                # Get price from purchase line
                purchase_price_unit = 0
                price_unit = 0
                last_purchase_cost = 0
                if move.purchase_line_id:
                    if move.purchase_line_id.invoice_lines:
                        inv_lines = move.purchase_line_id.invoice_lines.filtered(
                            lambda x: x.invoice_id.state in ['open', 'paid'])
                        if inv_lines:
                            price_unit = sum(
                                l.price_subtotal for l in inv_lines) / sum(
                                l.quantity for l in inv_lines)
                            # check for last purchase cost
                            if move.purchase_line_id.qty_invoiced > 0:
                                line_sorted = inv_lines.sorted(
                                    key=lambda l: l.invoice_id.date_invoice,
                                    reverse=True)
                                last_purchase_cost = line_sorted[0].price_unit
                    else:
                        invoice_data_incomplete = True
                    purchase_price_unit = move.purchase_line_id.price_subtotal / move.purchase_line_id.product_qty
                if evaluation_type == 'fifo':
                    if qty_to_go - product_qty >= 0:
                        tuples.append((move.product_id.id, product_qty,
                                       price_unit, qty_from,
                                       purchase_price_unit,
                                       invoice_data_incomplete,
                                       last_purchase_cost))
                        qty_to_go -= product_qty
                    else:
                        tuples.append((
                            move.product_id.id, qty_to_go, price_unit,
                            qty_from * qty_to_go / product_qty,
                            purchase_price_unit,
                            invoice_data_incomplete,
                            last_purchase_cost))
                        flag = True
                        break
                if evaluation_type == 'lifo':
                    # sale
                    if move.location_id.usage == 'internal' and \
                        move.location_dest_id.usage != 'internal':
                        older_qty += product_qty
                    # purchase
                    if move.location_id.usage != 'internal' and \
                        move.location_dest_id.usage == 'internal':
                        older_qty -= product_qty
                        if qty_to_go > older_qty > 0:
                            tuples.append((move.product_id.id,
                                           qty_to_go - older_qty, price_unit,
                                           qty_from, purchase_price_unit,
                                           invoice_data_incomplete,
                                           last_purchase_cost))
                            qty_to_go = older_qty
                        elif qty_to_go > older_qty <= 0:
                            tuples.append((move.product_id.id, qty_to_go,
                                           price_unit,
                                           qty_from * qty_to_go / product_qty,
                                           purchase_price_unit,
                                           invoice_data_incomplete,
                                           last_purchase_cost))
                            flag = True
                            break
                if evaluation_type == 'average':
                    tuples.append((move.product_id.id, product_qty,
                                   price_unit, qty_from, purchase_price_unit,
                                   invoice_data_incomplete,
                                   last_purchase_cost))
            if flag:
                break
        return tuples


class InventoryLine(models.TransientModel):
    _name = "stock.inventory.evaluation.line"
    _description = "Inventory Evaluation Line"
    _order = "product_id, inventory_id, location_id, prod_lot_id"

    inventory_id = fields.Many2one(
        'stock.inventory.evaluation', 'Inventory', index=True,
        ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', 'Product',
        domain=[('type', '=', 'product')],
        index=True, required=True)
    product_uom_id = fields.Many2one(
        'uom.uom', 'Product Unit of Measure',
        required=True)
    product_uom_category_id = fields.Many2one(string='Uom category',
                                              related='product_uom_id.category_id',
                                              readonly=True)
    product_qty = fields.Float(
        'Checked Quantity',
        digits=dp.get_precision('Product Unit of Measure'), default=0)
    location_id = fields.Many2one(
        'stock.location', 'Location',
        index=True, required=True)
    prod_lot_id = fields.Many2one(
        'stock.production.lot', 'Lot/Serial Number',
        domain="[('product_id','=',product_id)]")
    company_id = fields.Many2one(
        'res.company', 'Company', related='inventory_id.company_id',
        index=True, readonly=True, store=True)
    state = fields.Selection(
        'Status', related='inventory_id.state', readonly=True)
    theoretical_qty = fields.Float(
        'Theoretical Quantity', compute='_compute_theoretical_qty',
        digits=dp.get_precision('Product Unit of Measure'), readonly=True,
        store=True)
    inventory_location_id = fields.Many2one(
        'stock.location', 'Inventory Location',
        related='inventory_id.location_id', related_sudo=False, readonly=False)
    product_tracking = fields.Selection('Tracking',
                                        related='product_id.tracking',
                                        readonly=True)

    @api.one
    @api.depends('location_id', 'product_id', 'product_uom_id',
                 'company_id', 'prod_lot_id')
    def _compute_theoretical_qty(self):
        if not self.product_id:
            self.theoretical_qty = 0
            return
        theoretical_qty = self.product_id.get_theoretical_quantity(
            self.product_id.id,
            self.location_id.id,
            to_uom=self.product_uom_id.id,
        )
        self.theoretical_qty = theoretical_qty

    @api.onchange('product_id')
    def _onchange_product(self):
        res = {}
        # If no UoM or incorrect UoM put default one from product
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
            res['domain'] = {'product_uom_id': [
                ('category_id', '=', self.product_id.uom_id.category_id.id)]}
        return res

    @api.onchange('product_id', 'location_id', 'product_uom_id', 'prod_lot_id')
    def _onchange_quantity_context(self):
        if self.product_id and self.location_id and self.product_id.uom_id.category_id == self.product_uom_id.category_id:  # TDE FIXME: last part added because crash
            self._compute_theoretical_qty()
            self.product_qty = self.theoretical_qty

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if 'product_id' in values and 'product_uom_id' not in values:
                values['product_uom_id'] = self.env['product.product'].browse(
                    values['product_id']).uom_id.id
        res = super(InventoryLine, self).create(vals_list)
        return res

    @api.constrains('product_id')
    def _check_product_id(self):
        """ As no quants are created for consumable products, it should not be possible do adjust
        their quantity.
        """
        for line in self:
            if line.product_id.type != 'product':
                raise ValidationError(_(
                    "You can only adjust storable products.") + '\n\n%s -> %s' % (
                                          line.product_id.display_name,
                                          line.product_id.type))
