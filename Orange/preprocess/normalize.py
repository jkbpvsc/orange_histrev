import numpy as np

from Orange.data import Domain, ContinuousVariable
from Orange.statistics import basic_stats
from Orange.util import Reprable
from .preprocess import Normalize
from .transformation import Normalizer as Norm
__all__ = ["Normalizer"]


class Normalizer(Reprable):
    def __init__(self,
                 zero_based=True,
                 norm_type=Normalize.NormalizeBySD,
                 transform_class=False,
                 center=True,
                 normalize_datetime=False):
        self.zero_based = zero_based
        self.norm_type = norm_type
        self.transform_class = transform_class
        self.center = center
        self.normalize_datetime = normalize_datetime

    def __call__(self, data):
        stats = basic_stats.DomainBasicStats(data, compute_variance=True)
        new_attrs = [self.normalize(stats[i], var) for
                     (i, var) in enumerate(data.domain.attributes)]

        new_class_vars = data.domain.class_vars
        if self.transform_class:
            attr_len = len(data.domain.attributes)
            new_class_vars = [self.normalize(stats[i + attr_len], var) for
                              (i, var) in enumerate(data.domain.class_vars)]

        domain = Domain(new_attrs, new_class_vars, data.domain.metas)
        return data.transform(domain)

    def normalize(self, stats, var):
        if not var.is_continuous or (var.is_time and not self.normalize_datetime):
            return var
        elif self.norm_type == Normalize.NormalizeBySD:
            var = self.normalize_by_sd(stats, var)
        elif self.norm_type == Normalize.NormalizeBySpan:
            var = self.normalize_by_span(stats, var)
        return var

    def normalize_by_sd(self, stats, var: ContinuousVariable) -> ContinuousVariable:
        avg, sd = (stats.mean, stats.var**0.5)
        if np.isnan(avg):
            avg = 0
        if np.isnan(sd):
            sd = 1
        if sd == 0:
            sd = 1
        if self.center:
            compute_val = Norm(var, avg, 1 / sd)
        else:
            compute_val = Norm(var, 0, 1 / sd)

        # When dealing with integers, and multiplying by something smaller than
        # 1, the number of decimals should be decreased, but this integer will
        # likely turn into a float, which should have some default number of
        # decimals
        num_decimals = var.number_of_decimals + int(np.round(np.log10(sd)))
        num_decimals = max(num_decimals, 1)  # num decimals can't be negative

        return var.copy(compute_value=compute_val, number_of_decimals=num_decimals)

    def normalize_by_span(self, stats, var: ContinuousVariable) -> ContinuousVariable:
        dma, dmi = (stats.max, stats.min)
        diff = dma - dmi
        if diff < 1e-15:
            diff = 1
        if self.zero_based:
            compute_val = Norm(var, dmi, 1 / diff)
        else:
            compute_val = Norm(var, (dma + dmi) / 2, 2 / diff)
        if not np.isnan(diff):
            num_decimals = var.number_of_decimals + int(np.ceil(np.log10(diff)))
            num_decimals = max(num_decimals, 0)  # num decimals can't be negative
            return var.copy(compute_value=compute_val, number_of_decimals=num_decimals)
        else:
            return var.copy(compute_value=compute_val)
