# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

from trytond.pool import Pool

from . import stock

__all__ = ['register']


def register():
    Pool.register(
        stock.Move,
        stock.ShipmentIn,
        stock.ShipmentInReturn,
        stock.ShipmentOut,
        stock.ShipmentOutReturn,
        stock.Package,
        module='stock_shipment_measurements', type_='model')
    Pool.register(
        module='stock_shipment_measurements', type_='wizard')
    Pool.register(
        module='stock_shipment_measurements', type_='report')
