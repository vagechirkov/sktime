# -*- coding: utf-8 -*-
"""Columnwise transformer."""
# copyright: sktime developers, BSD-3-Clause License (see LICENSE file)

__author__ = ["fkiraly", "mloning"]
__all__ = ["ColumnwiseTransformer"]

import pandas as pd
from sklearn.utils.metaestimators import if_delegate_has_method

from sktime.base._meta import _ColumnEstimator, _HeterogenousMetaEstimator
from sktime.transformations.base import BaseTransformer
from sktime.utils.validation.series import check_series

# mtypes that are native pandas
# ColumnEnsembleTransformer uses these internally, since we need (pandas) columns
PANDAS_MTYPES = ["pd.DataFrame", "pd-multiindex", "pd_multiindex_hier"]


class ColumnEnsembleTransformer(_HeterogenousMetaEstimator, _ColumnEstimator):
    """Column-wise application of transformers.

    Applies transformations to columns of an array or pandas DataFrame. Simply
    takes the column transformer from sklearn
    and adds capability to handle pandas dataframe.

    This estimator allows different columns or column subsets of the input
    to be transformed separately and the features generated by each transformer
    will be concatenated to form a single feature space.
    This is useful for heterogeneous or columnar data, to combine several
    feature extraction mechanisms or transformations into a single transformer.

    Parameters
    ----------
    transformers : sktime trafo, or list of tuples (str, estimator, int or pd.index)
        if tuples, with name = str, estimator is transformer, index as int or index
        if last element is index, it must be int, str, or pd.Index coercable
        if last element is int x, and is not in columns, is interpreted as x-th column
        all columns must be present in an index

        If transformer, clones of transformer are applied to all columns.
        If list of tuples, transformer in tuple is applied to column with int/str index

    Attributes
    ----------
    transformers_ : list
        The collection of fitted transformations as tuples of
        (name, fitted_transformer, column). `fitted_transformer` can be an
        estimator, "drop", or "passthrough". In case there were no columns
        selected, this will be the unfitted transformer.
        If there are remaining columns, the final element is a tuple of the
        form:
        ("remainder", transformer, remaining_columns) corresponding to the
        ``remainder`` parameter. If there are remaining columns, then
        ``len(transformers_)==len(transformations)+1``, otherwise
        ``len(transformers_)==len(transformations)``.
    """

    _tags = {
        "X_inner_mtype": PANDAS_MTYPES,
        "y_inner_mtype": PANDAS_MTYPES,
        "fit_is_empty": False,
        "capability:unequal_length": True,
        "handles-missing-data": True,
    }

    # for default get_params/set_params from _HeterogenousMetaEstimator
    # _steps_attr points to the attribute of self
    # which contains the heterogeneous set of estimators
    # this must be an iterable of (name: str, estimator, ...) tuples for the default
    _steps_attr = "_transformers"
    # if the estimator is fittable, _HeterogenousMetaEstimator also
    # provides an override for get_fitted_params for params from the fitted estimators
    # the fitted estimators should be in a different attribute, _steps_fitted_attr
    # this must be an iterable of (name: str, estimator, ...) tuples for the default
    _steps_fitted_attr = "transformers_"

    def __init__(self, transformers):
        self.transformers = transformers
        super(ColumnEnsembleTransformer, self).__init__()

        # set requires-fh-in-fit depending on transformers
        if isinstance(transformers, BaseTransformer):
            tags_to_clone = [
                "fit_is_empty",
                "requires_y",
                "X-y-must-have-same-index",
                "transform-returns-same-time-index",
                "capability:unequal_length",
                "capability:unequal_length:removes",
                "handles-missing-data",
                "capability:missing_values:removes",
                "scitype:transform-output",
                "scitype:transform-labels",
            ]
            self.clone_tags(transformers, tags_to_clone)
        else:
            l_transformers = [(x[0], x[1]) for x in transformers]
            self._anytagis_then_set("fit_is_empty", False, True, l_transformers)
            self._anytagis_then_set("requires_y", True, False, l_transformers)
            self._anytagis_then_set(
                "X-y-must-have-same-index", True, False, l_transformers
            )
            self._anytagis_then_set(
                "transform-returns-same-time-index", False, True, l_transformers
            )
            self._anytagis_then_set(
                "capability:unequal_length", False, True, l_transformers
            )
            self._anytagis_then_set(
                "capability:unequal_length:removes", False, True, l_transformers
            )
            self._anytagis_then_set("handles-missing-data", False, True, l_transformers)
            self._anytagis_then_set(
                "capability:missing_values:removes", False, True, l_transformers
            )

            # must be all the same, currently not checking
            tags_to_clone = ["scitype:transform-output", "scitype:transform-labels"]
            self.clone_tags(transformers[0][1], tags_to_clone)

    @property
    def _transformers(self):
        """Make internal list of transformers.

        The list only contains the name and transformers, dropping
        the columns. This is for the implementation of get_params
        via _HeterogenousMetaEstimator._get_params which expects
        lists of tuples of len 2.
        """
        transformers = self.transformers
        if isinstance(transformers, BaseTransformer):
            return [("transformers", transformers)]
        else:
            return [(name, transformer) for name, transformer, _ in self.transformers]

    @_transformers.setter
    def _transformers(self, value):
        if len(value) == 1 and isinstance(value, BaseTransformer):
            self.transformers = value
        elif len(value) == 1 and isinstance(value, list):
            self.transformers = value[0][1]
        else:
            self.transformers = [
                (name, transformer, columns)
                for ((name, transformer), (_, _, columns)) in zip(
                    value, self.transformers
                )
            ]

    def _fit(self, X, y=None):
        """Fit transformer to X and y.

        private _fit containing the core logic, called from fit

        Parameters
        ----------
        X : Series or Panel of mtype X_inner_mtype
            if X_inner_mtype is list, _fit must support all types in it
            Data to fit transform to
        y : Series or Panel of mtype y_inner_mtype, default=None
            Additional data, e.g., labels for transformation

        Returns
        -------
        self: reference to self
        """
        transformers = self._check_transformers(y)

        self.transformers_ = []
        self._Xcolumns = list(X.columns)

        for (name, transformer, index) in transformers:
            transformer_ = transformer.clone()

            pd_index = self._coerce_to_pd_index(index)

            transformer_.fit(X.loc[:, pd_index], y)
            self.transformers_.append((name, transformer_, index))

        return self

    def _transform(self, X, y=None):
        """Transform X and return a transformed version.

        private _transform containing core logic, called from transform

        Parameters
        ----------
        X : Series or Panel of mtype X_inner_mtype
            if X_inner_mtype is list, _transform must support all types in it
            Data to be transformed
        y : Series or Panel of mtype y_inner_mtype, default=None
            Additional data, e.g., labels for transformation

        Returns
        -------
        transformed version of X
        """
        Xts = []
        keys = []
        for _, est, index in getattr(self, self._steps_fitted_attr):
            pd_index = self._coerce_to_pd_index(index)

            Xts += [est.transform(X.loc[:, pd_index], y)]
            keys += [index]

        keys = self._get_indices(self._Xcolumns, keys)

        Xt = pd.concat(Xts, axis=1)
        return Xt

    @classmethod
    def get_test_params(cls):
        """Return testing parameter settings for the estimator.

        Returns
        -------
        params : dict or list of dict, default = {}
            Parameters to create testing instances of the class
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`
        """
        from sktime.transformations.series.exponent import ExponentTransformer

        TRANSFORMERS = [
            ("transformer1", ExponentTransformer()),
            ("transformer2", ExponentTransformer()),
        ]

        return {
            "transformers": [(name, estimator, [0]) for name, estimator in TRANSFORMERS]
        }


class ColumnwiseTransformer(BaseTransformer):
    """Apply a transformer columnwise to multivariate series.

    Overview: input multivariate time series and the transformer passed
    in `transformer` parameter is applied to specified `columns`, each
    column is handled as a univariate series. The resulting transformed
    data has the same shape as input data.

    Parameters
    ----------
    transformer : Estimator
        scikit-learn-like or sktime-like transformer to fit and apply to series.
    columns : list of str or None
            Names of columns that are supposed to be transformed.
            If None, all columns are transformed.

    Attributes
    ----------
    transformers_ : dict of {str : transformer}
        Maps columns to transformers.
    columns_ : list of str
        Names of columns that are supposed to be transformed.

    See Also
    --------
    OptionalPassthrough

    Examples
    --------
    >>> from sktime.datasets import load_longley
    >>> from sktime.transformations.series.detrend import Detrender
    >>> from sktime.transformations.compose import ColumnwiseTransformer
    >>> _, X = load_longley()
    >>> transformer = ColumnwiseTransformer(Detrender())
    >>> Xt = transformer.fit_transform(X)
    """

    _tags = {
        "scitype:transform-input": "Series",
        # what is the scitype of X: Series, or Panel
        "scitype:transform-output": "Series",
        # what scitype is returned: Primitives, Series, Panel
        "scitype:instancewise": True,  # is this an instance-wise transform?
        "X_inner_mtype": "pd.DataFrame",
        # which mtypes do _fit/_predict support for X?
        "y_inner_mtype": "None",  # which mtypes do _fit/_predict support for y?
        "univariate-only": False,
        "fit_is_empty": False,
    }

    def __init__(self, transformer, columns=None):
        self.transformer = transformer
        self.columns = columns
        super(ColumnwiseTransformer, self).__init__()

        tags_to_clone = [
            "y_inner_mtype",
            "capability:inverse_transform",
            "handles-missing-data",
            "X-y-must-have-same-index",
            "transform-returns-same-time-index",
            "skip-inverse-transform",
        ]
        self.clone_tags(transformer, tag_names=tags_to_clone)

    def _fit(self, X, y=None):
        """Fit transformer to X and y.

        private _fit containing the core logic, called from fit

        Parameters
        ----------
        X : pd.DataFrame
            Data to fit transform to
        y : Series or Panel, default=None
            Additional data, e.g., labels for transformation

        Returns
        -------
        self: a fitted instance of the estimator
        """
        # check that columns are None or list of strings
        if self.columns is not None:
            if not isinstance(self.columns, list) and all(
                isinstance(s, str) for s in self.columns
            ):
                raise ValueError("Columns need to be a list of strings or None.")

        # set self.columns_ to columns that are going to be transformed
        # (all if self.columns is None)
        self.columns_ = self.columns
        if self.columns_ is None:
            self.columns_ = X.columns

        # make sure z contains all columns that the user wants to transform
        _check_columns(X, selected_columns=self.columns_)

        # fit by iterating over columns
        self.transformers_ = {}
        for colname in self.columns_:
            transformer = self.transformer.clone()
            self.transformers_[colname] = transformer
            self.transformers_[colname].fit(X[colname], y)
        return self

    def _transform(self, X, y=None):
        """Transform X and return a transformed version.

        private _transform containing the core logic, called from transform

        Returns a transformed version of X by iterating over specified
        columns and applying the wrapped transformer to them.

        Parameters
        ----------
        X : pd.DataFrame
            Data to be transformed
        y : Series or Panel, default=None
            Additional data, e.g., labels for transformation

        Returns
        -------
        Xt : pd.DataFrame
            transformed version of X
        """
        # make copy of z
        X = X.copy()

        # make sure z contains all columns that the user wants to transform
        _check_columns(X, selected_columns=self.columns_)
        for colname in self.columns_:
            X[colname] = self.transformers_[colname].transform(X[colname], y)
        return X

    def _inverse_transform(self, X, y=None):
        """Logic used by `inverse_transform` to reverse transformation on `X`.

        Returns an inverse-transformed version of X by iterating over specified
        columns and applying the univariate series transformer to them.
        Only works if `self.transformer` has an `inverse_transform` method.

        Parameters
        ----------
        X : pd.DataFrame
            Data to be inverse transformed
        y : Series or Panel, default=None
            Additional data, e.g., labels for transformation

        Returns
        -------
        Xt : pd.DataFrame
            inverse transformed version of X
        """
        # make copy of z
        X = X.copy()

        # make sure z contains all columns that the user wants to transform
        _check_columns(X, selected_columns=self.columns_)

        # iterate over columns that are supposed to be inverse_transformed
        for colname in self.columns_:
            X[colname] = self.transformers_[colname].inverse_transform(X[colname], y)

        return X

    @if_delegate_has_method(delegate="transformer")
    def update(self, X, y=None, update_params=True):
        """Update parameters.

        Update the parameters of the estimator with new data
        by iterating over specified columns.
        Only works if `self.transformer` has an `update` method.

        Parameters
        ----------
        X : pd.Series
            New time series.
        update_params : bool, optional, default=True

        Returns
        -------
        self : an instance of self
        """
        z = check_series(X)

        # make z a pd.DataFrame in univariate case
        if isinstance(z, pd.Series):
            z = z.to_frame()

        # make sure z contains all columns that the user wants to transform
        _check_columns(z, selected_columns=self.columns_)
        for colname in self.columns_:
            self.transformers_[colname].update(z[colname], X)
        return self

    @classmethod
    def get_test_params(cls, parameter_set="default"):
        """Return testing parameter settings for the estimator.

        Parameters
        ----------
        parameter_set : str, default="default"
            Name of the set of test parameters to return, for use in tests. If no
            special parameters are defined for a value, will return `"default"` set.


        Returns
        -------
        params : dict or list of dict, default = {}
            Parameters to create testing instances of the class
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`
        """
        from sktime.transformations.series.detrend import Detrender

        return {"transformer": Detrender()}


def _check_columns(z, selected_columns):
    # make sure z contains all columns that the user wants to transform
    z_wanted_keys = set(selected_columns)
    z_new_keys = set(z.columns)
    difference = z_wanted_keys.difference(z_new_keys)
    if len(difference) != 0:
        raise ValueError("Missing columns" + str(difference) + "in Z.")