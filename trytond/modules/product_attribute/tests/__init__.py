# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

try:
    from trytond.modules.product_attribute.tests.test_product_attribute import suite
except ImportError:
    from .test_product_attribute import suite

__all__ = ['suite']
