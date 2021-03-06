# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import datetime
from decimal import Decimal
from itertools import groupby

from sql import operators, Literal
from sql.conditionals import Coalesce

from trytond.model import ModelSQL, ModelView, Workflow, fields, \
        sequence_ordered
from trytond.pool import Pool
from trytond.pyson import Eval, If, Bool
from trytond.transaction import Transaction
from trytond.wizard import Wizard, StateView, StateAction, StateTransition, \
        Button

from trytond.modules.product import price_digits

__all__ = ['Subscription', 'Line', 'LineConsumption',
    'CreateLineConsumption', 'CreateLineConsumptionStart',
    'CreateSubscriptionInvoice', 'CreateSubscriptionInvoiceStart']
STATES = [
    ('draft', 'Draft'),
    ('quotation', 'Quotation'),
    ('running', 'Running'),
    ('closed', 'Closed'),
    ('canceled', 'Canceled'),
    ]


class Subscription(Workflow, ModelSQL, ModelView):
    "Subscription"
    __name__ = 'sale.subscription'
    _rec_name = 'number'

    company = fields.Many2One(
        'company.company', "Company", required=True, select=True,
        states={
            'readonly': Eval('state') != 'draft',
            },
        domain=[
            ('id', If(Eval('context', {}).contains('company'), '=', '!='),
                Eval('context', {}).get('company', -1)),
            ],
        depends=['state'],
        help="Make the subscription belong to the company.")

    number = fields.Char(
        "Number", readonly=True, select=True,
        help="The main identification of the subscription.")
    # TODO revision
    reference = fields.Char(
        "Reference", select=True,
        help="The identification of an external origin.")
    description = fields.Char("Description",
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])

    party = fields.Many2One(
        'party.party', "Party", required=True,
        states={
            'readonly': ((Eval('state') != 'draft')
                | (Eval('lines', [0]) & Eval('party'))),
            },
        depends=['state'],
        help="The party who subscribes.")
    invoice_address = fields.Many2One(
        'party.address', "Invoice Address",
        domain=[
            ('party', '=', Eval('party')),
            ],
        states={
            'readonly': Eval('state') != 'draft',
            'required': ~Eval('state').in_(['draft']),
            },
        depends=['party', 'state'])
    payment_term = fields.Many2One(
        'account.invoice.payment_term', "Payment Term",
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])

    currency = fields.Many2One(
        'currency.currency', "Currency", required=True,
        states={
            'readonly': ((Eval('state') != 'draft')
                | (Eval('lines', [0]) & Eval('currency', 0))),
            },
        depends=['state'])

    start_date = fields.Date(
        "Start Date", required=True,
        states={
            'readonly': ((Eval('state') != 'draft')
                | Eval('next_invoice_date')),
            },
        depends=['state', 'next_invoice_date'])
    end_date = fields.Date(
        "End Date",
        domain=['OR',
            ('end_date', '>=', If(
                    Bool(Eval('start_date')),
                    Eval('start_date', datetime.date.min),
                    datetime.date.min)),
            ('end_date', '=', None),
            ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['start_date', 'state'])

    invoice_recurrence = fields.Many2One(
        'sale.subscription.recurrence.rule.set', "Invoice Recurrence",
        required=True,
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    invoice_start_date = fields.Date("Invoice Start Date",
        states={
            'readonly': ((Eval('state') != 'draft')
                | Eval('next_invoice_date')),
            },
        depends=['state', 'next_invoice_date'])
    next_invoice_date = fields.Date("Next Invoice Date", readonly=True)

    lines = fields.One2Many(
        'sale.subscription.line', 'subscription', "Lines",
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])

    state = fields.Selection(
        STATES, "State", readonly=True, required=True,
        help="The current state of the subscription.")

    @classmethod
    def __setup__(cls):
        super(Subscription, cls).__setup__()
        cls._order = [
            ('start_date', 'DESC'),
            ('id', 'DESC'),
            ]
        cls._transitions |= set((
                ('draft', 'canceled'),
                ('draft', 'quotation'),
                ('quotation', 'canceled'),
                ('quotation', 'draft'),
                ('quotation', 'running'),
                ('running', 'draft'),
                ('running', 'closed'),
                ('canceled', 'draft'),
                ))
        cls._buttons.update({
                'cancel': {
                    'invisible': ~Eval('state').in_(['draft', 'quotation']),
                    'icon': 'tryton-cancel',
                    },
                'draft': {
                    'invisible': Eval('state').in_(['draft', 'closed']),
                    'icon': If(Eval('state') == 'canceled',
                        'tryton-clear', 'tryton-go-previous'),
                    },
                'quote': {
                    'invisible': Eval('state') != 'draft',
                    'readonly': ~Eval('lines', []),
                    'icon': 'tryton-go-next',
                    },
                'run': {
                    'invisible': Eval('state') != 'quotation',
                    'icon': 'tryton-go-next',
                    },
                })

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def default_currency(cls):
        pool = Pool()
        Company = pool.get('company.company')
        company = cls.default_company()
        if company:
            return Company(company).currency.id

    @classmethod
    def default_state(cls):
        return 'draft'

    @fields.depends('party')
    def on_change_party(self):
        self.invoice_address = None
        if self.party:
            self.invoice_address = self.party.address_get(type='invoice')
            self.payment_term = self.party.customer_payment_term

    @classmethod
    def set_number(cls, subscriptions):
        pool = Pool()
        Sequence = pool.get('ir.sequence')
        Config = pool.get('sale.configuration')

        config = Config(1)
        for subscription in subscriptions:
            if subscription.number:
                continue
            subscription.number = Sequence.get_id(
                config.subscription_sequence.id)
        cls.save(subscriptions)

    def compute_next_invoice_date(self):
        start_date = self.invoice_start_date or self.start_date
        date = self.next_invoice_date or self.start_date
        rruleset = self.invoice_recurrence.rruleset(start_date)
        dt = datetime.datetime.combine(date, datetime.time())
        inc = (start_date == date) and not self.next_invoice_date
        next_date = rruleset.after(dt, inc=inc)
        return next_date.date()

    @classmethod
    def copy(cls, subscriptions, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('state', 'draft')
        default.setdefault('number')
        default.setdefault('next_invoice_date')
        return super(Subscription, cls).copy(subscriptions, default=default)

    @classmethod
    @ModelView.button
    @Workflow.transition('canceled')
    def cancel(cls, subscriptions):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, subscriptions):
        pass

    @classmethod
    @ModelView.button
    @Workflow.transition('quotation')
    def quote(cls, subscriptions):
        cls.set_number(subscriptions)

    @classmethod
    @ModelView.button
    @Workflow.transition('running')
    def run(cls, subscriptions):
        pool = Pool()
        Line = pool.get('sale.subscription.line')
        lines = []
        for subscription in subscriptions:
            if not subscription.next_invoice_date:
                subscription.next_invoice_date = (
                    subscription.compute_next_invoice_date())
            for line in subscription.lines:
                if (line.next_consumption_date is None
                        and not line.consumed):
                    line.next_consumption_date = (
                        line.compute_next_consumption_date())
            lines.extend(subscription.lines)
        Line.save(lines)
        cls.save(subscriptions)

    @classmethod
    def process(cls, subscriptions):
        to_close = []
        for subscription in subscriptions:
            if all(l.next_consumption_date is None
                    for l in subscription.lines):
                to_close.append(subscription)
        cls.close(to_close)

    @classmethod
    @Workflow.transition('closed')
    def close(cls, subscriptions):
        pass

    @classmethod
    def generate_invoice(cls, date=None):
        pool = Pool()
        Date = pool.get('ir.date')
        Consumption = pool.get('sale.subscription.line.consumption')
        Invoice = pool.get('account.invoice')
        InvoiceLine = pool.get('account.invoice.line')

        if date is None:
            date = Date.today()

        consumptions = Consumption.search([
                ('invoice_line', '=', None),
                ('line.subscription.next_invoice_date', '<=', date),
                ('line.subscription.state', 'in', ['running', 'closed']),
                ],
            order=[
                ('line.subscription.id', 'DESC'),
                ])

        def keyfunc(consumption):
            return consumption.line.subscription
        invoices = {}
        lines = {}
        for subscription, consumptions in groupby(consumptions, key=keyfunc):
            invoices[subscription] = invoice = subscription._get_invoice()
            lines[subscription] = Consumption.get_invoice_lines(
                consumptions, invoice)

        all_invoices = invoices.values()
        Invoice.save(all_invoices)

        all_invoice_lines = []
        for subscription, invoice in invoices.iteritems():
            invoice_lines, _ = lines[subscription]
            for line in invoice_lines:
                line.invoice = invoice
            all_invoice_lines.extend(invoice_lines)
        InvoiceLine.save(all_invoice_lines)

        all_consumptions = []
        for values in lines.itervalues():
            for invoice_line, consumptions in zip(*values):
                for consumption in consumptions:
                    assert not consumption.invoice_line
                    consumption.invoice_line = invoice_line
                    all_consumptions.append(consumption)
        Consumption.save(all_consumptions)

        Invoice.update_taxes(all_invoices)

        subscriptions = cls.search([
                ('next_invoice_date', '<=', date),
                ])
        for subscription in subscriptions:
            if subscription.state == 'running':
                while subscription.next_invoice_date <= date:
                    subscription.next_invoice_date = (
                        subscription.compute_next_invoice_date())
            else:
                subscription.next_invoice_date = None
        cls.save(subscriptions)

    def _get_invoice(self):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        invoice = Invoice(
            company=self.company,
            type='out',
            party=self.party,
            invoice_address=self.invoice_address,
            currency=self.currency,
            account=self.party.account_receivable,
            )
        invoice.on_change_type()
        invoice.payment_term = self.payment_term
        return invoice


class Line(sequence_ordered(), ModelSQL, ModelView):
    "Subscription Line"
    __name__ = 'sale.subscription.line'
    _rec_name = 'description'

    subscription = fields.Many2One(
        'sale.subscription', "Subscription", required=True, select=True,
        ondelete='CASCADE',
        states={
            'readonly': ((Eval('subscription_state') != 'draft')
                & Bool(Eval('subscription'))),
            },
        depends=['subscription_state'],
        help="Add the line below the subscription.")
    subscription_state = fields.Function(
        fields.Selection(STATES, "Subscription State"),
        'on_change_with_subscription_state')
    subscription_start_date = fields.Function(
        fields.Date("Subscription Start Date"),
        'on_change_with_subscription_start_date')
    subscription_end_date = fields.Function(
        fields.Date("Subscription End Date"),
        'on_change_with_subscription_end_date')

    service = fields.Many2One(
        'sale.subscription.service', "Service", required=True,
        states={
            'readonly': Eval('subscription_state') != 'draft',
            },
        depends=['subscription_state'])
    description = fields.Text("Description", required=True,
        states={
            'readonly': Eval('subscription_state') != 'draft',
            },
        depends=['subscription_state'])

    quantity = fields.Float(
        "Quantity", digits=(16, Eval('unit_digits', 2)),
        states={
            'readonly': Eval('subscription_state') != 'draft',
            'required': Bool(Eval('consumption_recurrence')),
            },
        depends=[
            'unit_digits', 'subscription_state', 'consumption_recurrence'])
    unit = fields.Many2One(
        'product.uom', "Unit", required=True,
        states={
            'readonly': Eval('subscription_state') != 'draft',
            },
        domain=[
            If(Bool(Eval('service_unit_category')),
                ('category', '=', Eval('service_unit_category')),
                ('category', '!=', -1)),
            ],
        depends=['subscription_state', 'service_unit_category'])
    unit_digits = fields.Function(
        fields.Integer("Unit Digits"), 'on_change_with_unit_digits')
    service_unit_category = fields.Function(
        fields.Many2One('product.uom.category', "Service Unit Category"),
        'on_change_with_service_unit_category')

    unit_price = fields.Numeric(
        "Unit Price", digits=price_digits,
        states={
            'readonly': Eval('subscription_state') != 'draft',
            },
        depends=['subscription_state'])

    consumption_recurrence = fields.Many2One(
        'sale.subscription.recurrence.rule.set', "Consumption Recurrence",
        states={
            'readonly': Eval('subscription_state') != 'draft',
            },
        depends=['subscription_state'])
    consumption_delay = fields.TimeDelta(
        "Consumption Delay",
        states={
            'readonly': Eval('subscription_state') != 'draft',
            'invisible': ~Eval('consumption_recurrence'),
            },
        depends=['subscription_state', 'consumption_recurrence'])
    next_consumption_date = fields.Date("Next Consumption Date", readonly=True)
    next_consumption_date_delayed = fields.Function(
        fields.Date("Next Consumption Delayed"),
        'get_next_consumption_date_delayed')
    consumed = fields.Boolean("Consumed")
    start_date = fields.Date(
        "Start Date",
        domain=['OR',
            ('start_date', '>=', Eval('subscription_start_date')),
            ('start_date', '=', None),
            ],
        states={
            'readonly': ((Eval('subscription_state') != 'draft')
                | Eval('consumed')),
            },
        depends=['subscription_start_date', 'subscription_state', 'consumed'])
    end_date = fields.Date(
        "End Date",
        domain=['OR', [
                If(Bool(Eval('subscription_end_date')),
                    ('end_date', '<=', Eval('subscription_end_date')),
                    ('end_date', '>=', Eval('start_date'))),
                If(Bool(Eval('next_consumption_date')),
                    ('end_date', '>=', Eval('next_consumption_date')),
                    ('end_date', '>=', Eval('start_date'))),
                ],
            ('end_date', '=', None),
            ],
        states={
            'readonly': ((Eval('subscription_state') != 'draft')
                | (~Eval('next_consumption_date') & Eval('consumed'))),
            },
        depends=['subscription_end_date', 'start_date',
            'next_consumption_date', 'subscription_state', 'consumed'])

    @fields.depends('subscription', '_parent_subscription.state')
    def on_change_with_subscription_state(self, name=None):
        if self.subscription:
            return self.subscription.state

    @fields.depends('subscription', '_parent_subscription.start_date')
    def on_change_with_subscription_start_date(self, name=None):
        if self.subscription:
            return self.subscription.start_date

    @fields.depends('subscription', '_parent_subscription.end_date')
    def on_change_with_subscription_end_date(self, name=None):
        if self.subscription:
            return self.subscription.end_date

    @classmethod
    def default_quantity(cls):
        return 1

    @fields.depends('unit')
    def on_change_with_unit_digits(self, name=None):
        if self.unit:
            return self.unit.digits
        return 2

    @fields.depends('service')
    def on_change_with_service_unit_category(self, name=None):
        if self.service:
            return self.service.product.default_uom_category.id

    @fields.depends('service', 'quantity', 'unit', 'description',
        'subscription', '_parent_subscription.currency',
        '_parent_subscription.party', '_parent_subscription.start_date')
    def on_change_service(self):
        pool = Pool()
        Product = pool.get('product.product')

        if not self.service:
            self.consumption_recurrence = None
            self.consumption_delay = None
            return

        party = None
        party_context = {}
        if self.subscription and self.subscription.party:
            party = self.subscription.party
            if party.lang:
                party_context['language'] = party.lang.code

        product = self.service.product
        category = product.sale_uom.category
        if not self.unit or self.unit.category != category:
            self.unit = product.sale_uom
            self.unit_digits = product.sale_uom.digits

        with Transaction().set_context(self._get_context_sale_price()):
            self.unit_price = Product.get_sale_price(
                [product], self.quantity or 0)[product.id]
            if self.unit_price:
                self.unit_price = self.unit_price.quantize(
                    Decimal(1) / 10 ** self.__class__.unit_price.digits[1])

        if not self.description:
            with Transaction().set_context(party_context):
                self.description = Product(product.id).rec_name

        self.consumption_recurrence = self.service.consumption_recurrence
        self.consumption_delay = self.service.consumption_delay

    def _get_context_sale_price(self):
        context = {}
        if getattr(self, 'subscription', None):
            if getattr(self.subscription, 'currency', None):
                context['currency'] = self.subscription.currency.id
            if getattr(self.subscription, 'party', None):
                context['customer'] = self.subscription.party.id
            if getattr(self.subscription, 'start_date', None):
                context['sale_date'] = self.subscription.start_date
        if self.unit:
            context['uom'] = self.unit.id
        elif self.service:
            context['uom'] = self.service.sale_uom.id
        # TODO tax
        return context

    def get_next_consumption_date_delayed(self, name=None):
        if self.next_consumption_date and self.consumption_delay:
            return self.next_consumption_date + self.consumption_delay
        return self.next_consumption_date

    @classmethod
    def default_consumed(cls):
        return False

    @classmethod
    def domain_next_consumption_date_delayed(cls, domain, tables):
        field = cls.next_consumption_date_delayed._field
        table, _ = tables[None]
        name, operator, value = domain
        Operator = fields.SQL_OPERATORS[operator]
        column = (
            table.next_consumption_date + Coalesce(
                table.consumption_delay, datetime.timedelta()))
        expression = Operator(column, field._domain_value(operator, value))
        if isinstance(expression, operators.In) and not expression.right:
            expression = Literal(False)
        elif isinstance(expression, operators.NotIn) and not expression.right:
            expression = Literal(True)
        expression = field._domain_add_null(
            column, operator, value, expression)
        return expression

    @classmethod
    def generate_consumption(cls, date=None):
        pool = Pool()
        Date = pool.get('ir.date')
        Consumption = pool.get('sale.subscription.line.consumption')
        Subscription = pool.get('sale.subscription')

        if date is None:
            date = Date.today()

        remainings = all_lines = cls.search([
                ('consumption_recurrence', '!=', None),
                ('next_consumption_date_delayed', '<=', date),
                ('subscription.state', '=', 'running'),
                ])

        consumptions = []
        subscription_ids = set()
        while remainings:
            lines, remainings = remainings, []
            for line in lines:
                consumptions.append(
                    line.get_consumption(line.next_consumption_date))
                line.next_consumption_date = (
                    line.compute_next_consumption_date())
                line.consumed = True
                if line.next_consumption_date is None:
                    subscription_ids.add(line.subscription.id)
                elif line.get_next_consumption_date_delayed() <= date:
                    remainings.append(line)

        Consumption.save(consumptions)
        cls.save(all_lines)
        Subscription.process(Subscription.browse(list(subscription_ids)))

    def get_consumption(self, date):
        pool = Pool()
        Consumption = pool.get('sale.subscription.line.consumption')
        return Consumption(line=self, quantity=self.quantity, date=date)

    def compute_next_consumption_date(self):
        if not self.consumption_recurrence:
            return None
        start_date = self.start_date or self.subscription.start_date
        date = self.next_consumption_date or start_date
        rruleset = self.consumption_recurrence.rruleset(start_date)
        dt = datetime.datetime.combine(date, datetime.time())
        inc = (start_date == date) and not self.next_consumption_date
        next_date = rruleset.after(dt, inc=inc).date()
        for end_date in [self.end_date, self.subscription.end_date]:
            if end_date:
                if next_date > end_date:
                    return None
        return next_date

    @classmethod
    def copy(cls, lines, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('consumed')
        return super(Line, cls).copy(lines, default=default)


class LineConsumption(ModelSQL, ModelView):
    "Subscription Line Consumption"
    __name__ = 'sale.subscription.line.consumption'

    line = fields.Many2One(
        'sale.subscription.line', "Line", required=True, select=True,
        ondelete='RESTRICT')
    quantity = fields.Float(
        "Quantity", digits=(16, Eval('unit_digits', 2)),
        depends=['unit_digits'])
    unit_digits = fields.Function(
        fields.Integer("Unit Digits"), 'on_change_with_unit_digits')
    date = fields.Date("Date", required=True)
    invoice_line = fields.Many2One(
        'account.invoice.line', "Invoice Line", readonly=True)

    @classmethod
    def __setup__(cls):
        super(LineConsumption, cls).__setup__()
        cls._order.insert(0, ('date', 'DESC'))
        cls._error_messages.update({
                'modify_invoiced_consumption': (
                    "You can not modify invoiced consumption."),
                'delete_invoiced_consumption': (
                    "You can not delete invoiced consumption."),
                'missing_account_revenue': ('Product "%(product)s" '
                    'misses a revenue account.'),
                })

    @fields.depends('line')
    def on_change_with_unit_digits(self, name=None):
        if self.line and self.line.unit:
            return self.line.unit.digits
        return 2

    @classmethod
    def copy(cls, consumptions, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('invoice_line')
        return super(LineConsumption, cls).copy(consumptions, default=default)

    @classmethod
    def write(cls, *args):
        if any(c.invoice_line
                for consumptions in args[::2]
                for c in consumptions):
            cls.raise_user_error('modify_invoiced_consumption')
        super(LineConsumption, cls).write(*args)

    @classmethod
    def delete(cls, consumptions):
        if any(c.invoice_line for c in consumptions):
            cls.raise_user_error('delete_invoiced_consumption')
        super(LineConsumption, cls).delete(consumptions)

    @classmethod
    def get_invoice_lines(cls, consumptions, invoice):
        "Return a list of lines and a list of consumptions"
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')

        lines, grouped_consumptions = [], []
        consumptions = sorted(consumptions, key=cls._group_invoice_key)
        for key, sub_consumptions in groupby(
                consumptions, key=cls._group_invoice_key):
            sub_consumptions = list(sub_consumptions)
            line = InvoiceLine(**dict(key))
            line.invoice_type = 'out'
            line.type = 'line'
            line.quantity = sum(c.quantity for c in sub_consumptions)

            line.account = line.product.account_revenue_used
            if not line.account:
                cls.raise_user_error('missing_account_revenue', {
                        'product': line.product.rec_name,
                        })

            taxes = []
            pattern = line._get_tax_rule_pattern()
            party = invoice.party
            for tax in line.product.customer_taxes_used:
                if party.customer_tax_rule:
                    tax_ids = party.customer_tax_rule.apply(tax, pattern)
                    if tax_ids:
                        taxes.extend(tax_ids)
                    continue
                taxes.append(tax.id)
            if party.customer_tax_rule:
                tax_ids = party.customer_tax_rule.apply(None, pattern)
                if tax_ids:
                    taxes.extend(tax_ids)
            line.taxes = taxes

            lines.append(line)
            grouped_consumptions.append(sub_consumptions)
        return lines, grouped_consumptions

    @classmethod
    def _group_invoice_key(cls, consumption):
        return (
            ('company', consumption.line.subscription.company),
            ('unit', consumption.line.unit),
            ('product', consumption.line.service.product),
            ('unit_price', consumption.line.unit_price),
            ('description', consumption.line.description),
            ('origin', consumption.line),
            )


class CreateLineConsumption(Wizard):
    "Create Subscription Line Consumption"
    __name__ = 'sale.subscription.line.consumption.create'
    start = StateView(
        'sale.subscription.line.consumption.create.start',
        'sale_subscription.line_consumption_create_start_view_form', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Create", 'create_', 'tryton-ok', default=True),
            ])
    create_ = StateAction(
        'sale_subscription.act_subscription_line_consumption_form')

    def do_create_(self, action):
        pool = Pool()
        Line = pool.get('sale.subscription.line')
        Line.generate_consumption(date=self.start.date)
        return action, {}

    def transition_create_(self):
        return 'end'


class CreateLineConsumptionStart(ModelView):
    "Create Subscription Line Consumption"
    __name__ = 'sale.subscription.line.consumption.create.start'

    date = fields.Date("Date")

    @classmethod
    def default_date(cls):
        pool = Pool()
        Date = pool.get('ir.date')
        return Date.today()


class CreateSubscriptionInvoice(Wizard):
    "Create Subscription Invoice"
    __name__ = 'sale.subscription.create_invoice'
    start = StateView(
        'sale.subscription.create_invoice.start',
        'sale_subscription.create_invoice_start_view_form', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Create", 'create_', 'tryton-ok', default=True),
            ])
    create_ = StateTransition()

    def transition_create_(self):
        pool = Pool()
        Subscription = pool.get('sale.subscription')
        Subscription.generate_invoice(date=self.start.date)
        return 'end'


class CreateSubscriptionInvoiceStart(ModelView):
    "Create Subscription Invoice"
    __name__ = 'sale.subscription.create_invoice.start'

    date = fields.Date("Date")

    @classmethod
    def default_date(cls):
        pool = Pool()
        Date = pool.get('ir.date')
        return Date.today()
