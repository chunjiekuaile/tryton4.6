# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from trytond import backend
from trytond.model import ModelSQL, ValueMixin, fields
from trytond.pyson import Eval
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.tools.multivalue import migrate_property


__all__ = ['PurchaseRequest', 'PurchaseConfig',
    'PurchaseConfigPurchaseDropLocation', 'Purchase', 'PurchaseLine',
    'ProductSupplier', 'CreatePurchase', 'PurchaseHandleShipmentException']
purchase_drop_location = fields.Many2One(
    'stock.location', "Purchase Drop Location", domain=[('type', '=', 'drop')])


class PurchaseRequest:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.request'

    customer = fields.Many2One('party.party', 'Customer', readonly=True,
        states={
            'invisible': ~Eval('customer'),
            })
    delivery_address = fields.Many2One('party.address', 'Delivery Address',
        domain=[('party', '=', Eval('customer'))],
        states={
            'invisible': ~Eval('customer'),
            'readonly': Eval('state') != 'draft',
            },
        depends=['customer', 'state'])


class PurchaseConfig:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.configuration'

    purchase_drop_location = fields.MultiValue(purchase_drop_location)

    @classmethod
    def default_purchase_drop_location(cls, **pattern):
        return cls.multivalue_model(
            'purchase_drop_location').default_purchase_drop_location()


class PurchaseConfigPurchaseDropLocation(ModelSQL, ValueMixin):
    "Purchase Configuration Purchase Drop Location"
    __name__ = 'purchase.configuration.purchase_drop_location'
    purchase_drop_location = purchase_drop_location

    @classmethod
    def __register__(cls, module_name):
        TableHandler = backend.get('TableHandler')
        exist = TableHandler.table_exist(cls._table)

        super(PurchaseConfigPurchaseDropLocation, cls).__register__(
            module_name)

        if not exist:
            cls._migrate_property([], [], [])

    @classmethod
    def _migrate_property(cls, field_names, value_names, fields):
        field_names.append('purchase_drop_location')
        value_names.append('purchase_drop_location')
        migrate_property(
            'purchase.configuration', field_names, cls, value_names,
            fields=fields)

    @classmethod
    def default_purchase_drop_location(cls):
        pool = Pool()
        ModelData = pool.get('ir.model.data')
        try:
            return ModelData.get_id(
                'sale_supply_drop_shipment', 'location_drop')
        except KeyError:
            return None


class Purchase:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.purchase'

    customer = fields.Many2One('party.party', 'Customer', readonly=True,
        states={
            'invisible': ~Eval('customer'),
            })
    delivery_address = fields.Many2One('party.address', 'Delivery Address',
        domain=[('party', '=', Eval('customer'))],
        states={
            'readonly': Eval('state') != 'draft',
            'invisible': ~Eval('customer'),
            },
        depends=['state', 'customer'])
    drop_shipments = fields.Function(fields.One2Many('stock.shipment.drop',
            None, 'Drop Shipments',
            states={
                'invisible': ~Eval('customer'),
                },
            depends=['customer']),
        'get_drop_shipments')
    drop_location = fields.Many2One('stock.location', 'Drop Location',
        domain=[('type', '=', 'drop')],
        states={
            'invisible': ~Eval('customer', False),
            'required': Eval('customer', False),
            },
        depends=['customer'])

    @classmethod
    def __setup__(cls):
        super(Purchase, cls).__setup__()
        cls._error_messages.update({
                'delivery_address_required': ('A delivery address must be '
                    'defined for quotation of purchase "%s".'),
                })

    @staticmethod
    def default_drop_location():
        pool = Pool()
        PurchaseConfig = pool.get('purchase.configuration')

        config = PurchaseConfig(1)
        if config.purchase_drop_location:
            return config.purchase_drop_location.id

    @fields.depends('customer', 'delivery_address')
    def on_change_customer(self):
        self.delivery_address = None
        if self.customer:
            self.delivery_address = self.customer.address_get(type='delivery')

    @property
    def delivery_full_address(self):
        address = super(Purchase, self).delivery_full_address
        if self.customer and self.delivery_address:
            address = self.delivery_address.full_address
        return address

    def get_drop_shipments(self, name):
        DropShipment = Pool().get('stock.shipment.drop')
        return list(set(m.shipment.id for l in self.lines for m in l.moves
                if isinstance(m.shipment, DropShipment)))

    def check_for_quotation(self):
        super(Purchase, self).check_for_quotation()
        if self.customer and not self.delivery_address:
            self.raise_user_error('delivery_address_required', self.rec_name)

    def create_move(self, move_type):
        pool = Pool()
        DropShipment = pool.get('stock.shipment.drop')

        moves = super(Purchase, self).create_move(move_type)
        if moves and self.customer and move_type == 'in':
            drop_shipment = self.create_drop_shipment()
            drop_shipment.supplier_moves = moves
            drop_shipment.save()
            DropShipment.wait([drop_shipment])

        return moves

    def create_drop_shipment(self):
        pool = Pool()
        DropShipment = pool.get('stock.shipment.drop')

        return DropShipment(
            company=self.company,
            supplier=self.party,
            contact_address=self.party.address_get(),
            customer=self.customer,
            delivery_address=self.delivery_address,
            )


class PurchaseLine:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.line'

    def get_to_location(self, name):
        result = super(PurchaseLine, self).get_to_location(name)
        if self.purchase.customer:
            return self.purchase.drop_location.id
        return result

    def get_move(self, move_type):
        move = super(PurchaseLine, self).get_move(move_type)
        if move and self.purchase.customer:
            move.cost_price = move.unit_price
        return move


class ProductSupplier:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.product_supplier'

    drop_shipment = fields.Boolean('Drop Shipment',
        states={
            'invisible': ~Eval('drop_shipment_available', False),
            },
        depends=['drop_shipment_available'])
    drop_shipment_available = fields.Function(
        fields.Boolean("Drop Shipment Available"),
        'on_change_with_drop_shipment_available')

    @fields.depends('product',
        '_parent_product.type', '_parent_product.supply_on_sale')
    def on_change_with_drop_shipment_available(self, name=None):
        if self.product and self.product.type in {'goods', 'assets'}:
            return self.product.supply_on_sale


class CreatePurchase:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.request.create_purchase'

    @classmethod
    def _group_purchase_key(cls, requests, request):
        result = super(CreatePurchase, cls)._group_purchase_key(requests,
            request)
        result += (
            ('customer', request.customer.id if request.customer else None),
            ('delivery_address', request.delivery_address.id
                if request.delivery_address else None),
            )
        return result


class PurchaseHandleShipmentException:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.handle.shipment.exception'

    def transition_handle(self):
        pool = Pool()
        Sale = pool.get('sale.sale')
        Purchase = pool.get('purchase.purchase')
        Move = pool.get('stock.move')

        super(PurchaseHandleShipmentException, self).transition_handle()

        sales = set()
        moves = set()
        to_recreate = set(self.ask.recreate_moves)
        domain_moves = set(self.ask.domain_moves)
        purchase = Purchase(Transaction().context['active_id'])

        for line in purchase.lines:
            if not set(line.moves) & domain_moves:
                continue
            if not any(m in to_recreate for m in line.moves):
                for request in line.requests:
                    for sale_line in request.sale_lines:
                        moves.update({m for m in sale_line.moves
                                if (m.state != 'done'
                                    and m.from_location.type == 'drop')})
                        sales.add(sale_line.sale)

        if moves:
            Move.cancel(list(moves))
        if sales:
            Sale.process(list(sales))
        return 'end'
