# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import functools

from trytond.pool import Pool, PoolMeta

__all__ = ['Payment', 'Invoice']


def sale_payment_confirm(func):
    @functools.wraps(func)
    def wrapper(cls, payments, *args, **kwargs):
        pool = Pool()
        Sale = pool.get('sale.sale')

        result = func(cls, payments, *args, **kwargs)

        sales = [p.origin for p in payments
            if isinstance(p.origin, Sale)]
        sales = Sale.browse(sales)  # optimize cache
        Sale.payment_confirm(sales)

        return result
    return wrapper


class Payment:
    __metaclass__ = PoolMeta
    __name__ = 'account.payment'

    @classmethod
    def __setup__(cls):
        super(Payment, cls).__setup__()
        cls._error_messages.update({
                'payment_on_cancelled_sale': (
                    'Payment cannot be approved because sale "%(sale)s" '
                    'is cancelled.'),
                })

    @classmethod
    def _get_origin(cls):
        return super(Payment, cls)._get_origin() + ['sale.sale']

    @classmethod
    def validate(cls, payments):
        pool = Pool()
        Sale = pool.get('sale.sale')
        super(Payment, cls).validate(payments)
        for payment in payments:
            if isinstance(payment.origin, Sale):
                payment.check_sale_state()

    def check_sale_state(self):
        assert isinstance(self.origin, Pool().get('sale.sale'))
        if self.state == 'succeeded':
            # Do not prevent succeeding payment
            return
        if self.state != 'failed' and self.origin.state == 'draft':
            self.raise_user_error('payment_on_draft_sale', {
                    'payment': self.rec_name,
                    'sale': self.origin.rec_name,
                    })
        elif self.state == 'draft' and self.origin.state == 'cancel':
            self.raise_user_error('payment_on_cancelled_sale', {
                    'payment': self.rec_name,
                    'sale': self.origin.rec_name,
                    })

    @classmethod
    def create(cls, vlist):
        payments = super(Payment, cls).create(vlist)
        cls.trigger_authorized([p for p in payments if p.is_authorized])
        return payments

    @classmethod
    def write(cls, *args):
        payments = sum(args[0:None:2], [])
        unauthorized = {p for p in payments if not p.is_authorized}
        super(Payment, cls).write(*args)
        authorized = {p for p in payments if p.is_authorized}
        cls.trigger_authorized(cls.browse(unauthorized & authorized))

    @property
    def is_authorized(self):  # TODO: move to account_payment
        return self.state == 'succeeded'

    @classmethod
    @sale_payment_confirm
    def trigger_authorized(cls, payments):
        pass


class Invoice:
    __metaclass__ = PoolMeta
    __name__ = 'account.invoice'

    def add_payments(self, payments=None):
        "Add payments from sales lines to pay"
        if payments is None:
            payments = []
        else:
            payments = payments[:]
        for sale in self.sales:
            payments.extend(sale.payments)

        # Knapsack problem:
        # simple heuristic by trying to fill biggest amount first.
        payments.sort(key=lambda p: p.amount)
        lines_to_pay = sorted(
            self.lines_to_pay, key=lambda l: l.payment_amount)
        for line in lines_to_pay:
            if line.reconciliation:
                continue
            payment_amount = line.payment_amount
            for payment in payments:
                if payment.line or payment.state == 'failed':
                    continue
                if ((payment.kind == 'receivable' and line.credit > 0)
                        or (payment.kind == 'payable' and line.debit > 0)):
                    continue
                if payment.party != line.party:
                    continue
                if payment.amount <= payment_amount:
                    payment.line = line
                    payment_amount -= payment.amount
        return payments

    def reconcile_payments(self):
        pool = Pool()
        Payment = pool.get('account.payment')
        Line = pool.get('account.move.line')
        if not hasattr(Payment, 'clearing_move'):
            return
        to_reconcile = []
        for line in self.lines_to_pay:
            if line.reconciliation:
                continue
            lines = [line]
            for payment in line.payments:
                if payment.state == 'succeeded' and payment.clearing_move:
                    for pline in payment.clearing_move.lines:
                        if (pline.account == line.account
                                and not pline.reconciliation):
                            lines.append(pline)
            if not sum(l.debit - l.credit for l in lines):
                to_reconcile.append(lines)
        for lines in to_reconcile:
            Line.reconcile(lines)

    @classmethod
    def post(cls, invoices):
        pool = Pool()
        Payment = pool.get('account.payment')
        Move = pool.get('account.move')

        super(Invoice, cls).post(invoices)

        payments = []
        for invoice in invoices:
            payments.extend(invoice.add_payments())
        if payments:
            Payment.save(payments)
        if hasattr(Payment, 'clearing_move'):
            moves = []
            for payment in payments:
                if payment.state == 'succeeded':
                    # Ensure clearing move is created as succeed may happen
                    # before the payment has a line.
                    move = payment.create_clearing_move()
                    if move:
                        moves.append(move)
            if moves:
                Move.save(moves)
                Payment.write(*sum((([m.origin], {'clearing_move': m.id})
                            for m in moves), ()))

            for invoice in invoices:
                invoice.reconcile_payments()
