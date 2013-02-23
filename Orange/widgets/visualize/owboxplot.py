# -*- coding: utf-8 -*-

import operator
import math
import itertools
from scipy import stats
import numpy as np

from PyQt4 import QtCore
from PyQt4 import QtGui
import scipy

from Orange.data import DiscreteVariable, Table, Domain
from Orange.statistics import contingency, distribution

from Orange.widgets import widget, gui
from Orange.widgets.settings import (Setting, DomainContextHandler,
                                     ContextSetting)
from Orange.widgets.utils import datacaching
from Orange.widgets.utils.plot import owaxis


class BoxData:
    def __init__(self, dist):
        self.dist = dist
        self.N = N = np.sum(dist[1])
        if N == 0:
            return
        self.a_min = float(dist[0, 0])
        self.a_max = float(dist[0, -1])
        self.mean = float(np.sum(dist[0] * dist[1]) / N)
        self.var = float(np.sum(dist[1] * (dist[0] - self.mean) ** 2) / N)
        self.dev = math.sqrt(self.var)
        s = 0
        thresholds = [N/4, N/2, 3*N/4]
        thresh_i = 0
        q = []
        for i, e in enumerate(dist[1]):
            s += e
            if s >= thresholds[thresh_i]:
                if s == thresholds[thresh_i] and i + 1 < dist.shape[1]:
                    q.append(float((dist[0, i] + dist[0, i + 1]) / 2))
                else:
                    q.append(float(dist[0, i]))
                thresh_i += 1
                if thresh_i == 3:
                    break
        while len(q) < 3:
            q.append(q[-1])
        self.q25, self.median, self.q75 = q


class OWBoxPlot(widget.OWWidget):
    _name = "Box plot"
    _description = "Shows box plots"
    _long_description = """Shows box plots, either one for or multiple
    box plots for data split by an attribute value."""
    #_icon = "icons/Boxplot.svg"
    _priority = 100
    _author = "Amela Rakanović, Janez Demšar"
    inputs = [("Data", Table, "data")]
    outputs = [("Basic statistic", Table)]

    settingsHandler = DomainContextHandler()
    display = Setting(0)
    grouping_select = ContextSetting([0])
    attributes_select = ContextSetting([0])
    stattest = Setting(0)
    sig_threshold = Setting(0.05)

    _sorting_criteria_attrs = ["", "", "median", "mean"]
    _label_positions = ["q25", "median", "mean"]

    tick_pen = QtGui.QPen(QtCore.Qt.white, 5)
    axis_pen = QtGui.QPen(QtCore.Qt.darkGray, 3)
    for pen in (tick_pen, axis_pen):
        pen.setCosmetic(True)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
    axis_pen.setCapStyle(QtCore.Qt.RoundCap)
    tick_pen.setCapStyle(QtCore.Qt.FlatCap)

    def __init__(self, parent=None, signalManager=None, settings=None):
        super().__init__(parent, signalManager, settings)

        self.grouping = []
        self.attributes = []

        self.slider_intervals = 1
        self.ddataset = None

        ## Control Area
        self.attr_list_box = gui.listBox(self.controlArea, self,
            "attributes_select", "attributes", box="Variable",
            selectionMode=QtGui.QListWidget.SingleSelection,
            callback=self.attr_changed)

        gb = gui.widgetBox(self.controlArea, orientation=1)
        self.attrCombo = gui.listBox(gb, self,
            'grouping_select', "grouping", box="Grouping",
            selectionMode=QtGui.QListWidget.SingleSelection,
            callback=self.attr_changed)

        self.sorting_combo = gui.radioButtonsInBox(gb, self,
            'display', box='Display', callback=self.display_changed,
            btnLabels=["Box plots", "Anotated boxes",
                       "Compare medians", "Compare means"])

        gui.rubber(self.controlArea)

        ## Main Area
        result = gui.widgetBox(self.mainArea, addSpace=True)
        self.boxScene = QtGui.QGraphicsScene()
        self.boxView = QtGui.QGraphicsView(self.boxScene)
        self.boxView.setRenderHints(QtGui.QPainter.Antialiasing |
                                    QtGui.QPainter.TextAntialiasing |
                                    QtGui.QPainter.SmoothPixmapTransform)
        self.mainArea.layout().addWidget(self.boxView)
        self.posthoc_lines = []

        e = gui.widgetBox(self.mainArea, addSpace=False, orientation=0)
        self.infot1 = gui.widgetLabel(e, "<center>No test results.</center>")

        self.warning = gui.widgetBox(self.controlArea, "Warning:")
        self.warning_info = gui.widgetLabel(self.warning, "")
        self.warning.hide()

        #self.controlArea.setFixedWidth(250)
        self.mainArea.setMinimumWidth(650)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.layout_changed()

    def data(self, dataset):
        if dataset is not None and (
                not len(dataset) or not len(dataset.domain)):
            dataset = None
        self.closeContext()
        self.ddataset = dataset
        self.grouping_select = []
        self.attributes_select = []
        self.attr_list_box.clear()
        self.attrCombo.clear()
        self.openContext(self.ddataset)
        if dataset:
            self.attrCombo.addItem("(none)")
            attributes = []
            grouping = ["None"]
            for attr in self.ddataset.domain:
                if isinstance(attr, DiscreteVariable):
                    grouping.append((attr.name, attr.var_type))
                else:
                    attributes.append((attr.name, attr.var_type))
            self.attributes = attributes
            self.grouping = grouping
            self.grouping_select = [0]
            self.attributes_select = [0]
            self.attr_changed()
        else:
            self.reset_all_data()

    def reset_all_data(self):
        self.attr_list_box.clear()
        self.attrCombo.clear()
        self.boxScene.clear()
        self.send("Basic statistic", None)
        self.send("Significant data", None)

    def attr_changed(self):
        self.compute_box_data()
        self.sorting_combo.setDisabled(len(self.stats) < 2)
        self.layout_changed()

    def layout_changed(self):
        self.boxScene.clear()
        self.posthoc_lines = []
        if not self.stats:
            return

        attr = self.attributes[self.attributes_select[0]][0]
        attr = self.ddataset.domain[attr]

        self.draw_axis()
        self.boxes = [self.box_group(stat) for stat in self.stats]
        self.labels = [self.label_group(stat, attr, lab)
                       for stat, lab in zip(self.stats, self.label_txts)]
        self.attr_labels = [self.attr_label(lab) for lab in self.label_txts]
        for it in itertools.chain(self.labels, self.boxes, self.attr_labels):
            self.boxScene.addItem(it)
        self.display_changed()

    def display_changed(self):
        self.order = list(range(len(self.stats)))
        criterion = self._sorting_criteria_attrs[self.display]
        if criterion:
            self.order.sort(key=lambda i: getattr(self.stats[i], criterion))
        heights = 90 if self.display == 1 else 60

        for row, box_index in enumerate(self.order):
            y = (-len(self.stats) + row) * heights + 10
            self.boxes[box_index].setY(y)
            labels = self.labels[box_index]
            if self.display == 1:
                labels.show()
                labels.setY(y)
            else:
                labels.hide()
            label = self.attr_labels[box_index]
            label.setY(y - 15 - label.boundingRect().height())
            if self.display == 1:
                label.hide()
            else:
                stat = self.stats[box_index]
                poss = (stat.q25, -1, stat.median + 5 / self.scale_x,
                        stat.mean + 5 / self.scale_x)
                label.show()
                label.setX(poss[self.display] * self.scale_x)

        r = QtCore.QRectF(self.scene_min_x, -30 - len(self.stats) * heights,
                          self.scene_width, len(self.stats) * heights + 90)
        self.boxScene.setSceneRect(r)
        self.boxView.centerOn(self.scene_min_x + self.scene_width / 2,
                              -30 - len(self.stats) * heights / 2 + 45)

        self.compute_tests()
        self.show_posthoc()

    def show_posthoc(self):
        def line(y0, y1):
            it = self.boxScene.addLine(x, y0, x, y1, post_pen)
            it.setZValue(-100)
            self.posthoc_lines.append(it)

        while self.posthoc_lines:
            self.boxScene.removeItem(self.posthoc_lines.pop())
        if self.display < 2 or len(self.stats) < 2:
            return
        crit_line = self._sorting_criteria_attrs[self.display]
        post_pen = QtGui.QPen(QtCore.Qt.lightGray, 2)
        post_pen.setCosmetic(True)
        xs = []
        y_up = -len(self.stats) * 60 + 10
        for pos, box_index in enumerate(self.order):
            stat = self.stats[box_index]
            x = getattr(stat, crit_line) * self.scale_x
            xs.append(x)
            by = y_up + pos * 60
            line(by + 12, 3)
            line(by - 12, by - 25)

        grp_pen = QtGui.QPen(QtCore.Qt.lightGray, 4)
        grp_pen.setCosmetic(True)
        used_to = []
        last_to = 0
        for frm, frm_x in enumerate(xs[:-1]):
            for to in range(frm + 1, len(xs)):
                if xs[to] - frm_x > 1.5:
                    to -= 1
                    break
            if last_to == to or frm == to:
                continue
            for rowi, used in enumerate(used_to):
                if used < frm:
                    used_to[rowi] = to
                    break
            else:
                rowi = len(used_to)
                used_to.append(to)
            y = - 6 - rowi * 6
            it = self.boxScene.addLine(frm_x - 2, y, xs[to] + 2, y,
                                       grp_pen)
            self.posthoc_lines.append(it)
            last_to = to

    def compute_box_data(self):
        dataset = self.ddataset
        if dataset is None:
            self.stats = []
            return
        attr = self.attributes[self.attributes_select[0]][0]
        attr_ind = dataset.domain.index(attr)
        group_by = self.grouping_select[0]
        if group_by:
            group_attr = self.grouping[group_by][0]
            group_ind = dataset.domain.index(group_attr)
            self.conts = datacaching.getCached(dataset,
                contingency.get_contingency,
                (dataset, attr_ind, group_ind))
            self.stats = [BoxData(cont) for cont in self.conts]
            self.label_txts = dataset.domain[group_ind].values
        else:
            self.dist = datacaching.getCached(dataset,
                distribution.get_distribution, (attr_ind, dataset))
            self.stats = [BoxData(self.dist)]
            self.label_txts = [""]
        self.stats = [stat for stat in self.stats if stat.N > 0]

    def attr_label(self, text):
        return QtGui.QGraphicsSimpleTextItem(text)

    _pen_med = QtGui.QPen(QtGui.QBrush(QtGui.QColor(0xff, 0xff, 0x00)), 2)
    _pen_dark = QtGui.QPen(QtGui.QBrush(QtGui.QColor(0x33, 0x00, 0xff)), 2)
    _pen_dark_dotted = QtGui.QPen(QtGui.QBrush(QtGui.QColor(0x33, 0x00, 0xff)),
                                  1)
    _pen_dark_wide = QtGui.QPen(QtGui.QBrush(QtGui.QColor(0x33, 0x00, 0xff)),
                                2)
    for pen in (_pen_dark, _pen_med, _pen_dark_wide, _pen_dark_dotted):
        pen.setCosmetic(True)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
    _pen_dark_dotted.setStyle(QtCore.Qt.DotLine)

    def box_group(self, stat, height=20):
        def line(x0, y0, x1, y1, *args, **kwargs):
            return QtGui.QGraphicsLineItem(x0 * scale_x, y0, x1 * scale_x, y1,
                                           *args, **kwargs)

        scale_x = self.scale_x
        box = QtGui.QGraphicsItemGroup()
        whisker1 = line(stat.a_min, -1.5, stat.a_min, 1.5, box)
        whisker2 = line(stat.a_max, -1.5, stat.a_max, 1.5, box)
        vert_line = line(stat.a_min, 0, stat.a_max, 0, box)
        mean_line = line(stat.mean, -height / 3, stat.mean, height / 3, box)
        for it in (whisker1, whisker2, mean_line):
            it.setPen(self._pen_dark)
        vert_line.setPen(self._pen_dark_dotted)
        var_line = line(stat.mean - stat.dev, 0, stat.mean + stat.dev, 0, box)
        var_line.setPen(self._pen_dark_wide)

        mbox = QtGui.QGraphicsRectItem(stat.q25 * scale_x, -height / 2,
                                       (stat.q75 - stat.q25) * scale_x, height,
                                       box)
        mbox.setBrush(QtGui.QBrush(QtGui.QColor(0x33, 0x88, 0xff, 0xc0)))
        mbox.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        mbox.setZValue(-200)

        median_line = line(stat.median, -height / 2,
                           stat.median, height / 2, box)
        median_line.setPen(self._pen_med)
        median_line.setZValue(-150)

        return box


    _label_font = QtGui.QFont()
    _label_font.setPixelSize(11)
    _attr_brush = QtGui.QBrush(QtGui.QColor(0x33, 0x00, 0xff))

    def label_group(self, stat, attr, val_name):
        def repr_val(val):
            return "%.*f" % (attr.number_of_decimals + 1, val)

        def sc(x):
            return x * scale_x

        def centered_text(x, d, text=None):
            l = QtGui.QGraphicsLineItem(sc(x), 12 * d, sc(x), 20 * d, labels)
            t = QtGui.QGraphicsSimpleTextItem(
                repr_val(x) if text is None else text, labels)
            t.setFont(self._label_font)
            bbox = t.boundingRect()
            t.setPos(sc(x) - bbox.width() / 2,
                     22 * d - (bbox.height() if d == -1 else 0))
            return t, l

        def move_label(label, frm, to):
            label.setX(to)
            to += t_box.width() / 2
            path = QtGui.QPainterPath()
            path.lineTo(0, 4)
            path.lineTo(to - frm, 4)
            path.lineTo(to - frm, 8)
            p = QtGui.QGraphicsPathItem(path)
            p.setPos(frm, 12)
            labels.addToGroup(p)

        scale_x = self.scale_x
        labels = QtGui.QGraphicsItemGroup()
        t, _ = centered_text(stat.mean, -1)
        tpm = QtGui.QGraphicsSimpleTextItem(
            " \u00b1 " + repr_val(math.sqrt(stat.var)), labels)
        tpm.setFont(self._label_font)
        tpm.setPos(sc(stat.mean) + t.boundingRect().width() / 2,
                   -22 - tpm.boundingRect().height())
        if val_name:
            vnm = QtGui.QGraphicsSimpleTextItem(val_name + ": ", labels)
            vnm.setFont(self._label_font)
            vnm.setBrush(self._attr_brush)
            vb = vnm.boundingRect()
            vnm.setPos(sc(stat.mean) - t.boundingRect().width() / 2 -
                       vb.width(), -22 - vb.height())

        med_t, _ = centered_text(stat.median, 1)
        med_box = med_t.boundingRect()

        t, lne = centered_text(stat.q25, 1)
        t_box = t.boundingRect()
        med_left = sc(stat.median) - med_box.width() / 2
        if sc(stat.q25) + t_box.width() / 2 >= med_left - 10:
            labels.removeFromGroup(lne)
            move_label(t, sc(stat.q25), med_left - t_box.width() - 10)

        t, lne = centered_text(stat.q75, 1)
        t_box = t.boundingRect()
        med_right = sc(stat.median) + med_box.width() / 2
        if sc(stat.q75) - t_box.width() / 2 <= med_right + 10:
            labels.removeFromGroup(lne)
            move_label(t, sc(stat.q75), med_right + 10)

        return labels

    def draw_axis(self):
        """Draw the horizontal axis and sets self.scale_x"""
        bottom = min(stat.a_min for stat in self.stats)
        top = max(stat.a_max for stat in self.stats)

        first_val, step = owaxis.OWAxis.compute_scale(bottom, top)
        while bottom < first_val:
            first_val -= step
        bottom = first_val
        no_ticks = math.ceil((top - first_val) / step) + 1
        top = max(top, first_val + (no_ticks - 1) * step)

        gbottom = min(bottom, min(stat.mean - stat.dev for stat in self.stats))
        gtop = max(top, max(stat.mean + stat.dev for stat in self.stats))

        bv = self.boxView
        viewRect = bv.viewport().rect().adjusted(15, 15, -15, -30)
        self.scale_x = scale_x = viewRect.width() / (gtop - gbottom)

        self.scene_min_x = gbottom * scale_x
        self.scene_width = (gtop - gbottom) * scale_x

        font = QtGui.QFont()
        font.setPixelSize(12)
        val = first_val
        attr = self.attributes[self.attributes_select[0]][0]
        attr_desc = self.ddataset.domain[attr]
        while True:
            l = self.boxScene.addLine(val * scale_x, -1, val * scale_x, 1,
                                      self.tick_pen)
            l.setZValue(100)
            t = self.boxScene.addSimpleText(attr_desc.repr_val(val), font)
            t.setFlags(t.flags() |
                       QtGui.QGraphicsItem.ItemIgnoresTransformations)
            r = t.boundingRect()
            t.setPos(val * scale_x - r.width() / 2, 8)
            if val >= top:
                break
            val += step
        self.boxScene.addLine(bottom * scale_x - 4, 0,
                              top * scale_x + 4, 0, self.axis_pen)


    def compute_tests(self):
        self.warning.hide()
        if self.display < 2 or len(self.stats) < 2:
            t = ""
        elif any(s.N <= 1 for s in self.stats):
            t = "At least one group has just one instance, " \
                "cannot compute significance"
        elif len(self.stats) == 2:
            if self.display == 2:
                z, self.p = self.stat_wilc()
                t = "Mann-Whitney's z: %.1f (p=%.3f)" % (z, self.p)
            else:
                t, self.p = self.stat_ttest()
                t = "Student's t: %.3f (p=%.3f)" % (t, self.p)
        else:
            if self.display == 2:
                U, self.p = self.stat_kruskal()
                t = "Kruskal Wallis's U: %.1f (p=%.3f)" % (U, self.p)
            else:
                F, self.p = self.stat_ANOVA()
                t = "ANOVA: %.3f (p=%.3f)" % (F, self.p)
        self.infot1.setText("<center>%s</center>" % t)

    def stat_ttest(self):
        d1, d2 = self.stats
        pooled_var = d1.var / d1.N + d2.var / d2.N
        df = pooled_var ** 2 / \
            ((d1.var / d1.N) ** 2 / (d1.N - 1) +
             (d2.var / d2.N) ** 2 / (d2.N - 1))
        t = (d1.mean - d2.mean) / math.sqrt(pooled_var)
        # TODO check!!!
        p = 2 * (1 - scipy.special.stdtr(df, t))
        return t, p

    def stat_wilc(self):
        s1, s2 = self.stats
        d1, d2 = s1.dist, s2.dist
        ni1, ni2 = d1.shape[1], d2.shape[1]
        i1 = i2 = 0
        R = 0
        rank = 0
        while i1 < ni1 and i2 < ni2:
            if d1[0, i1] < d2[0, i2]:
                R += (rank + d1[1, i1] / 2)
                rank += d1[1, i1]
                i1 += 1
            elif d1[0, i1] == d2[0, i2]:
                br = d1[1, i1] + d2[1, i2]
                R += (rank + br / 2)
                rank += br
                i1 += 1
                i2 += 1
            else:
                rank += d2[1, i2]
                i2 += 1
        if i1 < ni1:
            R = np.sum(d1[1, i1:]) / 2
        U = R - s1.N * (s1.N + 1) / 2
        m = s1.N * (s1.N + s2.N + 1) / 2
        var = s2.N * m / 6
        z = (U - m) / math.sqrt(var)
        # TODO check!!!
        p = 2 * (1 - scipy.special.ndtr(z))
        return z, p

    def stat_ANOVA(self):
        N = sum(stat.N for stat in self.stats)
        grand_avg = sum(stat.N * stat.mean for stat in self.stats) / N
        var_between = sum(stat.N * (stat.mean - grand_avg) ** 2
                          for stat in self.stats)
        df_between = len(self.stats) - 1

        var_within = sum(stat.N * stat.var for stat in self.stats)
        df_within = N - len(self.stats)
        F = (var_between / df_between) / (var_within / df_within)
        p = 1 - scipy.special.fdtr(df_between, df_within, F)
        return F, p

    def stat_kruskal(self):
        return -1, -1
