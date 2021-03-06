# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from decimal import Decimal

from trytond.pool import PoolMeta

__all__ = ['LandedCost']


class LandedCost:
    __metaclass__ = PoolMeta
    __name__ = 'account.landed_cost'

    @classmethod
    def __setup__(cls):
        super(LandedCost, cls).__setup__()
        cls.allocation_method.selection.append(('weight', 'By Weight'))

    def allocate_cost_by_weight(self):
        self._allocate_cost(self._get_weight_factors())

    def _get_weight_factors(self):
        "Return the factor for each move based on weight"
        moves = [m for s in self.shipments for m in s.incoming_moves
            if m.state != 'cancel']

        sum_weight = Decimal(0)
        weights = {}
        for move in moves:
            weight = Decimal(str(move.internal_weight or 0))
            weights[move.id] = weight
            sum_weight += weight
        factors = {}
        length = Decimal(len(moves))
        for move in moves:
            if not sum_weight:
                factors[move.id] = 1 / length
            else:
                factors[move.id] = weights[move.id] / sum_weight
        return factors
