# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from trytond import backend
from trytond.model import ModelSQL, ValueMixin, fields
from trytond.pyson import Eval
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.tools.multivalue import migrate_property

__all__ = ['SaleConfig', 'SaleConfigSaleDropLocation', 'Sale', 'SaleLine']
sale_drop_location = fields.Many2One(
    'stock.location', "Sale Drop Location", domain=[('type', '=', 'drop')])


class SaleConfig:
    __metaclass__ = PoolMeta
    __name__ = 'sale.configuration'

    sale_drop_location = fields.MultiValue(sale_drop_location)

    @classmethod
    def default_sale_drop_location(cls, **pattern):
        return cls.multivalue_model(
            'sale_drop_location').default_sale_drop_location()


class SaleConfigSaleDropLocation(ModelSQL, ValueMixin):
    "Sale Configuration Sale Drop Location"
    __name__ = 'sale.configuration.sale_drop_location'
    sale_drop_location = sale_drop_location

    @classmethod
    def __register__(cls, module_name):
        TableHandler = backend.get('TableHandler')
        exist = TableHandler.table_exist(cls._table)

        super(SaleConfigSaleDropLocation, cls).__register__(
            module_name)

        if not exist:
            cls._migrate_property([], [], [])

    @classmethod
    def _migrate_property(cls, field_names, value_names, fields):
        field_names.append('sale_drop_location')
        value_names.append('sale_drop_location')
        migrate_property(
            'sale.configuration', field_names, cls, value_names,
            fields=fields)

    @classmethod
    def default_sale_drop_location(cls):
        pool = Pool()
        ModelData = pool.get('ir.model.data')
        try:
            return ModelData.get_id(
                'sale_supply_drop_shipment', 'location_drop')
        except KeyError:
            return None


class Sale:
    __metaclass__ = PoolMeta
    __name__ = 'sale.sale'

    drop_shipments = fields.Function(fields.One2Many('stock.shipment.drop',
            None, 'Drop Shipments',
            states={
                'invisible': ~Eval('drop_shipments'),
                }),
        'get_drop_shipments')
    drop_location = fields.Many2One('stock.location', 'Drop Location',
        domain=[('type', '=', 'drop')])

    @staticmethod
    def default_drop_location():
        pool = Pool()
        Config = pool.get('sale.configuration')

        config = Config(1)
        if config.sale_drop_location:
            return config.sale_drop_location.id

    def get_drop_shipments(self, name):
        DropShipment = Pool().get('stock.shipment.drop')
        return list(set(m.shipment.id for l in self.lines for m in l.moves
                if isinstance(m.shipment, DropShipment)))

    def create_shipment(self, shipment_type):
        shipments = super(Sale, self).create_shipment(shipment_type)
        if shipment_type == 'out':
            with Transaction().set_context(_drop_shipment=True):
                self.create_drop_shipment_moves()
        return shipments

    def create_drop_shipment_moves(self):
        pool = Pool()
        Move = pool.get('stock.move')

        moves = []
        for line in self.lines:
            moves += line.get_drop_moves()
        Move.save(moves)


class SaleLine:
    __metaclass__ = PoolMeta
    __name__ = 'sale.line'

    @property
    def supply_on_sale(self):
        supply_on_sale = super(SaleLine, self).supply_on_sale
        return (supply_on_sale
            or (self.moves and all(m.from_location.type == 'drop'
                    for m in self.moves)))

    @property
    def supply_on_sale_drop_move(self):
        "Return True if the sale line can have drop move"
        if not self.supply_on_sale:
            return False
        if not self.purchase_request or not self.purchase_request.customer:
            return False
        if self.purchase_request_state == 'cancel':
            return False
        purchase_line = self.purchase_request.purchase_line
        if purchase_line and purchase_line.move_done:
            return False
        return True

    def get_move(self, shipment_type):
        result = super(SaleLine, self).get_move(shipment_type)
        if (shipment_type == 'out'
                and not Transaction().context.get('_drop_shipment')):
            if self.supply_on_sale_drop_move:
                return
        return result

    def get_purchase_request(self):
        request = super(SaleLine, self).get_purchase_request()
        if request and request.party:
            drop_shipment = False
            if self.product and self.product.type in ('goods', 'assets'):
                # FIXME this doesn't ensure to find always the right
                # product_supplier
                for product_supplier in self.product.product_suppliers:
                    if product_supplier.party == request.party:
                        drop_shipment = product_supplier.drop_shipment
                        break
            if drop_shipment:
                request.customer = self.sale.party
                request.delivery_address = self.sale.shipment_address
        return request

    def get_drop_moves(self):
        if (self.type != 'line'
                or not self.product):
            return []
        moves = []
        if self.supply_on_sale_drop_move:
            move = self.get_move('out')
            if move is not None:
                move.from_location = self.sale.drop_location
                moves.append(move)
        return moves
