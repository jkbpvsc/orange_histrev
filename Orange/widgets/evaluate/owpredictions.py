from collections import namedtuple

import numpy
from AnyQt.QtWidgets import (
    QTableView, QListWidget, QSplitter, QStyledItemDelegate,
    QToolTip, QStyle, QApplication)
from AnyQt.QtGui import QPainter, QStandardItem, QPen, QColor
from AnyQt.QtCore import (
    Qt, QSize, QRect, QRectF, QPoint, QLocale,
    QModelIndex, QAbstractTableModel, QSortFilterProxyModel)

import Orange
from Orange.evaluation import Results
from Orange.base import Model
from Orange.data import ContinuousVariable, DiscreteVariable, Value, Domain
from Orange.data.table import DomainTransformationError
from Orange.widgets import gui, settings
from Orange.widgets.evaluate.utils import (
    ScoreTable, usable_scorers, learner_name, scorer_caller)
from Orange.widgets.utils.widgetpreview import WidgetPreview
from Orange.widgets.widget import OWWidget, Msg, Input, Output
from Orange.widgets.utils.itemmodels import TableModel
from Orange.widgets.utils.sql import check_sql_input


# Input slot for the Predictors channel
PredictorSlot = namedtuple(
    "PredictorSlot",
    ["predictor",  # The `Model` instance
     "name",       # Predictor name
     "results"]    # Computed prediction results or None.
)


class OWPredictions(OWWidget):
    name = "Predictions"
    icon = "icons/Predictions.svg"
    priority = 200
    description = "Display predictions of models for an input dataset."
    keywords = []

    class Inputs:
        data = Input("Data", Orange.data.Table)
        predictors = Input("Predictors", Model, multiple=True)

    class Outputs:
        predictions = Output("Predictions", Orange.data.Table)
        evaluation_results = Output("Evaluation Results", Results)

    class Warning(OWWidget.Warning):
        empty_data = Msg("Empty dataset")
        wrong_targets = Msg(
            "Some model(s) predict a different target (see more ...)\n{}")

    class Error(OWWidget.Error):
        predictor_failed = Msg("Some predictor(s) failed (see more ...)\n{}")
        scorer_failed = Msg("Some scorer(s) failed (see more ...)\n{}")

    settingsHandler = settings.ClassValuesContextHandler()
    score_table = settings.SettingProvider(ScoreTable)

    #: Display the full input dataset or only the target variable columns
    show_attrs = settings.Setting(True)
    #: Show predicted values
    show_predictions = settings.Setting(True)
    #: List of selected class value indices in the `class_values` list
    selected_classes = settings.ContextSetting([])
    #: Draw colored distribution bars
    draw_dist = settings.Setting(True)

    output_attrs = settings.Setting(True)
    output_predictions = settings.Setting(True)
    output_probabilities = settings.Setting(True)

    def __init__(self):
        super().__init__()

        self.data = None  # type: Optional[Orange.data.Table]
        self.predictors = {}  # type: Dict[object, PredictorSlot]
        self.class_values = []  # type: List[str]
        self._delegates = []

        box = gui.vBox(self.controlArea, "Show", spacing=-1, addSpace=False)

        gui.widgetLabel(box, "Show probabilities for:")
        gui.listBox(box, self, "selected_classes", "class_values",
                    callback=self._update_prediction_delegate,
                    selectionMode=QListWidget.MultiSelection,
                    addSpace=False)
        gui.checkBox(box, self, "show_predictions", "Show predicted class",
                     callback=self._update_prediction_delegate)
        gui.checkBox(box, self, "draw_dist", "Distribution bars",
                     callback=self._update_prediction_delegate)

        box = gui.vBox(self.controlArea, "Data View")
        gui.checkBox(box, self, "show_attrs", "Show full dataset",
                     callback=self._update_column_visibility)
        gui.button(box, self, "Restore Original Order",
                   callback=self._reset_order,
                   tooltip="Show rows in the original order")

        box = gui.vBox(self.controlArea, "Output", spacing=-1)
        self.checkbox_class = gui.checkBox(
            box, self, "output_attrs", "Original data",
            callback=self.commit)
        self.checkbox_class = gui.checkBox(
            box, self, "output_predictions", "Predictions",
            callback=self.commit)
        self.checkbox_prob = gui.checkBox(
            box, self, "output_probabilities", "Probabilities",
            callback=self.commit)

        gui.rubber(self.controlArea)

        self.vsplitter = gui.vBox(self.mainArea)

        self.splitter = QSplitter(
            orientation=Qt.Horizontal,
            childrenCollapsible=False,
            handleWidth=2,
        )
        self.dataview = TableView(
            verticalScrollBarPolicy=Qt.ScrollBarAlwaysOn,
            horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOn,
            horizontalScrollMode=QTableView.ScrollPerPixel,
            selectionMode=QTableView.NoSelection,
            focusPolicy=Qt.StrongFocus
        )
        self.predictionsview = TableView(
            verticalScrollBarPolicy=Qt.ScrollBarAlwaysOff,
            horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOn,
            horizontalScrollMode=QTableView.ScrollPerPixel,
            selectionMode=QTableView.NoSelection,
            focusPolicy=Qt.StrongFocus,
            sortingEnabled=True,
        )

        self.dataview.verticalHeader().hide()

        dsbar = self.dataview.verticalScrollBar()
        psbar = self.predictionsview.verticalScrollBar()

        psbar.valueChanged.connect(dsbar.setValue)
        dsbar.valueChanged.connect(psbar.setValue)

        self.dataview.verticalHeader().setDefaultSectionSize(22)
        self.predictionsview.verticalHeader().setDefaultSectionSize(22)
        self.dataview.verticalHeader().sectionResized.connect(
            lambda index, _, size:
            self.predictionsview.verticalHeader().resizeSection(index, size)
        )

        self.splitter.addWidget(self.predictionsview)
        self.splitter.addWidget(self.dataview)

        self.score_table = ScoreTable(self)
        self.vsplitter.layout().addWidget(self.splitter)
        self.vsplitter.layout().addWidget(self.score_table.view)

    @Inputs.data
    @check_sql_input
    def set_data(self, data):
        self.Warning.empty_data(shown=data is not None and not data)
        self.data = data
        if not data:
            self.dataview.setModel(None)
            self.predictionsview.setModel(None)
        else:
            # force full reset of the view's HeaderView state
            self.dataview.setModel(None)
            model = TableModel(data, parent=None)
            modelproxy = TableSortProxyModel()
            modelproxy.setSourceModel(model)
            self.dataview.setModel(modelproxy)
            self._update_column_visibility()

        self._invalidate_predictions()

    @property
    def class_var(self):
        return self.data and self.data.domain.class_var

    # pylint: disable=redefined-builtin
    @Inputs.predictors
    def set_predictor(self, predictor=None, id=None):
        if id in self.predictors:
            if predictor is not None:
                self.predictors[id] = self.predictors[id]._replace(
                    predictor=predictor, name=predictor.name, results=None)
            else:
                del self.predictors[id]
        elif predictor is not None:
            self.predictors[id] = PredictorSlot(predictor, predictor.name, None)

    def _set_class_values(self):
        class_values = []
        for slot in self.predictors.values():
            class_var = slot.predictor.domain.class_var
            if class_var and class_var.is_discrete:
                for value in class_var.values:
                    if value not in class_values:
                        class_values.append(value)

        if self.class_var and self.class_var.is_discrete:
            values = self.class_var.values
            self.class_values = sorted(
                class_values, key=lambda val: val not in values)
            self.selected_classes = [
                i for i, name in enumerate(class_values) if name in values]
        else:
            self.class_values = class_values  # This assignment updates listview
            self.selected_classes = []

    def handleNewSignals(self):
        self._set_class_values()
        self._call_predictors()
        self._update_scores()
        self._update_predictions_model()
        self._update_prediction_delegate()
        self._set_errors()
        self._update_info()
        self.commit()

    def _call_predictors(self):
        if not self.data:
            return
        if self.class_var:
            domain = self.data.domain
            classless_data = self.data.transform(
                Domain(domain.attributes, None, domain.metas))
        else:
            classless_data = self.data

        for inputid, slot in self.predictors.items():
            if isinstance(slot.results, Results):
                continue

            predictor = slot.predictor
            try:
                if predictor.domain.class_var.is_discrete:
                    pred, prob = predictor(classless_data, Model.ValueProbs)
                else:
                    pred = predictor(classless_data, Model.Value)
                    prob = numpy.zeros((len(pred), 0))
            except (ValueError, DomainTransformationError) as err:
                self.predictors[inputid] = \
                    slot._replace(results=f"{predictor.name}: {err}")
                continue

            results = Results()
            results.data = self.data
            results.domain = self.data.domain
            results.row_indices = numpy.arange(len(self.data))
            results.folds = (Ellipsis, )
            results.actual = self.data.Y
            results.unmapped_probabilities = prob
            results.unmapped_predicted = pred
            results.probabilities = results.predicted = None
            self.predictors[inputid] = slot._replace(results=results)

            target = predictor.domain.class_var
            if target != self.class_var:
                continue

            if target is not self.class_var and target.is_discrete:
                backmappers, n_values = predictor.get_backmappers(self.data)
                prob = predictor.backmap_probs(prob, n_values, backmappers)
                pred = predictor.backmap_value(pred, prob, n_values, backmappers)
            results.predicted = pred.reshape((1, len(self.data)))
            results.probabilities = prob.reshape((1,) + prob.shape)

    def _update_scores(self):
        model = self.score_table.model
        model.clear()
        scorers = usable_scorers(self.class_var) if self.class_var else []
        self.score_table.update_header(scorers)
        errors = []
        for inputid, pred in self.predictors.items():
            results = self.predictors[inputid].results
            if not isinstance(results, Results) or results.predicted is None:
                continue
            row = [QStandardItem(learner_name(pred.predictor)),
                   QStandardItem("N/A"), QStandardItem("N/A")]
            for scorer in scorers:
                item = QStandardItem()
                try:
                    score = scorer_caller(scorer, results)()[0]
                    item.setText(f"{score:.3f}")
                except Exception as exc:  # pylint: disable=broad-except
                    item.setToolTip(str(exc))
                    if scorer.name in self.score_table.shown_scores:
                        errors.append(str(exc))
                row.append(item)
            self.score_table.model.appendRow(row)

        view = self.score_table.view
        if model.rowCount():
            view.setVisible(True)
            view.ensurePolished()
            view.setFixedHeight(
                5 + view.horizontalHeader().height() +
                view.verticalHeader().sectionSize(0) * model.rowCount())
        else:
            view.setVisible(False)

        self.Error.scorer_failed("\n".join(errors), shown=bool(errors))

    def _set_errors(self):
        # Not all predictors are run every time, so errors can't be collected
        # in _call_predictors
        errors = "\n".join(
            f"- {p.predictor.name}: {p.results}"
            for p in self.predictors.values()
            if isinstance(p.results, str) and p.results)
        self.Error.predictor_failed(errors, shown=bool(errors))

        if self.class_var:
            inv_targets = "\n".join(
                f"- {pred.name} predicts '{pred.domain.class_var.name}'"
                for pred in (p.predictor for p in self.predictors.values()
                             if isinstance(p.results, Results)
                             and p.results.probabilities is None))
            self.Warning.wrong_targets(inv_targets, shown=bool(inv_targets))
        else:
            self.Warning.wrong_targets.clear()

    def _update_info(self):
        n_predictors = len(self.predictors)
        if not self.data and not n_predictors:
            self.info.set_input_summary(self.info.NoInput)
            return

        n_valid = len(self._non_errored_predictors())
        summary = str(len(self.data)) if self.data else "0"
        details = f"{len(self.data)} instances" if self.data else "No data"
        details += f"\n{n_predictors} models" if n_predictors else "No models"
        if n_valid != n_predictors:
            details += f" ({n_predictors - n_valid} failed)"
        self.info.set_input_summary(summary, details)

        discrete_predictors = any(
            slot.predictor.domain.class_var.is_discrete
            for slot in self.predictors.values())
        self.checkbox_class.setEnabled(discrete_predictors)
        self.checkbox_prob.setEnabled(discrete_predictors)

    def _invalidate_predictions(self):
        for inputid, pred in list(self.predictors.items()):
            self.predictors[inputid] = pred._replace(results=None)

    def _non_errored_predictors(self):
        return [p for p in self.predictors.values()
                if isinstance(p.results, Results)]

    def _update_predictions_model(self):
        results = []
        headers = []
        for p in self._non_errored_predictors():
            values = p.results.unmapped_predicted
            target = p.predictor.domain.class_var
            if target.is_discrete:
                prob = p.results.unmapped_probabilities
                values = [Value(target, v) for v in values]
            else:
                prob = numpy.zeros((len(values), 0))
            results.append((values, prob))
            headers.append(p.predictor.name)

        if results:
            results = list(zip(*(zip(*res) for res in results)))
            model = PredictionsModel(results, headers)
        else:
            model = None

        predmodel = PredictionsSortProxyModel()
        predmodel.setSourceModel(model)
        predmodel.setDynamicSortFilter(True)
        self.predictionsview.setModel(predmodel)
        hheader = self.predictionsview.horizontalHeader()
        hheader.setSortIndicatorShown(False)
        # SortFilterProxyModel is slow due to large abstraction overhead
        # (every comparison triggers multiple `model.index(...)`,
        # model.rowCount(...), `model.parent`, ... calls)
        hheader.setSectionsClickable(predmodel.rowCount() < 20000)

        predmodel.layoutChanged.connect(self._update_data_sort_order)
        self._update_data_sort_order()
        self.predictionsview.resizeColumnsToContents()

    def _update_column_visibility(self):
        if self.data:
            domain = self.data.domain
            first_attr = len(domain.class_vars) + len(domain.metas)
            for i in range(first_attr, first_attr + len(domain.attributes)):
                self.dataview.setColumnHidden(i, not self.show_attrs)
            if domain.class_var:
                self.dataview.setColumnHidden(0, False)

    def _update_data_sort_order(self):
        datamodel = self.dataview.model()  # data model proxy
        predmodel = self.predictionsview.model()  # predictions model proxy
        sortindicatorshown = False
        if datamodel is not None:
            assert isinstance(datamodel, TableSortProxyModel)
            n = datamodel.rowCount()
            if predmodel is not None and predmodel.sortColumn() >= 0:
                sortind = numpy.argsort(
                    [predmodel.mapToSource(predmodel.index(i, 0)).row()
                     for i in range(n)])
                sortind = numpy.array(sortind, numpy.int)
                sortindicatorshown = True
            else:
                sortind = None

            datamodel.setSortIndices(sortind)

        self.predictionsview.horizontalHeader() \
            .setSortIndicatorShown(sortindicatorshown)

    def _reset_order(self):
        datamodel = self.dataview.model()
        predmodel = self.predictionsview.model()
        if datamodel is not None:
            datamodel.sort(-1)
        if predmodel is not None:
            predmodel.sort(-1)
        self.predictionsview.horizontalHeader().setSortIndicatorShown(False)

    def _update_prediction_delegate(self):
        selected = {self.class_values[i] for i in self.selected_classes}
        self._delegates.clear()
        for col, slot in enumerate(self.predictors.values()):
            target = slot.predictor.domain.class_var
            shown_probs = () if target.is_continuous else \
                [i for i, name in enumerate(target.values) if name in selected]
            if not shown_probs and not self.show_predictions:
                self.predictionsview.setColumnHidden(col, True)
                continue
            delegate = PredictionsItemDelegate(
                target, self.show_predictions, shown_probs)
            # QAbstractItemView does not take ownership of delegates, so we must
            self._delegates.append(delegate)
            self.predictionsview.setItemDelegateForColumn(col, delegate)
            self.predictionsview.setColumnHidden(col, False)

        self.predictionsview.resizeColumnsToContents()
        self._update_spliter()

    def _update_spliter(self):
        if not self.data:
            return

        def width(view):
            h_header = view.horizontalHeader()
            v_header = view.verticalHeader()
            return h_header.length() + v_header.width()

        w = width(self.predictionsview) + 4
        w1, w2 = self.splitter.sizes()
        self.splitter.setSizes([w, w1 + w2 - w])

    def commit(self):
        self._commit_predictions()
        self._commit_evaluation_results()

    def _commit_evaluation_results(self):
        slots = [p for p in self._non_errored_predictors()
                 if p.results.predicted is not None]
        if not slots:
            self.Outputs.evaluation_results.send(None)
            return

        nanmask = numpy.isnan(self.data.get_column_view(self.class_var)[0])
        data = self.data[~nanmask]
        results = Results(data, store_data=True)
        results.folds = None
        results.row_indices = numpy.arange(len(data))
        results.actual = data.Y.ravel()
        results.predicted = numpy.vstack(
            tuple(p.results.predicted[0][~nanmask] for p in slots))
        if self.class_var and self.class_var.is_discrete:
            results.probabilities = numpy.array(
                [p.results.probabilities[0][~nanmask] for p in slots])
        results.learner_names = [p.name for p in slots]
        self.Outputs.evaluation_results.send(results)

    def _commit_predictions(self):
        if not self.data:
            self.Outputs.predictions.send(None)
            return

        newmetas = []
        newcolumns = []
        for slot in self._non_errored_predictors():
            if slot.predictor.domain.class_var.is_discrete:
                self._add_classification_out_columns(slot, newmetas, newcolumns)
            else:
                self._add_regression_out_columns(slot, newmetas, newcolumns)

        attrs = list(self.data.domain.attributes) if self.output_attrs else []
        metas = list(self.data.domain.metas) + newmetas
        domain = Orange.data.Domain(attrs, self.class_var, metas=metas)
        predictions = self.data.transform(domain)
        if newcolumns:
            newcolumns = numpy.hstack(
                [numpy.atleast_2d(cols) for cols in newcolumns])
            predictions.metas[:, -newcolumns.shape[1]:] = newcolumns
        self.Outputs.predictions.send(predictions)

    def _add_classification_out_columns(self, slot, newmetas, newcolumns):
        # Mapped or unmapped predictions?!
        # Or provide a checkbox so the user decides?
        pred = slot.predictor
        name = pred.name
        values = pred.domain.class_var.values
        newmetas.append(DiscreteVariable(name=name, values=values))
        newcolumns.append(slot.results.unmapped_predicted.reshape(-1, 1))
        if self.output_probabilities:
            newmetas += [ContinuousVariable(name=f"{name} ({value})")
                         for value in values]
            newcolumns.append(slot.results.unmapped_probabilities)

    @staticmethod
    def _add_regression_out_columns(slot, newmetas, newcolumns):
        newmetas.append(ContinuousVariable(name=slot.predictor.name))
        newcolumns.append(slot.results.unmapped_predicted.reshape((-1, 1)))

    def send_report(self):
        def merge_data_with_predictions():
            data_model = self.dataview.model()
            predictions_model = self.predictionsview.model()

            # use ItemDelegate to style prediction values
            style = lambda x: self.predictionsview.itemDelegate().displayText(x, QLocale())

            # iterate only over visible columns of data's QTableView
            iter_data_cols = list(filter(lambda x: not self.dataview.isColumnHidden(x),
                                         range(data_model.columnCount())))

            # print header
            yield [''] + \
                  [predictions_model.headerData(col, Qt.Horizontal, Qt.DisplayRole)
                   for col in range(predictions_model.columnCount())] + \
                  [data_model.headerData(col, Qt.Horizontal, Qt.DisplayRole)
                   for col in iter_data_cols]

            # print data & predictions
            for i in range(data_model.rowCount()):
                yield [data_model.headerData(i, Qt.Vertical, Qt.DisplayRole)] + \
                      [style(predictions_model.data(predictions_model.index(i, j)))
                       for j in range(predictions_model.columnCount())] + \
                      [data_model.data(data_model.index(i, j))
                       for j in iter_data_cols]

        if self.data:
            text = self.infolabel.text().replace('\n', '<br>')
            if self.selected_classes:
                text += '<br>Showing probabilities for: '
                text += ', '. join([self.class_values[i]
                                    for i in self.selected_classes])
            self.report_paragraph('Info', text)
            self.report_table("Data & Predictions", merge_data_with_predictions(),
                              header_rows=1, header_columns=1)



class PredictionsItemDelegate(QStyledItemDelegate):
    """
    A Item Delegate for custom formatting of predictions/probabilities
    """
    def __init__(self, target, show_predictions, shown_probabilities=(),
                 parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.target = target
        self.colors = None if target.is_continuous else \
                [QColor(*color) for color in target.colors]
        self.shown_probabilities = self.fmt = self.tooltip = None  # set below
        self.setFormat(show_predictions, shown_probabilities)

    def setFormat(self, show_predictions, shown_probabilities=()):
        self.shown_probabilities = shown_probabilities
        target = self.target
        if target.is_continuous:
            self.fmt = f"{{value:{target.format_str[1:]}}}"
        else:
            self.fmt = " \N{RIGHTWARDS ARROW} ".join(
                [" : ".join(f"{{dist[{i}]:.2f}}" for i in shown_probabilities)]
                * bool(shown_probabilities)
                + ["{value!s}"] * show_predictions)
        self.tooltip = "" if not shown_probabilities else \
            f"p({', '.join(target.values[i] for i in shown_probabilities)})"

    def displayText(self, value, _locale):
        try:
            value, dist = value
        except ValueError:
            return ""
        else:
            return self.fmt.format(value=value, dist=dist)

    def helpEvent(self, event, view, option, index):
        if self.tooltip is not None:
            # ... but can be an empty string, so the current tooltip is removed
            QToolTip.showText(event.globalPos(), self.tooltip, view)
            return True
        else:
            return super().helpEvent(event, view, option, index)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if self.target.is_continuous:
            option.displayAlignment = \
                (option.displayAlignment & Qt.AlignVertical_Mask) | \
                Qt.AlignRight

    def sizeHint(self, option, index):
        # reimplemented
        sh = super().sizeHint(option, index)
        if option.widget is not None:
            style = option.widget.style()
        else:
            style = QApplication.style()
        margin = style.pixelMetric(
            QStyle.PM_FocusFrameHMargin, option, option.widget) + 1
        metrics = option.fontMetrics
        height = sh.height() + metrics.leading() + 2 * margin
        return QSize(sh.width(), height)

    @staticmethod
    def distribution(index):
        value = index.data(Qt.DisplayRole)
        if isinstance(value, tuple) and len(value) == 2:
            _, dist = value
            return dist
        else:
            return None

    def paint(self, painter, option, index):
        dist = self.distribution(index)
        if dist is None or self.colors is None:
            super().paint(painter, option, index)
            return
        if not numpy.isfinite(numpy.sum(dist)):
            super().paint(painter, option, index)
            return

        if option.widget is not None:
            style = option.widget.style()
        else:
            style = QApplication.style()

        self.initStyleOption(option, index)

        text = option.text
        metrics = option.fontMetrics

        margin = style.pixelMetric(
            QStyle.PM_FocusFrameHMargin, option, option.widget) + 1
        bottommargin = min(margin, 1)
        rect = option.rect.adjusted(margin, margin, -margin, -bottommargin)

        textrect = style.subElementRect(
            QStyle.SE_ItemViewItemText, option, option.widget)
        # Are the margins included in the subElementRect?? -> No!
        textrect = textrect.adjusted(margin, margin, -margin, -bottommargin)

        text = option.fontMetrics.elidedText(
            text, option.textElideMode, textrect.width())

        spacing = max(metrics.leading(), 1)

        distheight = rect.height() - metrics.height() - spacing
        distheight = numpy.clip(distheight, 2, metrics.height())

        painter.save()
        painter.setClipRect(option.rect)
        painter.setFont(option.font)
        painter.setRenderHint(QPainter.Antialiasing)

        style.drawPrimitive(
            QStyle.PE_PanelItemViewRow, option, painter, option.widget)
        style.drawPrimitive(
            QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        if option.state & QStyle.State_Selected:
            color = option.palette.highlightedText().color()
        else:
            color = option.palette.text().color()
        painter.setPen(QPen(color))

        textrect = textrect.adjusted(0, 0, 0, -distheight - spacing)
        distrect = QRect(
            textrect.bottomLeft() + QPoint(0, spacing),
            QSize(rect.width(), distheight))
        painter.setPen(QPen(Qt.lightGray, 0.3))
        self.drawDistBar(painter, distrect, dist)
        painter.restore()
        if text:
            style.drawItemText(
                painter, textrect, option.displayAlignment, option.palette,
                option.state & QStyle.State_Enabled, text)

    def drawDistBar(self, painter, rect, distribution):
        painter.save()
        painter.translate(rect.topLeft())
        for i in self.shown_probabilities:
            dvalue = distribution[i]
            if not dvalue > 0:  # This also skips nans
                continue
            painter.setBrush(self.colors[i])
            width = rect.width() * dvalue
            painter.drawRoundedRect(QRectF(0, 0, width, rect.height()), 1, 2)
            painter.translate(width, 0.0)
        painter.restore()


class PredictionsSortProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.__probInd = None

    def setProbInd(self, indices):
        self.__probInd = indices
        self.invalidate()

    def lessThan(self, left, right):
        role = self.sortRole()
        left_data = self.sourceModel().data(left, role)
        right_data = self.sourceModel().data(right, role)

        return self._key(left_data) < self._key(right_data)

    def _key(self, prediction):
        value, probs = prediction
        if probs is not None:
            if self.__probInd is not None:
                probs = probs[self.__probInd]
            probs = tuple(probs)

        return probs, value


class PredictionsModel(QAbstractTableModel):
    def __init__(self, table=None, headers=None, parent=None):
        super().__init__(parent)
        self._table = [[]] if table is None else table
        if headers is None:
            headers = [None] * len(self._table)
        self._header = headers
        self.__columnCount = max([len(row) for row in self._table] or [0])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._table)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.__columnCount

    def _value(self, index):
        return self._table[index.row()][index.column()]

    def data(self, index, role=Qt.DisplayRole):
        if role in (Qt.DisplayRole, Qt.EditRole):
            return self._value(index)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return (self._header[section] if section < len(self._header)
                    else str(section))
        return None


class TableView(QTableView):
    MaxSizeHintSamples = 1000

    def sizeHintForColumn(self, column):
        """
        Reimplemented from `QTableView.sizeHintForColumn`

        Note: This does not match the QTableView's implementation,
        in particular size hints from editor/index widgets are not taken
        into account.

        Parameters
        ----------
        column : int
        """
        # This is probably not needed in Qt5?
        if self.model() is None:
            return -1

        self.ensurePolished()
        model = self.model()
        vheader = self.verticalHeader()
        top = vheader.visualIndexAt(0)
        bottom = vheader.visualIndexAt(self.viewport().height())
        if bottom < 0:
            bottom = self.model().rowCount()

        options = self.viewOptions()
        options.widget = self

        width = 0
        sample_count = 0

        for row in range(top, bottom):
            if not vheader.isSectionHidden(vheader.logicalIndex(row)):
                index = model.index(row, column)
                delegate = self.itemDelegate(index)
                if not delegate:
                    continue
                size = delegate.sizeHint(options, index)
                width = max(size.width(), width)
                sample_count += 1

            if sample_count >= TableView.MaxSizeHintSamples:
                break

        return width + 1 if self.showGrid() else width


class TableSortProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.__sortInd = None

    def setSortIndices(self, indices):
        if indices is not None:
            indices = numpy.array(indices, dtype=numpy.int)
            if indices.shape != (self.rowCount(),):
                raise ValueError("indices.shape != (self.rowCount(),)")
            indices.flags.writeable = False

        self.__sortInd = indices

        if self.sortColumn() < 0 and self.__sortInd is not None:
            self.sort(0)  # need some valid sort column
        elif self.__sortInd is None:
            self.sort(-1)  # explicit sort reset
        else:
            self.invalidate()

    def sortIndices(self):
        return self.__sortInd

    def lessThan(self, left, right):
        if self.__sortInd is None:
            return super().lessThan(left, right)

        assert not (left.parent().isValid() or right.parent().isValid()), \
            "Not a table model"

        rleft, rright = left.row(), right.row()
        try:
            ileft, iright = self.__sortInd[rleft], self.__sortInd[rright]
        except IndexError:
            return False
        else:
            return ileft < iright


def tool_tip(value):
    value, dist = value
    if dist is not None:
        return "{!s} {!s}".format(value, dist)
    else:
        return str(value)


if __name__ == "__main__":  # pragma: no cover
    filename = "iris.tab"
    iris = Orange.data.Table(filename)
    idom = iris.domain
    dom = Domain(idom.attributes, DiscreteVariable(idom.class_var.name, idom.class_var.values[:2]))
    iris2 = iris[:100].transform(dom)

    def pred_error(data, *args, **kwargs):
        raise ValueError

    pred_error.domain = iris.domain
    pred_error.name = "To err is human"

    if iris.domain.has_discrete_class:
        predictors = [
            Orange.classification.SVMLearner(probability=True)(iris2),
            Orange.classification.LogisticRegressionLearner()(iris),
            pred_error
        ]
    elif iris.domain.has_continuous_class:
        predictors = [
            Orange.regression.RidgeRegressionLearner(alpha=1.0)(iris),
            Orange.regression.LinearRegressionLearner()(iris),
            pred_error
        ]
    else:
        predictors = [pred_error]

    WidgetPreview(OWPredictions).run(
        set_data=iris2,
        set_predictor=[(pred, i) for i, pred in enumerate(predictors)])
