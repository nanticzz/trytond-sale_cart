# This file is part of sale_cart module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.model import ModelSQL, ModelView, fields
from trytond.wizard import Wizard, StateTransition, StateAction
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.pyson import Eval, PYSONEncoder
from trytond.config import config as config_
from decimal import Decimal
import sys

__all__ = ['SaleCart', 'CartCreateSale']

STATES = {
    'readonly': (Eval('state') != 'draft')
    }
DIGITS = config_.getint('product', 'price_decimal', default=4)


class SaleCart(ModelSQL, ModelView):
    'Sale Cart'
    __name__ = 'sale.cart'
    _rec_name = 'product'
    shop = fields.Many2One('sale.shop', 'Shop', required=True, domain=[
        ('id', 'in', Eval('context', {}).get('shops', [])),
        ])
    cart_date = fields.Date('Date',
        states=STATES, depends=['state'], required=True)
    party = fields.Many2One('party.party', 'Party',
        states=STATES)
    quantity = fields.Float('Quantity',
        digits=(16, 2), states=STATES, required=True)
    product = fields.Many2One('product.product', 'Product',
        domain=[('salable', '=', True)], states=STATES, required=True,
        context={
            'salable': True,
            })
    unit_price = fields.Numeric('Unit Price', digits=(16, DIGITS),
        states=STATES, required=True)
    unit_price_w_tax = fields.Function(fields.Numeric('Unit Price with Tax',
        digits=(16, DIGITS)), 'get_price_with_tax')
    untaxed_amount = fields.Function(fields.Numeric('Untaxed',
            digits=(16, Eval('currency_digits', 2)),
            depends=['quantity', 'product', 'unit_price', 'currency',
                'currency_digits'],
            ), 'get_untaxed_amount')
    amount_w_tax = fields.Function(fields.Numeric('Amount with Tax',
        digits=(16, DIGITS)), 'get_price_with_tax')
    currency = fields.Many2One('currency.currency', 'Currency',
        states=STATES, required=True, depends=['state'])
    currency_digits = fields.Function(fields.Integer('Currency Digits'),
        'on_change_with_currency_digits')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('wait', 'Waiting'),
        ('done', 'Done'),
    ], 'State', readonly=True, required=True)

    @classmethod
    def __setup__(cls):
        super(SaleCart, cls).__setup__()
        cls._order.insert(0, ('cart_date', 'DESC'))
        cls._order.insert(1, ('id', 'DESC'))
        cls._error_messages.update({
            'delete_done': ('Cart "%s - %s" is done. Can not delete.'),
            'add_party': ('Add a party in ID "%s" cart.'),
            })

    @staticmethod
    def default_shop():
        User = Pool().get('res.user')
        user = User(Transaction().user)
        return user.shop.id if user.shop else None

    @staticmethod
    def default_cart_date():
        Date = Pool().get('ir.date')
        return Date.today()

    @staticmethod
    def default_quantity():
        return 1

    @staticmethod
    def default_state():
        return 'draft'

    @staticmethod
    def default_currency():
        shop = Transaction().context.get('shop')
        company = Transaction().context.get('company')

        if shop:
            Shop = Pool().get('sale.shop')
            shop = Shop(shop)
            if hasattr(shop, 'esale_currency'):
                if shop.esale_currency:
                    return shop.esale_currency.id
        if company:
            Company = Pool().get('company.company')
            return Company(company).currency.id

    def _get_context_sale_price(self):
        User = Pool().get('res.user')

        context = {}
        if self.party:
            context['customer'] = self.party.id
        if self.party and self.party.sale_price_list:
            context['price_list'] = self.party.sale_price_list.id
        else:
            user = User(Transaction().user)
            context['price_list'] = user.shop.price_list.id if user.shop else None
        return context

    @fields.depends('product', 'unit', 'quantity', 'party', 'currency')
    def on_change_product(self):
        Product = Pool().get('product.product')

        if not self.product:
            return

        with Transaction().set_context(
                self._get_context_sale_price()):
            prices = Product.get_sale_price([self.product], self.quantity or 0)
            self.unit_price = prices[self.product.id]

    @fields.depends('product', 'quantity', 'unit', 'currency', 'party')
    def on_change_quantity(self):
        Product = Pool().get('product.product')

        if not self.product:
            return

        with Transaction().set_context(
                self._get_context_sale_price()):
            prices = Product.get_sale_price([self.product], self.quantity or 0)
            self.unit_price = prices[self.product.id]

    @fields.depends('currency')
    def on_change_with_currency_digits(self, name=None):
        if self.currency:
            return self.currency.digits
        return 2

    @fields.depends('quantity', 'product', 'unit_price', 'currency')
    def on_change_with_untaxed_amount(self, name=None):
        return self.get_untaxed_amount(name)

    @fields.depends('quantity', 'product', 'unit_price', 'untaxed_amount', 'currency')
    def on_change_with_unit_price_w_tax(self, name=None):
        return self.get_price_with_tax([self],
            ['unit_price_w_tax'])['unit_price_w_tax'][self.id]

    @fields.depends('quantity', 'product', 'unit_price', 'untaxed_amount', 'currency')
    def on_change_with_amount_w_tax(self, name=None):
        return self.get_price_with_tax([self],
            ['amount_w_tax'])['amount_w_tax'][self.id]

    def get_untaxed_amount(self, name):
        if self.quantity and self.unit_price:
            return self.currency.round(
                Decimal(str(self.quantity)) * self.unit_price)
        return Decimal('0.0')

    @classmethod
    def get_price_with_tax(cls, lines, names):
        pool = Pool()
        Tax = pool.get('account.tax')

        amount_w_tax = {}
        unit_price_w_tax = {}

        for line in lines:
            currency = line.currency
            if line.quantity and line.unit_price and line.product and line.untaxed_amount:
                taxes = line.product.customer_taxes_used

                if taxes and line.party and line.party.customer_tax_rule:
                    new_taxes = []
                    for tax in taxes:
                        tax_ids = line.party.customer_tax_rule.apply(tax, pattern={})
                        new_taxes = new_taxes + tax_ids
                    if new_taxes:
                        taxes = Tax.browse(new_taxes)

                tax_list = Tax.compute(taxes,
                    line.unit_price or Decimal('0.0'),
                    line.quantity or 0.0)
                tax_amount = sum([t['amount'] for t in tax_list], Decimal('0.0'))
                amount = line.untaxed_amount + tax_amount
                unit_price = amount / Decimal(str(line.quantity))
            else:
                amount = Decimal('0.0')
                unit_price = Decimal('0.0')

            amount_w_tax[line.id] = currency.round(amount)
            unit_price_w_tax[line.id] = currency.round(unit_price)

        result = {
            'amount_w_tax': amount_w_tax,
            'unit_price_w_tax': unit_price_w_tax,
            }
        for key in result.keys():
            if key not in names:
                del result[key]
        return result

    @classmethod
    def delete(cls, carts):
        for cart in carts:
            if cart.state == 'done':
                cls.raise_user_error('delete_done', (cart.party.rec_name,
                    cart.product.rec_name,))
        super(SaleCart, cls).delete(carts)

    def get_sale_line(self, sale):
        Line = Pool().get('sale.line')

        line = Line.get_sale_line_data(sale, self.product, self.quantity)
        line.unit_price = self.unit_price
        return line

    @classmethod
    def create_sale(cls, carts, values={}):
        '''
        Create sale from cart
        :param carts: list
        :param values: dict default values
        return obj list, error
        '''
        Sale = Pool().get('sale.sale')

        cart_group = {}
        sales = []

        # Group carts in party
        for cart in carts:
            if cart.state == 'done':
                continue

            if not cart.party:
                cls.raise_user_error('add_party', (cart.id,))

            if not cart.party in cart_group:
                cart_group[cart.party] = [cart]
            else:
                lines = cart_group.get(cart.party)
                lines.append(cart)
                cart_group[cart.party] = lines

        # Create sale and sale lines
        for party, pcarts in cart_group.iteritems():
            sale = Sale.get_sale_data(party)
            if values:
                for k, v in values.iteritems():
                    setattr(sale, k, v)
            lines = []
            for pcart in pcarts:
                line = pcart.get_sale_line(sale)
                lines.append(line)
            sale.lines = lines
            sales.append(sale)

        if sales:
            try:
                Sale.save(sales)
            except:
                exc_type, exc_value = sys.exc_info()[:2]
                return [], exc_value

        cls.write(carts, {'state': 'done'})
        return sales, None


class CartCreateSale(Wizard):
    'Create Sale from Cart'
    __name__ = 'cart.create_sale'
    start_state = 'create_sale'
    create_sale = StateTransition()
    open_ = StateAction('sale.act_sale_form')

    def transition_create_sale(self):
        Cart = Pool().get('sale.cart')
        carts = Cart.browse(Transaction().context['active_ids'])
        self.sales = Cart.create_sale(carts)
        return 'open_'

    def do_open_(self, action):
        sales, _ = self.sales
        ids = [sale.id for sale in sales]
        action['pyson_domain'] = PYSONEncoder().encode(
            [('id', 'in', ids)])
        return action, {}
