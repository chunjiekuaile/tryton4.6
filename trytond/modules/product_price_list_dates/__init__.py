# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

from trytond.pool import Pool
from . import product
from . import sale

__all__ = ['register']


def register():
    Pool.register(
        product.PriceList,
        product.PriceListLine,
        product.PriceListLineContext,
        sale.Line,
        module='product_price_list_dates', type_='model')
